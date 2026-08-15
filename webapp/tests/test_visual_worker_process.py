from __future__ import annotations

import asyncio
import json

import pytest

from kovaak_tracker.visual_signals import VisualPreprocessingUnavailable
from webapp.backend import visual_worker_process, worker


class _FakeProcess:
    def __init__(self, response: dict, *, block: bool = False) -> None:
        self._response = response
        self._block = block
        self._release = asyncio.Event()
        self.request: bytes | None = None
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    async def communicate(self, request: bytes) -> tuple[bytes, bytes]:
        self.request = request
        if self._block:
            await self._release.wait()
        self.returncode = 0
        return json.dumps(self._response).encode("utf-8"), b""

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        self._release.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -1
        self._release.set()

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return self.returncode or 0


def test_child_environment_removes_tokens_secrets_and_capture_endpoint() -> None:
    environment = visual_worker_process.build_child_environment({
        "PATH": "python-path",
        "DATA_ROOT": "data-root",
        "AIMING_COOKIE_DESKTOP_TOKEN": "desktop-secret",
        "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_ADDR": "127.0.0.1:1234",
        "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET": "capture-secret",
        "OPENAI_API_KEY": "provider-secret",
        "AWS_ACCESS_KEY_ID": "access-secret",
        "OTHER_PASSWORD": "password-secret",
    })

    assert environment == {"PATH": "python-path", "DATA_ROOT": "data-root"}


@pytest.mark.parametrize(
    ("logical_cpu_count", "expected_threads"),
    [(8, 8), (16, 16), (32, 16)],
)
def test_child_configures_opencv_threads_without_over_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    logical_cpu_count: int,
    expected_threads: int,
) -> None:
    calls: list[int] = []

    class FakeCV2:
        @staticmethod
        def setNumThreads(limit: int) -> None:
            calls.append(limit)

    monkeypatch.setitem(__import__("sys").modules, "cv2", FakeCV2)
    monkeypatch.setattr(
        visual_worker_process.os,
        "cpu_count",
        lambda: logical_cpu_count,
    )

    visual_worker_process._configure_opencv_threads()

    assert calls == [expected_threads]


def test_child_configures_opencv_immediately_before_visual_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def configure_opencv_threads() -> None:
        calls.append("configure")

    def parse_stats(_snapshot: dict) -> object:
        calls.append("parse")
        return object()

    def preprocess(_job: dict, *, parsed_stats: object) -> dict:
        assert parsed_stats is not None
        calls.append("preprocess")
        return {"safe_summary": {"status": "available"}}

    monkeypatch.setattr(
        visual_worker_process,
        "_configure_opencv_threads",
        configure_opencv_threads,
    )
    monkeypatch.setattr(worker, "_parse_frozen_stats_for_visual", parse_stats)
    monkeypatch.setattr(worker, "run_visual_preprocessing", preprocess)

    assert visual_worker_process._run_visual_job({"input_snapshot": {}}) == {
        "safe_summary": {"status": "available"},
    }
    assert calls == ["parse", "configure", "preprocess"]


def test_evidence_only_commit_does_not_configure_opencv_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def configure_opencv_threads() -> None:
        calls.append(8)

    monkeypatch.setattr(
        visual_worker_process,
        "_configure_opencv_threads",
        configure_opencv_threads,
        raising=False,
    )

    result = visual_worker_process.execute_request(
        {
            "operation": "commit_continuous_tracking_evidence",
            "job": {"id": 7, "user_id": "u1", "input_snapshot": {}},
            "result": {"analysis_version": "continuous_tracking.v1"},
            "visual_result": {"safe_summary": {"status": "available"}},
            "tracking_result": {"analysis_version": "continuous_tracking.v1"},
        },
        evidence_runner=lambda _job, result, _visual, _tracking: result,
    )

    assert result == {
        "ok": True,
        "result": {"analysis_version": "continuous_tracking.v1"},
    }
    assert calls == []


def test_child_protocol_preserves_result_and_safe_failure_kinds() -> None:
    result = visual_worker_process.execute_request(
        {"job": {"id": 7}},
        runner=lambda job: {"analysis_ref": f"analysis:{job['id']}"},
    )
    assert result == {"ok": True, "result": {"analysis_ref": "analysis:7"}}

    source = visual_worker_process.execute_request(
        {"job": {"id": 7}},
        runner=lambda _job: (_ for _ in ()).throw(
            worker.SourceSnapshotChangedError("source_unavailable: video revision changed")
        ),
    )
    assert source == {
        "ok": False,
        "error": {
            "kind": "source_snapshot_changed",
            "code": "source_unavailable: video revision changed",
        },
    }

    unavailable = visual_worker_process.execute_request(
        {"job": {"id": 7}},
        runner=lambda _job: (_ for _ in ()).throw(
            VisualPreprocessingUnavailable("visual_quality_profile_unavailable")
        ),
    )
    assert unavailable == {
        "ok": False,
        "error": {
            "kind": "visual_preprocessing_unavailable",
            "code": "visual_quality_profile_unavailable",
        },
    }

    unknown = visual_worker_process.execute_request(
        {"job": {"id": 7}},
        runner=lambda _job: (_ for _ in ()).throw(
            RuntimeError("C:/private/video.mp4 must never cross the boundary")
        ),
    )
    assert unknown == {
        "ok": False,
        "error": {"kind": "visual_preprocessing_failed", "code": "runtime_error"},
    }


