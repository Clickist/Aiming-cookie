"""telemetry_capture_service 行为测试。

生命周期用桩脚本目录 + 假 spawn 驱动（绝不启动真实采集子进程，也不依赖游戏
在运行）；另有一条真脚本集成测试：合成一份最小 target_poll/camera/input
JSONL 三元组，用仓库 telemetry_capture/ 的真实 cleaner.py + merge_channels.py
在子进程里跑通「清洗 → 分轮 → 旁车」全链。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
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


def test_run_telemetry_child_survives_cp1252_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[fix cp1252 2026-10-13] 冻结 bootloader 按系统 locale 初始化 stdio 且无视
    PYTHONIOENCODING（1013 巴西报障分诊）：cp1252 stdout 下打印中文的采集脚本
    必须被 run_telemetry_child 开头的 stdio 修正救回来，不抛 UnicodeEncodeError，
    且落进 BytesIO 的字节可按 utf-8 解码出中文原文。
    """
    custom = tmp_path / "custom-capture"
    custom.mkdir()
    (custom / "tp1.py").write_text("# marker\n", encoding="utf-8")
    (custom / "cn_child.py").write_text(
        "print('CHILD 遥测附着成功')\n", encoding="utf-8"
    )
    monkeypatch.setenv(service_mod.SCRIPTS_DIR_ENV, str(custom))
    out_buf = io.BytesIO()
    err_buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out_buf, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err_buf, encoding="cp1252"))
    run_telemetry_child("cn_child.py", [])
    sys.stdout.flush()
    assert "CHILD 遥测附着成功" in out_buf.getvalue().decode("utf-8")


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
    # [fix 2026-10-17 全量 teardown error] 受理即登记的切窗线程是 fire-and-forget
    # 后台线程，finally 里还要落盘 diagnostics；测试不等它就返回时，fixture 收尾
    # （monkeypatch 还原真实 file_store + isolated_db rmtree）会与其 mkstemp/
    # replace 竞争（tmp 在 rmtree 列目后被 replace 消失 → unlink 失败）。用生产
    # 等待门真实等线程收尾：event 在 diagnostics 落盘与登记清除之后才 set，返回
    # False（=已完成并清除登记）即本线程的写入已全部结束。
    assert service_mod.wait_run_cut(2, 10.0) is False
    assert service._run_cuts.get(2) == "no_target_data"  # 线程 outcome 已落账

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
    # input_log_bytes（P1-15 加性字段）：僵尸通道（文件在但字节停涨）的指纹。
    assert diag["session_outputs"] == {
        "target_files": 0,
        "camera_files": 0,
        "input_log": False,
        "input_log_bytes": 0,
    }

    # input 产出件出现后清单如实反映。
    (session_dir / "input_log.jsonl").write_text("", encoding="utf-8")
    outputs = service.diagnostics()["session_outputs"]
    assert outputs["input_log"] is True
    assert outputs["input_log_bytes"] == 0

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


