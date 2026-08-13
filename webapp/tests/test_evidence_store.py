from __future__ import annotations

import json

import pytest

from kovaak_tracker.analysis_evidence import build_page_descriptor_v1
from webapp.backend import config, evidence_store, file_store, queue


def _artifact(analysis_ref: str) -> dict:
    return {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 0,
            "end_ms": 1_000,
            "duration_ms": 1_000,
            "window_semantics": "half_open",
            "timebase_version": "test.v1",
            "start_source": "fixture",
            "end_source": "fixture",
            "warnings": [],
        },
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": ["fixture"],
    }


@pytest.mark.asyncio
async def test_atomic_artifact_write_and_owner_revision_bound_read(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    sid = await queue.enqueue("owner:1", "", "")
    await queue.claim_next("worker:1")

    ref = evidence_store.write_analysis_evidence_artifact(
        session_id=sid,
        owner_id="owner:1",
        artifact=_artifact(f"analysis:{sid}"),
    )
    assert set(ref) == {
        "artifact_ref", "evidence_revision", "contract_version", "checksum_sha256", "size_bytes"
    }
    assert "path" not in json.dumps(ref)

    result = {
        "schema_version": "analysis_result.v2",
        "analysis_id": f"analysis:{sid}",
        "evidence": {"derived_artifact": ref},
    }
    session = file_store.read_json(f"sessions/{sid}.json")
    assert isinstance(session, dict)
    session.update({"status": "done", "worker_id": None, "result": result})
    file_store.write_json(f"sessions/{sid}.json", session)

    loaded = await evidence_store.read_analysis_evidence_artifact(
        owner_id="owner:1",
        analysis_ref=f"analysis:{sid}",
        artifact_ref=ref["artifact_ref"],
        evidence_revision=ref["evidence_revision"],
    )
    assert loaded == _artifact(f"analysis:{sid}")

    with pytest.raises(evidence_store.EvidenceAccessError, match="owner"):
        await evidence_store.read_analysis_evidence_artifact(
            owner_id="owner:2",
            analysis_ref=f"analysis:{sid}",
            artifact_ref=ref["artifact_ref"],
            evidence_revision=ref["evidence_revision"],
        )
    with pytest.raises(evidence_store.EvidenceAccessError, match="revision"):
        await evidence_store.read_analysis_evidence_artifact(
            owner_id="owner:1",
            analysis_ref=f"analysis:{sid}",
            artifact_ref=ref["artifact_ref"],
            evidence_revision="sha256:" + "0" * 64,
        )


@pytest.mark.asyncio
async def test_artifact_v2_write_read_preserves_actual_contract_version(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    sid = await queue.enqueue("owner:1", "", "")
    artifact = _artifact(f"analysis:{sid}")
    artifact["schema_version"] = "analysis_evidence_artifact.v2"

    ref = evidence_store.write_analysis_evidence_artifact(
        session_id=sid,
        owner_id="owner:1",
        artifact=artifact,
    )
    assert ref["contract_version"] == "analysis_evidence_artifact.v2"

    session = file_store.read_json(f"sessions/{sid}.json")
    assert isinstance(session, dict)
    session.update({"status": "done", "result": {"evidence": {"derived_artifact": ref}}})
    file_store.write_json(f"sessions/{sid}.json", session)

    assert await evidence_store.read_analysis_evidence_artifact(
        owner_id="owner:1",
        analysis_ref=f"analysis:{sid}",
        artifact_ref=ref["artifact_ref"],
        evidence_revision=ref["evidence_revision"],
    ) == artifact


def test_failed_validation_leaves_no_half_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    bad = _artifact("analysis:9")
    bad["raw_samples"] = [[1, 2]]
    with pytest.raises(ValueError):
        evidence_store.write_analysis_evidence_artifact(
            session_id=9,
            owner_id="owner:1",
            artifact=bad,
        )
    evidence_root = evidence_store.analysis_evidence_root(9)
    assert not evidence_root.exists() or list(evidence_root.iterdir()) == []


@pytest.mark.asyncio
async def test_nonterminal_or_deleted_analysis_cannot_read_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    sid = await queue.enqueue("owner:1", "", "")
    ref = evidence_store.write_analysis_evidence_artifact(
        session_id=sid,
        owner_id="owner:1",
        artifact=_artifact(f"analysis:{sid}"),
    )
    with pytest.raises(evidence_store.EvidenceAccessError, match="terminal"):
        await evidence_store.read_analysis_evidence_artifact(
            owner_id="owner:1",
            analysis_ref=f"analysis:{sid}",
            artifact_ref=ref["artifact_ref"],
            evidence_revision=ref["evidence_revision"],
        )

    await queue.claim_next("worker:1")
    await queue.mark_failed(sid, "fixture failure", worker_id="worker:1")
    await queue.delete_session(sid, "owner:1")
    assert not evidence_store.analysis_evidence_root(sid).exists()
    with pytest.raises(evidence_store.EvidenceAccessError, match="not found"):
        await evidence_store.read_analysis_evidence_artifact(
            owner_id="owner:1",
            analysis_ref=f"analysis:{sid}",
            artifact_ref=ref["artifact_ref"],
            evidence_revision=ref["evidence_revision"],
        )


@pytest.mark.asyncio
async def test_checksum_tamper_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    sid = await queue.enqueue("owner:1", "", "")
    ref = evidence_store.write_analysis_evidence_artifact(
        session_id=sid,
        owner_id="owner:1",
        artifact=_artifact(f"analysis:{sid}"),
    )
    artifact_file = evidence_store._artifact_file(sid, ref["evidence_revision"])
    artifact_file.write_text("{}", encoding="utf-8")
    with pytest.raises(evidence_store.EvidenceIntegrityError, match="checksum"):
        evidence_store.validate_committed_analysis_evidence(
            session_id=sid,
            owner_id="owner:1",
            safe_ref=ref,
        )


@pytest.mark.asyncio
async def test_outcome_page_reader_revalidates_owner_revision_and_query(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    sid = await queue.enqueue("owner:1", "", "")
    artifact = _artifact(f"analysis:{sid}")
    artifact["normalized_outcome_records"] = [
        {
            "canonical_time_ms": 500,
            "source_time": {
                "clock_domain": "performance_challenge_relative",
                "value": 0.5,
                "unit": "seconds",
                "precision": "float32",
            },
            "source_priority": 20,
            "source_event_index": 0,
            "values": [
                {
                    "metric_key": "performance.shotsFired",
                    "value": 1,
                    "value_semantics": "count_increment",
                    "unit": "count",
                }
            ],
            "source_refs": ["run:1:performance"],
        }
    ]
    ref = evidence_store.write_analysis_evidence_artifact(
        session_id=sid,
        owner_id="owner:1",
        artifact=artifact,
    )
    session = file_store.read_json(f"sessions/{sid}.json")
    assert isinstance(session, dict)
    session.update({
        "status": "done",
        "result": {
            "schema_version": "analysis_result.v2",
            "analysis_id": f"analysis:{sid}",
            "evidence": {"derived_artifact": ref},
        },
    })
    file_store.write_json(f"sessions/{sid}.json", session)
    descriptor = build_page_descriptor_v1(
        owner_id="owner:1",
        analysis_ref=f"analysis:{sid}",
        evidence_revision=ref["evidence_revision"],
        scope="whole_run",
        segment_ref=None,
        selected_series=["performance.shotsFired"],
        offset=0,
    )
    page = await evidence_store.read_normalized_outcome_page(
        owner_id="owner:1",
        analysis_ref=f"analysis:{sid}",
        artifact_ref=ref["artifact_ref"],
        descriptor=descriptor,
    )
    assert len(page["timeline"]["records"]) == 1

    drifted = dict(descriptor)
    drifted["selected_series"] = ["performance.shotsHit"]
    with pytest.raises(evidence_store.EvidenceAccessError, match="stale or invalid"):
        await evidence_store.read_normalized_outcome_page(
            owner_id="owner:1",
            analysis_ref=f"analysis:{sid}",
            artifact_ref=ref["artifact_ref"],
            descriptor=drifted,
        )