def test_child_protocol_can_finish_tracking_in_the_same_process() -> None:
    result = visual_worker_process.execute_request(
        {"job": {"id": 7}, "postprocess": "continuous_tracking"},
        runner=lambda job: {"analysis_ref": f"analysis:{job['id']}"},
        continuous_tracking_runner=lambda job, visual: {
            "analysis_ref": visual["analysis_ref"],
            "job_id": job["id"],
        },
    )

    assert result == {
        "ok": True,
        "result": {
            "visual_result": {"analysis_ref": "analysis:7"},
            "family_result": {"analysis_ref": "analysis:7", "job_id": 7},
        },
    }


def test_child_protocol_runs_target_switching_episodes_on_serialized_frames(
    monkeypatch,
) -> None:
    visual_result = {
        "analysis_ref": "analysis:7",
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["target_switching"],
        },
        "frame_observations": [
            {
                "source_pts_ms": 0.0,
                "targets": [
                    {"x": 80.0, "y": 100.0, "visible_radius": 10.0, "confidence": 1.0},
                    {"x": 130.0, "y": 100.0, "visible_radius": 12.0, "confidence": 1.0},
                ],
                "target_ambiguities": [],
                "scene": "gameplay",
            },
            {
                "source_pts_ms": 16.0,
                "targets": [
                    {"x": 128.0, "y": 100.0, "visible_radius": 12.0, "confidence": 1.0},
                    {"x": 82.0, "y": 100.0, "visible_radius": 10.0, "confidence": 1.0},
                ],
                "target_ambiguities": [],
                "scene": "gameplay",
            },
        ],
    }

    projected = {"analysis_ref": "analysis:7", "projected": True}
    monkeypatch.setattr(
        "kovaak_tracker.visual_signals.project_visual_target_episodes_v1",
        lambda source, episodes: projected,
    )

    result = visual_worker_process.execute_request(
        {"job": {"id": 7}, "postprocess": "target_switching"},
        runner=lambda _job: visual_result,
    )

    assert result["ok"] is True
    assert result["result"]["visual_result"] is projected
    family = result["result"]["family_result"]
    assert family["schema_version"] == "visual_target_episode_artifact.v1"
    assert family["status"] == "available"
    assert [episode["episode_ref"] for episode in family["episodes"]] == [
        "analysis:7:target-episode:1",
        "analysis:7:target-episode:2",
    ]


@pytest.mark.parametrize(
    "visual_result",
    [
        {"analysis_ref": "analysis:7"},
        {
            "analysis_ref": "analysis:7",
            "quality": {
                "status": "accepted",
                "enabled_metric_families": ["tracking"],
            },
            "frame_observations": [],
        },
    ],
)
def test_target_switching_postprocess_fails_closed_without_valid_identity_input(
    visual_result: dict,
) -> None:
    result = visual_worker_process.execute_request(
        {"job": {"id": 7}, "postprocess": "target_switching"},
        runner=lambda _job: visual_result,
    )

    assert result == {
        "ok": False,
        "error": {"kind": "family_analysis_failed", "code": "value_error"},
        "visual_result": visual_result,
    }


def test_child_rejects_unknown_postprocess() -> None:
    assert visual_worker_process.execute_request(
        {"job": {"id": 7}, "postprocess": "target_switching_v2"},
    ) == {
        "ok": False,
        "error": {
            "kind": "visual_preprocessing_failed",
            "code": "invalid_request",
        },
    }


def test_child_protocol_can_commit_tracking_evidence() -> None:
    result = visual_worker_process.execute_request(
        {
            "operation": "commit_continuous_tracking_evidence",
            "job": {"id": 7, "user_id": "u1", "input_snapshot": {}},
            "result": {"analysis_version": "continuous_tracking.v1"},
            "visual_result": {"safe_summary": {"status": "available"}},
            "tracking_result": {"analysis_version": "continuous_tracking.v1"},
        },
        evidence_runner=lambda job, analysis, visual, tracking: {
            **analysis,
            "committed_for": job["user_id"],
            "visual_status": visual["safe_summary"]["status"],
            "tracking_version": tracking["analysis_version"],
        },
    )

    assert result == {
        "ok": True,
        "result": {
            "analysis_version": "continuous_tracking.v1",
            "committed_for": "u1",
            "visual_status": "available",
            "tracking_version": "continuous_tracking.v1",
        },
    }


