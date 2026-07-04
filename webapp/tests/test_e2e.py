"""端到端:真实 KovaaK 录像跑通完整 pipeline。

默认 skip(需 E2E_VIDEO + E2E_CSV 环境变量指向真实录像 + Stats CSV)。
点点睡醒后设环境变量跑:
    E2E_VIDEO="6月23日.mp4" E2E_CSV="stats.csv" pytest webapp/tests/test_e2e.py -v -s

验证:Worker ↔ kovaak_tracker 真实衔接(analyze_flicking_video → FlickAnalysis.summary
→ build_report(backend=) → CoachReport)。真实 LLM 需配 DeepSeek key,无 key 时
build_report best-effort(narration=None)。
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import queue, worker
from webapp.backend.app import app

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
    # 视频源文件已删(隐私)
    assert not os.path.exists(s["video_path"])
