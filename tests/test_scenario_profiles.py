from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from kovaak_tracker import scenario_profiles


def _profile(*, entry_id: str = "static.example", entry_version: int = 1,
             scenario_hash: str = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             status: str = "active") -> dict:
    return {
        "entry_id": entry_id,
        "entry_version": entry_version,
        "status": status,
        "scenario_hash": scenario_hash,
        "display_name": "Reviewed Static Example",
        "taxonomy_source": "reviewed_registry",
        "reviewed_at": "2026-07-20T00:00:00Z",
        "source_refs": ["review:scenario-static-example"],
        "supersedes": [],
        "aim_family": "static_clicking",
        "subdomains": ["precision"],
        "target_motion": {"model": "static", "target_count_model": "single"},
        "allowed_analyzers": ["native_flicking.v1"],
        "allowed_metric_families": ["input_kinematics", "static_clicking"],
        "classification_confidence": "confirmed",
        "limitations": ["Only the reviewed scenario hash is classified."],
    }


def _registry(*profiles: dict) -> dict:
    return {
        "schema_version": "scenario_profile_registry.v1",
        "registry_version": "2026-07-20.v1",
        "entries": list(profiles),
    }


def _manifest(profile: dict, *, status: str = "active") -> dict:
    return {
        "schema_version": "launch_scenario_manifest.v1",
        "manifest_version": "2026-07-20.v1",
        "entries": [{
            "scenario_hash": profile["scenario_hash"],
            "scenario_profile_ref": scenario_profiles.scenario_profile_ref(profile),
            "fixture_ref": "fixture:scenario-static-example",
            "review_source_ref": "review:scenario-static-example",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "family_gate_refs": ["gate:static-clicking"],
            "status": status,
        }],
    }


def test_packaged_registry_activates_only_the_reviewed_single_target_tracking_hash():
    registry = scenario_profiles.load_registry()
    manifest = scenario_profiles.load_launch_manifest()

    assert [entry["scenario_hash"] for entry in registry["entries"]] == [
        "b2ae4a24b710e36afc6e57c61f590ab4",
    ]
    assert [entry["scenario_hash"] for entry in manifest["entries"]] == [
        "b2ae4a24b710e36afc6e57c61f590ab4",
    ]
    resolution = scenario_profiles.resolve_scenario_profile(
        "b2ae4a24b710e36afc6e57c61f590ab4",
        display_name="WHJ SmoothStrafeSphere Easy",
    )
    assert resolution["scenario_profile_ref"] == (
        "scenario:tracking.whj_smooth_strafe_sphere_easy@1"
    )
    assert resolution["aim_family"] == "continuous_tracking"
    assert resolution["target_motion"] == {
        "model": "predictable", "target_count_model": "single",
    }
    assert resolution["allowed_analyzers"] == ["continuous_tracking.v1"]
    assert resolution["allowed_metric_families"] == ["continuous_tracking"]
    assert resolution["family_analyzer_dispatch"] == "allowed"

    resolution = scenario_profiles.resolve_scenario_profile("sha256:unknown")
    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "unknown"
    assert resolution["classification_confidence"] == "unknown"
    assert resolution["family_analyzer_dispatch"] == "none"
    assert resolution["claim_ceiling"] == "outcome_only"


def test_exact_active_hash_returns_reviewed_profile_and_allows_dispatch():
    profile = _profile()
    resolution = scenario_profiles.resolve_scenario_profile(
        profile["scenario_hash"],
        display_name="renamed display value",
        registry=_registry(profile),
        manifest=_manifest(profile),
    )

    assert resolution == {
        "schema_version": "scenario_resolution.v1",
        "scenario_hash": profile["scenario_hash"],
        "display_name": "renamed display value",
        "registry_version": "2026-07-20.v1",
        "manifest_version": "2026-07-20.v1",
        "scenario_profile_ref": "scenario:static.example@1",
        "classification_source": "reviewed_registry",
        "classification_confidence": "confirmed",
        "profile_status": "active",
        "reviewed_at": "2026-07-20T00:00:00Z",
        "source_refs": ["review:scenario-static-example"],
        "supersedes": [],
        "manifest_status": "active",
        "fixture_ref": "fixture:scenario-static-example",
        "review_source_ref": "review:scenario-static-example",
        "manifest_reviewed_at": "2026-07-20T00:00:00Z",
        "family_gate_refs": ["gate:static-clicking"],
        "aim_family": "static_clicking",
        "subdomains": ["precision"],
        "target_motion": {"model": "static", "target_count_model": "single"},
        "allowed_analyzers": ["native_flicking.v1"],
        "allowed_metric_families": ["input_kinematics", "static_clicking"],
        "claim_ceiling": "family_specific",
        "family_analyzer_dispatch": "allowed",
        "limitations": ["Only the reviewed scenario hash is classified."],
    }


