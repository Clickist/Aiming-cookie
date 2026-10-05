# -*- coding: utf-8 -*-
"""merge_channels 验收稳定性单测（精确锚分级验收对重洗漂移的免疫）。

[fix 2026-10-06] 生产病例（1.3.9 cut-run51）：同一局数据三跑两样——首跑
tracking_aim 2.446° accepted，rounds_index 重洗（增量→全量）后样本集漂移到
5.95°/6.58° 连续 exit=2。两道修复的行为合同：
  A. 同源数据 verdict 复用：游戏指纹（源件名 + t0_epoch + 相机锚 + 输入 delta）
     匹配的已过验 manifest，重洗后重跑不得翻案；
  B. tracking_aim 缓冲带：median ∈ (5°, 7°] 且 share 达标 ⇒ 降级 grade=
     tracking_aim_degraded 放行（可观测，非静默），>7° 仍 fail-closed。
"""
import json
import math
import os
import sys

import pytest

from telemetry_capture import merge_channels as mc

YEAR = 2026
STEM = "target_poll_out_1006_120000"
T0_EPOCH = 1791300000.0


# ---------------- 合成会话构造 ----------------

def _az_deg(i):
    """目标 i 的方位角（度）：彼此错开，准星甩靶即产生可设计的角误差。"""
    return 10.0 + 2.0 * i


def _target_pos(i):
    az = math.radians(_az_deg(i))
    return [round(1000.0 * math.cos(az), 1), round(1000.0 * math.sin(az), 1), 0.0]


def _build_camera(path, locked_err_deg, swung_deg, life_ends, span_s=30.0):
    """相机文件：死亡窗内锁定（误差 locked_err_deg），窗外甩靶（swung_deg）。

    yaw 时间线按 life_ends 分段：窗 [te-0.25, te] 指向目标 i+锁定误差；
    其后指向目标 i+甩靶角（死亡后已甩向别处的跟枪语义）。
    """
    rows = [{"ev": "clock_map", "t": T0_EPOCH}]
    t = 0.0
    while t <= span_s:
        yaw = None
        for i, te in enumerate(life_ends):
            if te - 0.25 <= t <= te:
                yaw = _az_deg(i) + locked_err_deg
                break
            if t > te:
                yaw = _az_deg(i) + swung_deg
        if yaw is None:
            yaw = _az_deg(0)
        rows.append({"ev": "cam", "t": round(t, 3), "pos": [0.0, 0.0, 0.0],
                     "rot": [0.0, yaw, 0.0], "fov": 103.0})
        t += 0.05
    _write_jsonl(path, rows)


def _build_input(path):
    _write_jsonl(path, [
        {"ev": "clock_map", "t": T0_EPOCH, "t_unix": T0_EPOCH},
        {"ev": "m", "t": T0_EPOCH + 0.1, "dx": 3, "dy": 1, "btn": []},
        {"ev": "m", "t": T0_EPOCH + 0.2, "dx": 2, "dy": 0, "btn": []},
    ])


def _build_round_file(path, n_targets, span_s=30.0):
    rows = []
    t = 0.0
    while t <= span_s:
        rows.append({"ev": "frame", "t": round(t, 3),
                     "targets": [[100 + i] + _target_pos(i) for i in range(n_targets)]})
        t += 0.1
    _write_jsonl(path, rows)


