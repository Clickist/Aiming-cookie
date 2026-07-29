from __future__ import annotations

import json

import pytest

from webapp.backend import benchmark_catalog, benchmark_store, coach_context_refs, coach_store


def _records(*, easier_completed: int = 39, medium_completed: int = 39) -> list[dict]:
    catalog = benchmark_catalog.load_catalog()
    observed_at = "2026-07-29T10:15:00Z"
    records: list[dict] = []
    for difficulty in ("easier", "medium"):
        completed = easier_completed if difficulty == "easier" else medium_completed
        for index, pair in enumerate(catalog["pairs"]):
            scenario = pair[difficulty]
            records.extend((
                {
                    "provider": "kovaaks-webapp",
                    "catalog_version": catalog["catalog_version"],
                    "scenario_id": scenario["scenario_id"],
                    "metric_key": "score",
                    "value": float(index + 1) if index < completed else 0.0,
                    "observed_at": observed_at,
                    "external_identity_ref": "private-external-identity",
                },
                {
                    "provider": "kovaaks-webapp",
                    "catalog_version": catalog["catalog_version"],
                    "scenario_id": scenario["scenario_id"],
                    "metric_key": "scenario_rank",
                    "value": float(index % 10),
                    "observed_at": observed_at,
                    "external_identity_ref": "private-external-identity",
                },
            ))
        records.append({
            "provider": "kovaaks-webapp",
            "catalog_version": catalog["catalog_version"],
            "scenario_id": f"benchmark:viscose-s2:{difficulty}",
            "metric_key": "overall_rank",
            "value": 3.0,
            "observed_at": observed_at,
            "external_identity_ref": "private-external-identity",
        })
    return records


def test_coach_benchmark_summary_is_deidentified_complete_and_bounded():
    summary = coach_context_refs.project_benchmark_summary(_records())

    assert summary is not None
    assert summary["schema_version"] == "coach_benchmark_summary.v1"
    assert summary["completion"] == {
        "easier": {"completed": 39, "required": 39},
        "medium": {"completed": 39, "required": 39},
    }
    assert summary["provisional_ranks"] == {
        "easier": 3,
        "medium": 3,
    }
    assert len(summary["scenarios"]) == 78
    assert len(summary["review_candidates"]) == 8
    assert all(set(item) == {
        "difficulty", "scenario_name", "category", "subcategory", "score", "scenario_rank",
    }
               for item in summary["scenarios"])
    assert all(set(item) == {
        "difficulty", "scenario_name", "category", "subcategory", "score", "scenario_rank",
    }
               for item in summary["review_candidates"])
    assert summary["scenarios"][0]["category"] == "control_tracking"
    assert summary["scenarios"][0]["subcategory"] == "arm"

    wire = json.dumps(summary, ensure_ascii=False)
    assert "private-external-identity" not in wire
    assert "external_identity_ref" not in wire
    assert "http" not in wire
    assert "payload" not in wire


def test_coach_benchmark_summary_counts_only_played_scenarios_as_complete():
    summary = coach_context_refs.project_benchmark_summary(
        _records(easier_completed=18, medium_completed=7),
    )

    assert summary is not None
    assert summary["completion"] == {
        "easier": {"completed": 18, "required": 39},
        "medium": {"completed": 7, "required": 39},
    }


def test_coach_benchmark_summary_ignores_partial_newer_and_other_provider_records():
    records = _records()
    records.append({
        "provider": "manual-local",
        "catalog_version": benchmark_catalog.load_catalog()["catalog_version"],
        "scenario_id": "viscose-s2:easier:01",
        "metric_key": "score",
        "value": 999.0,
        "observed_at": "2026-07-30T10:15:00Z",
    })
    records.append({
        "provider": "kovaaks-webapp",
        "catalog_version": benchmark_catalog.load_catalog()["catalog_version"],
        "scenario_id": "viscose-s2:easier:01",
        "metric_key": "score",
        "value": 999.0,
        "observed_at": "2026-07-30T10:15:00Z",
    })

    summary = coach_context_refs.project_benchmark_summary(records)

    assert summary is not None
    assert summary["observed_at"] == "2026-07-29T10:15:00Z"
    assert summary["scenarios"][0]["score"] == 1.0


def test_coach_benchmark_summary_rejects_duplicate_or_unsafe_record_values():
    duplicate = _records()
    duplicate.append(dict(duplicate[0]))
    assert coach_context_refs.project_benchmark_summary(duplicate) is None

    unsafe = _records()
    unsafe[0]["scenario_id"] = "https://example.invalid/private"
    assert coach_context_refs.project_benchmark_summary(unsafe) is None

    contradictory = coach_context_refs.project_benchmark_summary(_records())
    assert contradictory is not None
    contradictory["review_candidates"][0]["score"] += 1
    assert coach_context_refs._coerce_benchmark_summary(contradictory) is None


def test_coach_benchmark_summary_rejects_unknown_or_mismatched_source_course_labels():
    summary = coach_context_refs.project_benchmark_summary(_records())
    assert summary is not None

    unknown = json.loads(json.dumps(summary))
    unknown["scenarios"][0]["category"] = "untrusted_category"
    assert coach_context_refs._coerce_benchmark_summary(unknown) is None

    mismatched = json.loads(json.dumps(summary))
    mismatched["scenarios"][0]["subcategory"] = "wrist"
    assert coach_context_refs._coerce_benchmark_summary(mismatched) is None

    provider = json.loads(json.dumps(summary))
    provider["scenarios"][0]["provider_payload"] = {"url": "https://example.invalid/private"}
    assert coach_context_refs._coerce_benchmark_summary(provider) is None


@pytest.mark.asyncio
async def test_context_bundle_attaches_only_the_owner_complete_benchmark_snapshot():
    records = _records()
    for record in records:
        record.update({
            "provider_license_note": "User-authorized KovaaK benchmark score import.",
            "unit": "rank" if record["metric_key"].endswith("rank") else "points",
            "availability": "available",
            "external_identity_ref": None,
            "identity_consent": False,
        })
    await benchmark_store.create_records_atomically("owner-a", records)
    thread_a = await coach_store.get_or_create_primary_thread("owner-a")
    thread_b = await coach_store.get_or_create_primary_thread("owner-b")

    bundle_a, _ = await coach_context_refs.build_context_bundle(int(thread_a["id"]), None)
    bundle_b, _ = await coach_context_refs.build_context_bundle(int(thread_b["id"]), None)

    assert bundle_a["benchmark_summary"] is not None
    assert bundle_a["benchmark_summary"]["observed_at"] == "2026-07-29T10:15:00Z"
    assert bundle_b["benchmark_summary"] is None
    wire = json.dumps(bundle_a, ensure_ascii=False)
    assert "private-external-identity" not in wire
    assert "http" not in wire
