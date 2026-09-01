"""External telemetry import (ExternalTelemetryRun) — DoD acceptance suite.

Covers the ten acceptance items of the import module design
(IMPORT_MODULE_DESIGN.md §6): full backfill, idempotent re-scan, mapping
spot checks, T2K rollup parity against session_quant, coarse pairing,
proposal-only label passthrough, quality gates, API endpoints, failure
isolation and the docs contract.

Frozen test set: the upstream ``cleaned/`` tree (69 index-covered rounds +
5 index-less validation rounds = 74 at freeze time). The integration tests
derive counts/expectations from that tree and SKIP when it is absent, so the
suite stays green on machines without the upstream checkout. The tree is
never written to; frozen frame copies land in the isolated DATA_ROOT only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, external_telemetry_ingest as ingest
from webapp.backend import external_telemetry_store as store, file_store
from webapp.backend.app import app

# ---------------------------------------------------------------- frozen set

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FROZEN_ROOT = _REPO_ROOT.parent / "FPSAimTrainer" / "analysis" / "external" / "cleaned"


def _frozen_root() -> Path | None:
    override = os.environ.get("EXTERNAL_TELEMETRY_FROZEN_ROOT", "").strip()
    candidate = Path(override) if override else DEFAULT_FROZEN_ROOT
    return candidate if candidate.is_dir() else None


FROZEN_ROOT = _frozen_root()

requires_frozen = pytest.mark.skipif(
    FROZEN_ROOT is None,
    reason="frozen cleaned/ tree not available (set EXTERNAL_TELEMETRY_FROZEN_ROOT)",
)

# D4 pins: session_quant_0830.md 逐局表（官方 .perf 六局）与当前冻结 cleaned/
# 独立重算值（numpy 线性插值口径）。|imported − pin| ≤ 0.001s。
T2K_P50_PINS = {
    1: 0.8776,
    2: 0.5256,
    3: 0.5648,
    4: 0.5582,
    5: 0.5631,
    9: 2.1577,
}
SESSION_QUANT_MD_P50 = {1: 0.877, 2: 0.526, 3: 0.565, 4: 0.558, 5: 0.563, 9: 2.158}
# D5 pins: crosscheck_0830_report.json 六局的 .perf start_unix（挑战窗起点）。
CROSSCHECK_START_UNIX = {
    1: 1788030324.0,
    2: 1788030863.0,
    3: 1788030998.0,
    4: 1788031186.0,
    5: 1788031387.0,
    9: 1788032014.0,
}
PERF_ROUND_SCENARIOS = {
    1: "beanClick",
    2: "beanClick Valorant",
    3: "beanClick Valorant",
    4: "beanClick Valorant",
    5: "beanClick Valorant",
    9: "1wall 6targets small",
}


def _watcher(root: Path, *, stable_scans: int = 1) -> ingest.ExternalTelemetryWatcher:
    return ingest.ExternalTelemetryWatcher(root, stable_scans=stable_scans)


def _ledger_runs() -> dict[str, dict]:
    return {
        key: entry
        for key, entry in store.read_ledger().items()
        if isinstance(entry, dict) and entry.get("status") == "imported"
    }


def _meta_for(key: str) -> dict:
    entry = store.read_ledger().get(key)
    assert isinstance(entry, dict), f"ledger entry missing: {key}"
    meta = store.load_meta(str(entry["external_run_id"]))
    assert meta is not None, f"meta missing for {key}"
    return meta


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


# ----------------------------------------------------------------- synthetic

def _frame_line(t: float, tr: float, targets: str = "[]") -> str:
    return json.dumps({"ev": "frame", "t": t, "tr": tr, "targets": json.loads(targets)})


def _round_payload(n_frames: int = 40, t0: float = 900.0, bad_last: bool = False) -> bytes:
    lines = [
        _frame_line(t0 + i * 0.031, i * 0.031, "[[2750432813280, 1953.7, 1313.2, 850.2]]")
        for i in range(n_frames)
    ]
    if bad_last:
        lines[-1] = "{ this is not json"
    return ("\n".join(lines) + "\n").encode("utf-8")


def _index_entry(
    *,
    round_number: int = 1,
    file: str = "round_01.jsonl",
    t_start: float = 900.0,
    t_end: float = 901.2,
    targets: list[dict] | None = None,
    n_targets: int | None = None,
) -> dict:
    targets = targets if targets is not None else [{
        "tid": 0, "addr": 2750432813280, "addr_hex": "0x280629220e0", "motion": "static",
        "birth": t_start, "death": t_end - 0.2, "alive_window": [t_start, t_end - 0.2],
        "n_samples": 30, "n_lives": 2, "path_length": 0.0,
        "lives": [
            {"t_start": t_start, "t_end": t_start + 0.5, "n": 15, "path": 0.0},
            {"t_start": t_start + 0.5, "t_end": t_end - 0.2, "n": 15, "path": 0.0},
        ],
        "domain": {"x": [0.0, 4096.0], "y": [0.0, 2048.0], "z": [660.0, 1300.0]},
    }]
    return {
        "round": round_number,
        "file": file,
        "t_start": t_start,
        "t_end": t_end,
        "duration": t_end - t_start,
        "n_frames": 40,
        "n_targets": n_targets if n_targets is not None else len(targets),
        "n_moving_targets": 0,
        "motion_mix": "static",
        "targets": targets,
    }


def _write_index(
    index_path: Path,
    *,
    format_version: int = 1,
    source: str = "target_poll_out_0101_010203.jsonl",
    outdir: str = "target_poll_out_0101_010203",
    rounds: list[dict] | None = None,
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({
        "format_version": format_version,
        "generator": "cleaner.py",
        "params": {"jump_dist": 2000.0},
        "sources": [{
            "source": source,
            "outdir": outdir,
            "rounds": rounds or [_index_entry()],
            "discarded": {"garbage_points": 3},
            "per_addr_cut_stats": {"respawn2_cuts": 1},
        }],
    }, ensure_ascii=False), encoding="utf-8")


def _write_scenario(path: Path, rounds: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format_version": 1,
        "generator": "scenario_meta.py label",
        "generated_at": "2026-08-30T04:26:27",
        "rounds": rounds,
    }, ensure_ascii=False), encoding="utf-8")


def make_cleaned_tree(tmp_path: Path) -> Path:
    """Mini cleaned/ tree: index batch + v2 bad index + index-less source."""
    root = tmp_path / "cleaned"
    source_dir = root / "batch" / "target_poll_out_0101_010203"
    source_dir.mkdir(parents=True)
    (source_dir / "round_01.jsonl").write_bytes(_round_payload())
    _write_index(root / "batch" / "rounds_index.json")
    _write_scenario(source_dir / "scenario.json", [{
        "round": 1,
        "round_file": "round_01.jsonl",
        "scenario": {
            "status": "matched", "label": "beanClick", "score": 0.989, "margin": 0.0,
            "coverage": 0.78, "score_parts": {"count": 1.0},
            "candidates": [{"name": "beanClick", "score": 0.989}], "tie_group": None,
        },
    }])

    bad_dir = root / "badver" / "target_poll_out_0102_010405"
    bad_dir.mkdir(parents=True)
    (bad_dir / "round_01.jsonl").write_bytes(_round_payload())
    _write_index(
        root / "badver" / "rounds_index.json", format_version=2,
        source="target_poll_out_0102_010405.jsonl", outdir="target_poll_out_0102_010405",
    )

    late_dir = root / "late"
    late_dir.mkdir()
    (late_dir / "round_01.jsonl").write_bytes(_round_payload(n_frames=50, t0=800.0))
    return root


@pytest.fixture
def cleaned_tree(tmp_path: Path) -> Path:
    return make_cleaned_tree(tmp_path)


# ------------------------------------------------------------ D1/D2/D3 core

def test_full_backfill_imports_every_round_once_and_keeps_watch_root_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    before = _tree_snapshot(root)

    watcher = _watcher(root)
    summary = watcher.scan_once()

    # 2 个可导入轮（batch index 覆盖 + late orphan）；v2 假 index 同 scan 被 fail-closed。
    assert summary["imported"] == 2 and summary["failed"] == 0
    assert summary["rejected"] == 1 and summary["skipped"] == 0
    runs = _ledger_runs()
    assert len(runs) == 2
    for key, entry in runs.items():
        meta = store.load_meta(str(entry["external_run_id"]))
        assert meta is not None
        assert (config.DATA_ROOT / meta["frames_path"]).is_file()
    # watch root untouched (read-only upstream contract).
    assert _tree_snapshot(root) == before


def test_rescan_is_fully_idempotent_and_byte_flip_creates_content_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    watcher = _watcher(root)
    assert watcher.scan_once()["imported"] == 2

    meta_before = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    meta_path = config.DATA_ROOT / store.meta_path(meta_before["external_run_id"])
    mtime_before = meta_path.stat().st_mtime_ns

    second = watcher.scan_once()
    assert second["imported"] == 0 and second["revised"] == 0 and second["failed"] == 0
    assert meta_path.stat().st_mtime_ns == mtime_before

    round_file = root / "batch" / "target_poll_out_0101_010203" / "round_01.jsonl"
    payload = round_file.read_bytes()
    round_file.write_bytes(payload[:-2] + b'\n')  # 重洗同轮：内容变化，身份不变
    revised = watcher.scan_once()
    assert revised["revised"] == 1 and revised["imported"] == 0

    meta_after = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert meta_after["external_run_id"] == meta_before["external_run_id"]
    assert len(meta_after["revisions"]) == 1
    assert meta_after["revisions"][0]["round_sha256"] == meta_before["fingerprints"]["round_sha256"]
    assert meta_after["fingerprints"]["round_sha256"] != meta_before["fingerprints"]["round_sha256"]


def test_mapping_fields_and_tid_identity_survive_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    watcher = _watcher(make_cleaned_tree(tmp_path))
    watcher.scan_once()

    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert meta["schema_version"] == "external_run.v1"
    assert meta["origin"]["source_file"] == "target_poll_out_0101_010203.jsonl"
    assert meta["origin"]["round"] == 1
    assert meta["counts"]["n_targets"] == 1
    assert meta["counts"]["spawns"] == 2 and meta["counts"]["deaths"] == 2
    assert meta["counts"]["timeouts"] == 0
    target = meta["targets"][0]
    assert target["tid"] == 0 and target["addr_hex"] == "0x280629220e0"
    assert target["n_lives"] == 2 and len(target["lives"]) == 2
    assert target["target_path_length_cm"] == 0.0  # 目标路径 *_cm 命名，与输入侧 path_length 区分
    assert meta["time"]["epoch_anchor"]["method"] == "filename_wallclock"
    assert meta["time"]["epoch_anchor"]["epoch_start_est"] > 0
    # tid 在轮内唯一；身份三元组 = (source_file, round, tid)。
    tids = [target["tid"] for target in meta["targets"]]
    assert len(tids) == len(set(tids))
    # anchor: 0101_010203 → 本地 1 月 1 日 01:02:03（当年）。
    import datetime
    expected = datetime.datetime(datetime.datetime.now().year, 1, 1, 1, 2, 3).timestamp()
    assert abs(meta["time"]["epoch_anchor"]["epoch_start_est"] - expected) < 1.0


def test_t2k_rollup_matches_independent_computation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = tmp_path / "cleaned"
    source_dir = root / "s" / "target_poll_out_0101_010203"
    source_dir.mkdir(parents=True)
    (source_dir / "round_01.jsonl").write_bytes(_round_payload())
    # T2K 口径（session_quant 对拍）：窗内出生且 death < 窗末的 life；窗末存活=timeout。
    lives = [
        {"t_start": 900.0, "t_end": 900.4, "n": 5, "path": 0.0},
        {"t_start": 900.4, "t_end": 901.1, "n": 5, "path": 0.0},  # >0.5s
        {"t_start": 901.1, "t_end": 901.201, "n": 5, "path": 0.0},  # 窗末存活 → timeout
    ]
    targets = [{
        "tid": 0, "addr_hex": "0x1", "motion": "static",
        "birth": 900.0, "death": 901.201, "alive_window": [900.0, 901.201],
        "n_samples": 15, "n_lives": 3, "path_length": 0.0, "lives": lives,
        "domain": {"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 1.0]},
    }]
    _write_index(root / "s" / "rounds_index.json", rounds=[_index_entry(targets=targets)])
    _watcher(root).scan_once()

    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|s")
    t2k = meta["rollups"]["t2k"]
    assert t2k["n"] == 2  # 第三个 life 仍在窗末存活 → timeout，不入分布
    assert abs(t2k["p50"] - 0.55) <= 0.001
    assert meta["counts"]["timeouts"] == 1 and meta["counts"]["deaths"] == 2
    assert abs(meta["counts"]["timeout_rate"] - 1 / 3) <= 1e-6


# ------------------------------------------------- integration (frozen set)

def expected_frozen_round_count(root: Path) -> int:
    """Index-covered rounds + index-less orphan round files, per upstream layout."""
    covered: set[Path] = set()
    total = 0
    for index_path in root.rglob("rounds_index.json"):
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if data.get("format_version") != 1:
            continue
        for source in data.get("sources", []):
            outdir = source.get("outdir")
            round_dir = index_path.parent / outdir if outdir and (index_path.parent / outdir).is_dir() else index_path.parent
            covered.add(round_dir.resolve())
            total += len(source.get("rounds", []))
    orphans = sum(
        1 for path in root.rglob("round_*.jsonl")
        if path.is_file() and path.parent.resolve() not in covered
    )
    return total + orphans


@requires_frozen
def test_d1_full_backfill_of_frozen_cleaned_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    expected = expected_frozen_round_count(FROZEN_ROOT)
    # 冻结集钉死：69 个 index 覆盖轮 + 5 个无 index 验证轮（再演进需重钉）。
    assert expected == 74, "frozen cleaned/ tree changed; re-pin DoD expectations"
    before = _tree_snapshot(FROZEN_ROOT)
    watcher = _watcher(FROZEN_ROOT)
    summary = watcher.scan_once()

    assert summary["imported"] == expected and summary["failed"] == 0
    assert summary["skipped"] == 0 and summary["rejected"] == 0
    runs = _ledger_runs()
    assert len(runs) == expected
    for key, entry in runs.items():
        meta = store.load_meta(str(entry["external_run_id"]))
        assert meta is not None and (config.DATA_ROOT / meta["frames_path"]).is_file()
    assert _tree_snapshot(FROZEN_ROOT) == before  # watch 根零变化


@requires_frozen
def test_d2_rescan_is_idempotent_on_frozen_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    watcher = _watcher(FROZEN_ROOT)
    watcher.scan_once()
    meta = _meta_for("target_poll_out_0830_030352.jsonl|1|cleaned")
    meta_path = config.DATA_ROOT / store.meta_path(meta["external_run_id"])
    mtime_before = meta_path.stat().st_mtime_ns

    summary = watcher.scan_once()
    assert summary["imported"] == 0 and summary["revised"] == 0 and summary["failed"] == 0
    assert meta_path.stat().st_mtime_ns == mtime_before


@requires_frozen
def test_d3_mapping_spot_checks_on_frozen_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _watcher(FROZEN_ROOT).scan_once()

    top = _meta_for("target_poll_out_0830_030352.jsonl|3|cleaned")
    assert top["counts"]["n_targets"] == 1
    assert top["targets"][0]["n_lives"] == 83
    night = _meta_for("target_poll_out_0830_030352.jsonl|3|night_0830_fixed")
    assert night["targets"][0]["n_lives"] == 84
    # 同 (source, round) 双 index 目录 → 身份不同、内容各自成立。
    assert night["external_run_id"] != top["external_run_id"]

    morning = _meta_for("target_poll_out_0830_094655.jsonl|1|morning_0830")
    assert morning["counts"]["n_targets"] == 4
    assert [target["motion"] for target in morning["targets"]] == ["static"] * 4
    for meta in (top, night, morning):
        tids = [target["tid"] for target in meta["targets"]]
        assert len(tids) == len(set(tids))


@requires_frozen
def test_d4_t2k_rollups_match_session_quant_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _watcher(FROZEN_ROOT).scan_once()
    for round_number, pin in T2K_P50_PINS.items():
        meta = _meta_for(f"target_poll_out_0830_030352.jsonl|{round_number}|cleaned")
        p50 = meta["rollups"]["t2k"]["p50"]
        assert p50 == pytest.approx(pin, abs=0.001), f"round {round_number}"
        # 与 session_quant_0830.md 逐局表对拍（±0.001s 舍入容差）。
        assert p50 == pytest.approx(SESSION_QUANT_MD_P50[round_number], abs=0.001)
    round9 = _meta_for("target_poll_out_0830_030352.jsonl|9|cleaned")
    assert round9["counts"]["timeouts"] == 6


@requires_frozen
def test_d5_coarse_pairing_matches_perf_rounds_and_leaves_others_unpaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    # 官方 .perf 六局的 KovaaK run 窗（挑战窗起点=文件名锚 ±1s 求交的期望值）。
    for offset, (round_number, start_unix) in enumerate(CROSSCHECK_START_UNIX.items()):
        start_ms = start_unix * 1000.0
        file_store.write_json(f"runs/{901 + offset}/meta.json", {
            "id": 901 + offset,
            "user_id": config.DESKTOP_LOCAL_PROFILE,
            "window_start_epoch_ms": start_ms,
            "window_end_epoch_ms": start_ms + 60_000.0,
            "performance_summary": {
                "header": {"scenario_name": PERF_ROUND_SCENARIOS[round_number]},
                "totals": {"kills": 1, "shotsFired": 2},
            },
        })
    _watcher(FROZEN_ROOT).scan_once()

    for round_number, expected_run_id in zip(
        sorted(CROSSCHECK_START_UNIX), [901, 902, 903, 904, 905, 906], strict=True,
    ):
        meta = _meta_for(f"target_poll_out_0830_030352.jsonl|{round_number}|cleaned")
        pairing = meta["pairing"]
        assert pairing["pair_confidence"] == "coarse", f"round {round_number}"
        assert expected_run_id in pairing["matched_run_ids"]
        assert pairing["perf_official"]["scenario_name"] == PERF_ROUND_SCENARIOS[round_number]
    # 无官方 .perf 的轮（6/7/8）与 validation 轮保持未配对，不报错。
    for key in (
        "target_poll_out_0830_030352.jsonl|6|cleaned",
        "target_poll_out_0830_030352.jsonl|7|cleaned",
        "target_poll_out_0830_030352.jsonl|8|cleaned",
        "round_01.jsonl|0|validation_static_1wall6targets_001352",
    ):
        assert _meta_for(key)["pairing"]["matched_run_ids"] == []
    # 官方名 vs auto-label：round1 agree（beanClick），round2 disagree（top-1 可错）。
    agree = _meta_for("target_poll_out_0830_030352.jsonl|1|cleaned")["pairing"]["label_agreement"]
    disagree = _meta_for("target_poll_out_0830_030352.jsonl|2|cleaned")["pairing"]["label_agreement"]
    assert agree == "agree" and disagree == "disagree"


@requires_frozen
def test_d6_label_passthrough_is_field_exact_and_never_touches_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    overrides_path = config.DATA_ROOT / "config" / "scenario-overrides.json"
    _watcher(FROZEN_ROOT).scan_once()

    passthrough = 0
    pending = 0
    saw_tie_group_missing = False
    saw_tie_group_list = False
    saw_uncertain = False
    for key, entry in _ledger_runs().items():
        meta = store.load_meta(str(entry["external_run_id"]))
        assert meta is not None
        proposal = meta["scenario_proposal"]
        round_file = FROZEN_ROOT / str(meta["origin"]["round_file"])
        scenario_path = round_file.parent / "scenario.json"
        if not scenario_path.is_file():
            assert proposal == {"source": "pending"}
            pending += 1
            continue
        source = json.loads(scenario_path.read_text(encoding="utf-8"))
        # orphan 轮的 origin.round 是合成值：round_file 名优先，round 号仅兜底。
        file_name = Path(round_file).name
        round_entry = next(
            (item for item in source["rounds"] if item.get("round_file") == file_name),
            None,
        )
        if round_entry is None:
            round_entry = next(
                item for item in source["rounds"] if item.get("round") == meta["origin"]["round"]
            )
        for field, value in round_entry["scenario"].items():
            assert proposal[field] == value, f"{key}: field {field}"
        assert proposal["generator"] == source["generator"]
        assert proposal["generated_at"] == source["generated_at"]
        assert proposal["source"] == "scenario.json"
        passthrough += 1
        saw_uncertain = saw_uncertain or proposal.get("status") == "uncertain"
        # tie_group 各态（当前冻结集：缺失 12 + 非空列表 50；历史数据另有 null/[]）。
        saw_tie_group_missing = saw_tie_group_missing or "tie_group" not in proposal
        saw_tie_group_list = saw_tie_group_list or isinstance(proposal.get("tie_group"), list)
    assert passthrough + pending == 74
    assert pending == 12  # 030352 主目录 9 轮 + verify0831（0831_192538）3 轮无 scenario.json
    assert saw_uncertain and saw_tie_group_missing and saw_tie_group_list
    assert not overrides_path.exists()  # proposal-only：绝不写 scenario-overrides.json


@requires_frozen
def test_d5_unpaired_rounds_without_any_runs_stay_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    summary = _watcher(FROZEN_ROOT).scan_once()
    assert summary["imported"] == 74 and summary["failed"] == 0
    meta = _meta_for("round_01.jsonl|0|validation_static_1wall6targets_001352")
    assert meta["pairing"]["matched_run_ids"] == []
    assert meta["pairing"]["pair_confidence"] is None


@requires_frozen
def test_orphan_round_files_import_with_missing_index_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _watcher(FROZEN_ROOT).scan_once()
    meta = _meta_for("round_01.jsonl|0|validation_static_1wall6targets_001352")
    assert meta["origin"]["index_file"] is None
    assert "missing_rounds_index" in meta["quality"]["known_issues"]
    assert meta["targets"] == [] and meta["rollups"]["t2k"]["n"] == 0
    assert meta["time"]["n_frames"] > 0 and meta["time"]["duration"] > 0
    # 有场景旁车：proposal 照常透传。
    assert meta["scenario_proposal"]["label"] == "1wall 6targets small"


# ------------------------------------------------------------- D7 quality gates

def test_unsupported_format_version_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    summary = _watcher(root).scan_once()
    # 健全 index（batch+late orphan）正常导入；v2 index 整体 fail-closed。
    assert summary["imported"] == 2 and summary["rejected"] == 1

    rejected = [entry for entry in store.read_ledger().values()
                if isinstance(entry, dict) and entry.get("status") == "rejected"]
    assert len(rejected) == 1 and rejected[0]["code"] == "unsupported_version"
    # 不产 meta：badver 的轮没有任何 external 产物。
    runs = _ledger_runs()
    assert "target_poll_out_0102_010405.jsonl|1|badver" not in runs
    assert not any(
        meta["origin"]["source_file"] == "target_poll_out_0102_010405.jsonl"
        for meta in (store.load_meta(str(entry["external_run_id"])) for entry in runs.values())
    )


def test_degraded_frames_and_target_count_conflict_set_gate_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = tmp_path / "cleaned"
    source_dir = root / "g" / "target_poll_out_0101_010203"
    source_dir.mkdir(parents=True)
    # 89 好行 + 1 坏行 → 解析率 98.9% < 99% → degraded（继续导入，不丢弃）。
    (source_dir / "round_01.jsonl").write_bytes(_round_payload(n_frames=90, bad_last=True)[:-1] + b"{bad\n")
    _write_scenario(source_dir / "scenario.json", [{
        "round": 1, "round_file": "round_01.jsonl",
        "scenario": {"status": "matched", "label": "6targets check", "score": 0.99},
    }])
    conflict_targets = [
        {"tid": 0, "addr_hex": "0x1", "motion": "static", "birth": 900.0, "death": 901.0,
         "alive_window": [900.0, 901.0], "n_samples": 10, "n_lives": 1, "path_length": 0.0,
         "lives": [{"t_start": 900.0, "t_end": 901.0, "n": 10, "path": 0.0}],
         "domain": {"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 1.0]}},
        {"tid": 1, "addr_hex": "0x2", "motion": "static", "birth": 900.0, "death": 901.0,
         "alive_window": [900.0, 901.0], "n_samples": 10, "n_lives": 1, "path_length": 0.0,
         "lives": [{"t_start": 900.0, "t_end": 901.0, "n": 10, "path": 0.0}],
         "domain": {"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 1.0]}},
    ]
    _write_index(
        root / "g" / "rounds_index.json",
        rounds=[_index_entry(targets=conflict_targets, n_targets=6)],
    )
    _watcher(root).scan_once()
    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|g")
    assert meta["quality"]["gates"]["frames_readable"] == "degraded"
    # 标签宣称 6 靶，index 只有 2 个 target → warn（暴露而非掩盖）。
    assert meta["quality"]["gates"]["target_count"] == "warn"


def test_unknown_target_count_knowledge_stays_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    assert ingest.known_target_count("1wall 6targets small") == 6
    assert ingest.known_target_count("1wall5targets_pasu") == 5
    assert ingest.known_target_count("1w2ts reload") is None  # 无官方数知识 → 不检查
    assert ingest.known_target_count(None) is None


# ----------------------------------------------------- proposal patch (late sidecar)

def test_late_scenario_sidecar_patches_pending_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    watcher = _watcher(root)
    watcher.scan_once()
    meta = _meta_for("round_01.jsonl|0|late")
    assert meta["scenario_proposal"] == {"source": "pending"}

    # scenario.json 后到（morning 实测滞后场景）→ 单字段 patch，身份不变。
    (root / "late" / "scenario.json").write_text(json.dumps({
        "generator": "scenario_meta.py label",
        "generated_at": "2026-08-30T09:57:57",
        "rounds": [{"round_file": "round_01.jsonl", "scenario": {
            "status": "uncertain", "label": "1w2ts reload", "score": 0.573,
            "margin": 0.005, "coverage": 0.74, "score_parts": {},
            "candidates": [{"name": "1w2ts reload", "score": 0.573}], "tie_group": [],
        }}],
    }, ensure_ascii=False), encoding="utf-8")
    summary = watcher.scan_once()
    assert summary["proposal_patches"] == 1

    patched = _meta_for("round_01.jsonl|0|late")
    assert patched["external_run_id"] == meta["external_run_id"]
    assert patched["scenario_proposal"]["label"] == "1w2ts reload"
    assert patched["scenario_proposal"]["score"] == 0.573
    assert patched["scenario_proposal"]["tie_group"] == []
    # 再扫一遍不重复 patch。
    assert watcher.scan_once()["proposal_patches"] == 0


def test_import_never_writes_scenario_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    watcher = _watcher(root)
    watcher.scan_once()
    (root / "late" / "scenario.json").write_text("{}", encoding="utf-8")
    watcher.scan_once()
    assert not (config.DATA_ROOT / "config" / "scenario-overrides.json").exists()


# ------------------------------------------------- sidecars (SIDECARS.md v1)

_ABSENT_SIDECAR = {"present": False, "sha256": None, "size": None}


def _sidecar_payloads(round_number: int = 1) -> dict[str, bytes]:
    """Contract-shaped minimal sidecar bytes (SIDECARS.md §0 件清单)。"""
    nn = f"{round_number:02d}"
    views = "\n".join(
        json.dumps({"t": 900.0 + i * 0.031, "pos": [1.0, 2.0, 3.0],
                    "rot": [0.0, float(i), 0.0], "fov": 103.0})
        for i in range(4)
    ) + "\n"
    inputs = "\n".join(
        json.dumps({"t": 900.1 + i * 0.05, "dx": 12, "dy": -3, "btn": ["L_down"] if i == 0 else []})
        for i in range(3)
    ) + "\n"
    bb = json.dumps({
        "schema_version": "round_bb.v1",
        "challenges": [{"perf_file": "x.perf", "scenario": "s", "rounds": [round_number],
                        "window_t": [900.0, 901.2], "timescale": 1.0, "bots": []}],
    })
    manifest = json.dumps({
        "alignment": {"method": "xcorr", "s_epoch_of_t0": 1788107500.368, "accepted": True},
        "rounds": [{"round": round_number, "n_views": 4, "n_inputs": 3, "view_gaps_gt_200ms": 0}],
    })
    return {
        f"views_{nn}.jsonl": views.encode("utf-8"),
        f"inputs_{nn}.jsonl": inputs.encode("utf-8"),
        "bb.json": bb.encode("utf-8"),
        "merge_manifest.json": manifest.encode("utf-8"),
    }


def make_sidecar_tree(tmp_path: Path, *, with_sidecars: bool = True) -> Path:
    """两轮 index 目录：round_01 全套旁车；round_02 只有共享件（无 views/inputs）。"""
    root = tmp_path / "cleaned"
    source_dir = root / "batch" / "target_poll_out_0101_010203"
    source_dir.mkdir(parents=True)
    (source_dir / "round_01.jsonl").write_bytes(_round_payload())
    (source_dir / "round_02.jsonl").write_bytes(_round_payload(n_frames=45, t0=905.0))
    if with_sidecars:
        for name, payload in _sidecar_payloads(1).items():
            (source_dir / name).write_bytes(payload)
    _write_index(root / "batch" / "rounds_index.json", rounds=[
        _index_entry(),
        _index_entry(round_number=2, file="round_02.jsonl", t_start=905.0, t_end=906.35),
    ])
    return root


def test_sidecar_import_freezes_copies_and_records_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_sidecar_tree(tmp_path)
    summary = _watcher(root).scan_once()
    assert summary["imported"] == 2 and summary["failed"] == 0

    payloads = _sidecar_payloads(1)
    meta1 = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    sidecars = meta1["sidecars"]
    assert set(sidecars) == {"views", "inputs", "bb", "merge_manifest"}
    for key, name in (("views", "views_01.jsonl"), ("inputs", "inputs_01.jsonl"),
                      ("bb", "bb.json"), ("merge_manifest", "merge_manifest.json")):
        assert sidecars[key] == {
            "present": True,
            "sha256": hashlib.sha256(payloads[name]).hexdigest(),
            "size": len(payloads[name]),
        }
        frozen = config.DATA_ROOT / store.sidecar_path(meta1["external_run_id"], name)
        assert frozen.read_bytes() == payloads[name]  # 原名冻结、字节一致

    # round_02 无 views_02/inputs_02 → present=False；目录级共享件照记同一指纹。
    meta2 = _meta_for("target_poll_out_0101_010203.jsonl|2|batch")
    assert meta2["sidecars"]["views"] == _ABSENT_SIDECAR
    assert meta2["sidecars"]["inputs"] == _ABSENT_SIDECAR
    assert meta2["sidecars"]["bb"]["sha256"] == sidecars["bb"]["sha256"]
    assert meta2["sidecars"]["merge_manifest"]["sha256"] == sidecars["merge_manifest"]["sha256"]


def test_late_sidecars_freeze_without_content_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_sidecar_tree(tmp_path, with_sidecars=False)
    watcher = _watcher(root)
    assert watcher.scan_once()["imported"] == 2
    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert all(fp["present"] is False for fp in meta["sidecars"].values())

    # 旁车后到（merge_channels 滞后于 cleaner）→ skip 轻路径补冻结，身份不变。
    source_dir = root / "batch" / "target_poll_out_0101_010203"
    payloads = _sidecar_payloads(1)
    for name, payload in payloads.items():
        (source_dir / name).write_bytes(payload)
    summary = watcher.scan_once()
    assert summary["imported"] == 0 and summary["revised"] == 0 and summary["failed"] == 0
    refreshed = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert refreshed["external_run_id"] == meta["external_run_id"]
    assert refreshed["revisions"] == []
    assert refreshed["sidecars"]["views"]["sha256"] == hashlib.sha256(payloads["views_01.jsonl"]).hexdigest()
    frozen = config.DATA_ROOT / store.sidecar_path(refreshed["external_run_id"], "views_01.jsonl")
    assert frozen.read_bytes() == payloads["views_01.jsonl"]

    # merge 重跑（bb.json 变化）→ 刷指纹+副本，仍不触发 content revision。
    new_bb = payloads["bb.json"] + b"\n"
    (source_dir / "bb.json").write_bytes(new_bb)
    summary = watcher.scan_once()
    assert summary["revised"] == 0 and summary["imported"] == 0
    rerun = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert rerun["sidecars"]["bb"]["sha256"] == hashlib.sha256(new_bb).hexdigest()
    frozen_bb = config.DATA_ROOT / store.sidecar_path(rerun["external_run_id"], "bb.json")
    assert frozen_bb.read_bytes() == new_bb
    assert rerun["revisions"] == []


def test_sidecar_rescan_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_sidecar_tree(tmp_path)
    watcher = _watcher(root)
    watcher.scan_once()
    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    meta_path = config.DATA_ROOT / store.meta_path(meta["external_run_id"])
    frozen = {
        name: config.DATA_ROOT / store.sidecar_path(meta["external_run_id"], name)
        for name in ("views_01.jsonl", "inputs_01.jsonl", "bb.json", "merge_manifest.json")
    }
    meta_mtime = meta_path.stat().st_mtime_ns
    frozen_mtimes = {name: path.stat().st_mtime_ns for name, path in frozen.items()}

    assert watcher.scan_once()["imported"] == 0
    # 换新 watcher（无内存快路径，走 hash-equal 路径）重扫：同样不重写。
    assert _watcher(root).scan_once()["imported"] == 0

    after = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert after["sidecars"] == meta["sidecars"]
    assert meta_path.stat().st_mtime_ns == meta_mtime
    for name, path in frozen.items():
        assert path.stat().st_mtime_ns == frozen_mtimes[name]


def test_sidecar_freeze_write_failure_is_isolated_and_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """P2-4：冻结副本写失败（如 Windows 杀软锁目标文件触发 PermissionError）
    不中止 scan——其余旁车与轮次照常导入、failed 可观测、哈希缓存不滞留旧
    stat（快路径不得沿用旧指纹），写恢复后下轮 scan 成功补齐副本。"""
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_sidecar_tree(tmp_path)
    watcher = _watcher(root)
    assert watcher.scan_once()["imported"] == 2

    source_dir = root / "batch" / "target_poll_out_0101_010203"
    v1 = _sidecar_payloads(1)
    views_path = source_dir / "views_01.jsonl"
    v2 = v1["views_01.jsonl"] + b'{"t": 900.2, "x": 1}\n'
    views_path.write_bytes(v2)

    real_write = store.write_frozen_sidecar

    def locked_write(external_run_id: str, name: str, payload: bytes) -> None:
        if name == "views_01.jsonl":
            raise PermissionError("antivirus lock")
        real_write(external_run_id, name, payload)

    monkeypatch.setattr(store, "write_frozen_sidecar", locked_write)
    # 写失败被隔离：skip 刷新照常完成，failed 计数可观测，指纹保持 recorded。
    summary = watcher.scan_once()
    assert summary["imported"] == 0 and summary["failed"] == 1
    meta = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert meta["sidecars"]["views"] == {
        "present": True,
        "sha256": hashlib.sha256(v1["views_01.jsonl"]).hexdigest(),
        "size": len(v1["views_01.jsonl"]),
    }
    # 缓存不滞留：连续失败 scan 每轮都重试写（快路径不得沿用旧指纹）。
    assert watcher.scan_once()["failed"] == 1

    monkeypatch.setattr(store, "write_frozen_sidecar", real_write)
    assert watcher.scan_once()["failed"] == 0
    recovered = _meta_for("target_poll_out_0101_010203.jsonl|1|batch")
    assert recovered["sidecars"]["views"] == {
        "present": True,
        "sha256": hashlib.sha256(v2).hexdigest(),
        "size": len(v2),
    }
    frozen = config.DATA_ROOT / store.sidecar_path(
        recovered["external_run_id"], "views_01.jsonl",
    )
    assert frozen.read_bytes() == v2


def test_dirs_without_sidecars_are_unaffected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = make_cleaned_tree(tmp_path)
    summary = _watcher(root).scan_once()
    assert summary["imported"] == 2 and summary["failed"] == 0
    for key in ("target_poll_out_0101_010203.jsonl|1|batch", "round_01.jsonl|0|late"):
        meta = _meta_for(key)
        assert meta["sidecars"] == {name: _ABSENT_SIDECAR for name in
                                    ("views", "inputs", "bb", "merge_manifest")}
        ext_dir = config.DATA_ROOT / "external" / meta["external_run_id"]
        assert sorted(path.name for path in ext_dir.iterdir()) == ["meta.json", "round.jsonl"]


# ------------------------------------------------------------------- D8 API

def _desktop_headers() -> dict[str, str]:
    return {"X-Aiming-Cookie-Desktop-Token": "test-launch-token"}


class _FakeService:
    def __init__(self, root: Path | None) -> None:
        self._root = root

    @property
    def watch_root(self) -> Path | None:
        return self._root

    def reconfigure(self, root: Path | None) -> bool:
        self._root = root
        return True

    def diagnostics(self) -> dict:
        return {"version": "external_telemetry_watcher.v1", "configured": self._root is not None}


@pytest.mark.asyncio
async def test_external_telemetry_api_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "test-launch-token")
    root = make_cleaned_tree(tmp_path)
    watcher = _watcher(root)
    watcher.scan_once()

    previous = getattr(app.state, "external_telemetry_service", None)
    app.state.external_telemetry_service = _FakeService(root)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unauthorized = await client.get("/api/external-runs")
            assert unauthorized.status_code == 401

            unset = await client.get(
                "/api/external-telemetry", headers=_desktop_headers(),
            )
            assert unset.status_code == 200
            assert unset.json()["watch_root"] is None
            assert unset.json()["run_count"] == 2

            invalid = await client.put(
                "/api/external-telemetry", headers=_desktop_headers(),
                json={"watch_root": "relative/path"},
            )
            assert invalid.status_code == 422

            saved = await client.put(
                "/api/external-telemetry", headers=_desktop_headers(),
                json={"watch_root": str(root)},
            )
            assert saved.status_code == 200
            assert saved.json()["activation"] == "activated"
            assert saved.json()["watch_root"] == str(root.resolve())

            runs = await client.get("/api/external-runs?limit=10", headers=_desktop_headers())
            assert runs.status_code == 200
            body = runs.json()
            assert body["total"] == 2 and len(body["items"]) == 2
            item = next(item for item in body["items"] if item["source_file"] == "target_poll_out_0101_010203.jsonl")
            assert item["proposal_label"] == "beanClick"
            assert item["proposal_status"] == "matched"
            assert item["t2k_p50"] is not None

            detail = await client.get(
                f"/api/external-runs/{item['external_run_id']}", headers=_desktop_headers(),
            )
            assert detail.status_code == 200
            assert detail.json()["run"]["schema_version"] == "external_run.v1"

            traversal = await client.get(
                "/api/external-runs/..%2F..%2Fconfig", headers=_desktop_headers(),
            )
            assert traversal.status_code == 404
            malformed = await client.get(
                "/api/external-runs/ext-zzzz", headers=_desktop_headers(),
            )
            assert malformed.status_code == 404
    finally:
        if previous is None:
            app.state.external_telemetry_service = None
        else:
            app.state.external_telemetry_service = previous


def test_external_and_kovaak_diagnostics_are_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    service = ingest.ExternalTelemetryService()
    assert service.diagnostics() == {
        "version": "external_telemetry_watcher.v1", "source": "automatic", "configured": False,
    }
    snapshot = ingest.ExternalTelemetryWatcher(tmp_path / "missing", stable_scans=1)
    summary = snapshot.scan_once()
    assert summary["imported"] == 0
    assert snapshot.diagnostics()["directory_state"] == "directory_missing"


# --------------------------------------------------------------- D9 isolation

def test_missing_watch_root_is_observable_and_half_written_index_never_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    root = tmp_path / "cleaned"
    source_dir = root / "b" / "target_poll_out_0101_010203"
    source_dir.mkdir(parents=True)
    (source_dir / "round_01.jsonl").write_bytes(_round_payload())
    index_path = root / "b" / "rounds_index.json"
    index_path.write_text("{ truncated by cleaner crash", encoding="utf-8")

    watcher = _watcher(root, stable_scans=2)
    first = watcher.scan_once()
    assert first["failed"] == 0  # 第一眼：尚未稳定，不解析
    second = watcher.scan_once()
    assert second["failed"] == 1  # 稳定后半成品 → 重试预算，不产 meta
    assert watcher.scan_once()["imported"] == 0
    assert _ledger_runs() == {}

    # cleaner 补写完成 → 同一路径自然恢复导入。
    _write_index(index_path)
    assert watcher.scan_once()["imported"] == 0  # 重新计稳定
    assert watcher.scan_once()["imported"] == 1
    assert len(_ledger_runs()) == 1


def test_service_reconfiguration_swaps_watchers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    service = ingest.ExternalTelemetryService(poll_interval=0.05)
    assert service.watch_root is None
    root = make_cleaned_tree(tmp_path)
    service.reconfigure(root)
    assert service.watch_root == root
    service.start()
    try:
        assert service.diagnostics()["configured"] is True
        assert service.diagnostics()["running"] is True
    finally:
        service.stop()
    service.reconfigure(None)
    assert service.watch_root is None


# ---------------------------------------------------------------- D10 docs

def test_docs_page_documents_schema_and_pins_upstream_format() -> None:
    docs_page = _REPO_ROOT / "docs" / "EXTERNAL_TELEMETRY_IMPORT.md"
    assert docs_page.is_file()
    text = docs_page.read_text(encoding="utf-8")
    assert "external_run.v1" in text
    assert "FORMAT.md" in text
    assert "v2" in text  # 上游格式版本钉死
    assert "scenario-overrides" in text  # proposal-only 语义必须写明