def test_name_only_match_is_candidate_and_never_selects_a_profile():
    profile = _profile()
    resolution = scenario_profiles.resolve_scenario_profile(
        None,
        display_name=profile["display_name"],
        registry=_registry(profile),
        manifest=_manifest(profile),
    )

    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "name_heuristic"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == "unknown"
    assert resolution["target_motion"] == {
        "model": "unknown", "target_count_model": "unknown",
    }
    assert resolution["family_analyzer_dispatch"] == "none"
    assert resolution["claim_ceiling"] == "outcome_only"


def test_same_display_name_with_different_hashes_keeps_distinct_identities():
    first = _profile()
    second = _profile(
        entry_id="static.other",
        scenario_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    second["display_name"] = first["display_name"]
    registry = _registry(first, second)
    first_manifest = _manifest(first)["entries"][0]
    second_manifest = _manifest(second)["entries"][0]
    manifest = {
        "schema_version": "launch_scenario_manifest.v1",
        "manifest_version": "2026-07-20.v1",
        "entries": [first_manifest, second_manifest],
    }

    first_resolution = scenario_profiles.resolve_scenario_profile(
        first["scenario_hash"], registry=registry, manifest=manifest,
    )
    second_resolution = scenario_profiles.resolve_scenario_profile(
        second["scenario_hash"], registry=registry, manifest=manifest,
    )

    assert first_resolution["scenario_profile_ref"] == "scenario:static.example@1"
    assert second_resolution["scenario_profile_ref"] == "scenario:static.other@1"


@pytest.mark.parametrize("status", ["pending_gate", "retired"])
def test_non_active_manifest_entries_are_outcome_only(status):
    profile = _profile(status="retired" if status == "retired" else "active")
    resolution = scenario_profiles.resolve_scenario_profile(
        profile["scenario_hash"], registry=_registry(profile), manifest=_manifest(profile, status=status)
    )

    assert resolution["classification_confidence"] == "confirmed"
    assert resolution["manifest_status"] == status
    assert resolution["family_analyzer_dispatch"] == "none"
    assert resolution["claim_ceiling"] == "outcome_only"


def test_unlisted_manifest_is_outcome_only_for_an_exact_reviewed_hash():
    profile = _profile()
    resolution = scenario_profiles.resolve_scenario_profile(
        profile["scenario_hash"], registry=_registry(profile), manifest={
            "schema_version": "launch_scenario_manifest.v1",
            "manifest_version": "2026-07-20.v1",
            "entries": [],
        }
    )

    assert resolution["scenario_profile_ref"] == "scenario:static.example@1"
    assert resolution["manifest_status"] == "unlisted"
    assert resolution["family_analyzer_dispatch"] == "none"


def test_registry_rejects_cross_entry_hash_and_multiple_active_versions():
    profile = _profile()
    duplicate_hash = _profile(entry_id="static.other")
    with pytest.raises(scenario_profiles.ScenarioProfileError, match="ambiguous hash"):
        scenario_profiles.validate_registry(_registry(profile, duplicate_hash))

    newer = _profile(entry_version=2)
    with pytest.raises(scenario_profiles.ScenarioProfileError, match="multiple active"):
        scenario_profiles.validate_registry(_registry(profile, newer))


def test_historical_versions_replay_the_same_identity_and_resolve_the_active_version():
    previous = _profile(status="superseded")
    current = _profile(entry_version=2)
    current["supersedes"] = ["scenario:static.example@1"]
    resolution = scenario_profiles.resolve_scenario_profile(
        current["scenario_hash"],
        registry=_registry(current, previous),
        manifest=_manifest(current),
    )

    assert resolution["scenario_profile_ref"] == "scenario:static.example@2"
    assert resolution["family_analyzer_dispatch"] == "allowed"

    current["supersedes"] = ["scenario:other.profile@1"]
    with pytest.raises(scenario_profiles.ScenarioProfileError, match="supersedes"):
        scenario_profiles.validate_registry(_registry(previous, current))


def test_frozen_resolution_keeps_review_and_supersession_provenance_after_update():
    previous = _profile()
    frozen = scenario_profiles.resolve_scenario_profile(
        previous["scenario_hash"],
        registry=_registry(previous),
        manifest=_manifest(previous),
    )
    current = _profile(entry_version=2)
    current["source_refs"] = ["review:scenario-static-example-v2"]
    current["reviewed_at"] = "2026-07-21T00:00:00Z"
    current["supersedes"] = ["scenario:static.example@1"]
    archived_previous = copy.deepcopy(previous)
    archived_previous["status"] = "superseded"
    updated = scenario_profiles.resolve_scenario_profile(
        current["scenario_hash"],
        registry=_registry(archived_previous, current),
        manifest=_manifest(current),
    )

    replayed = json.loads(json.dumps(frozen))
    assert replayed["scenario_profile_ref"] == "scenario:static.example@1"
    assert replayed["reviewed_at"] == "2026-07-20T00:00:00Z"
    assert replayed["source_refs"] == ["review:scenario-static-example"]
    assert replayed["supersedes"] == []
    assert replayed["review_source_ref"] == "review:scenario-static-example"
    assert updated["scenario_profile_ref"] == "scenario:static.example@2"
    assert updated["supersedes"] == ["scenario:static.example@1"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["entries"][0].update(aim_family="unsupported"),
        lambda value: value["entries"][0].update(classification_confidence="candidate"),
        lambda value: value["entries"][0].update(limitations=[]),
        lambda value: value["entries"][0].update(display_name="C:\\private\\run.csv"),
        lambda value: value["entries"][0].update(unexpected="value"),
    ],
)
def test_registry_rejects_unknown_enums_and_unsafe_or_incomplete_shapes(mutation):
    value = _registry(_profile())
    mutation(value)
    with pytest.raises(scenario_profiles.ScenarioProfileError):
        scenario_profiles.validate_registry(value)


