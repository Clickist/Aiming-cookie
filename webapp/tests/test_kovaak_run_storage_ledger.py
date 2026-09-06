"""加载慢专项（工作流 A/B/C）的记账、缓存与解耦合同测试。

- A：run 存储台账（storage_ledger）持久化、legacy 自愈、外部删除兜底重算；
  incomplete 列表改用 stat 身份并跳过 meta.json。
- B：meta.json / sessions 轻缓存的指纹校验与单文件重读。
- C：capture-status 1s 轮询与全量 session 解析解耦。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, file_store, kovaak_run_store, queue
import webapp.backend.routes as routes_mod
from webapp.backend.app import app
from webapp.backend.schemas import IncompleteCaptureItemOut

TEST_WORKER = "ledger-worker"


def _write_video_bundle(video_path, *, run_id: int, contents: bytes = b"run-owned-mp4"):
    receipt = {
        "version": "capture_receipt.v1",
        "requestDigest": "a" * 64,
        "requestId": "request-1",
        "runId": run_id,
        "captureSessionId": "session-1",
        "startEpochMs": 1_000,
        "endEpochMs": 2_000,
        "replay": {
            "requestedStart100ns": 20_000_000,
            "requestedEnd100ns": 30_000_000,
            "decodeStart100ns": 19_000_000,
            "visibleDuration100ns": 10_000_000,
            "decodePreroll100ns": 1_000_000,
            "packetCount": 60,
            "encodedBytes": len(contents),
            "reencodedFrames": 0,
            "captureClock": {
                "utcEpochMs": 1_000,
                "qpcNs": 2_000_000_000,
                "clockSource": "utc_epoch_ms+qpc+wgc_system_relative_time",
                "timebaseVersion": "time_alignment.v2",
            },
        },
        "file": {
            "size": len(contents),
            "digest": hashlib.sha256(contents).hexdigest(),
        },
    }
    receipt_path = video_path.with_name(f"{video_path.stem}.receipt.json")
    receipt_path.write_text(json.dumps(receipt, separators=(",", ":")), encoding="utf-8")
    return receipt_path


async def _seed_run_with_evidence(owner_id: str = "u1") -> dict:
    """One run with an attached Raw trace, an attached video and one leftover
    partial artifact — the full storage-bucket matrix."""
    run = await kovaak_run_store.upsert_kovaak_run(user_id=owner_id, source_key="ledger")
    run_root = config.DATA_ROOT / "runs" / str(run["id"])
    run_root.mkdir(parents=True, exist_ok=True)

    trace = run_root / "trace-ledger.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    await kovaak_run_store.begin_mouse_trace_attach(run["id"], owner_id, trace)
    await kovaak_run_store.attach_mouse_trace(
        run["id"], owner_id, str(trace), expected_pending_trace_path=trace,
    )

    video = run_root / "video-request-1.mp4"
    video.write_bytes(b"run-owned-mp4")
    _write_video_bundle(video, run_id=run["id"])
    await kovaak_run_store.begin_run_video_attach(
        run["id"], owner_id,
        pending_video_path=video,
        request_digest="a" * 64,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=config.DATA_ROOT,
    )
    await kovaak_run_store.attach_run_video(
        run["id"], owner_id, video,
        expected_pending_video_path=video,
        expected_request_digest="a" * 64,
        data_root=config.DATA_ROOT,
    )

    partial = run_root / ".video-request-1.partial-recovery.mp4"
    partial.write_bytes(b"partial-bytes")
    return run


# ---- A：存储台账 ----


@pytest.mark.asyncio
async def test_storage_ledger_written_after_attach_with_correct_buckets() -> None:
    run = await _seed_run_with_evidence()
    run_root = config.DATA_ROOT / "runs" / str(run["id"])

    usage = await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)

    assert usage["run_video_bytes"] == (
        (run_root / "video-request-1.mp4").stat().st_size
        + (run_root / "video-request-1.receipt.json").stat().st_size
    )
    assert usage["run_raw_bytes"] == (run_root / "trace-ledger.bin").stat().st_size
    assert usage["incomplete_recovery_bytes"] == len(b"partial-bytes")

    persisted = file_store.read_json(f"runs/{run['id']}/meta.json")
    ledger = persisted["storage_ledger"]
    assert ledger["schema_version"] == "run_storage_ledger.v1"
    assert ledger["video_bytes"] == usage["run_video_bytes"]
    assert ledger["raw_bytes"] == usage["run_raw_bytes"]
    assert ledger["other_bytes"] == usage["incomplete_recovery_bytes"]
    # 台账只存 stat 指纹（size + mtime_ns），不允许存全文件哈希。
    for entry in ledger["files"].values():
        assert set(entry) == {"bucket", "size", "mtime_ns"}

    # 记账后重复读取不再重扫：台账命中即服务。
    calls = {"scan": 0}
    real_scan = kovaak_run_store._scan_run_storage

    def counting_scan(run_dict, data_root):
        calls["scan"] += 1
        return real_scan(run_dict, data_root)

    original = kovaak_run_store._scan_run_storage
    kovaak_run_store._scan_run_storage = counting_scan
    try:
        warm = await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)
    finally:
        kovaak_run_store._scan_run_storage = original
    assert warm == usage
    assert calls["scan"] == 0, "a current ledger must be served without rescanning"


@pytest.mark.asyncio
async def test_legacy_run_without_ledger_self_heals_once_on_first_read() -> None:
    run = await kovaak_run_store.upsert_kovaak_run(user_id="u1", source_key="legacy")
    run_root = config.DATA_ROOT / "runs" / str(run["id"])
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "trace-old.bin").write_bytes(b"raw-bytes")
    # 模拟升级前的存量 meta：没有 storage_ledger 字段。
    legacy_meta = file_store.read_json(f"runs/{run['id']}/meta.json")
    legacy_meta.pop("storage_ledger", None)
    file_store.write_json(f"runs/{run['id']}/meta.json", legacy_meta)
    kovaak_run_store._RUN_META_CACHE.clear()
    kovaak_run_store._LEDGER_VALIDATED_DIR_MTIME.clear()

    usage = await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)

    # 台账里只有未归属文件（trace-old.bin 从未 attach）：legacy 自愈的价值
    # 在于“首读回写一次”，桶归类语义与常规路径一致。
    assert usage == {
        "run_video_bytes": 0,
        "run_raw_bytes": 0,
        "incomplete_recovery_bytes": len(b"raw-bytes"),
    }
    healed = file_store.read_json(f"runs/{run['id']}/meta.json")
    assert healed["storage_ledger"]["schema_version"] == "run_storage_ledger.v1"
    assert healed["storage_ledger"]["other_bytes"] == len(b"raw-bytes")
    # 自愈回写只发生一次：再次读取直接命中台账。
    calls = {"scan": 0}
    real_scan = kovaak_run_store._scan_run_storage

    def counting_scan(run_dict, data_root):
        calls["scan"] += 1
        return real_scan(run_dict, data_root)

    original = kovaak_run_store._scan_run_storage
    kovaak_run_store._scan_run_storage = counting_scan
    try:
        await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)
    finally:
        kovaak_run_store._scan_run_storage = original
    assert calls["scan"] == 0


@pytest.mark.asyncio
async def test_external_deletion_is_detected_and_ledger_recomputed() -> None:
    run = await _seed_run_with_evidence()
    run_root = config.DATA_ROOT / "runs" / str(run["id"])
    partial = run_root / ".video-request-1.partial-recovery.mp4"

    before = await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)
    assert before["incomplete_recovery_bytes"] == len(b"partial-bytes")

    partial.unlink()  # 外部删除：目录 mtime 变化必须触发该 run 重算。
    after = await kovaak_run_store.run_storage_usage("u1", config.DATA_ROOT)

    assert after["incomplete_recovery_bytes"] == 0
    healed = file_store.read_json(f"runs/{run['id']}/meta.json")
    assert (
        f"runs/{run['id']}/.video-request-1.partial-recovery.mp4"
        not in healed["storage_ledger"]["files"]
    )


# ---- A：incomplete 列表（stat 身份 + 跳过 meta.json + 移除仍安全）----


@pytest.mark.asyncio
async def test_incomplete_items_use_stat_identity_skip_meta_and_remove_safely() -> None:
    run = await _seed_run_with_evidence()

    items = await kovaak_run_store.list_incomplete_capture_items("u1", config.DATA_ROOT)

    assert [item["_relative_path"] for item in items] == [
        f"runs/{run['id']}/.video-request-1.partial-recovery.mp4",
    ], "attached video/receipt/raw and store-internal meta.json are never listed"
    item = items[0]
    assert item["schema_version"] == "incomplete_capture_item.v1"
    assert set(IncompleteCaptureItemOut.model_fields) <= set(item)
    assert item["item_ref"].startswith("incomplete:") and len(item["item_ref"]) == 43
    assert item["size_bytes"] == len(b"partial-bytes")
    assert item["reason"] == "interrupted_finalization"

    result = await kovaak_run_store.remove_incomplete_capture_item(
        "u1", str(item["item_ref"]), config.DATA_ROOT,
    )
    assert result["removal_state"] == "completed"
    assert result["reclaimed_bytes"] == len(b"partial-bytes")
    assert not (config.DATA_ROOT / "runs" / str(run["id"]) / ".video-request-1.partial-recovery.mp4").exists()

    again = await kovaak_run_store.remove_incomplete_capture_item(
        "u1", str(item["item_ref"]), config.DATA_ROOT,
    )
    assert again["removal_state"] == "already_unavailable"


# ---- B：meta / session 指纹缓存 ----


@pytest.mark.asyncio
async def test_run_meta_cache_revalidates_a_single_rewritten_file() -> None:
    run = await kovaak_run_store.upsert_kovaak_run(user_id="cache-user", source_key="cache")
    assert len(kovaak_run_store._all_runs("cache-user")) == 1

    calls = {"read_json": 0}
    real_read_json = file_store.read_json

    def counting_read_json(relative_path: str):
        calls["read_json"] += 1
        return real_read_json(relative_path)

    original = file_store.read_json
    file_store.read_json = counting_read_json
    try:
        assert kovaak_run_store._all_runs("cache-user")
        assert calls["read_json"] == 0, "writes pre-warm the meta cache"
    finally:
        file_store.read_json = original

    # 绕过 store 直写一份 meta（模拟外部进程 / 测试播种）→ 只重读该文件。
    meta = file_store.read_json(f"runs/{run['id']}/meta.json")
    meta["scenario"] = "externally renamed"
    file_store.write_json(f"runs/{run['id']}/meta.json", meta)

    original = file_store.read_json
    file_store.read_json = counting_read_json
    try:
        runs = kovaak_run_store._all_runs("cache-user")
    finally:
        file_store.read_json = original
    assert calls["read_json"] == 1, "exactly the rewritten meta must be re-parsed"
    assert runs[0]["scenario"] == "externally renamed"

    # 写路径（_save_run）同样要刷新 summaries/capture-status 看到的状态。
    meta["finalization_state"] = "finalized"
    file_store.write_json(f"runs/{run['id']}/meta.json", meta)
    summaries = await kovaak_run_store.list_kovaak_run_summaries("cache-user")
    assert summaries[0]["finalization_state"] == "finalized"
    attachments = await kovaak_run_store.list_kovaak_run_attachments("cache-user")
    assert attachments[0]["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_analysis_counts_cache_revalidates_a_single_rewritten_session() -> None:
    run = await kovaak_run_store.upsert_kovaak_run(user_id="counts-user", source_key="counts")
    sid = await queue.enqueue("counts-user", "v.mp4", "s.csv", kovaak_run_id=run["id"])
    counts = kovaak_run_store._analysis_counts_by_run("counts-user")
    assert counts == {run["id"]: 1}

    calls = {"read_json": 0}
    real_read_json = file_store.read_json

    def counting_read_json(relative_path: str):
        calls["read_json"] += 1
        return real_read_json(relative_path)

    original = file_store.read_json
    file_store.read_json = counting_read_json
    try:
        assert kovaak_run_store._analysis_counts_by_run("counts-user") == {run["id"]: 1}
        assert calls["read_json"] == 0, "warm scans must not re-parse sessions"
    finally:
        file_store.read_json = original

    # _rewrite_session 式外部改写：解绑 run 归属 → 只重读该文件并反映新状态。
    session = file_store.read_json(f"sessions/{sid}.json")
    session["kovaak_run_id"] = None
    file_store.write_json(f"sessions/{sid}.json", session)

    original = file_store.read_json
    file_store.read_json = counting_read_json
    try:
        counts = kovaak_run_store._analysis_counts_by_run("counts-user")
    finally:
        file_store.read_json = original
    assert calls["read_json"] == 1
    assert counts == {}


# ---- C：capture-status 与扫描解耦 ----


@pytest.mark.asyncio
async def test_capture_status_triggers_zero_full_session_parses(monkeypatch) -> None:
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE, source_key="decoupled",
    )
    await queue.enqueue(
        config.DESKTOP_LOCAL_PROFILE, "v.mp4", "s.csv", kovaak_run_id=run["id"],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(desktop_shutdown_requested=False)),
    )

    # 预热：首个请求允许建立缓存。
    first = await routes_mod.get_capture_status(request=request, _=None)
    assert first.availability == "available"

    calls = {"session_parses": 0, "meta_reads": 0}
    real_read_json = file_store.read_json

    def counting_read_json(relative_path: str):
        if relative_path.startswith("sessions/"):
            calls["session_parses"] += 1
        elif relative_path.startswith("runs/"):
            calls["meta_reads"] += 0  # meta 读走缓存；记录位仅用于可读性
        return real_read_json(relative_path)

    monkeypatch.setattr(file_store, "read_json", counting_read_json)
    second = await routes_mod.get_capture_status(request=request, _=None)

    assert second.availability == "available"
    assert calls["session_parses"] == 0, (
        "1s capture-status polls must never trigger a full session re-parse"
    )
    assert [entry.run_ref for entry in second.runs] == [f"run:{run['id']}"]


# ---- 端点合同：/api/storage/incomplete 响应形状不变 ----


@pytest.mark.asyncio
async def test_storage_incomplete_endpoint_shape_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "ledger-token")
    await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    headers = {"X-Aiming-Cookie-Desktop-Token": "ledger-token"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/storage/incomplete", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "incomplete_capture_list.v1"
    assert body["total_bytes"] == len(b"partial-bytes")
    assert len(body["items"]) == 1
    item = body["items"][0]
    # 响应只暴露公共字段；内部 _ 前缀键被剥掉，形状与改造前一致。
    assert set(item) == set(IncompleteCaptureItemOut.model_fields)
    IncompleteCaptureItemOut.model_validate(item)
    assert item["item_ref"].startswith("incomplete:")
    assert item["run_ref"].startswith("run:")
    assert item["removable"] is True
