"""外部遥测源注入分析输入快照（telemetry_multimodal 切片）。

覆盖：配对轮 → external_telemetry 源可用且 tier 选 telemetry_multimodal；
无配对 → 不可用 + 原因，tier 落回 multimodal；导入清单与冻结侧不符 →
sidecars_stale。producer 本切片不接（分析仍走 CV 路径）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from webapp.backend import external_telemetry_store as store
from webapp.backend import file_store, kovaak_run_store
from webapp.backend.source_requirements import validate_source_requirements
from webapp.tests.test_kovaak_runs import (
    _complete_multimodal_run,
    _write_capture_video_bundle,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


async def _attach_run_video(run: dict) -> None:
    """给 run 冻结一份可用视频（走真实 begin/attach 收据校验路径）。"""
    data_root = file_store._data_root()
    video = data_root / "runs" / str(run["id"]) / "video-request-1.mp4"
    _write_capture_video_bundle(video, run_id=run["id"])
    request_digest = "a" * 64
    await kovaak_run_store.begin_run_video_attach(
        run["id"], run["user_id"],
        pending_video_path=video,
        request_digest=request_digest,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        alignment_summary=run["alignment_summary"],
        data_root=data_root,
    )
    attached = await kovaak_run_store.attach_run_video(
        run["id"], run["user_id"], video,
        expected_pending_video_path=video,
        expected_request_digest=request_digest,
        data_root=data_root,
    )
    assert attached is not None and attached["video_state"] == "attached"


def _fingerprint(payload: bytes) -> dict[str, object]:
    return {"present": True, "sha256": _sha256(payload), "size": len(payload)}


def _write_paired_external_run(
    run_id: int,
    user_id: str,
    *,
    external_run_id: str,
    imported_at: str,
    t_start: float = 0.0,
    t_end: float = 0.031,
    epoch_start_est: float | None = None,
    matched_run_ids: list[int] | None = None,
    perf_official: dict | None = None,
) -> dict:
    """写一个配对到指定 KovaaK run 的 ExternalTelemetryRun（meta + 冻结件）。

    epoch_start_est 给出时写入 time.epoch_anchor，使选轮逻辑可用绝对纪元窗
    做挑战窗贴合度比较；matched_run_ids/perf_official 默认照旧（单匹配、
    无主指认）。
    """
    frames = b'{"ev":"frame","t":0.0}\n{"ev":"frame","t":0.031}\n'
    sidecar_sources = {
        "bb": b'{"bb": [1, 2, 3]}',
        "merge_manifest": b'{"rounds": [3]}',
    }
    store.write_frozen_frames(external_run_id, frames)
    for key, payload in sidecar_sources.items():
        name = store.sidecar_source_name(key, "round_03.jsonl")
        assert name is not None
        store.write_frozen_sidecar(external_run_id, name, payload)
    time_meta: dict = {"t_start": t_start, "t_end": t_end, "duration": t_end - t_start}
    if epoch_start_est is not None:
        time_meta["epoch_anchor"] = {
            "epoch_start_est": epoch_start_est,
            "method": "filename_stamp",
        }
    meta = {
        "schema_version": store.SCHEMA_VERSION,
        "external_run_id": external_run_id,
        "user_id": user_id,
        "origin": {
            "source_file": "demo_0801_101010.jsonl",
            "round": 3,
            "round_file": "upstream/demo/round_03.jsonl",
            "index_file": "upstream/demo/rounds_index.json",
            "generator": None,
            "format_version": store.SUPPORTED_FORMAT_VERSION,
            "params": None,
        },
        "fingerprints": {
            "round_sha256": _sha256(frames),
            "round_size": len(frames),
            "round_mtime_ns": 0,
            "import_parser_version": store.IMPORT_PARSER_VERSION,
        },
        "time": time_meta,
        "scenario_proposal": {"source": "pending"},
        # pairing.matched_run_ids 由 ingest.pair_runs 生成（±1s 窗配对）。
        "pairing": {
            "matched_run_ids": (
                [run_id] if matched_run_ids is None else matched_run_ids
            ),
            "pair_confidence": "coarse",
            "perf_official": perf_official,
            "label_agreement": "unverifiable",
        },
        "sidecars": {
            key: _fingerprint(payload) for key, payload in sidecar_sources.items()
        },
        "frames_path": store.frames_path(external_run_id),
        "imported_at": imported_at,
        "revisions": [],
    }
    store.save_meta(external_run_id, meta)
    ledger = store.read_ledger()
    ledger[f"demo|3|{external_run_id}"] = {
        "status": "imported",
        "external_run_id": external_run_id,
        "content_hash": _sha256(frames),
        "imported_at": imported_at,
    }
    store.write_ledger(ledger)
    return meta


@pytest.mark.asyncio
async def test_paired_run_snapshot_carries_available_external_telemetry(
    tmp_path: Path,
):
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="telemetry-run",
    )
    meta = _write_paired_external_run(
        run["id"], "u1", external_run_id="ext-paired01", imported_at="2026-08-31T00:00:00Z",
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    source = snapshot["sources"]["external_telemetry"]

    assert source["availability"] == "available"
    assert source["external_run_id"] == "ext-paired01"
    assert source["round"] == 3
    assert source["frames_path"] is not None
    assert source["pairing_confidence"] == "coarse"
    # 旁车指纹摘要：present 与否逐项可见（views/inputs 未冻结 → not present）。
    assert source["sidecars"]["bb"] == _fingerprint(b'{"bb": [1, 2, 3]}')
    assert source["sidecars"]["merge_manifest"] == _fingerprint(b'{"rounds": [3]}')

    gate = validate_source_requirements(snapshot)
    assert gate["selected_mode"] == "telemetry_multimodal"
    assert gate["supported_modes"][0] == "telemetry_multimodal"

    # 公开投影保持 path-free：frames_path 不出边界，external_run_id 保留。
    public = kovaak_run_store.public_analysis_input_snapshot(snapshot)
    public_source = public["sources"]["external_telemetry"]
    assert "frames_path" not in public_source
    assert public_source["external_run_id"] == "ext-paired01"
    assert public_source["availability"] == "available"
    assert meta is not None


@pytest.mark.asyncio
async def test_unpaired_run_snapshot_marks_telemetry_unavailable(tmp_path: Path):
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="no-telemetry-run",
    )
    await _attach_run_video(run)

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    source = snapshot["sources"]["external_telemetry"]

    assert source["availability"] == "unavailable"
    assert source["reason"] == "telemetry_not_paired"
    assert source["external_run_id"] is None

    gate = validate_source_requirements(snapshot)
    assert gate["selected_mode"] == "multimodal"
    assert "telemetry_multimodal" not in gate["supported_modes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("damage", "damaged"),
    [
        ("sidecar", "bb"),
        ("frames", "round"),
    ],
)
async def test_frozen_side_divergence_fails_closed_as_sidecars_stale(
    tmp_path: Path, damage: str, damaged: str,
):
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key=f"stale-telemetry-{damage}",
    )
    await _attach_run_video(run)
    _write_paired_external_run(
        run["id"], "u1", external_run_id="ext-stale01", imported_at="2026-08-31T00:00:00Z",
    )

    # 冻结侧被改写，导入清单（meta 指纹）不再匹配 → fail-closed。
    if damaged == "bb":
        frozen = (
            store.file_store._data_root()
            / store.sidecar_path("ext-stale01", "bb.json")
        )
        frozen.write_bytes(b'{"bb": ["tampered"]}')
    else:
        frozen = (
            store.file_store._data_root()
            / store.frames_path("ext-stale01")
        )
        frozen.write_bytes(b'{"ev":"frame","t":9.9}\n')

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    source = snapshot["sources"]["external_telemetry"]

    assert source["availability"] == "unavailable"
    assert source["reason"] == "sidecars_stale"
    assert source["external_run_id"] == "ext-stale01"

    gate = validate_source_requirements(snapshot)
    assert gate["selected_mode"] == "multimodal"


@pytest.mark.asyncio
async def test_newest_paired_external_run_wins(tmp_path: Path):
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="newest-telemetry-run",
    )
    _write_paired_external_run(
        run["id"], "u1", external_run_id="ext-old0001", imported_at="2026-08-30T00:00:00Z",
    )
    _write_paired_external_run(
        run["id"], "u1", external_run_id="ext-new0001", imported_at="2026-08-31T12:00:00Z",
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")

    assert snapshot["sources"]["external_telemetry"]["external_run_id"] == "ext-new0001"


@pytest.mark.asyncio
async def test_multiple_matched_rounds_pick_best_window_fit(tmp_path: Path):
    """多 ext 轮匹配同一 run：按挑战窗覆盖度选优，不按最新导入。

    回归 0901 真机 54044：相邻轮窗交叠使粗配对同时命中 round 2/round 3，
    旧逻辑按 imported_at 取 latest 错选 round 2（1wall6 的靶子数据）。
    """
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="multi-match-telemetry-run",
    )
    # 全覆盖轮（旧导入）：绝对窗 [0, 4000]ms 完整罩住 run 窗 [1000, 2000]ms。
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-fullcov1", imported_at="2026-08-30T00:00:00Z",
        t_start=0.0, t_end=4.0, epoch_start_est=0.0,
    )
    # 边缘重叠轮（新导入）：绝对窗 [1900, 3000]ms 只盖 run 窗尾部 10%。
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-edgecov1", imported_at="2026-08-31T12:00:00Z",
        t_start=1.9, t_end=3.0, epoch_start_est=0.0,
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")

    assert snapshot["sources"]["external_telemetry"]["external_run_id"] == "ext-fullcov1"


@pytest.mark.asyncio
async def test_window_tie_prefers_run_unique_primary_match(tmp_path: Path):
    """覆盖率平手：优先“该 run 是其唯一/主要匹配”的 ext，而非最新导入。"""
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="tie-telemetry-run",
    )
    # 唯一匹配 + perf_official 主指认（旧导入），窗口与共享轮同样全覆盖。
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-uniq0001", imported_at="2026-08-30T00:00:00Z",
        t_start=0.5, t_end=2.5, epoch_start_est=0.0,
        perf_official={"run_id": run["id"], "scenario_name": "Fixture"},
    )
    # 多匹配、无主指认（新导入），覆盖率同为 1.0。
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-shared01", imported_at="2026-08-31T12:00:00Z",
        t_start=0.5, t_end=2.5, epoch_start_est=0.0,
        matched_run_ids=[run["id"], 999_999],
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")

    assert snapshot["sources"]["external_telemetry"]["external_run_id"] == "ext-uniq0001"


@pytest.mark.asyncio
async def test_single_and_no_match_selection_unchanged(tmp_path: Path):
    """单匹配照选唯一轮；无匹配照旧 unavailable + telemetry_not_paired。"""
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="single-match-telemetry-run",
    )
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-single01", imported_at="2026-08-30T00:00:00Z",
        t_start=0.0, t_end=4.0, epoch_start_est=0.0,
    )
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    assert snapshot["sources"]["external_telemetry"]["external_run_id"] == "ext-single01"
    assert snapshot["sources"]["external_telemetry"]["availability"] == "available"

    unpaired, _, _, _ = await _complete_multimodal_run(
        tmp_path, user_id="u1", source_key="no-match-telemetry-run",
    )
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(unpaired["id"], "u1")
    assert snapshot["sources"]["external_telemetry"]["availability"] == "unavailable"
    assert snapshot["sources"]["external_telemetry"]["reason"] == "telemetry_not_paired"


async def _stats_only_run(tmp_path: Path, *, user_id: str, source_key: str) -> dict:
    """stats+performance 就绪、无 trace、无视频的 run（遥测档的目标形态）。"""
    stats = tmp_path / f"{source_key}-Stats.csv"
    performance = tmp_path / f"{source_key}-Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=source_key,
        scenario="Complete test scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
    )
    return await kovaak_run_store.set_run_alignment(
        run["id"],
        user_id,
        state="resolved",
        summary={
            "start_ms": 1_000,
            "end_ms": 2_000,
            "duration_ms": 1_000,
            "start_source": "test_start",
            "end_source": "test_end",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    ) or run


@pytest.mark.asyncio
async def test_create_analysis_passes_with_telemetry_and_stats_only(tmp_path: Path):
    """回归 0901 真机 54052-54055：遥测+stats、无视频/raw 的 run 创建分析
    不再被源门以 raw_input_missing/video_missing 拦截，入队 telemetry_multimodal。"""
    from webapp.backend import analysis_service, queue

    run = await _stats_only_run(
        tmp_path, user_id="u1", source_key="telemetry-only-create",
    )
    _write_paired_external_run(
        run["id"], "u1",
        external_run_id="ext-telemonly1", imported_at="2026-09-01T00:00:00Z",
    )

    created = await analysis_service.create_analysis_from_run("u1", run["id"])

    assert "reused" not in created
    session = await queue.get_session(created["session_id"])
    assert session["input_mode"] == "telemetry_multimodal"
    assert not session.get("video_path")
    ext = session["input_snapshot"]["sources"]["external_telemetry"]
    assert ext["availability"] == "available"
    assert ext["external_run_id"] == "ext-telemonly1"


@pytest.mark.asyncio
async def test_create_analysis_stays_fail_closed_without_telemetry(tmp_path: Path):
    """stats 有、遥测无、无视频/raw：无可用档，源门保持 fail-closed。"""
    from webapp.backend import analysis_service

    run = await _stats_only_run(
        tmp_path, user_id="u1", source_key="no-telemetry-create",
    )

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run("u1", run["id"])

    assert exc_info.value.code == "input_unavailable"
    assert "external_telemetry_missing" in exc_info.value.message
    assert "raw_input_missing" in exc_info.value.message
    assert "video_missing" in exc_info.value.message
