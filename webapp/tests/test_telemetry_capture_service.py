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
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.terminated = True
        self.returncode = 1

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


def _make_service(
    tmp_path: Path, stub_scripts: Path, game_state: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> TelemetryCaptureService:
    spawned: list[_FakeChild] = []
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=stub_scripts,
        poll_interval=0.05,
        finalize_grace_seconds=0.1,
        finalize_retry_seconds=0.5,
        game_processes_fn=lambda: game_state["procs"],
    )

    def fake_spawn(
        self, argv: list[str], session_dir: Path, role: str,
    ) -> _FakeChild:
        child = _FakeChild(pid=1000 + len(spawned))
        spawned.append(child)
        service.spawn_log.append(list(argv))  # type: ignore[attr-defined]
        return child

    service.spawn_log = []  # type: ignore[attr-defined]
    service.spawned_children = spawned  # type: ignore[attr-defined]
    # monkeypatch 而非裸类赋值：真实 spawn 测试与本桩共用同一进程，裸赋值会
    # 把假实现泄漏给后续测试（[fix 2026-09-30] 新增的真实 _spawn_process 测试踩中）。
    monkeypatch.setattr(TelemetryCaptureService, "_spawn_process", fake_spawn)
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


def test_ensure_managed_watch_root_repoints_stale_managed_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """1.3.0 存储迁移后 watch 根指向旧托管路径且已不存在时，重指当前托管根。

    自定义路径（无论存在与否）永不触碰；托管长相但当前托管根也没建时不重指
    （避免把 fresh 安装误改写）。
    """
    saved: list[str] = []
    monkeypatch.setattr(
        external_telemetry_store, "save_watch_root", lambda raw: saved.append(str(raw))
    )
    stale = tmp_path / "old-root" / "external-capture" / "cleaned"  # 托管长相但不存在
    current = tmp_path / "data" / "external-capture" / "cleaned"
    current.mkdir(parents=True)
    # 隔离 DATA_ROOT：ensure 内部取的是 config.DATA_ROOT 下的托管根。
    monkeypatch.setattr(
        service_mod, "managed_capture_roots",
        lambda data_root=None: (tmp_path / "data" / "external-capture" / "sessions", current),
    )

    monkeypatch.setattr(external_telemetry_store, "get_watch_root", lambda: stale)
    assert ensure_managed_watch_root() is True
    assert saved == [str(current)]

    # 自定义路径（非托管长相）缺失也不动。
    saved.clear()
    custom_missing = tmp_path / "my-custom-telemetry"
    monkeypatch.setattr(external_telemetry_store, "get_watch_root", lambda: custom_missing)
    assert ensure_managed_watch_root() is False
    assert saved == []


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
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
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


def test_monitor_loop_reports_dead_child_process(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """采集子进程静默退出必须有可见性：log.error + _last_error + 落盘诊断。

    子进程崩溃的现场在 {role}.log；本轮询检查只做可见性，不自动重启。
    """
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)

    # target_channel 崩溃退出（非零 rc），其余仍在运行。
    service.spawned_children[0].returncode = 3  # type: ignore[attr-defined]
    assert _wait_until(
        lambda: "child_exited" in str(service.diagnostics()["last_error"] or "")
    )
    last_error = service.diagnostics()["last_error"]
    assert "target" in str(last_error) and "3" in str(last_error)
    # 诊断确实落盘（no_diagnostics 记录 file_store.write_json 调用）。
    assert any("telemetry" in rel or "capture" in rel for rel, _ in no_diagnostics)
    # 无日志文件时 child_log_tail 保持 None（可选字段不硬造空值）。
    assert service.diagnostics()["child_log_tail"] is None

    service.stop()


# --------------------------------------------------------------- [fix 2026-09-30]

