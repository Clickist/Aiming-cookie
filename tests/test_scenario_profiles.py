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


def test_active_scenario_profile_refs_intersect_active_registry_and_manifest():
    active = _profile()
    pending = _profile(
        entry_id="static.pending",
        scenario_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    retired = _profile(
        entry_id="static.retired",
        scenario_hash="sha256:cccccccccccccccccccccccccccccccc",
        status="retired",
    )
    manifest = _manifest(active)
    pending_entry = _manifest(pending, status="pending_gate")["entries"][0]
    retired_entry = _manifest(retired, status="retired")["entries"][0]
    manifest["entries"].extend([pending_entry, retired_entry])

    assert scenario_profiles.active_scenario_profile_refs(
        registry=_registry(active, pending, retired), manifest=manifest,
    ) == {"scenario:static.example@1"}


def test_packaged_registry_activates_only_reviewed_launch_hashes():
    registry = scenario_profiles.load_registry()
    manifest = scenario_profiles.load_launch_manifest()

    assert registry["registry_version"] == "2026-07-28.v1"
    assert manifest["manifest_version"] == "2026-07-28.v1"

    assert [entry["scenario_hash"] for entry in registry["entries"]] == [
        "b2ae4a24b710e36afc6e57c61f590ab4",
        "7378a811f430b6072d052a75896afb98",
        "a37d2ba4f3f33d59ae7018e37445a5e9",
        "3b42bdfd38a6b194737d650f3f53e8c1",
    ]
    assert [entry["scenario_hash"] for entry in manifest["entries"]] == [
        "b2ae4a24b710e36afc6e57c61f590ab4",
        "7378a811f430b6072d052a75896afb98",
        "a37d2ba4f3f33d59ae7018e37445a5e9",
        "3b42bdfd38a6b194737d650f3f53e8c1",
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

    resolution = scenario_profiles.resolve_scenario_profile(
        "7378a811f430b6072d052a75896afb98",
        display_name="1wall 6targets small",
    )
    assert resolution["manifest_status"] == "active"
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["target_motion"] == {
        "model": "static", "target_count_model": "concurrent",
    }
    assert resolution["allowed_analyzers"] == ["native_flicking.v1"]
    assert resolution["allowed_metric_families"] == [
        "input_kinematics", "static_clicking",
    ]
    assert resolution["family_analyzer_dispatch"] == "allowed"

    fixture = json.loads((
        Path(__file__).parent / "fixtures" / "scenarios"
        / "1wall-6targets-small-static.v1.json"
    ).read_text(encoding="utf-8"))
    assert fixture["scenario_hash"] == resolution["scenario_hash"]
    assert fixture["scenario_profile_ref"] == resolution["scenario_profile_ref"]
    assert fixture["field_review"] == {
        "review_ref": "review:1wall-6targets-small-static-input-native",
        "reviewed_at": "2026-07-27T00:00:00Z",
        "target_motion": "static",
        "simultaneous_target_count": 6,
        "target_count_model": "concurrent",
    }
    assert fixture["aggregate_evidence"]["available_sources"] == [
        "stats", "performance", "raw_input", "mp4",
    ]
    assert "path" not in json.dumps(fixture).casefold()

    resolution = scenario_profiles.resolve_scenario_profile(
        "a37d2ba4f3f33d59ae7018e37445a5e9",
        display_name="pasu small reload",
    )
    assert resolution["manifest_status"] == "active"
    assert resolution["scenario_profile_ref"] == (
        "scenario:dynamic.pasu_small_reload@1"
    )
    assert resolution["aim_family"] == "dynamic_clicking"
    assert resolution["target_motion"] == {
        "model": "reactive", "target_count_model": "concurrent",
    }
    assert resolution["allowed_analyzers"] == ["dynamic_clicking.v1"]
    assert resolution["allowed_metric_families"] == ["dynamic_clicking"]
    assert resolution["family_analyzer_dispatch"] == "allowed"

    dynamic_fixture = json.loads((
        Path(__file__).parent / "fixtures" / "scenarios"
        / "pasu-small-reload-dynamic.v1.json"
    ).read_text(encoding="utf-8"))
    assert dynamic_fixture["scenario_hash"] == resolution["scenario_hash"]
    assert dynamic_fixture["scenario_profile_ref"] == (
        resolution["scenario_profile_ref"]
    )
    assert dynamic_fixture["visual_gate"]["status"] == "accepted"
    assert dynamic_fixture["visual_gate"]["calibration"]["run_ref"] == (
        "run:1032"
    )
    assert dynamic_fixture["visual_gate"]["untouched_holdout"]["run_ref"] == (
        "run:1347"
    )
    assert dynamic_fixture["visual_gate"]["validation_results"] == {
        "center_error_median_px": 1.032295,
        "center_error_p95_px": 3.519083,
        "radius_or_hitbox_error_px": 0.749257,
        "false_positive_rate": 0.0,
        "minimum_coverage": 0.992,
        "identity_switch_rate": None,
        "occlusion_reentry_accuracy": None,
    }
    assert "path" not in json.dumps(dynamic_fixture).casefold()

    same_name_unknown = scenario_profiles.resolve_scenario_profile(
        "a37d2ba4f3f33d59ae7018e37445a5ea",
        display_name="pasu small reload",
    )
    assert same_name_unknown["scenario_profile_ref"] is None
    assert same_name_unknown["manifest_status"] == "unlisted"
    assert same_name_unknown["classification_source"] == "name_heuristic"
    assert same_name_unknown["classification_confidence"] == "candidate"
    assert same_name_unknown["aim_family"] == "dynamic_clicking"
    assert same_name_unknown["allowed_analyzers"] == ["dynamic_clicking.baseline.v1"]
    assert same_name_unknown["family_analyzer_dispatch"] == "allowed"
    assert same_name_unknown["claim_ceiling"] == "descriptive_only"

    same_name_unknown = scenario_profiles.resolve_scenario_profile(
        "7378a811f430b6072d052a75896afb99",
        display_name="1wall 6targets small",
    )
    assert same_name_unknown["scenario_profile_ref"] is None
    assert same_name_unknown["classification_source"] == "name_heuristic"
    assert same_name_unknown["manifest_status"] == "unlisted"
    assert same_name_unknown["aim_family"] == "static_clicking"
    assert same_name_unknown["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert same_name_unknown["family_analyzer_dispatch"] == "allowed"
    assert same_name_unknown["claim_ceiling"] == "descriptive_only"

    resolution = scenario_profiles.resolve_scenario_profile("sha256:unknown")
    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "family_default"
    assert resolution["classification_confidence"] == "unknown"
    assert resolution["aim_family"] == "static_clicking"
    assert "scenario_family_unresolved" in resolution["limitations"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"


def test_beants_larger_switching_activates_only_after_accepted_local_episode_gate():
    resolution = scenario_profiles.resolve_scenario_profile(
        "3b42bdfd38a6b194737d650f3f53e8c1",
        display_name="beanTS Larger",
    )
    fixture = json.loads((
        Path(__file__).parent / "fixtures" / "scenarios"
        / "beants-larger-switching.v1.json"
    ).read_text(encoding="utf-8"))

    assert resolution["scenario_profile_ref"] == "scenario:switching.beants_larger@1"
    assert resolution["aim_family"] == "target_switching"
    assert resolution["manifest_status"] == "active"
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "family_specific"
    assert resolution["allowed_analyzers"] == ["target_switching.v1"]
    assert resolution["allowed_metric_families"] == ["target_switching"]
    manifest_entry = next(
        entry for entry in scenario_profiles.load_launch_manifest()["entries"]
        if entry["scenario_hash"] == resolution["scenario_hash"]
    )
    assert manifest_entry == {
        "scenario_hash": resolution["scenario_hash"],
        "scenario_profile_ref": resolution["scenario_profile_ref"],
        "fixture_ref": "fixture:beants-larger-switching.v1",
        "review_source_ref": "review:beants-larger-switching-calibration-holdout",
        "reviewed_at": "2026-07-28T00:00:00Z",
        "family_gate_refs": ["gate:beants-larger-event-local-episodes.v1"],
        "status": "active",
    }
    gate = fixture["episode_gate"]
    assert gate["status"] == "accepted"
    assert gate["producer"] == {
        "id": "visual_signals.event_local_target_episode",
        "version": "visual_target_episode.local_unique_match.v1",
    }
    assert gate["detector_config_ref"] == (
        "detector-config:sha256:"
        "b3a5ee7add541acfcb172cb5eebcb91af4d506bfcf165f658809912d782cfea5"
    )
    assert gate["visual_quality_profile_ref"] == (
        "visual-quality:visual_signals.event_local_target_episode@"
        "visual_target_episode.local_unique_match.v1"
    )
    assert gate["acceptance"] == {
        "minimum_local_contact_duration_ms": 50,
        "maximum_local_contact_gap_ms": 50,
        "minimum_local_target_confidence": 0.45,
        "local_match_position_residual_px": 24.0,
        "minimum_kill_chain_coverage": 0.6597938144329897,
    }
    assert gate["calibration"] == {
        "run_ref": "run:1036",
        "source_freeze_sha256": "596cbbcf9665fe0a583af2f574711b75ae565112bc0c123add987a56d18ab6a5",
        "video_sha256": "2e1bf5f2938982866005aad8efe62bfcb1e352dc45fdca37a56999cad7958889",
        "ledger_sha256": "0bc0a1baae9c336aade0d268a06062b987240ec9c0f780481dd12a84703f83df",
        "stats_kill_count": 97,
        "episode_count": 642,
        "included_count": 64,
        "rejected_count": 33,
        "coverage": 0.6597938144329897,
        "path_observable_count": 13,
    }
    assert gate["untouched_holdout"] == {
        "run_ref": "run:1038",
        "source_freeze_sha256": "072a2ac69c92959a744d31d0630a0ba0884062e7ad62c9bc9f84e8eb17bb7ad8",
        "video_sha256": "a054a4159862e9d9382bda4489cbaf8885886c9fd8490485334ca23313548ff4",
        "ledger_sha256": "169851f6a0ee4a9d16a4935823b0dd400a2a432dc1b938d4637bc043ce114664",
        "stats_kill_count": 123,
        "episode_count": 862,
        "included_count": 83,
        "rejected_count": 40,
        "coverage": 0.6747967479674797,
        "path_observable_count": 48,
    }
    assert fixture["input_activation_semantics"]["continuous_hold"] == "optional"
    assert fixture["input_activation_semantics"]["discrete"] == "optional"
    assert fixture["outcome_association"] == {
        "status": "optional_enrichment_unavailable",
        "rule_ref": None,
        "rule_schema_version": None,
        "outcome_semantics": "stats_kill_boundary",
    }
    serialized = json.dumps(fixture).casefold()
    assert "c:\\\\" not in serialized
    assert "e:\\\\" not in serialized
    assert "\\\\users\\\\" not in serialized


def test_beants_larger_name_only_routes_a_switching_family_candidate():
    resolution = scenario_profiles.resolve_scenario_profile(
        None, display_name="beanTS Larger",
    )
    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "name_heuristic"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == "target_switching"
    assert resolution["allowed_analyzers"] == ["target_switching.baseline.v1"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"

    same_name_unknown_hash = scenario_profiles.resolve_scenario_profile(
        "3b42bdfd38a6b194737d650f3f53e8c2", display_name="beanTS Larger",
    )
    assert same_name_unknown_hash["scenario_profile_ref"] is None
    assert same_name_unknown_hash["aim_family"] == "target_switching"
    assert same_name_unknown_hash["family_analyzer_dispatch"] == "allowed"


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
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["target_motion"] == {
        "model": "unknown", "target_count_model": "unknown",
    }
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["allowed_metric_families"] == ["outcome", "input_kinematics"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert "scenario_name_is_a_candidate_not_an_identity" in resolution["limitations"]


def test_local_scenario_definition_routes_reactive_concurrent_hitscan_to_dynamic_baseline():
    definition = """Name=1wall5targets_pasu
AddedBots=test.bot;test.bot;test.bot;test.bot;test.bot
PlayerCharacters=A_air_pistol_frozen
// KovaaK scenario definitions may include explanatory comments.

[Bot Profile]
Name=test
DodgeProfileNames=test
CharacterProfile=react
free-form metadata from the local scenario author

[Character Profile]
Name=react
MaxSpeed=1300.0

[Dodge Profile]
Name=test
ToggleLeftRight=true
ToggleForwardBack=true

[Character Profile]
Name=A_air_pistol_frozen
WeaponProfileNames=pistol

[Weapon Profile]
Name=pistol
Type=Hitscan
ShotsPerClick=1
DamagePerShot=1000.0
Category=SemiAuto
"""
    descriptor = scenario_profiles.parse_local_scenario_behavior_descriptor(
        definition.encode("utf-8"),
        expected_display_name="1wall5targets_pasu",
    )

    assert descriptor == {
        "schema_version": "scenario_behavior_descriptor.v1",
        "display_name": "1wall5targets_pasu",
        "source_sha256": descriptor["source_sha256"],
        "bot_count": 5,
        "reactive_bot_count": 5,
        "dodge_axes": ["horizontal", "depth"],
        "weapon": {
            "delivery": "hitscan",
            "fire_mode": "semi_auto",
            "shots_per_click": 1,
            "damage_per_shot": 1000.0,
        },
    }

    resolution = scenario_profiles.resolve_scenario_profile(
        "a5be19c6e6aeb0d774c5e9d9fb497e91",
        display_name="1wall5targets_pasu",
        behavior_descriptor=descriptor,
    )

    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "local_scenario_definition"
    assert resolution["classification_confidence"] == "confirmed"
    assert resolution["aim_family"] == "dynamic_clicking"
    assert resolution["target_motion"] == {
        "model": "reactive", "target_count_model": "concurrent",
    }
    assert resolution["allowed_analyzers"] == ["dynamic_clicking.baseline.v1"]
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert resolution["family_analyzer_dispatch"] == "allowed"


def test_local_scenario_definition_routes_static_hitscan_to_static_baseline():
    definition = """Name=1wall 6targets small
AddedBots=target.bot;target.bot;target.bot;target.bot;target.bot;target.bot
PlayerCharacters=Player

[Bot Profile]
Name=target
CharacterProfile=target

[Character Profile]
Name=target
MaxSpeed=0.0

[Character Profile]
Name=Player
WeaponProfileNames=BB Gun

[Weapon Profile]
Name=BB Gun
Type=Hitscan
ShotsPerClick=1
DamagePerShot=1.0
Category=SemiAuto
"""
    descriptor = scenario_profiles.parse_local_scenario_behavior_descriptor(
        definition.encode("utf-8"),
        expected_display_name="1wall 6targets small",
    )

    assert descriptor is not None
    assert descriptor["bot_count"] == 6
    assert descriptor["reactive_bot_count"] == 0
    assert descriptor["dodge_axes"] == []
    resolution = scenario_profiles.resolve_scenario_profile(
        "unknown-static-hash",
        display_name="1wall 6targets small",
        behavior_descriptor=descriptor,
    )
    assert resolution["scenario_profile_ref"] is None
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["target_motion"] == {
        "model": "static", "target_count_model": "concurrent",
    }
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert resolution["family_analyzer_dispatch"] == "allowed"


def test_local_scenario_definition_is_not_a_name_fallback_or_path_leak():
    assert scenario_profiles.parse_local_scenario_behavior_descriptor(
        b"Name=1wall5targets_pasu\nAddedBots=test.bot\n",
        expected_display_name="1wall5targets_pasu",
    ) is None
    assert scenario_profiles.parse_local_scenario_behavior_descriptor(
        (b"Name=1wall5targets_pasu\n" + b"x" * (3 * 1024 * 1024)),
        expected_display_name="1wall5targets_pasu",
    ) is None


@pytest.mark.parametrize(
    ("display_name", "aim_family"),
    [
        ("WHJ SmoothStrafeSphere Easy", "continuous_tracking"),
        ("Bouncing Tracking #3", "continuous_tracking"),
        ("Bounceshot Switch", "target_switching"),
        ("1wall5targets_pasu", "dynamic_clicking"),
        ("pasu small reload", "dynamic_clicking"),
        # reload 是空枪换弹惩罚而非移动靶标记（点点社群校订 2026-08-16）
        ("1w2ts reload", "static_clicking"),
        ("1wall 6targets reload", "static_clicking"),
        ("1wall 6targets small", "static_clicking"),
        ("Tile Frenzy", "static_clicking"),
    ],
)
def test_name_keywords_route_unreviewed_scenarios_to_family_candidates(display_name, aim_family):
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash", display_name=display_name,
    )
    assert resolution["classification_source"] == "name_heuristic"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == aim_family
    assert resolution["allowed_analyzers"] == [f"{aim_family}.baseline.v1"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert "scenario_name_is_a_candidate_not_an_identity" in resolution["limitations"]


def test_no_reload_name_does_not_route_to_dynamic_clicking():
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash", display_name="1wall 6targets no reload",
    )
    assert resolution["aim_family"] == "static_clicking"


def test_unnamed_scenario_defaults_to_static_baseline_with_unresolved_flag():
    resolution = scenario_profiles.resolve_scenario_profile("unreviewed-hash")
    assert resolution["classification_source"] == "family_default"
    assert resolution["classification_confidence"] == "unknown"
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert "scenario_family_unresolved" in resolution["limitations"]


def _shape(kills: int, duration_ms: int, button_samples_held: int | None = None) -> dict:
    shape = {
        "schema_version": "scenario_challenge_shape.v1",
        "kills": kills,
        "duration_ms": duration_ms,
    }
    if button_samples_held is not None:
        shape["button_samples_held"] = button_samples_held
    return shape


def test_challenge_shape_classifier_reads_fire_mode_from_button_samples():
    # 实测 6 局真实数据：点射类每杀 16/27 个按住采样，持续开火追踪 426/445，
    # 零杀纯追踪 12013/17273。量级差 20 倍以上，判据用开火模式而非击杀密度。
    assert scenario_profiles.classify_challenge_shape_v1(103, 60_000, 1641) == {
        "shape_class": "clicking_candidate", "basis": "fire_mode_tap",
        "kills": 103, "duration_ms": 60_000,
        "button_samples_held": 1641, "button_samples_per_kill": 15.93,
    }
    assert scenario_profiles.classify_challenge_shape_v1(74, 60_000, 2004)[
        "shape_class"
    ] == "clicking_candidate"  # Pasu SuperbAim 每杀 27
    assert scenario_profiles.classify_challenge_shape_v1(39, 90_000, 16594)[
        "shape_class"
    ] == "tracking_candidate"  # AscendedTracking90 每杀 426
    assert scenario_profiles.classify_challenge_shape_v1(34, 60_000, 15153)[
        "shape_class"
    ] == "tracking_candidate"  # AscendedTracking v3 每杀 445
    zero_kill = scenario_profiles.classify_challenge_shape_v1(0, 60_000, 12013)
    assert zero_kill["shape_class"] == "tracking_candidate"
    assert zero_kill["basis"] == "zero_kill_sustained_fire"
    assert zero_kill["button_samples_per_kill"] is None  # VertSmoothness 0 杀
    assert scenario_profiles.classify_challenge_shape_v1(0, 60_000, 17273)[
        "shape_class"
    ] == "tracking_candidate"  # AirAngelic 0 杀 17273 采样
    assert scenario_profiles.classify_challenge_shape_v1(0, 60_000, 100) is None
    # 按住转火实测（2026-08-16）：patCircleSwitch 226 采样/杀但 53 杀/分钟、
    # Target Switching 360 Static 128/杀 65 杀/分钟——按住节奏落在追踪带，
    # 但击杀节奏证明是点击类挑战，护栏判回 clicking。
    assert scenario_profiles.classify_challenge_shape_v1(53, 60_000, 12_010)[
        "shape_class"
    ] == "clicking_candidate"
    assert scenario_profiles.classify_challenge_shape_v1(53, 60_000, 12_010)[
        "basis"
    ] == "fire_mode_hold_rapid_kills"
    assert scenario_profiles.classify_challenge_shape_v1(65, 60_000, 8_354)[
        "shape_class"
    ] == "clicking_candidate"


def test_challenge_shape_fire_mode_keeps_the_undecided_band():
    # 边界：每杀恰 100 / 恰 50 / 带内 75 均不判定；带外判定；短局不判定。
    assert scenario_profiles.classify_challenge_shape_v1(2, 20_000, 200) is None
    assert scenario_profiles.classify_challenge_shape_v1(2, 20_000, 100) is None
    assert scenario_profiles.classify_challenge_shape_v1(2, 20_000, 150) is None
    assert scenario_profiles.classify_challenge_shape_v1(2, 20_000, 75)["shape_class"] == (
        "clicking_candidate"
    )
    assert scenario_profiles.classify_challenge_shape_v1(2, 20_000, 300)["shape_class"] == (
        "tracking_candidate"
    )
    assert scenario_profiles.classify_challenge_shape_v1(0, 2_000, 12_013) is None


def test_challenge_shape_without_raw_keeps_only_the_pure_tracking_corner():
    # 击杀密度已被实测推翻（34-39 杀的追踪图与 43 杀的点击图重叠）；
    # 无 raw 时仅 ≤6 杀的纯追踪可判，其余诚实不判定。
    assert scenario_profiles.classify_challenge_shape_v1(6, 15_000)["basis"] == (
        "kill_density_fallback"
    )
    assert scenario_profiles.classify_challenge_shape_v1(0, 30_000)["shape_class"] == (
        "tracking_candidate"
    )
    assert scenario_profiles.classify_challenge_shape_v1(34, 90_000) is None
    assert scenario_profiles.classify_challenge_shape_v1(43, 60_000) is None
    assert scenario_profiles.classify_challenge_shape_v1(12, 20_000) is None
    assert scenario_profiles.classify_challenge_shape_v1(3, 2_000) is None


def test_challenge_shape_fire_mode_routes_zero_kill_tracking_without_name_keywords():
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Air Angelic 4 Voltaic Easy",
        challenge_shape=_shape(0, 30_000, 17_273),
    )
    assert resolution["classification_source"] == "challenge_shape"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == "continuous_tracking"
    assert resolution["allowed_analyzers"] == ["continuous_tracking.baseline.v1"]
    assert resolution["allowed_metric_families"] == ["outcome", "input_kinematics"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert (
        "challenge_shape_fire_mode_kills_0_button_samples_held_17273"
        "_button_samples_per_kill_inf" in resolution["limitations"]
    )


def test_challenge_shape_fire_mode_separates_high_kill_tracking_from_clicking():
    # 39 杀的追踪图（旧密度判据会误判/不判）按持续开火正确进入 tracking。
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Air Angelic 4 Voltaic Easy",
        challenge_shape=_shape(39, 90_000, 16_594),
    )
    assert resolution["classification_source"] == "challenge_shape"
    assert resolution["aim_family"] == "continuous_tracking"


def test_challenge_shape_tapping_lets_the_name_refine_the_clicking_family():
    static = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="1w4ts Voltaic Easy",
        challenge_shape=_shape(103, 60_000, 1_641),
    )
    assert static["classification_source"] == "challenge_shape"
    assert static["aim_family"] == "static_clicking"

    dynamic = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Pasu SuperbAim",
        challenge_shape=_shape(74, 60_000, 2_004),
    )
    assert dynamic["classification_source"] == "challenge_shape"
    assert dynamic["aim_family"] == "dynamic_clicking"

    switching = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Bounceshot Switch",
        challenge_shape=_shape(103, 60_000, 1_641),
    )
    assert switching["classification_source"] == "challenge_shape"
    assert switching["aim_family"] == "target_switching"


def test_challenge_shape_tapping_rejects_a_contradicting_tracking_name():
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Bouncing Tracking #3",
        challenge_shape=_shape(74, 60_000, 2_004),
    )
    assert resolution["classification_source"] == "challenge_shape"
    assert resolution["aim_family"] == "static_clicking"


