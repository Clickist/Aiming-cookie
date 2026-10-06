# -*- coding: utf-8 -*-
"""_freeze_windowed_jsonl 增量读等价性验收（[perf C] 2026-10-06）。

红线：切窗产物与现有全量读法字节级等价、遥测零丢失、任何回退可观测。

两层：
a. 特征化测试：锁现全量扫描行为快照——锚取文件首部、clock_map 行无条件全写、
   完整坏行 break（其后永不可达）、撕裂尾行丢弃、窗口过滤、target/camera/input
   三通道差异、坏行/非 dict/缺字段行的跳过语义。
b. 等价性测试：合成 JSONL + 随机化切窗序列（相邻局、乱序局、同窗重切、撕裂
   尾行补全、完整坏行、多 clock_map、模拟服务重启清状态），断言增量输出与
   全量参考实现（现算法逐字拷贝）输出 sha256 相等；fallback 计数在乱序/坏行
   场景正确递增、在良好顺序切窗下零误报。

参考实现 ``_reference_full_scan`` 是改造前全量算法的逐字拷贝，禁止改动。
"""
import hashlib
import json
import random
from pathlib import Path

import webapp.backend.telemetry_capture_service as tcs

T0 = 1_760_000_000.0        # 会话附着的绝对纪元锚（clock_map 的 t）
ADDR = 0x7FF600000001
CHANNELS = ("target", "camera", "input")


def _reset_state():
    """模拟服务重启（清进程级切窗记账）；旧版实现无状态时为 no-op。"""
    state = getattr(tcs, "_FREEZE_STATE", None)
    if state is not None:
        state.clear()


def _fallbacks():
    getter = getattr(tcs, "_freeze_full_scan_fallbacks", None)
    return getter() if getter else 0


def _reference_full_scan(src, dst, channel, lo_epoch_s, hi_epoch_s):
    """现全量实现的逐字拷贝（2026-10-06 增量改造前）。"""
    kept = 0
    anchor: float | None = None
    first_delta: float | None = None
    try:
        with src.open("r", encoding="utf-8", errors="replace") as source, \
                dst.open("w", encoding="utf-8") as target:
            for line in source:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except ValueError:
                    break  # 采集进程写了一半的尾行：到此为止
                if not isinstance(record, dict):
                    continue
                # 单行坏记录（缺字段/非数值）只跳过该行，绝不中断整个冻结。
                try:
                    ev = record.get("ev")
                    if channel == "input":
                        if ev == "clock_map":
                            if first_delta is None:
                                first_delta = (
                                    float(record["t_unix"]) - float(record["t"])
                                )
                            target.write(stripped + "\n")
                            kept += 1
                            continue
                        if ev != "m":
                            continue
                        base = first_delta
                    elif channel in {"target", "camera"}:
                        if ev == "clock_map":
                            if anchor is None:
                                anchor = float(record["t"])
                            target.write(stripped + "\n")
                            kept += 1
                            continue
                        # death 行（目标死亡边沿，flag 路径）与 frame 同域同锚，放行
                        if channel == "target" and ev not in (None, "frame", "death"):
                            continue
                        if channel == "camera" and ev != "cam":
                            continue
                        base = anchor
                    else:
                        continue
                    if base is not None and \
                            lo_epoch_s <= base + float(record["t"]) <= hi_epoch_s:
                        target.write(stripped + "\n")
                        kept += 1
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return kept
    return kept


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _freeze(src, dst, channel, lo, hi):
    return tcs._freeze_windowed_jsonl(Path(src), Path(dst), channel, lo, hi)


def _write_lines(path: Path, lines, torn_last=False):
    """追加写入；torn_last 时最后一行不带换行（模拟采集进程写一半）。"""
    with open(path, "a", encoding="utf-8", newline="") as f:
        for i, line in enumerate(lines):
            if torn_last and i == len(lines) - 1:
                f.write(line)
            else:
                f.write(line + "\n")


def _complete_torn(path: Path, line: str):
    """把上一轮的撕裂尾行补全（追加剩余半行 + 换行），模拟写入完成。"""
    half = len(line) // 2
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(line[half:] + "\n")


# ---------------------------------------------------------------- 特征化快照

