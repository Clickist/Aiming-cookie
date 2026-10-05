# -*- coding: utf-8 -*-
"""修C回归：增量冻结件相机通道零帧时 merge_channels 须 fail-closed 拒旁车
（exit=2 + cause=camera_zero_frames），不得 IndexError crash（exit=1）。

00:19 Administrator 机报障形态：incr/cut-run3、cut-run4 连续两次
kill_click_check cam["frames"][c] IndexError（v1.3.9:562）——空 cam_ts 时
钳制 min(max(c,0), len(cam_ts)-1) 产出 -1，负索引越界。修A/B/1.4.1 不覆盖。
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from telemetry_capture import merge_channels  # noqa: E402


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture()
def zero_camera_scene(tmp_path):
    """有 t0_epoch 的 rounds_index + 轮文件 + 零帧相机（仅 clock_map）+ 正常输入。"""
    t0 = 1791128865.1261
    round_dir = tmp_path / "incr" / "cut-run3-000" / "target_poll_out_1006_001500"
    round_dir.mkdir(parents=True)
    entry = {
        "source": "target_poll_out_1006_001500.jsonl",
        "outdir": "target_poll_out_1006_001500",
        "t0_epoch": t0,
        "rounds": [{
            "round": 1, "file": "round_01.jsonl",
            "t_start": 1.0, "t_end": 8.0,
            "targets": [{
                "addr": 1234, "tid": 0,
                "lives": [{"t_start": k + 0.5, "t_end": k + 1.0}
                          for k in range(1, 7)],
            }],
        }],
    }
    (round_dir / "rounds_index.json").write_text(json.dumps({
        "format_version": 1, "generator": "cleaner.py",
        "params": {"epoch_window": [t0, t0 + 75.0]},
        "sources": [entry],
    }, ensure_ascii=False), encoding="utf-8")
    # 轮文件：覆盖 lives 的帧（正常目标轨迹）
    frames = []
    for i in range(140):
        t = 1.0 + i * 0.05
        frames.append({"ev": "frame", "t": t, "targets": [[1234, 100.0, 50.0, 10.0]]})
    _write(str(round_dir / "round_01.jsonl"), frames)
    # 相机：只有 clock_map，零 cam 行（00:19 案形态）
    _write(str(tmp_path / "camera.jsonl"), [{"ev": "clock_map", "t": 88.0}])
    # 输入：clock_map + 鼠标事件 + L_down 点击（死亡前 ≤250ms 配对——00:19 案
    # 走到 cam["frames"][c] 炸点的前提：kill_click_check 的 click↔death 配对成立）
    inp = [{"ev": "clock_map", "t_unix": t0 + 88.0, "t": 88.0}]
    for i in range(10):
        inp.append({"ev": "m", "t": 100.0 + i, "dx": 0, "dy": 0, "btn": []})
    for k in range(1, 7):   # 每条 life 一个配对点击（t_end-0.1）
        inp.append({"ev": "m", "t": k + 0.9, "dx": 0, "dy": 0, "btn": ["L_down"]})
    _write(str(tmp_path / "input_log.jsonl"), inp)
    return {"round_dir": str(round_dir), "camera": str(tmp_path / "camera.jsonl"),
            "input": str(tmp_path / "input_log.jsonl"), "t0": t0}


def test_zero_frame_camera_fails_closed_not_crash(zero_camera_scene, capsys, monkeypatch):
    """空相机通道：exit=2 + cause 码，绝不 IndexError。"""
    s = zero_camera_scene
    monkeypatch.setattr(sys, "argv", [
        "merge_channels.py",
        "--round-dir", s["round_dir"],
        "--camera", s["camera"],
        "--input", s["input"],
    ])
    rc = merge_channels.main()
    assert rc == 2
    out = capsys.readouterr().out
    assert "camera_zero_frames" in out


def test_zero_frame_camera_writes_no_sidecar(zero_camera_scene):
    """拒绝路径不落任何旁车件。"""
    s = zero_camera_scene
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sys, "argv", [
        "merge_channels.py",
        "--round-dir", s["round_dir"],
        "--camera", s["camera"],
        "--input", s["input"],
    ])
    try:
        merge_channels.main()
    finally:
        monkeypatch.undo()
    rd = s["round_dir"]
    assert not os.path.isfile(os.path.join(rd, "merge_manifest.json"))
    assert not os.path.isfile(os.path.join(rd, "views_01.jsonl"))
    assert not os.path.isfile(os.path.join(rd, "inputs_01.jsonl"))


def test_nonempty_camera_still_passes(zero_camera_scene, monkeypatch, tmp_path):
    """健康相机（有 cam 行）同场景不被新守卫误拒，且 aim 验收过 → exit 0。"""
    import math
    s = zero_camera_scene
    cam_path = str(tmp_path / "camera_ok.jsonl")
    rows = [{"ev": "clock_map", "t": 88.0}]
    t0 = s["t0"]
    # cam 帧朝向对准目标 (100,50,10)；帧 t=文件内相对秒，cam_ts=epoch+t-s，
    # 取 t = t0-86+i*0.02 ⇒ cam_ts = 2+i*0.02，覆盖死亡窗与轮窗
    yaw = math.degrees(math.atan2(50.0, 100.0))
    pitch = math.degrees(math.atan2(10.0, math.hypot(100.0, 50.0)))
    for i in range(200):
        rows.append({"ev": "cam", "t": t0 - 86.0 + i * 0.02,
                     "pos": [0.0, 0.0, 0.0], "rot": [pitch, yaw], "fov": 90.0})
    _write(cam_path, rows)
    monkeypatch.setattr(sys, "argv", [
        "merge_channels.py",
        "--round-dir", s["round_dir"],
        "--camera", cam_path,
        "--input", s["input"],
    ])
    rc = merge_channels.main()
    assert rc == 0
    assert os.path.isfile(os.path.join(s["round_dir"], "merge_manifest.json"))