def test_spawn_process_merges_child_output_into_role_log(
    tmp_path: Path, stub_scripts: Path,
) -> None:
    """子进程 stdout/stderr 同一句柄合并落会话目录 {role}.log，且恒 utf-8。

    中文 Windows 子进程默认 cp936，若无 PYTHONIOENCODING=utf-8，中文输出会以
    GBK 字节落进按 utf-8 打开的日志文件（乱码/解不开）。
    """
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    service = TelemetryCaptureService(
        data_root=tmp_path / "data", scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )
    argv = [sys.executable, "-c",
            "import sys; print('OUT-遥测'); "
            "sys.stderr.write('ERR-遥测\\n'); sys.exit(0)"]
    child = service._spawn_process(argv, session_dir, "target")
    assert child.wait(timeout=30) == 0
    logged = (session_dir / "target.log").read_text(encoding="utf-8")
    assert "OUT-遥测" in logged and "ERR-遥测" in logged


def test_spawn_process_role_log_open_failure_falls_back_to_devnull(
    tmp_path: Path, stub_scripts: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """{role}.log 打不开（此处：该名字被目录占用）→ 降级 DEVNULL + warning。

    真实场景是用户数据目录在移动盘且已拔出；拉起本身绝不能因此失败。
    """
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "camera.log").mkdir()  # open("a") 会 PermissionError
    service = TelemetryCaptureService(
        data_root=tmp_path / "data", scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )
    with caplog.at_level("WARNING", logger=service_mod.log.name):
        child = service._spawn_process(
            [sys.executable, "-c", "print('ok')"], session_dir, "camera",
        )
    # 拉起成功（子进程正常跑完），日志降级只留 warning。
    assert child.wait(timeout=30) == 0
    assert "camera" in caplog.text and "DEVNULL" in caplog.text
    assert (session_dir / "camera.log").is_dir()  # 目录原样，未被改写


