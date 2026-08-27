"""端到端:真实 KovaaK 录像跑通完整 pipeline。

默认 skip(需 E2E_VIDEO + E2E_CSV 环境变量指向真实录像 + Stats CSV)。
点点睡醒后设环境变量跑:
    E2E_VIDEO="6月23日.mp4" E2E_CSV="stats.csv" pytest webapp/tests/test_e2e.py -v -s

验证:Worker ↔ kovaak_tracker 真实衔接(analyze_flicking_fair_summary → fair-summary dict
→ build_report(backend=) → CoachReport)。真实 LLM 需配 DeepSeek key,无 key 时
build_report best-effort(narration=None)。
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import queue, worker
from webapp.backend.app import app
from webapp.backend.contracts import ANALYSIS_RESULT_V2_SCHEMA_VERSION

VIDEO = os.environ.get("E2E_VIDEO", "")
CSV = os.environ.get("E2E_CSV", "")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (VIDEO and os.path.exists(VIDEO)) or not (CSV and os.path.exists(CSV)),
    reason="设 E2E_VIDEO + E2E_CSV 环境变量指向真实 KovaaK 录像 + Stats CSV",
)
async def test_full_pipeline_real_video():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with open(VIDEO, "rb") as fv, open(CSV, "rb") as fc:
            resp = await client.post(
                "/api/analyze",
                files={
                    "video": ("v.mp4", fv, "video/mp4"),
                    "csv": ("s.csv", fc, "text/csv"),
                },
                headers={"X-User-Id": "e2e"},
            )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    handled = await worker.process_one()
    assert handled is True

    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert isinstance(s["result"], dict)
    assert s["result"]["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    # 视频保留——coach 页 /api/sessions/{id}/video 流式播放依赖此文件。
    # worker 成功路径不删(仅失败路径删),与 worker.process_one 现行行为一致。
    assert os.path.exists(s["video_path"])

    # ---- 金标准数字锚定 -----------------------------------------------------
    # 这一局(data/6月23日.mp4 + 1wall 6targets small Stats.csv)的真值来自
    # CSV 权威口径:Kills=114、Hit Count=114、Miss Count=7(见 08-2x 人肉重算
    # 校准记录)。视频链路在这一局识别出 73 次甩枪。
    # 锚定的意义:单测喂的是手工编的假数据,算法在真实录像上的回归(例如
    # 08-2x 那次 degraded 回退点污染导致命中率虚高 96.8%)单测照不到——只有
    # 用真录像把关键数字钉死,这类回归才会在提交前变红而不是发版后才炸。
    deterministic = s["result"].get("deterministic") or {}
    metrics = deterministic.get("metrics") or {}

    # 击杀数:timeline 里的 kill 事件数必须等于 CSV kills 表行数 114
    # (点击计数以 KVK stats 为权威口径的拍板)。这是防"击杀对不上"回归的锚。
    timeline = deterministic.get("timeline") or []
    kill_count = sum(1 for event in timeline if event.get("type") == "kill")
    assert kill_count == 114, (
        f"timeline kill 事件数={kill_count},金标准=114(CSV kills 行数),"
        "击杀计数或时间对齐疑似回归"
    )

    # 甩枪识别数:允许算法微调带来的 ±10% 浮动,但必须不离谱(防虚高/全丢)。
    flick_metric = metrics.get("flick_count") or {}
    flick_count = flick_metric.get("value")
    assert flick_count is not None, "flick_count 必须在真实录像上可用"
    assert 60 <= flick_count <= 85, (
        f"flick_count={flick_count} 偏离金标准 73 超过 ±10%,疑似算法回归"
    )