@pytest.mark.asyncio
async def test_parent_round_trip_is_async_and_keeps_the_job_bounded(monkeypatch) -> None:
    process = _FakeProcess({"ok": True, "result": {"safe_summary": {"status": "available"}}})
    observed: dict = {}

    async def create_subprocess(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return process

    monkeypatch.setenv("AIMING_COOKIE_DESKTOP_TOKEN", "desktop-secret")
    monkeypatch.setenv("AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET", "capture-secret")
    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", create_subprocess)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(3):
            await asyncio.sleep(0)
            ticks += 1

    result, _ = await asyncio.gather(
        worker.run_visual_preprocessing_isolated({
            "id": 7,
            "input_snapshot": {},
            "desktop_token": "must-not-cross",
        }),
        ticker(),
    )

    assert result == {"safe_summary": {"status": "available"}}
    assert ticks == 3
    assert observed["args"][1:] == ("-m", "webapp.backend.visual_worker_process")
    assert "AIMING_COOKIE_DESKTOP_TOKEN" not in observed["kwargs"]["env"]
    assert "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET" not in observed["kwargs"]["env"]
    assert json.loads(process.request) == {"job": {"id": 7, "input_snapshot": {}}}


@pytest.mark.asyncio
async def test_parent_requests_tracking_postprocess_without_expanding_job(monkeypatch) -> None:
    process = _FakeProcess({
        "ok": True,
        "result": {
            "visual_result": {"safe_summary": {"status": "available"}},
            "family_result": {"analysis_version": "continuous_tracking.v1"},
        },
    })

    async def create_subprocess(*_args, **_kwargs):
        return process

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", create_subprocess)
    visual, tracking = await worker.run_continuous_tracking_pipeline_isolated({
        "id": 7,
        "input_snapshot": {},
        "desktop_token": "must-not-cross",
    })

    assert visual == {"safe_summary": {"status": "available"}}
    assert tracking == {"analysis_version": "continuous_tracking.v1"}
    assert json.loads(process.request) == {
        "job": {"id": 7, "input_snapshot": {}},
        "postprocess": "continuous_tracking",
    }


@pytest.mark.asyncio
async def test_parent_commits_tracking_evidence_with_a_bounded_job(monkeypatch) -> None:
    process = _FakeProcess({
        "ok": True,
        "result": {"analysis_version": "continuous_tracking.v1", "committed": True},
    })

    async def create_subprocess(*_args, **_kwargs):
        return process

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", create_subprocess)
    result = await worker.commit_continuous_tracking_evidence_isolated(
        {
            "id": 7,
            "user_id": "u1",
            "input_snapshot": {},
            "video_path": "C:/private/video.mp4",
            "desktop_token": "must-not-cross",
        },
        {"analysis_version": "continuous_tracking.v1"},
        {"safe_summary": {"status": "available"}},
        {"analysis_version": "continuous_tracking.v1"},
    )

    assert result == {"analysis_version": "continuous_tracking.v1", "committed": True}
    assert json.loads(process.request) == {
        "operation": "commit_continuous_tracking_evidence",
        "job": {"id": 7, "user_id": "u1", "input_snapshot": {}},
        "result": {"analysis_version": "continuous_tracking.v1"},
        "visual_result": {"safe_summary": {"status": "available"}},
        "tracking_result": {"analysis_version": "continuous_tracking.v1"},
    }


@pytest.mark.asyncio
async def test_parent_restores_typed_errors_and_terminates_cancelled_child(monkeypatch) -> None:
    source_process = _FakeProcess({
        "ok": False,
        "error": {
            "kind": "source_snapshot_changed",
            "code": "source_unavailable: video revision changed",
        },
    })

    async def source_subprocess(*_args, **_kwargs):
        return source_process

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", source_subprocess)
    with pytest.raises(worker.SourceSnapshotChangedError, match="video revision changed"):
        await worker.run_visual_preprocessing_isolated({"id": 8})

    blocked = _FakeProcess({"ok": True, "result": {}}, block=True)

    async def blocked_subprocess(*_args, **_kwargs):
        return blocked

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", blocked_subprocess)
    task = asyncio.create_task(worker.run_visual_preprocessing_isolated({"id": 9}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert blocked.terminated is True
    assert blocked.killed is False


@pytest.mark.asyncio
async def test_parent_fails_bounded_and_kills_a_hung_visual_child(monkeypatch) -> None:
    # The consume loop is serial, so a hung CV child must hit a timeout,
    # be terminated, and surface as a bounded error — not block forever.
    hung = _FakeProcess({"ok": True, "result": {}}, block=True)

    async def hung_subprocess(*_args, **_kwargs):
        return hung

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", hung_subprocess)
    monkeypatch.setattr(worker, "VISUAL_WORKER_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(RuntimeError, match="visual_preprocessing_timeout"):
        await worker.run_visual_preprocessing_isolated({"id": 10})

    assert hung.terminated is True