def test_dead_child_reports_log_tail_truncated(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子进程死亡 → {role}.log 尾部（≤8000 字符）入 diagnostics 的 child_log_tail。"""
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)
    session_dir = Path(
        service.spawn_log[0][service.spawn_log[0].index("--out-dir") + 1]  # type: ignore[attr-defined]
    )
    (session_dir / "target.log").write_text(
        "x" * 20000 + "TAIL-MARKER-靶点", encoding="utf-8",
    )
    (session_dir / "camera.log").write_text("CAM-TAIL", encoding="utf-8")

    service.spawned_children[0].returncode = 1  # type: ignore[attr-defined]
    service.spawned_children[1].returncode = 1  # type: ignore[attr-defined]
    assert _wait_until(lambda: bool(service.diagnostics()["child_log_tail"]))
    tails = service.diagnostics()["child_log_tail"]
    assert isinstance(tails, dict)
    # 截断到尾部 8000 字符（对齐 finalize.log 先例），且保住末尾标记。
    assert len(tails["target"]) == 8000
    assert tails["target"].endswith("TAIL-MARKER-靶点")
    assert tails["camera"] == "CAM-TAIL"
    # 存活的 input 不出现在尾部字典里。
    assert set(tails) == {"target", "camera"}

    service.stop()


def test_stop_with_raw_files_marks_pending(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
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


# ------------------------------------------------------------------ 按局增量切窗


def _write_long_synthetic_session(session_dir: Path, *, duration_s: float = 40.0) -> float:
    """两轮结构的加长三元组：0x1000 生于 t=0 亡于 t=15；0x2000 生于 t=20。

    出生间隔 20s > BIRTH_GAP(10s) → cleaner 必切两轮；用于按局切窗测试。
    """
    epoch = time.time()
    session_dir.mkdir(parents=True, exist_ok=True)

    target = session_dir / "target_poll_out_0906_120000.jsonl"
    with target.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": epoch}) + "\n")
        for i in range(int(duration_s * _HZ)):
            t = i / _HZ
            targets = []
            if t <= 15.0:
                targets.append([0x1000, 500 + 300 * t, 1000.0, 700.0])
            if t >= 20.0:
                targets.append([0x2000, 1500 + 80 * (t - 20.0), 800.0, 700.0])
            if not targets:
                # 全灭间隙：cleaner 据此分轮，帧流不能断
                targets.append([0x9000, 0.0, 0.0, 0.0])
            f.write(json.dumps({"ev": "frame", "t": t, "targets": targets}) + "\n")

    camera = session_dir / "camera_probe_out_0906_120000.jsonl"
    with camera.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": epoch}) + "\n")
        for i in range(int(duration_s * _HZ)):
            f.write(json.dumps({
                "ev": "cam", "t": i / _HZ, "pos": [0.0, 0.0, 300.0],
                "rot": [0.0, 0.0, 0.0], "fov": 90.0,
            }) + "\n")

    perf0 = 1000.0
    with (session_dir / "input_log.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": perf0, "t_unix": epoch + perf0}) + "\n")
        for i in range(int(duration_s * _HZ)):
            f.write(json.dumps({
                "ev": "m", "t": perf0 + i / _HZ, "qpc": 0, "dx": 0, "dy": 0,
                "btn": ["L_down"] if i % 50 == 10 else [],
            }) + "\n")
    return epoch


def test_freeze_windowed_jsonl_filters_by_epoch_and_drops_torn_tail(
    tmp_path: Path,
) -> None:
    from webapp.backend.telemetry_capture_service import _freeze_windowed_jsonl

    epoch = 1_700_000_000.0
    src = tmp_path / "target_poll_out_0906_120000.jsonl"
    lines = [
        json.dumps({"ev": "clock_map", "t": epoch}),
        json.dumps({"ev": "frame", "t": 1.0, "targets": [[1, 1.0, 2.0, 3.0]]}),
        json.dumps({"ev": "frame", "t": 5.0, "targets": [[1, 1.0, 2.0, 3.0]]}),
        json.dumps({"ev": "frame", "t": 9.0, "targets": [[1, 1.0, 2.0, 3.0]]}),
        '{"ev": "frame", "t": 12.0, "targ',  # 撕裂尾行（写到一半）
    ]
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dst = tmp_path / "frozen.jsonl"

    kept = _freeze_windowed_jsonl(
        src, dst, "target", epoch + 4.0, epoch + 6.0,
    )

    assert kept == 2  # 锚 + t=5 一帧；t=1/9 出窗、撕裂行丢弃
    records = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines()]
    assert records[0]["ev"] == "clock_map"
    assert records[1]["t"] == 5.0


def test_freeze_windowed_jsonl_skips_single_bad_record_without_truncating(
    tmp_path: Path,
) -> None:
    """中间一条坏记录（缺字段/非数值）只跳过该行，其后窗口内的帧照常保留。"""
    from webapp.backend.telemetry_capture_service import _freeze_windowed_jsonl

    epoch = 1_700_000_000.0
    src = tmp_path / "target_poll_out_0906_120000.jsonl"
    lines = [
        json.dumps({"ev": "clock_map", "t": epoch}),
        json.dumps({"ev": "frame", "t": 4.0, "targets": [[1, 1.0, 2.0, 3.0]]}),
        json.dumps({"ev": "frame", "targets": "not-a-list"}),   # 坏记录：缺 t
        json.dumps({"ev": "frame", "t": "oops", "targets": []}),  # 坏记录：t 非数值
        json.dumps({"ev": "frame", "t": 5.0, "targets": [[1, 1.0, 2.0, 3.0]]}),
    ]
    src.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    dst = tmp_path / "frozen.jsonl"

    kept = _freeze_windowed_jsonl(src, dst, "target", epoch + 3.0, epoch + 6.0)

    assert kept == 3  # 锚 + t=4 + t=5；两条坏行跳过而非截断
    records = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines()]
    assert [r.get("t") for r in records[1:]] == [4.0, 5.0]


def test_request_run_cut_windowed_real_scripts_e2e(
    tmp_path: Path, no_diagnostics,
) -> None:
    real_scripts = resolve_scripts_dir()
    assert real_scripts is not None, "repo telemetry_capture/ must exist"
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=real_scripts,
        poll_interval=0.05,
        game_processes_fn=lambda: [object()],
    )
    session_dir = service.sessions_root / "session-260929-210000"
    epoch = _write_long_synthetic_session(session_dir)
    service._session_dir = session_dir

    # 游戏不在场：切窗直接跳过（退场全量收尾负责）。
    assert service.request_run_cut(901, int((epoch + 18) * 1000), int((epoch + 28) * 1000)) is False

    service._game_present = True
    assert service.request_run_cut(902, int((epoch + 18) * 1000), int((epoch + 28) * 1000)) is True
    assert service_mod.run_cut_pending(902) is True
    assert service_mod.wait_run_cut(902, 60.0) is False  # 完成后登记清除

    # 幂等去重：同 run 第二次不再切。
    assert service.request_run_cut(902, int((epoch + 18) * 1000), int((epoch + 28) * 1000)) is False

    incr_indexes = list((service.cleaned_root / "incr").glob("cut-run902-*/rounds_index.json"))
    assert len(incr_indexes) == 1, "增量切窗必须产出独立的 rounds_index.json"
    index = json.loads(incr_indexes[0].read_text(encoding="utf-8"))
    [source] = index["sources"]
    assert source["t0_epoch"] == pytest.approx(epoch, abs=1e-3)
    rounds = source["rounds"]
    assert rounds, "窗口内必须有轮次"
    # 前垫 10s → 窗口起点 epoch+8：第一轮从窗口首帧起（中途截入），第二轮生于 t=20。
    assert [round["t_start"] for round in rounds] == pytest.approx([8.0, 20.0], abs=0.2)
    assert rounds[0]["t_end"] == pytest.approx(15.0, abs=0.2)
    # 全量收尾路径未被触碰（增量与全量产物分家）。
    assert not (service.cleaned_root / "rounds_index.json").exists()
    # 冻结原料用后即清。
    assert not list((session_dir / "cuts").glob("*/*.jsonl"))
    # 合成数据 merge 验收按设计 fail-closed（exit_2）→ 旁车缺失如实记账。
    assert service._run_cuts[902] == "merge_unavailable"
    assert service._last_cut["run_id"] == 902
    diag = service.diagnostics()
    assert diag["last_cut"]["outcome"] == "merge_unavailable"
    assert diag["run_cuts_total"] == 1


def test_run_cut_rejection_reasons_are_counted(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """切窗前置守卫拒绝必须计数可见（b1 报障形态：零产出机器静默空转）。"""
    game_state = {"procs": []}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True) is False

    # 游戏不在场：game_absent。
    assert service.request_run_cut(1, 0, 1) is False
    assert service.diagnostics()["run_cut_rejections"] == {"game_absent": 1}

    # 游戏在场但会话目录没有任何 target 产出件：no_session_outputs。
    game_state["procs"] = ["game"]
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)
    assert service.request_run_cut(1, 0, 1) is False
    rejections = service.diagnostics()["run_cut_rejections"]
    assert rejections == {"game_absent": 1, "no_session_outputs": 1}

    # 产出件出现后守卫放行（线程路径由集成测试覆盖，这里只验拒绝面）。
    session_dir = service._session_dir
    assert session_dir is not None
    (session_dir / "target_poll_out_0906_120000.jsonl").write_text("", encoding="utf-8")
    assert service.request_run_cut(2, 0, 1) is True
    assert service.diagnostics()["run_cut_rejections"] == {
        "game_absent": 1, "no_session_outputs": 1,
    }

    service.stop()


def test_diagnostics_reports_live_child_log_tails_and_session_outputs(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """活着但零产出的子进程必须可诊断：日志尾 + 三通道产出清单。"""
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)

    session_dir = service._session_dir
    assert session_dir is not None
    # 子进程活着（_FakeChild poll() = None）但在日志里循环打附着失败：
    # 旧实现此刻 child_log_tail=None，完全不可见。
    (session_dir / "target.log").write_text(
        "[wait] 附着失败: offsets not found\n[wait] 15s 后重试...\n",
        encoding="utf-8",
    )

    diag = service.diagnostics()
    assert diag["child_log_tail"] is not None
    assert "附着失败" in diag["child_log_tail"]["target"]
    assert diag["session_outputs"] == {
        "target_files": 0,
        "camera_files": 0,
        "input_log": False,
    }

    # input 产出件出现后清单如实反映。
    (session_dir / "input_log.jsonl").write_text("", encoding="utf-8")
    assert service.diagnostics()["session_outputs"]["input_log"] is True

    service.stop()


def test_diagnostics_attach_causes_from_child_log_tail(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """病灶 B2a：child log 尾部最后一个已知 cause= 码 → diagnostics.attach_causes。

    野外形态（b3 报障）：target 子进程整场刷 OpenProcess err=5，现在诊断包必须
    给出机读归因（open_process_denied / open_process_failed），未知码不收录。
    """
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)

    session_dir = service._session_dir
    assert session_dir is not None
    # 尾部有多个 token 时取最后一个；前面出现过被拒、后面恢复了不算被拒。
    (session_dir / "target.log").write_text(
        "OpenProcess(12940) failed err=5 cause=open_process_denied\n"
        "[wait] 15s 后重试...\n",
        encoding="utf-8",
    )
    # 未知码不收录（不编造归因）。
    (session_dir / "camera.log").write_text(
        "cause=some_future_code\n", encoding="utf-8",
    )

    diag = service.diagnostics()
    assert diag["attach_causes"] == {"target": "open_process_denied"}

    # 无任何 token：字段为空 dict（加性字段恒在，消费方免 None 判断）。
    (session_dir / "target.log").write_text("all good\n", encoding="utf-8")
    (session_dir / "camera.log").write_text("all good\n", encoding="utf-8")
    assert service.diagnostics()["attach_causes"] == {}

    service.stop()


def test_diagnostics_attach_causes_include_dead_child_tails(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """病灶 B2a：附着被拒后子进程死亡（终态尾部）同样进 attach_causes。"""
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)

    session_dir = service._session_dir
    assert session_dir is not None
    (session_dir / "target.log").write_text(
        "OpenProcess(12940) failed err=5 cause=open_process_denied\n",
        encoding="utf-8",
    )
    # 子进程死亡：终态尾部进 _child_log_tails（{role}.log 现场读之外的兜底）。
    service._dead_children.add("target")
    service._child_log_tails["target"] = (
        "OpenProcess(12940) failed err=5 cause=open_process_denied\n")

    assert service.diagnostics()["attach_causes"] == {
        "target": "open_process_denied",
    }

    service.stop()


def test_no_session_outputs_rejection_records_attach_denied(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """病灶 B2a：no_session_outputs 拒绝记账携带 attach_denied 根因说明。

    零产出（附着从未成功）此前只记 no_session_outputs 计数，根因悬案；
    现在把「零产出的根因是附着被拒」写进拒绝说明。既有 reason 码不动。
    """
    game_state = {"procs": ["game"]}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)

    session_dir = service._session_dir
    assert session_dir is not None
    (session_dir / "target.log").write_text(
        "OpenProcess(12940) failed err=5 cause=open_process_denied\n",
        encoding="utf-8",
    )
    assert service.request_run_cut(1, 0, 1) is False
    diag = service.diagnostics()
    assert diag["run_cut_rejections"] == {"no_session_outputs": 1}
    assert diag["run_cut_rejection_details"] == {
        "no_session_outputs": "target:open_process_denied",
    }

    # 最近一次拒绝无 cause 说明时，旧说明必须清掉（说明描述最近一次拒绝）。
    (session_dir / "target.log").write_text("attached, writing...\n", encoding="utf-8")
    assert service.request_run_cut(2, 0, 1) is False
    assert service.diagnostics()["run_cut_rejection_details"] == {}

    service.stop()