def _build_index(path, life_ends, t0_epoch=T0_EPOCH):
    targets = [
        {"tid": i, "addr": 100 + i,
         "lives": [{"t_start": round(max(0.0, te - 3.0), 4), "t_end": round(te, 4)}]}
        for i, te in enumerate(life_ends)
    ]
    index = {
        "format_version": 1,
        "generator": "cleaner.py",
        "sources": [{
            "source": STEM + ".jsonl",
            "outdir": STEM,
            "t0_epoch": round(t0_epoch, 4),
            "rounds": [{
                "round": 1, "file": "round_01.jsonl",
                "t_start": 0.0, "t_end": round(max(life_ends), 4),
                "n_targets": len(targets), "targets": targets,
            }],
        }],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _run_merge(tmp_path, monkeypatch, swung_deg, drift_s, t0_epoch=T0_EPOCH):
    """搭一局合成会话并跑一次 merge；drift_s>0 模拟重洗后 life 边界漂移。

    返回 (exit_code, manifest_dict|None)。"""
    cleaned = tmp_path / "cleaned"
    round_dir = cleaned / STEM
    round_dir.mkdir(parents=True)
    life_ends = [2.0 + 3.0 * i for i in range(8)]        # 8 目标各 1 life
    washed_ends = [te + drift_s for te in life_ends]
    _build_camera(round_dir / "camera.jsonl", 0.3, swung_deg, life_ends)
    _build_input(round_dir / "input_log.jsonl")
    _build_round_file(round_dir / "round_01.jsonl", 8)
    _build_index(cleaned / "rounds_index.json", washed_ends, t0_epoch)
    argv = ["merge_channels.py", "--round-dir", str(round_dir),
            "--camera", str(round_dir / "camera.jsonl"),
            "--input", str(round_dir / "input_log.jsonl"),
            "--year", str(YEAR)]
    return _invoke(round_dir, argv, monkeypatch)


def _invoke(round_dir, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    code = mc.main()
    mf = round_dir / "merge_manifest.json"
    manifest = None
    if mf.is_file():
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    return code, manifest


def _rewash(tmp_path, drift_s, t0_epoch=T0_EPOCH, n_targets=8):
    """重洗：改写 rounds_index（life 边界漂移）+ 重写轮文件（内容可等价）。"""
    cleaned = tmp_path / "cleaned"
    round_dir = cleaned / STEM
    life_ends = [2.0 + 3.0 * i for i in range(n_targets)]
    _build_index(cleaned / "rounds_index.json",
                 [te + drift_s for te in life_ends], t0_epoch)
    _build_round_file(round_dir / "round_01.jsonl", n_targets)


# ---------------- 缓冲带纯逻辑 ----------------

def aim(n, median, share10):
    return {"n": n, "median_deg": median, "share_lt_10deg": share10}


def test_aim_session_grade_full_pass_unchanged():
    assert mc._aim_session_grade(aim(95, 2.446, 0.55)) == "tracking_aim"


def test_aim_session_grade_degraded_band_covers_wash_drift():
    # cut-run51 重洗漂移实测 5.95/6.58°：缓冲带内 ⇒ 降级放行（可观测）。
    assert mc._aim_session_grade(aim(101, 5.95, 0.52)) == "tracking_aim_degraded"
    assert mc._aim_session_grade(aim(101, 6.58, 0.52)) == "tracking_aim_degraded"


def test_aim_session_grade_band_upper_is_fail_closed():
    # 病例2 量级（7.5~14.7°）在带外：仍拒绝。错锚负对照 34°+ 同理。
    assert mc._aim_session_grade(aim(101, 7.5, 0.52)) is None
    assert mc._aim_session_grade(aim(101, 34.3, 0.10)) is None


def test_aim_session_grade_share_gate_holds_in_band():
    assert mc._aim_session_grade(aim(101, 6.0, 0.45)) is None


def test_aim_session_grade_n_and_median_gates():
    assert mc._aim_session_grade(aim(mc.AIM_CHECK_MIN_N - 1, 1.0, 0.9)) is None
    assert mc._aim_session_grade(aim(10, None, 0.9)) is None


# ---------------- 游戏指纹 ----------------

def test_fingerprint_exact_match():
    fp = {"source": "a.jsonl", "t0_epoch": 1.0,
          "camera_epoch_anchor": 2.0, "input_delta_epoch_perf": 3.0}
    assert mc._fingerprint_match(fp, dict(fp)) is True


def test_fingerprint_input_delta_tolerates_window_drift():
    fp = {"source": "a.jsonl", "t0_epoch": 1.0,
          "camera_epoch_anchor": 2.0, "input_delta_epoch_perf": 3.0}
    cur = dict(fp, input_delta_epoch_perf=3.012)   # 切窗冻结 vs 全量的漂移量级
    assert mc._fingerprint_match(fp, cur) is True
    cur = dict(fp, input_delta_epoch_perf=3.5)     # 超容差：不同数据
    assert mc._fingerprint_match(fp, cur) is False


def test_fingerprint_rejects_different_recording():
    fp = {"source": "a.jsonl", "t0_epoch": 1.0,
          "camera_epoch_anchor": 2.0, "input_delta_epoch_perf": 3.0}
    assert mc._fingerprint_match(fp, dict(fp, t0_epoch=1.0 + 5.0)) is False
    assert mc._fingerprint_match(fp, dict(fp, camera_epoch_anchor=2.0 + 1.0)) is False
    assert mc._fingerprint_match(fp, dict(fp, source="b.jsonl")) is False


# ---------------- 端到端：同数据三跑两样（红→绿主用例） ----------------

def test_rewash_drift_in_band_degrades_not_rejects(tmp_path, monkeypatch):
    """cut-run51 形态：首跑 2.4° 过验 → 重洗漂到 6.3° 不得翻案成 exit=2。"""
    code, m1 = _run_merge(tmp_path, monkeypatch, swung_deg=6.3, drift_s=0.0)
    assert code == 0
    assert m1["alignment"]["accepted"] is True
    assert m1["alignment"]["accept_grade"] == "tracking_aim"
    assert m1["alignment"].get("verdict_reuse") is None

    _rewash(tmp_path, drift_s=0.6)                  # life 边界 +600ms ⇒ 6.3° 灰区
    round_dir, argv = _invoke_args(tmp_path)
    code, m2 = _invoke(round_dir, argv, monkeypatch)
    assert code == 0                                # 修复前：2（翻案）
    aln = m2["alignment"]
    assert aln["accepted"] is True
    assert aln["accept_grade"] == "tracking_aim_degraded"   # 降级可观测
    assert m2["aim_check"]["median_deg"] > mc.AIM_MEDIAN_MAX_DEG  # 漂移证据留痕


def _invoke_args(tmp_path):
    cleaned = tmp_path / "cleaned"
    round_dir = cleaned / STEM
    argv = ["merge_channels.py", "--round-dir", str(round_dir),
            "--camera", str(round_dir / "camera.jsonl"),
            "--input", str(round_dir / "input_log.jsonl"),
            "--year", str(YEAR)]
    return round_dir, argv


def test_rewash_hard_drift_reuses_prior_verdict(tmp_path, monkeypatch):
    """漂移超缓冲带（9.5°）但同源数据：复用首跑 verdict，不得翻案。"""
    code, m1 = _run_merge(tmp_path, monkeypatch, swung_deg=9.5, drift_s=0.0)
    assert code == 0
    assert m1["alignment"]["accept_grade"] == "tracking_aim"

    _rewash(tmp_path, drift_s=0.6)                  # ⇒ 9.5° > 带上界
    round_dir, argv = _invoke_args(tmp_path)
    monkeypatch.setattr(sys, "argv", argv)
    code = mc.main()
    assert code == 0                                # 修复前：2（翻案）
    m2 = json.loads((round_dir / "merge_manifest.json").read_text(encoding="utf-8"))
    aln = m2["alignment"]
    assert aln["accepted"] is True
    assert aln["accept_grade"] == "tracking_aim"    # 继承同源已过验 grade
    reuse = aln.get("verdict_reuse")
    assert reuse and reuse["prior_grade"] == "tracking_aim"
    assert m2["aim_check"]["median_deg"] > mc.AIM_MEDIAN_DEGRADED_MAX_DEG


def test_hard_drift_without_prior_still_fail_closed(tmp_path, monkeypatch):
    """无已过验前科（首跑即 9.5°）⇒ fail-closed 拒写旁车，exit=2。"""
    code, manifest = _run_merge(tmp_path, monkeypatch, swung_deg=9.5, drift_s=0.6)
    assert code == 2
    assert manifest is None


def test_hard_drift_different_recording_no_reuse(tmp_path, monkeypatch):
    """指纹不匹配（t0_epoch/相机锚都换了 = 另一局）⇒ 前科不适用，exit=2。"""
    code, m1 = _run_merge(tmp_path, monkeypatch, swung_deg=9.5, drift_s=0.0)
    assert code == 0
    # 重洗成另一局：t0_epoch +5s，相机/输入锚同步 +5s（cam_ts 不变，几何仍 9.5°）
    round_dir, argv = _invoke_args(tmp_path)
    _rewash(tmp_path, drift_s=0.6, t0_epoch=T0_EPOCH + 5.0)
    _shift_anchors(round_dir, 5.0)
    monkeypatch.setattr(sys, "argv", argv)
    code = mc.main()
    assert code == 2


def _shift_anchors(round_dir, shift_s):
    cam_path = round_dir / "camera.jsonl"
    rows = [json.loads(l) for l in cam_path.read_text(encoding="utf-8").splitlines() if l]
    for r in rows:
        if r.get("ev") == "clock_map":
            r["t"] += shift_s
    _write_jsonl(cam_path, rows)
    inp_path = round_dir / "input_log.jsonl"
    rows = [json.loads(l) for l in inp_path.read_text(encoding="utf-8").splitlines() if l]
    for r in rows:
        if r.get("ev") == "clock_map":
            r["t"] += shift_s
            r["t_unix"] += shift_s
    _write_jsonl(inp_path, rows)


def test_prior_manifest_lookup_prefers_same_dir(tmp_path):
    """同目录前科优先；incr/cut-run*/<stem>/ 下的已过验 manifest 也能命中。"""
    cleaned = tmp_path / "cleaned"
    fp = {"source": STEM + ".jsonl", "t0_epoch": T0_EPOCH,
          "camera_epoch_anchor": T0_EPOCH, "input_delta_epoch_perf": 0.0}
    cut_dir = cleaned / "incr" / "cut-run51-1791300000000" / STEM
    cut_dir.mkdir(parents=True)
    (cut_dir / "merge_manifest.json").write_text(json.dumps({
        "generated": "2026-10-06T12:00:00",
        "game_fingerprint": fp,
        "alignment": {"accepted": True, "accept_grade": "tracking_aim"},
    }), encoding="utf-8")
    hit = mc._find_prior_accepted_manifest(str(cleaned / STEM), fp)
    assert hit is not None and hit["prior_grade"] == "tracking_aim"
    other = dict(fp, t0_epoch=T0_EPOCH + 9.0)
    assert mc._find_prior_accepted_manifest(str(cleaned / STEM), other) is None


def test_prior_manifest_old_schema_fingerprint_synthesized(tmp_path):
    """存量前科（修复前的 manifest，无 game_fingerprint 键）也能复用。

    四元身份字段在 schema v1 里本就存在，据此合成——否则修复对全部存量
    已验收局无效（重放实证：152319 的 9R manifest 救活 54100 退化洗）。"""
    cleaned = tmp_path / "cleaned"
    round_dir = cleaned / STEM
    round_dir.mkdir(parents=True)
    (round_dir / "merge_manifest.json").write_text(json.dumps({
        "generated": "2026-10-05T15:24:00",
        "source": STEM + ".jsonl",
        "alignment": {"accepted": True, "accept_grade": "click_geom",
                      "t0_epoch_from_index": T0_EPOCH},
        "camera": {"epoch_anchor": T0_EPOCH},
        "input": {"delta_epoch_perf": 0.004},
    }), encoding="utf-8")
    fp = {"source": STEM + ".jsonl", "t0_epoch": T0_EPOCH,
          "camera_epoch_anchor": round(T0_EPOCH, 4),
          "input_delta_epoch_perf": 0.006}      # 切窗冻结 vs 全量的 delta 漂移
    hit = mc._find_prior_accepted_manifest(str(round_dir), fp)
    assert hit is not None and hit["prior_grade"] == "click_geom"


def test_rejected_prior_manifest_not_reused(tmp_path):
    """被拒的 manifest 不是前科（exit=2 本来就不写 manifest，防御双保险）。"""
    round_dir = tmp_path / "cleaned" / STEM
    round_dir.mkdir(parents=True)
    (round_dir / "merge_manifest.json").write_text(json.dumps({
        "alignment": {"accepted": False, "accept_grade": None},
    }), encoding="utf-8")
    fp = {"source": STEM + ".jsonl", "t0_epoch": T0_EPOCH,
          "camera_epoch_anchor": T0_EPOCH, "input_delta_epoch_perf": 0.0}
    assert mc._find_prior_accepted_manifest(str(round_dir), fp) is None