def test_characterize_target_exact_bytes(tmp_path):
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        json.dumps({"ev": "frame", "t": 5.0, "targets": []}),          # 窗外（早）
        json.dumps({"ev": "frame", "t": 15.0, "targets": [[ADDR, 1, 2, 3]]}),
        json.dumps({"ev": "hud", "t": 16.0}),                          # target 必跳过
        json.dumps({"ev": "death", "t": 17.0}),                        # 放行
        json.dumps({"t": 18.0}),                                       # 无 ev：放行
        json.dumps({"ev": "frame", "t": 25.0, "targets": []}),         # 窗外（晚）
        json.dumps({"ev": "frame", "t": 95.0, "targets": []}),         # 远窗外
        json.dumps({"ev": "clock_map", "t": T0 + 60.0}),               # 无条件写
    ]
    src = tmp_path / "target_poll_out_x.jsonl"
    _write_lines(src, lines)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "target", T0 + 10.0, T0 + 20.0)
    expected = "".join(lines[i] + "\n" for i in (0, 2, 4, 5, 8))
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 5


def test_characterize_camera_only_cam_lines(tmp_path):
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        json.dumps({"ev": "cam", "t": 12.0, "path": "a.jpg"}),
        json.dumps({"ev": "frame", "t": 13.0, "targets": []}),   # camera 必跳过
        json.dumps({"ev": "cam", "t": 19.0, "path": "b.jpg"}),
        json.dumps({"ev": "cam", "t": 21.0, "path": "c.jpg"}),   # 窗外
        json.dumps({"ev": "cam", "t": 88.0, "path": "d.jpg"}),   # 窗外
    ]
    src = tmp_path / "camera_probe_out_x.jsonl"
    _write_lines(src, lines)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "camera", T0 + 10.0, T0 + 20.0)
    expected = "".join(lines[i] + "\n" for i in (0, 1, 3))
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 3


def test_characterize_input_first_delta_and_all_clock_maps(tmp_path):
    # 锚 = 首个 clock_map 的 delta（t_unix - t）；后续 clock_map 只无条件写出、
    # 不参与过滤。m15 在首个 delta 下落在窗内（若错用第二个 delta 的锚则窗外）。
    lines = [
        json.dumps({"ev": "clock_map", "qpc": 1, "t": 0.0, "t_unix": T0}),
        json.dumps({"ev": "m", "t": 5.0, "qpc": 2, "dx": 3, "dy": -1, "btn": []}),
        json.dumps({"ev": "m", "t": 15.0, "qpc": 3, "dx": 1, "dy": 0, "btn": ["L_down"]}),
        json.dumps({"ev": "m", "t": 25.0, "qpc": 4, "dx": 0, "dy": 2, "btn": []}),
        json.dumps({"ev": "x", "t": 16.0}),                      # 非 m：跳过
        json.dumps({"ev": "clock_map", "qpc": 5, "t": 30.0, "t_unix": T0 + 1030.0}),
        json.dumps({"ev": "m", "t": 35.0, "qpc": 6, "dx": 9, "dy": 9, "btn": []}),
    ]
    src = tmp_path / "input_log.jsonl"
    _write_lines(src, lines)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "input", T0 + 10.0, T0 + 20.0)
    expected = "".join(lines[i] + "\n" for i in (0, 2, 5))
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 3


def test_characterize_complete_bad_line_breaks_unreachable(tmp_path):
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        json.dumps({"ev": "frame", "t": 12.0, "targets": []}),
        '{"ev": "frame", "t": 13.0, "targets": [',   # 完整带换行的坏行 → break
        json.dumps({"ev": "frame", "t": 14.0, "targets": []}),   # 永不可达
    ]
    src = tmp_path / "target_poll_out_bad.jsonl"
    _write_lines(src, lines)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "target", T0 - 100.0, T0 + 100.0)
    expected = lines[0] + "\n" + lines[1] + "\n"
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 2


def test_characterize_torn_tail_and_recoverable_lines(tmp_path):
    # 撕裂尾行（坏 JSON）丢弃；空行/合法非 dict/缺字段行跳过但不中断。
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        "",                                                   # 空行跳过
        "123",                                                # 合法非 dict 跳过
        json.dumps({"ev": "frame"}),                          # 缺 t：跳过
        json.dumps({"ev": "frame", "t": "abc", "targets": []}),  # 非数值：跳过
        json.dumps({"ev": "frame", "t": 12.0, "targets": []}),
        '{"ev": "frame", "t": 13.',                           # 撕裂尾行：丢弃
    ]
    src = tmp_path / "target_poll_out_torn.jsonl"
    _write_lines(src, lines, torn_last=True)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "target", T0 + 10.0, T0 + 20.0)
    expected = lines[0] + "\n" + lines[5] + "\n"
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 2


