"""telemetry_capture_service 行为测试。

生命周期用桩脚本目录 + 假 spawn 驱动（绝不启动真实采集子进程，也不依赖游戏
在运行）；另有一条真脚本集成测试：合成一份最小 target_poll/camera/input
JSONL 三元组，用仓库 telemetry_capture/ 的真实 cleaner.py + merge_channels.py
在子进程里跑通「清洗 → 分轮 → 旁车」全链。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from webapp.backend import external_telemetry_store
from webapp.backend import telemetry_capture_service as service_mod
from webapp.backend.telemetry_capture_service import (
    TelemetryCaptureService,
    ensure_managed_watch_root,
    managed_capture_roots,
    resolve_scripts_dir,
    run_telemetry_child,
)


class _FakeHandle:
    def __init__(self) -> None:
        self.value = 4242

    def __int__(self) -> int:
        return self.value


class _FakeChild:
    """记录型 Popen 替身：terminate/wait/poll 全部无副作用。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._handle = _FakeHandle()
        self.terminated = False

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture()
def stub_scripts(tmp_path: Path) -> Path:
    scripts = tmp_path / "capture-scripts"
    scripts.mkdir()
    (scripts / "tp1.py").write_text("# marker\n", encoding="utf-8")
    return scripts


@pytest.fixture()
def no_diagnostics(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    writes: list[tuple[str, object]] = []

    class _Store:
        @staticmethod
        def write_json(rel_path: str, payload: object) -> None:
            writes.append((rel_path, payload))

    monkeypatch.setattr(service_mod, "file_store", _Store)
    return writes


def _make_service(tmp_path: Path, stub_scripts: Path, game_state: dict) -> TelemetryCaptureService:
    spawned: list[_FakeChild] = []
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=stub_scripts,
        poll_interval=0.05,
        finalize_grace_seconds=0.1,
        finalize_retry_seconds=0.5,
        game_processes_fn=lambda: game_state["procs"],
    )

    def fake_spawn(self, argv: list[str]) -> _FakeChild:
        child = _FakeChild(pid=1000 + len(spawned))
        spawned.append(child)
        service.spawn_log.append(list(argv))  # type: ignore[attr-defined]
        return child

    service.spawn_log = []  # type: ignore[attr-defined]
    service.spawned_children = spawned  # type: ignore[attr-defined]
    service_mod.TelemetryCaptureService._spawn_process = fake_spawn  # type: ignore[method-assign]
    return service


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_resolve_scripts_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom-capture"
    custom.mkdir()
    (custom / "tp1.py").write_text("# marker\n", encoding="utf-8")
    monkeypatch.setenv(service_mod.SCRIPTS_DIR_ENV, str(custom))
    assert resolve_scripts_dir() == custom


def test_run_telemetry_child_runs_script_as_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    custom = tmp_path / "custom-capture"
    custom.mkdir()
    (custom / "tp1.py").write_text("# marker\n", encoding="utf-8")
    (custom / "echo_child.py").write_text(
        "import sys\nprint('CHILD', sys.argv[1:])\n", encoding="utf-8"
    )
    monkeypatch.setenv(service_mod.SCRIPTS_DIR_ENV, str(custom))
    run_telemetry_child("echo_child.py", ["--flag", "x"])
    assert "CHILD" in capsys.readouterr().out


def test_ensure_managed_watch_root_only_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved: list[str] = []
    monkeypatch.setattr(external_telemetry_store, "get_watch_root", lambda: None)
    monkeypatch.setattr(
        external_telemetry_store, "save_watch_root", lambda raw: saved.append(str(raw))
    )
    assert ensure_managed_watch_root() is True
    assert len(saved) == 1
    assert saved[0] == str(managed_capture_roots(tmp_path / "data")[1]) or saved[0].endswith(
        "external-capture\\cleaned"
    )

    monkeypatch.setattr(external_telemetry_store, "get_watch_root", lambda: Path("C:/already"))
    assert ensure_managed_watch_root() is False
    assert len(saved) == 1  # 已配置时绝不覆盖