def test_manifest_requires_an_exact_existing_profile_ref_and_validates_schema_assets():
    profile = _profile()
    registry = _registry(profile)
    manifest = _manifest(profile)
    manifest["entries"][0]["scenario_profile_ref"] = "scenario:other.profile@1"
    with pytest.raises(scenario_profiles.ScenarioProfileError, match="profile ref"):
        scenario_profiles.validate_launch_manifest(manifest, registry=registry)

    unreviewed = _profile()
    unreviewed["taxonomy_source"] = "unknown"
    with pytest.raises(scenario_profiles.ScenarioProfileError, match="reviewed taxonomy"):
        scenario_profiles.validate_launch_manifest(
            _manifest(unreviewed, status="pending_gate"),
            registry=_registry(unreviewed),
        )

    root = Path(__file__).resolve().parents[1] / "knowledge" / "scenarios"
    schema = json.loads((root / "schema.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for value in (
        _registry(profile),
        _manifest(profile),
        json.loads((root / "registry.v1.json").read_text(encoding="utf-8")),
        json.loads((root / "launch-manifest.v1.json").read_text(encoding="utf-8")),
    ):
        assert list(validator.iter_errors(value)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["entries"][0].update(classification_confidence="candidate"),
        lambda value: value["entries"][0].update(aim_family="unknown"),
        lambda value: value.update(registry_version="v" * 81),
        lambda value: value["entries"][0].update(display_name="x" * 241),
    ],
)
def test_json_schema_rejects_registry_shapes_rejected_by_loader(mutation):
    root = Path(__file__).resolve().parents[1] / "knowledge" / "scenarios"
    schema = json.loads((root / "schema.v1.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    value = copy.deepcopy(_registry(_profile()))
    mutation(value)

    assert list(validator.iter_errors(value))
    with pytest.raises(scenario_profiles.ScenarioProfileError):
        scenario_profiles.validate_registry(value)