def test_request_run_cut_rejection_writes_run_meta_receipt(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[fix 2026-10-07 W4] 守卫拒绝回写 run meta（telemetry_cut_state/error），
    让单局能回答「动作层为何缺失」，不再只有服务级聚合计数。"""
    import asyncio

    from webapp.backend import config, kovaak_run_store

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    game_state = {"procs": []}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    assert service.start() is True

    run = asyncio.run(kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="w4-receipt - challenge - 2026.10.07-20.00.00",
        scenario="W4 Receipt",
    ))

    # 游戏不在场：game_absent 拒绝 → run meta 带码。
    assert service.request_run_cut(run["id"], 0, 1) is False
    stored = asyncio.run(kovaak_run_store.get_kovaak_run(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
    ))
    assert stored["telemetry_cut_state"] == "rejected"
    assert stored["telemetry_cut_error"] == "game_absent"

    # 游戏在场但零产出：no_session_outputs 覆盖回执。
    game_state["procs"] = ["game"]
    assert _wait_until(lambda: service.diagnostics()["game_present"] is True)
    assert service.request_run_cut(run["id"], 0, 1) is False
    stored = asyncio.run(kovaak_run_store.get_kovaak_run(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
    ))
    assert stored["telemetry_cut_state"] == "rejected"
    assert stored["telemetry_cut_error"] == "no_session_outputs"

    service.stop()


# ------------------------------------------------------- [rb P1-16 2026-10-11]

def _manual_session_service(
    tmp_path: Path, stub_scripts: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[TelemetryCaptureService, Path]:
    """不起监控线程的服务实例：死亡→拉起簿记全部同步驱动，无竞态。

    （返回 (service, session_dir)。）
    """
    game_state = {"procs": []}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    service.sessions_root.mkdir(parents=True, exist_ok=True)
    session_dir = service.sessions_root / "session-261011-120000"
    session_dir.mkdir()
    service._session_dir = session_dir
    service._children = {"target": _FakeChild(pid=500)}
    return service, session_dir


def test_child_death_restarts_in_same_session_within_budget(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通道死亡 → 预算内自动拉起：同 role 同会话目录，死亡登记清除。"""
    service, session_dir = _manual_session_service(
        tmp_path, stub_scripts, monkeypatch,
    )
    dead = service._children["target"]
    dead.returncode = 1
    service._check_children_alive()

    # 拉起发生：新替身入列（fake_spawn 按 spawned 数分配 pid），死亡登记与
    # 尾部缓存清空，pids 落盘。
    restarted = service._children["target"]
    assert restarted is not dead
    assert restarted.pid != dead.pid
    assert "target" not in service._dead_children
    assert "target" not in service._child_log_tails
    assert (session_dir / "pids.json").is_file()
    # 拉起 argv 指向同一会话目录（增量切窗/退场收尾继续看到该通道产出件）。
    restart_argv = service.spawn_log[-1]  # type: ignore[attr-defined]
    assert restart_argv[1].endswith("target_poll2.py")
    assert str(session_dir) in restart_argv
    # 簿记：窗口内 1 次，诊断可见。
    assert service._restarts_total == 1
    diagnostics = service.diagnostics()
    assert diagnostics["restart_exhausted"] is False
    assert diagnostics["child_restarts_total"] == 1
    last = diagnostics["last_child_restart"]
    assert last["role"] == "target" and last["ok"] is True and last["cause"] is None
    # 拉起成功不改写 child_exited 的既有 _last_error（死亡现场仍可诊断）。
    assert "child_exited" in str(diagnostics["last_error"])


def test_child_death_exhausts_restart_budget_and_stays_down(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """60s 窗最多拉起 2 次：第 3 次死亡不再拉起，明确失败（稳定 cause 码 +
    诊断投影 restart_exhausted=True），绝不静默降级。"""
    service, _session_dir = _manual_session_service(
        tmp_path, stub_scripts, monkeypatch,
    )
    for round_index in range(3):
        current = service._children["target"]
        current.returncode = 1
        service._check_children_alive()
        if round_index < 2:
            assert service._children["target"] is not current
        else:
            # 预算耗尽：第 3 具尸体原地保留，不再拉起。
            assert service._children["target"] is current
    diagnostics = service.diagnostics()
    assert diagnostics["restart_exhausted"] is True
    assert diagnostics["child_restarts_total"] == 2
    assert "restart_exhausted" in str(diagnostics["last_error"])
    # 死亡现场日志尾部仍可诊断（死亡登记不被超限抹掉）。
    assert "target" in service._dead_children


def test_restart_budget_resets_with_new_session(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算按会话计：耗尽后新会话（_spawn_session）重新获得完整预算。"""
    service, _session_dir = _manual_session_service(
        tmp_path, stub_scripts, monkeypatch,
    )
    service._restart_epochs = [time.time(), time.time()]
    service._restart_exhausted = True
    assert service._spawn_session() is True
    assert service._restart_exhausted is False
    assert service._restart_epochs == []


def test_restart_window_slides_old_attempts_expire(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """滑动窗防抖：窗外的拉起时刻过期不占预算（60s 前的拉起不算数）。"""
    service, _session_dir = _manual_session_service(
        tmp_path, stub_scripts, monkeypatch,
    )
    service._restart_epochs = [time.time() - service_mod._RESTART_WINDOW_SECONDS - 1]
    service._restarts_total = 1
    service._children["target"].returncode = 1
    service._check_children_alive()
    # 窗内只有本次 → 拉起放行。
    assert service.diagnostics()["restart_exhausted"] is False
    assert service._restarts_total == 2
    assert len(service._restart_epochs) == 1


def test_failed_restart_attempts_consume_budget(
    tmp_path: Path, stub_scripts: Path, no_diagnostics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉起本身失败（OSError）同样占预算：不同通道各失败一次即耗尽预算，
    进入明确失败状态；失败通道保持死亡登记（不重试同一具尸体）。"""
    game_state = {"procs": []}
    service = _make_service(tmp_path, stub_scripts, game_state, monkeypatch)
    service.sessions_root.mkdir(parents=True, exist_ok=True)
    session_dir = service.sessions_root / "session-261011-120000"
    session_dir.mkdir()
    service._session_dir = session_dir
    service._children = {
        "target": _FakeChild(pid=500),
        "camera": _FakeChild(pid=501),
    }

    def failing_spawn(
        self, argv: list[str], session_dir: Path, role: str,
    ) -> _FakeChild:
        raise OSError("disk full")

    monkeypatch.setattr(TelemetryCaptureService, "_spawn_process", failing_spawn)
    service._children["target"].returncode = 1
    service._children["camera"].returncode = 1
    service._check_children_alive()

    diagnostics = service.diagnostics()
    assert diagnostics["restart_exhausted"] is True
    assert diagnostics["child_restarts_total"] == 2
    assert "restart_exhausted" in str(diagnostics["last_error"])
    last = diagnostics["last_child_restart"]
    assert last["ok"] is False and "disk full" in str(last["cause"])
    # 失败通道不重试同一具尸体：死亡登记保留，monitor 轮询不会热循环。
    assert service._dead_children == {"target", "camera"}
    assert set(service._children) == {"target", "camera"}


# ---------------------------------------------------------------------------
# 系统评审 2026-10-16 #3 回归：按局冻结必须保留 flag 补采行（真实 freeze +
# 真实 cleaner 交叉对照，不是白名单镜像断言）。
# ---------------------------------------------------------------------------


def test_freeze_windowed_jsonl_keeps_flag_and_death_rows_for_target_channel(
    tmp_path: Path,
) -> None:
    """坐标缺席帧的 flag 补采行必须与 frame/death 同样放行。

    cleaner 的死亡账本以 flag 行为步证来源（_dc_walk 步进匹配）；冻结丢 flag
    会让同 tick 的 death 行失去步证 → 生命边界被并段。blind_watch/scale 等
    旁线行维持既有过滤不动。
    """
    from webapp.backend.telemetry_capture_service import _freeze_windowed_jsonl

    epoch = 1_700_000_000.0
    src = tmp_path / "target_poll_out_0906_120000.jsonl"
    lines = [
        json.dumps({"ev": "clock_map", "t": epoch}),
        json.dumps({"ev": "frame", "t": 4.5,
                    "targets": [[0x1000, 1.0, 2.0, 3.0, 100, 0]]}),
        json.dumps({"ev": "flag", "t": 5.0, "addr": 0x1000, "hp": 0, "dc": 1}),
        json.dumps({"ev": "death", "t": 5.0, "addr": 0x1000, "dc": 1}),
        json.dumps({"ev": "blind_watch", "t": 5.0, "addr": 0x1000,
                    "phase": "suspect"}),
        json.dumps({"ev": "scale", "t": 5.0, "addr": 0x1000,
                    "s": [1.0, 1.0, 1.0]}),
        json.dumps({"ev": "frame", "t": 5.5,
                    "targets": [[0x1000, 1.0, 2.0, 3.0, 25, 1]]}),
    ]
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dst = tmp_path / "frozen.jsonl"

    kept = _freeze_windowed_jsonl(src, dst, "target", epoch + 4.0, epoch + 6.0)

    assert kept == 5  # 锚 + 两帧 + flag + death；blind_watch/scale 照旧丢弃
    events = [
        json.loads(line)["ev"]
        for line in dst.read_text(encoding="utf-8").splitlines()
    ]
    assert events == ["clock_map", "frame", "flag", "death", "frame"]


def _write_flag_repro_target(path: Path, epoch: float) -> None:
    """坐标缺席间隙 + 同 tick flag/death 的最小复现局（50Hz，8s）。

    0x1000：t<4.0 在场（帧条目第 6 列 dc=0）；t∈[4.0,4.4] 坐标读不到（帧条
    目缺席，仅 flag 行补 dc=1）；t=4.0 同 tick 发 death 行；t>4.4 重生在场
    （dc=1）。死亡步证只有 flag 行能背书（dc 0→1 步进恰好落在 t=4.0，容差
    DEATH_ROW_TOL=0.1s）；帧列的下一观测在 t=4.42，距 death 行 0.42s 出容
    差。形态对齐 target_poll2.py 的 flag_rows 分支（坐标读失败但 hp/dc 可
    读）。
    """
    gap_lo, gap_hi = 4.0, 4.4
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": epoch}) + "\n")
        for i in range(int(8.0 * _HZ)):
            t = i / _HZ
            if gap_lo <= t <= gap_hi:
                # 0x1000 坐标缺席：由移动填充目标补位帧流。填充必须能活过
                # cleaner 的静态幽灵门（path > MIN_MOVE），否则会被先丢、间隙
                # 变全灭（> DEAD_GAP 0.05s）而把局切成两轮——生命边界断言就
                # 不在同一轮里了。填充不写 dc 列（4 列条目），不进 dc 账本。
                targets = [[0x9000, 1000 + 600 * (t - gap_lo), 2000.0, 700.0]]
            else:
                targets = [[0x1000, 500 + 300 * t, 1000.0, 700.0,
                            100 if t < gap_lo else 25, 0 if t < gap_lo else 1]]
            f.write(json.dumps({"ev": "frame", "t": t, "targets": targets}) + "\n")
            if t == gap_lo:
                f.write(json.dumps(
                    {"ev": "flag", "t": t, "addr": 0x1000, "hp": 0, "dc": 1}
                ) + "\n")
                f.write(json.dumps(
                    {"ev": "death", "t": t, "addr": 0x1000, "dc": 1}
                ) + "\n")


def _run_real_cleaner(src: Path, outdir: Path) -> dict:
    scripts = resolve_scripts_dir()
    assert scripts is not None, "repo telemetry_capture/ must exist"
    completed = subprocess.run(  # noqa: S603 - 固定仓库脚本
        [sys.executable, str(scripts / "cleaner.py"), str(src),
         "--outdir", str(outdir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(
        (outdir / "rounds_index.json").read_text(encoding="utf-8"))


def test_cut_freeze_preserves_flag_backed_death_boundary_for_cleaner(
    tmp_path: Path,
) -> None:
    """同一窗口：原始件与冻结件进入真实 cleaner 的死亡步证与生命边界一致。

    全量路径（原始件）与按局路径（真实 _freeze_windowed_jsonl 冻结件）分别
    跑仓库真实 cleaner CLI。冻结丢 flag 时：death 行在（白名单里有 death），
    但 dc 账本缺 t=4.0 步进 → cleaner 判 unbacked → 生命段并回一条、
    reorg_audit.ok 翻 false——正是评审复现的受损形态。deaths_summary.total
    两路都是 1（受损的是生命边界/计数与 T2K 证据，不是官方击杀总数）。
    """
    from webapp.backend.telemetry_capture_service import _freeze_windowed_jsonl

    epoch = 1_760_000_000.0
    raw = tmp_path / "target_poll_out_0906_120000.jsonl"
    _write_flag_repro_target(raw, epoch)

    frozen = tmp_path / "frozen" / raw.name
    frozen.parent.mkdir(parents=True, exist_ok=True)
    kept = _freeze_windowed_jsonl(raw, frozen, "target", epoch - 1.0, epoch + 20.0)
    # 全窗冻结：403 = 锚 + 400 帧 + flag + death（丢 flag 的旧实现是 402）。
    assert kept == 403, "整窗冻结必须逐行保留 flag 补采行"

    index_full = _run_real_cleaner(raw, tmp_path / "cleaned-full")
    index_cut = _run_real_cleaner(frozen, tmp_path / "cleaned-cut")
    full = index_full["sources"][0]
    cut = index_cut["sources"][0]

    assert full["deaths_source"] == "flag"
    stats_full = full["per_addr_cut_stats"]["0x1000"]
    stats_cut = cut["per_addr_cut_stats"]["0x1000"]
    # 全量路径基线：死亡行有步证、落成生命边界。
    assert stats_full["reorg_ok"] is True
    assert stats_full["death_rows_rejected_unbacked"] == 0
    assert stats_full["reorg_lives"] == 2
    # 交叉对照：冻结件进 cleaner 必须得到同一结论（修复点）。
    assert cut["deaths_source"] == "flag"
    assert stats_cut["reorg_ok"] is True
    assert stats_cut["death_rows_rejected_unbacked"] == 0
    assert stats_cut["reorg_lives"] == stats_full["reorg_lives"]
    lives_full = full["rounds"][0]["targets"][0]["lives"]
    lives_cut = cut["rounds"][0]["targets"][0]["lives"]
    assert [life["death_event"] for life in lives_full] == [True, False]
    assert lives_cut == lives_full  # 权威死亡边界 t_end=4.0 两路一致
    # 口径边界：减法口径死亡总数不受 flag 丢弃影响，两路一致。
    assert cut["deaths_summary"]["total"] == full["deaths_summary"]["total"] == 1


# ---------------------------------------------------------------------------
# 系统评审 2026-10-16 #1 回归：切窗回执与 run meta 读改写并发。
# 生产合同：run meta 的读改写全部收敛到 runtime 事件循环线程（切窗线程的
# 回执经单次投递回环执行、有界等待生效后才放行 completion barrier）；投递
# 失败/超时绝不回退跨线程直写（那会重新引入丢更新与 queued future 后到双
# 写），只记明确 cause——pending 未写必须可见，不静默装正常。
# ---------------------------------------------------------------------------


def test_cut_receipt_from_worker_thread_never_loses_loop_update(
    tmp_path: Path, stub_scripts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切窗线程回执 vs 事件循环 finalization 强制交错：两组字段都必须保留。

    旧实现回执在切窗线程整份读改写 run meta：先读到旧快照、被 loop 的
    finalization 抢先落盘后再整份写回 → finalization_state 被倒回。测试用
    write_json 钩子（按线程身份识别切窗线程的写）把「旧快照写回」钉在
    「loop finalization 落盘」之前，确定性复现丢更新。
    """
    import asyncio

    from webapp.backend import config, file_store, kovaak_run_store

    run_id = 990001
    profile = config.DESKTOP_LOCAL_PROFILE
    meta_rel = f"runs/{run_id}/meta.json"
    file_store.write_json(meta_rel, {
        "id": run_id, "user_id": profile, "finalization_state": "discovered",
    })

    real_write = file_store.write_json
    worker_ident: dict[str, int] = {}
    save_reached = threading.Event()
    release = threading.Event()
    worker_done = threading.Event()

    def intercepting_write(relative_path: str, payload: object) -> None:
        if (relative_path == meta_rel
                and threading.get_ident() == worker_ident.get("id")):
            save_reached.set()
            release.wait(timeout=5.0)
        real_write(relative_path, payload)

    monkeypatch.setattr(file_store, "write_json", intercepting_write)

    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )

    def worker() -> None:
        worker_ident["id"] = threading.get_ident()
        try:
            service._report_cut_state(run_id, "ok", None)
        finally:
            worker_done.set()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        # 修前无 set_receipt_loop（直写路径=被测缺陷）；修后附真环走投递路径。
        if hasattr(service, "set_receipt_loop"):
            service.set_receipt_loop(loop)
        thread = threading.Thread(
            target=worker, name="telemetry-cut", daemon=True)
        thread.start()
        forced_interleave = await asyncio.to_thread(save_reached.wait, 2.0)
        if forced_interleave:
            # 切窗线程已持有旧快照并被钉住：loop 此刻完成 finalization 落盘，
            # 再放行旧快照写回——旧行为在这之后把 finalized 倒回 discovered。
            await kovaak_run_store.set_run_finalization_state(
                run_id, profile, "finalized")
            release.set()
        await asyncio.to_thread(worker_done.wait, 10.0)
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "切窗回执不得挂死工作线程"
        if not forced_interleave:
            # 回执已先经事件循环生效（future 返回即已落盘），finalization 后行。
            await kovaak_run_store.set_run_finalization_state(
                run_id, profile, "finalized")

    asyncio.run(scenario())

    meta = file_store.read_json(meta_rel)
    assert meta["telemetry_cut_state"] == "ok"
    assert meta["finalization_state"] == "finalized", (
        "切窗回执的旧快照写回不得覆盖 loop 已落的 finalization 状态")


def test_cut_run_window_receipt_applied_before_completion_barrier(
    tmp_path: Path, stub_scripts: Path, no_diagnostics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """护栏：completion barrier（登记清除 + event.set）放行时回执必须已生效。"""
    import asyncio

    from webapp.backend import config, file_store, kovaak_run_store

    run_id = 990002
    profile = config.DESKTOP_LOCAL_PROFILE
    file_store.write_json(f"runs/{run_id}/meta.json", {
        "id": run_id, "user_id": profile, "finalization_state": "discovered",
    })
    monkeypatch.setattr(
        TelemetryCaptureService, "_cut_run_window_inner",
        lambda self, rid, start, end: "ok",
    )
    service = TelemetryCaptureService(
        data_root=tmp_path / "data",
        scripts_dir=stub_scripts,
        game_processes_fn=lambda: [],
    )

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        if hasattr(service, "set_receipt_loop"):
            service.set_receipt_loop(loop)
        # loop 侧先完成 finalization，再起切窗线程：barrier 放行时两者都在。
        await kovaak_run_store.set_run_finalization_state(
            run_id, profile, "finalized")
        event = threading.Event()
        thread = threading.Thread(
            target=service._cut_run_window,
            args=(run_id, 0, 1, event), name="telemetry-cut", daemon=True,
        )
        thread.start()
        finished = await asyncio.to_thread(event.wait, 10.0)
        thread.join(timeout=5.0)
        assert finished and not thread.is_alive()

    asyncio.run(scenario())

    meta = file_store.read_json(f"runs/{run_id}/meta.json")
    assert meta["telemetry_cut_state"] == "ok"
    assert meta["finalization_state"] == "finalized"
    assert service_mod.run_cut_pending(run_id) is False


def test_cut_receipt_never_falls_back_to_direct_write_when_loop_unavailable(
    tmp_path: Path, stub_scripts: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """loop 不可投递（已关/未运行/写失败）＝按因分类记录、绝不跨线程直写、
    也不挂死。

    loop 关闭不证明无并发写方（shutdown 线程仍在途）；超时说明回执仍挂在
    loop 队列稍后执行——直写会造成 queued future 后到双写。三种情况都必须：
    工作线程有界返回、run meta 无本次回执键、error 日志带明确 cause
    （delivery_loop_unavailable / delivery_pending / write_failed）。
    """
    import asyncio

    from webapp.backend import config, file_store, kovaak_run_store

    profile = config.DESKTOP_LOCAL_PROFILE

    def run_case(loop, *, expected_cause: str, timeout_patched: bool,
                 fail_write: bool = False) -> None:
        file_store.write_json("runs/990003/meta.json", {
            "id": 990003, "user_id": profile,
            "finalization_state": "discovered",
        })
        service = TelemetryCaptureService(
            data_root=tmp_path / "data",
            scripts_dir=stub_scripts,
            game_processes_fn=lambda: [],
        )
        if hasattr(service, "set_receipt_loop"):
            service.set_receipt_loop(loop)
        if timeout_patched:
            monkeypatch.setattr(service_mod, "_CUT_RECEIPT_TIMEOUT_SECONDS", 0.2)
        if fail_write:
            def failing_write(*args, **kwargs):
                raise OSError("simulated meta write failure on loop")
            monkeypatch.setattr(
                kovaak_run_store, "set_run_telemetry_cut_state_sync",
                failing_write)
        done = threading.Event()

        def worker() -> None:
            try:
                service._report_cut_state(990003, "ok", None)
            finally:
                done.set()

        with caplog.at_level("ERROR", logger="webapp.backend.telemetry_capture_service"):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout=5.0)
        assert not thread.is_alive(), "投递失败必须有界返回，不挂死切窗线程"
        meta = file_store.read_json("runs/990003/meta.json")
        assert "telemetry_cut_state" not in meta, (
            "投递失败不得跨线程直写 run meta（重新引入丢更新/双写）")
        assert meta["finalization_state"] == "discovered"
        assert expected_cause in caplog.text, (
            f"必须以 cause={expected_cause} 明确记损，不静默装正常")

    with caplog.at_level("ERROR"):
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        run_case(closed_loop, expected_cause="delivery_loop_unavailable",
                 timeout_patched=False)

        stalled_loop = asyncio.new_event_loop()  # 从不 run_forever：投递永挂
        try:
            run_case(stalled_loop, expected_cause="delivery_pending",
                     timeout_patched=True)
        finally:
            stalled_loop.close()

        live_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=live_loop.run_forever, daemon=True)
        loop_thread.start()
        try:
            run_case(live_loop, expected_cause="write_failed",
                     timeout_patched=False, fail_write=True)
        finally:
            live_loop.call_soon_threadsafe(live_loop.stop)
            loop_thread.join(timeout=5.0)
            live_loop.close()