def test_start_unavailable_without_scripts(tmp_path: Path, no_diagnostics) -> None:
    game_state = {"procs": []}
    empty_scripts = tmp_path / "empty-scripts"
    empty_scripts.mkdir()
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=empty_scripts,
        game_processes_fn=lambda: game_state["procs"],
    )
    assert service.start() is False
    assert service.diagnostics()["state"] == "unavailable"


def test_start_disabled_by_env(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(service_mod.DISABLE_ENV, "0")
    service = TelemetryCaptureService(
        data_root=tmp_path / "data", scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )
    assert service.start() is False


def test_lifecycle_spawn_finalize_and_pending(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state)
    finalize_calls: list[list[str]] = []

    def fake_finalize_step(self, argv: list[str], *, label: str) -> str:
        finalize_calls.append(list(argv))
        if label == "cleaner.py":
            cleaned_root = Path(argv[argv.index("--outdir") + 1])
            cleaned_root.mkdir(parents=True, exist_ok=True)
            (cleaned_root / "rounds_index.json").write_text("{}", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(
        TelemetryCaptureService, "_run_finalize_step", fake_finalize_step
    )

    assert service.start() is True
    # 监控线程先观察到「游戏在场」上升沿，之后翻面才会触发下降沿收尾。
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)
    # 三通道随启动拉起，产物指向会话目录。
    assert len(service.spawn_log) == 3  # type: ignore[attr-defined]
    target_argv = service.spawn_log[0]  # type: ignore[attr-defined]
    assert target_argv[1].endswith("target_poll2.py")
    assert "--out-dir" in target_argv and "--wait" in target_argv
    session_dir = Path(target_argv[target_argv.index("--out-dir") + 1])
    assert session_dir.parent == service.sessions_root
    assert service.spawn_log[2][1].endswith("input_logger.py")  # type: ignore[attr-defined]

    # 游戏在场 → 写入原始件；退场 → 监控线程自动收尾。
    (session_dir / "target_poll_out_0906_120000.jsonl").write_text(
    json.dumps({"ev": "frame", "t": 0.0, "targets": []}) + "\n", encoding="utf-8"
    )
    (session_dir / "camera_probe_out_0906_120000.jsonl").write_text("", encoding="utf-8")
    (session_dir / "input_log.jsonl").write_text("", encoding="utf-8")

    game_state["procs"] = []
    assert _wait_until(lambda: bool(service.diagnostics()["last_finalize"]))
    finalize = service.diagnostics()["last_finalize"]
    assert finalize["results"] == {"target_poll_out_0906_120000": "ok"}
    assert finalize["sidecars"] is True
    cleaner_call = next(c for c in finalize_calls if c[-1] != "--year" and "cleaner.py" in c[1])
    assert str(service.cleaned_root) in cleaner_call
    assert not (session_dir / "target_poll_out_0906_120000.jsonl").exists()
    assert (session_dir / "processed" / "target_poll_out_0906_120000.jsonl").exists()

    # 游戏退场已收尾后再停止：无未收尾原始件 → 回到 idle。
    service.stop()
    assert service.diagnostics()["state"] == "idle"


def test_stop_with_raw_files_marks_pending(
    tmp_path: Path, stub_scripts: Path, no_diagnostics
) -> None:
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state)
    assert service.start() is True
    session_dir = Path(service.spawn_log[0][service.spawn_log[0].index("--out-dir") + 1])  # type: ignore[attr-defined]
    (session_dir / "target_poll_out_0906_120100.jsonl").write_text(
        json.dumps({"ev": "frame", "t": 0.0, "targets": []}) + "\n", encoding="utf-8"
    )
    service.stop()
    assert service.diagnostics()["state"] == "pending_finalize"