def test_characterize_torn_tail_valid_json_still_processed(tmp_path):
    # 撕裂恰好切在完整 JSON 之后（内容合法、只缺换行）：全量读法会正常处理它。
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        json.dumps({"ev": "frame", "t": 12.0, "targets": []}),
    ]
    src = tmp_path / "target_poll_out_torn2.jsonl"
    _write_lines(src, lines, torn_last=True)
    dst = tmp_path / "frozen.jsonl"
    _reset_state()
    kept = _freeze(src, dst, "target", T0 + 10.0, T0 + 20.0)
    expected = lines[0] + "\n" + lines[1] + "\n"
    assert dst.read_text(encoding="utf-8") == expected
    assert kept == 2


def test_characterize_empty_and_clockmap_only(tmp_path):
    src = tmp_path / "target_poll_out_empty.jsonl"
    src.write_text("", encoding="utf-8")
    dst = tmp_path / "frozen1.jsonl"
    _reset_state()
    assert _freeze(src, dst, "target", T0, T0 + 10.0) == 0
    assert dst.read_bytes() == b""
    src2 = tmp_path / "target_poll_out_cm.jsonl"
    _write_lines(src2, [json.dumps({"ev": "clock_map", "t": T0})])
    dst2 = tmp_path / "frozen2.jsonl"
    kept = _freeze(src2, dst2, "target", T0 + 100.0, T0 + 200.0)  # 窗外
    assert kept == 1      # clock_map 无条件保留（现行为）
    assert dst2.read_text(encoding="utf-8") == \
        json.dumps({"ev": "clock_map", "t": T0}) + "\n"


# ---------------------------------------------------------------- 等价性

def _clock_map_target(t_rel):
    return json.dumps({"ev": "clock_map", "t": T0 + t_rel})


def _clock_map_input(t_rel, drift_us):
    return json.dumps({"ev": "clock_map", "qpc": int(t_rel * 1e6),
                       "t": t_rel, "t_unix": T0 + t_rel + drift_us / 1e6})


def _run_lines(rng, run_start_rel, run_end_rel, with_bad_line):
    """生成一局的（target, camera, input）原始行。时间相对 T0 单调递增。"""
    target, camera, inputs = [], [], []
    t = run_start_rel
    while t < run_end_rel:
        t = round(t, 3)
        jitter = rng.uniform(-0.01, 0.01)
        target.append(json.dumps({
            "ev": "frame", "t": t,
            "targets": [[ADDR, 500.0 + t, 500.0, 100.0]]}))
        if rng.random() < 0.05:
            target.append(json.dumps({"ev": "death", "t": t + jitter}))
        if rng.random() < 0.05:
            target.append(json.dumps({"t": t + jitter, "fps": 240}))   # 无 ev 放行
        if rng.random() < 0.05:
            target.append(json.dumps({"ev": "hud", "t": t + jitter}))  # 跳过
        camera.append(json.dumps({
            "ev": "cam", "t": t + jitter, "path": f"f{int(t*1000)}.jpg"}))
        btn = ["L_down"] if rng.random() < 0.1 else []
        inputs.append(json.dumps({
            "ev": "m", "t": t + jitter, "qpc": int(t * 1e6),
            "dx": rng.randint(-30, 30), "dy": rng.randint(-30, 30), "btn": btn}))
        if with_bad_line and rng.random() < 0.02:
            # 完整坏行（带换行）：全量在此 break，其后永不可达——两种读法
            # 必须同样止步。
            target.append('{"ev": "frame", "t": %s, "targets": [' % t)
        t += 0.5
    target.append(_clock_map_target(run_end_rel))
    camera.append(_clock_map_target(run_end_rel + 0.01))
    inputs.append(_clock_map_input(run_end_rel + 0.02, drift_us=rng.randint(0, 5000)))
    return target, camera, inputs


def _gen_runs(rng):
    runs = []
    cursor = rng.uniform(5.0, 30.0)          # 附着领先量
    for _ in range(rng.randint(4, 7)):
        dur = rng.uniform(15.0, 45.0)
        start, end = cursor, cursor + dur
        runs.append((start, end))
        cursor = end + rng.choice([2.0, 6.0, 12.0, 18.0, 40.0])  # <10s 会触发回退
    return runs