def test_challenge_shape_undecided_fire_mode_falls_back_to_the_name_layer():
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Air Angelic 4 Voltaic Easy",
        challenge_shape=_shape(2, 20_000, 150),
    )
    assert resolution["classification_source"] == "name_heuristic"
    assert resolution["aim_family"] == "static_clicking"


def test_challenge_shape_kill_density_fallback_still_routes_pure_tracking():
    # 无 raw 的历史局：0-6 杀仍按弱判据进 tracking，依据摘要如实标注 fallback。
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Air Angelic 4 Voltaic Easy",
        challenge_shape=_shape(6, 15_000),
    )
    assert resolution["classification_source"] == "challenge_shape"
    assert resolution["aim_family"] == "continuous_tracking"
    assert (
        "challenge_shape_kill_density_kills_6_duration_ms_15000"
        in resolution["limitations"]
    )


def test_challenge_shape_stays_below_the_local_scenario_definition_layer():
    descriptor = {
        "schema_version": "scenario_behavior_descriptor.v1",
        "display_name": "unknown static",
        "source_sha256": "a" * 64,
        "bot_count": 2,
        "reactive_bot_count": 0,
        "dodge_axes": [],
        "weapon": {
            "delivery": "hitscan",
            "fire_mode": "semi_auto",
            "shots_per_click": 1,
            "damage_per_shot": 1.0,
        },
    }
    resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash",
        display_name="unknown static",
        behavior_descriptor=descriptor,
        challenge_shape=_shape(0, 60_000, 12_013),
    )
    assert resolution["classification_source"] == "local_scenario_definition"
    assert resolution["aim_family"] == "static_clicking"


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
def test_non_active_manifest_entries_keep_family_baseline_dispatch(status):
    profile = _profile(status="retired" if status == "retired" else "active")
    resolution = scenario_profiles.resolve_scenario_profile(
        profile["scenario_hash"], registry=_registry(profile), manifest=_manifest(profile, status=status)
    )

    assert resolution["classification_confidence"] == "confirmed"
    assert resolution["manifest_status"] == status
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["allowed_metric_families"] == ["outcome", "input_kinematics"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert "exact_manifest_gate_inactive_visual_claims_unavailable" in resolution["limitations"]


def test_unlisted_manifest_keeps_family_baseline_for_an_exact_reviewed_hash():
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
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"


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