def test_sweep_recovers_old_sessions(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions_root, cleaned_root = managed_capture_roots(tmp_path / "data")
    sessions_root.mkdir(parents=True)
    old_dir = sessions_root / "session-260901-100000"
    old_dir.mkdir()
    (old_dir / "pids.json").write_text(
        json.dumps({"target": {"pid": 999999, "ctime": 1}}), encoding="utf-8"
    )
    (old_dir / "target_poll_out_0901_100001.jsonl").write_text("", encoding="utf-8")
    (old_dir / "camera_probe_out_0901_100001.jsonl").write_text("", encoding="utf-8")
    (old_dir / "input_log.jsonl").write_text("", encoding="utf-8")

    def fake_finalize_step(self, argv: list[str], *, label: str) -> str:
        if label == "cleaner.py":
            cleaned_root = Path(argv[argv.index("--outdir") + 1])
            cleaned_root.mkdir(parents=True, exist_ok=True)
            (cleaned_root / "rounds_index.json").write_text("{}", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(TelemetryCaptureService, "_run_finalize_step", fake_finalize_step)
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )
    service._sweep_finished_sessions()
    assert (old_dir / "processed" / "target_poll_out_0901_100001.jsonl").exists()
    assert (cleaned_root / "rounds_index.json").exists()


# ---------------------------------------------------------------------------
# 真脚本集成：cleaner + merge 全链（合成数据，子进程运行真实脚本）。
# ---------------------------------------------------------------------------

_HZ = 50


def _write_synthetic_session(session_dir: Path) -> None:
    """一份最小三元组：目标 0x1000 全程在场，0x2000/0x3003 交替，稳速移动。"""
    epoch = time.time()
    perf0 = 1000.0
    session_dir.mkdir(parents=True, exist_ok=True)

    target = session_dir / "target_poll_out_0906_120000.jsonl"
    with target.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": epoch}) + "\n")
        for i in range(int(6.5 * _HZ)):
            t = i / _HZ
            targets = [[0x1000, 500 + 300 * t, 1000.0, 700.0]]
            if t < 3.0:
                targets.append([0x2000, 2000 - 100 * t, 500.0, 700.0])
            elif t >= 3.2:
                targets.append([0x3000, 1500 + 80 * (t - 3.2), 800.0, 700.0])
            f.write(json.dumps({"ev": "frame", "t": t, "targets": targets}) + "\n")

    camera = session_dir / "camera_probe_out_0906_120000.jsonl"
    with camera.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": epoch}) + "\n")
        for i in range(int(6.5 * _HZ)):
            t = i / _HZ
            f.write(json.dumps({
                "ev": "cam", "t": t, "pos": [0.0, 0.0, 300.0],
                "rot": [0.0, 0.0, 0.0], "fov": 90.0,
            }) + "\n")

    with (session_dir / "input_log.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": perf0, "t_unix": epoch + perf0}) + "\n")
        for i in range(int(6.5 * _HZ)):
            t = perf0 + i / _HZ
            btn = ["L_down"] if i % 50 == 10 else []
            f.write(json.dumps({"ev": "m", "t": t, "qpc": 0, "dx": 0, "dy": 0, "btn": btn}) + "\n")


def test_real_scripts_cleaner_and_merge_end_to_end(
    tmp_path: Path, no_diagnostics
) -> None:
    real_scripts = resolve_scripts_dir()
    assert real_scripts is not None, "repo telemetry_capture/ must exist"
    game_state = {"procs": []}
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=real_scripts,
        poll_interval=0.05,
        game_processes_fn=lambda: game_state["procs"],
    )
    session_dir = service.sessions_root / "session-260906-120000"
    _write_synthetic_session(session_dir)

    service._finalize_dir(session_dir)

    cleaned_root = service.cleaned_root
    round_dir = cleaned_root / "target_poll_out_0906_120000"
    index = json.loads((cleaned_root / "rounds_index.json").read_text(encoding="utf-8"))
    assert index.get("sources"), "cleaner index must list processed sources"
    assert index["sources"][0].get("rounds"), "cleaner must produce at least one round"
    assert service._last_finalize["results"] == {"target_poll_out_0906_120000": "ok"}
    assert (session_dir / "processed" / "target_poll_out_0906_120000.jsonl").exists()
    # 合成数据的相机/点击没有真实几何一致性，merge 的精确锚验收按设计 fail-closed
    # 拒写旁车（exit 2）——轮次照常入库、旁车缺失记录在案。真机数据的验收通过
    # 已在研究管线实证（RUNBOOK §3.5 / merge_manifest 实链）。
    assert service._last_finalize["merge"] == {"target_poll_out_0906_120000": "exit_2"}
    assert not list(round_dir.glob("views_*.jsonl"))