def _gen_events(rng, runs):
    """生成 (kind, *args) 事件序列：按局生成+切，含撕裂尾行（切→补全→再切）、
    同窗重切、乱序局、服务重启。"""
    events = []
    for i in range(len(runs)):
        torn = rng.random() < 0.3
        events.append(("gen_run", i, torn))
        events.append(("cut", i))                    # 撕裂在途即切（若 torn）
        if torn:
            events.append(("complete_tail", i))
            events.append(("cut", i))                # 补全后重切（含回读半行）
        if rng.random() < 0.3:
            events.append(("cut", i))                # 同窗重切（守卫回退）
        if rng.random() < 0.3:
            events.append(("restart",))              # 模拟服务重启清状态
    # 乱序段：先切时间上更晚的一局，再切更早的一局（守卫回退全量）
    if len(runs) >= 2:
        a, b = sorted(rng.sample(range(len(runs)), 2), reverse=True)
        events.append(("cut", a))
        events.append(("cut", b))
    return events


def _seed_initial_clock_maps(trees):
    """每棵树播种首行 clock_map（附着即写，锚行）。"""
    for ch in CHANNELS:
        ref, incr = trees[ch]
        if ch == "input":
            line = json.dumps({"ev": "clock_map", "qpc": 0, "t": 0.0, "t_unix": T0})
        else:
            line = json.dumps({"ev": "clock_map", "t": T0})
        _write_lines(ref, [line])
        _write_lines(incr, [line])


def _assert_cut_equal(tmp_path, trees, channel, run):
    src_ref, src_incr = trees[channel]
    start_rel, end_rel = run
    lo = T0 + start_rel - 10.0
    hi = T0 + end_rel + 5.0
    if channel == "input":
        lo -= 15.0
        hi += 15.0
    dst_ref = tmp_path / f"dst_ref_{channel}.jsonl"
    dst_incr = tmp_path / f"dst_incr_{channel}.jsonl"
    kept_ref = _reference_full_scan(src_ref, dst_ref, channel, lo, hi)
    kept_incr = _freeze(src_incr, dst_incr, channel, lo, hi)
    sha_ref = _sha256(dst_ref)
    sha_incr = _sha256(dst_incr)
    assert sha_ref == sha_incr, (
        f"{channel} 切窗 [{lo},{hi}] 增量输出与全量参考不等\n"
        f"ref  ({dst_ref.stat().st_size}B): {sha_ref}\n"
        f"incr ({dst_incr.stat().st_size}B): {sha_incr}")
    assert kept_incr == kept_ref, f"{channel} kept 不等: {kept_incr} != {kept_ref}"


def _run_scenario(tmp_path, seed, with_bad_line):
    rng = random.Random(seed)
    names = {"target": "target_poll_out_s.jsonl",
             "camera": "camera_probe_out_s.jsonl",
             "input": "input_log.jsonl"}
    trees = {ch: (tmp_path / ("ref_" + names[ch]),
                  tmp_path / ("incr_" + names[ch])) for ch in CHANNELS}
    _seed_initial_clock_maps(trees)
    runs = _gen_runs(rng)
    for event in _gen_events(rng, runs):
        kind = event[0]
        if kind == "gen_run":
            i, torn = event[1], event[2]
            start_rel, end_rel = runs[i]
            lines = _run_lines(rng, start_rel, end_rel, with_bad_line)
            for ch, ch_lines in zip(CHANNELS, lines):
                last = ch_lines[-1]
                body = ch_lines[:-1]
                if torn:
                    body.append(last[:len(last) // 2])
                _write_lines(trees[ch][0], body, torn_last=torn)
                _write_lines(trees[ch][1], body, torn_last=torn)
            if torn:
                _torn_last_lines.update(
                    {ch: ch_lines[-1] for ch, ch_lines in zip(CHANNELS, lines)})
        elif kind == "complete_tail":
            # 补全只需字节本身：gen_run 撕裂时暂存的各通道尾行原文。
            for ch, last in _torn_last_lines.items():
                _complete_torn(trees[ch][0], last)
                _complete_torn(trees[ch][1], last)
        elif kind == "cut":
            for ch in CHANNELS:
                _assert_cut_equal(tmp_path, trees, ch, runs[event[1]])
        elif kind == "restart":
            _reset_state()


# gen_run 与 complete_tail 之间共享的撕裂尾行原文（按通道）。
_torn_last_lines: dict[str, str] = {}


def test_equivalence_random_scenarios(tmp_path):
    # 随机化切窗序列：相邻局（多种间距，含会触发守卫回退的 <10s 间距）、
    # 撕裂尾行切→补全→重切、同窗重切、乱序局、多 clock_map、服务重启清状态。
    for seed in (20261006, 42, 777, 20261007, 999):
        _torn_last_lines.clear()
        _reset_state()
        _run_scenario(tmp_path, seed, with_bad_line=False)
        _reset_state()


def test_equivalence_with_complete_bad_lines(tmp_path):
    # 完整坏行毒化文件：两种读法同样 break（其后不可达），增量永不多读。
    for seed in (31337, 8888):
        _torn_last_lines.clear()
        _reset_state()
        _run_scenario(tmp_path, seed, with_bad_line=True)
        _reset_state()


def test_fallback_counter_out_of_order_increments(tmp_path):
    _reset_state()
    names = {"target": "target_poll_out_oo.jsonl",
             "camera": "camera_probe_out_oo.jsonl",
             "input": "input_log.jsonl"}
    trees = {ch: (tmp_path / ("ref_" + names[ch]),
                  tmp_path / ("incr_" + names[ch])) for ch in CHANNELS}
    _seed_initial_clock_maps(trees)
    rng = random.Random(7)
    runs = [(0.0, 30.0), (100.0, 130.0)]
    # 先生成两局数据，再先切时间上更晚的 run1、后切 run0（乱序 → 守卫回退）
    for i in (0, 1):
        for ch, ch_lines in zip(CHANNELS, _run_lines(rng, *runs[i], False)):
            _write_lines(trees[ch][0], ch_lines)
            _write_lines(trees[ch][1], ch_lines)
    before = _fallbacks()
    for ch in CHANNELS:
        _assert_cut_equal(tmp_path, trees, ch, runs[1])
    after_run1 = _fallbacks()
    for ch in CHANNELS:
        _assert_cut_equal(tmp_path, trees, ch, runs[0])
    assert _fallbacks() == after_run1 + 3   # 三通道各回退一次
    assert after_run1 >= before


def test_fallback_counter_poisoned_increments_on_later_cuts(tmp_path):
    _reset_state()
    before = _fallbacks()
    src = tmp_path / "target_poll_out_p.jsonl"
    lines = [
        json.dumps({"ev": "clock_map", "t": T0}),
        json.dumps({"ev": "frame", "t": 12.0, "targets": []}),
        '{"ev": "frame", "t": 13.0, "targets": [',   # 完整坏行 → 毒化
        json.dumps({"ev": "frame", "t": 14.0, "targets": []}),
    ]
    _write_lines(src, lines)
    dst1 = tmp_path / "f1.jsonl"
    kept1 = _freeze(src, dst1, "target", T0 - 100.0, T0 + 100.0)
    first_cut = _fallbacks()
    assert kept1 == 2
    # 首扫发现毒化不算回退；之后的每次切窗都是全量回退
    dst2 = tmp_path / "f2.jsonl"
    kept2 = _freeze(src, dst2, "target", T0 - 100.0, T0 + 100.0)
    assert kept2 == kept1
    assert _sha256(dst1) == _sha256(dst2)
    assert _fallbacks() == first_cut + 1
    assert first_cut >= before


def test_fallback_counter_no_false_positive_ordered(tmp_path):
    # 良好顺序（局间距 >10s）的增量切窗不得回退。
    _reset_state()
    base = _fallbacks()
    src = tmp_path / "target_poll_out_ok.jsonl"
    dst = tmp_path / "frozen_ok.jsonl"
    rng = random.Random(11)
    _write_lines(src, [json.dumps({"ev": "clock_map", "t": T0})])
    for start, end in ((10.0, 40.0), (60.0, 90.0), (110.0, 140.0)):
        lines = _run_lines(rng, start, end, False)[0]
        _write_lines(src, lines)
        kept = _freeze(src, dst, "target", T0 + start - 10.0, T0 + end + 5.0)
        assert kept > 0
    assert _fallbacks() == base
