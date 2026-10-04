# -*- coding: utf-8 -*-
"""test_poll_perf.py — target_poll2.py 提速离线自测（不需要游戏运行）。

做法：用一个内存假进程（duck-type tp1.Proc 的读接口：read/u8/u32/i32/u64/f32，
读不到返回 None 的语义一致）构造带 GUObjectArray 布局的合成内存；calibrate() 的
产物 cal 手工组装（校准阶段依赖 FNamePool，不属于"采样循环"，不在 mock 范围），
然后调用真实的 target_poll2.run() 采样循环——含混合等待调度、后台差分/重扫线程、
serial 摘除、12B 批量平移读——跑 ~10 秒，断言：

  ① 帧率 ≥200Hz（以 220Hz 名义跑，留 10% 余量证明 "200Hz+"）
  ② 输出 schema 与现有格式逐字节兼容（clock_map + frame 行，json.dumps 往返一致；
     目标行内容与合成内存真值一致，含运行中动态发现的新目标）
  ③ 后台重扫/差分不阻塞采样（2 万槽重扫与差分种子在采样期间完成，dt 无长停顿）

中断方式：假进程在 sampler 线程于 RUN_SECS 后首次读内存时抛 KeyboardInterrupt，
借 run() 原生的 Ctrl+C 退出路径干净收尾（同生产退出语义）。

用法: python test_poll_perf.py
"""
import bisect
import json
import os
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tp1 as t             # noqa: E402
import target_poll2 as tp2  # noqa: E402

HZ = 220            # 名义 220Hz，断言 ≥200Hz（10% 余量）
RUN_SECS = 10.2     # 采样线程在此刻后首次读内存时收到 KeyboardInterrupt
NUM_ITEMS = 20000   # 对象数组槽数：给差分种子/重扫真实工作量（~0.5MB/轮）
BASE = 0x7FF600000000
HUD_CLS = 0xAAAA0001
NEUTRAL_CLS = 0xBBBB0002
CHUNK = BASE + 0x200000
NEW_OWNER = BASE + 0x304000
NEW_COMP = BASE + 0x404000
NEW_XYZ = (1960.5, 1320.0, 855.0)


def f32(v):
    """float32 语义下的值（采样读回的是 f32，期望值必须同域比较）。"""
    return struct.unpack("<f", struct.pack("<f", v))[0]


class FakeProc:
    """内存假进程：稀疏段表 + 与 tp1.Proc 相同的读接口语义。锁保护段表
    （差分/重扫/变异线程与采样线程并发访问）。"""

    def __init__(self):
        self.base = BASE
        self.t0 = time.time()
        self.sampler_ident = None
        self.lock = threading.Lock()
        self.segs = []    # [(start, bytes)] 按 start 排序、互不重叠
        self.starts = []

    def _put(self, addr, data):
        i = bisect.bisect_left(self.starts, addr)
        self.starts.insert(i, addr)
        self.segs.insert(i, (addr, bytes(data)))

    def w(self, addr, data):          # 新段（运行中动态出现的新对象用）
        with self.lock:
            self._put(addr, data)

    def w_mut(self, addr, data):      # 原地改写既有段内字节（模拟游戏写内存）
        with self.lock:
            i = bisect.bisect_right(self.starts, addr) - 1
            assert i >= 0, hex(addr)
            s0, b = self.segs[i]
            assert s0 <= addr and addr + len(data) <= s0 + len(b), hex(addr)
            o = addr - s0
            self.segs[i] = (s0, b[:o] + bytes(data) + b[o + len(data):])

    def read(self, addr, n):
        if (self.sampler_ident == threading.get_ident()
                and time.time() - self.t0 > RUN_SECS):
            raise KeyboardInterrupt   # 借 run() 原生 Ctrl+C 路径收尾
        with self.lock:
            i = bisect.bisect_right(self.starts, addr) - 1
            if i < 0:
                return None
            s0, b = self.segs[i]
            if s0 <= addr and addr + n <= s0 + len(b):
                o = addr - s0
                return b[o:o + n]
        return None

    def u8(self, a):  d = self.read(a, 1);  return d[0] if d else None
    def u32(self, a): d = self.read(a, 4);  return struct.unpack("<I", d)[0] if d else None
    def i32(self, a): d = self.read(a, 4);  return struct.unpack("<i", d)[0] if d else None
    def u64(self, a): d = self.read(a, 8);  return struct.unpack("<Q", d)[0] if d else None
    def f32(self, a): d = self.read(a, 4);  return struct.unpack("<f", d)[0] if d else None


def build_world(p):
    """布置 GUObjectArray 头 + 对象数组 chunk + 3 个采样目标 actor/component。
    布局判定走真实的 tp1.chunk_layout / tp1.make_item_addr（在假内存上解析）。"""
    guoa = p.base + t.RVA_GUOBJECTARRAY
    chunklist = p.base + 0x100000
    # chunk_layout 从 guoa+0x10 解读：chunkptr@+0x10, maxe@+0x20, nume@+0x24,
    # maxc@+0x28, numc@+0x2C —— read_num 读的 +0x24/+0x2C 与此同址（生产代码语义）
    hdr = bytearray(0x30)
    struct.pack_into("<Q", hdr, 0x10, chunklist)
    struct.pack_into("<i", hdr, 0x20, 65536)      # maxe → per_chunk=65536
    struct.pack_into("<i", hdr, 0x24, NUM_ITEMS)  # nume
    struct.pack_into("<i", hdr, 0x28, 1)          # maxc
    struct.pack_into("<i", hdr, 0x2C, 1)          # numc
    p.w(guoa, hdr)
    p.w(chunklist, struct.pack("<Q", CHUNK))

    blob = bytearray(NUM_ITEMS * 0x18)            # item stride 0x18

    def slot(i, ptr, serial):
        struct.pack_into("<Q", blob, i * 0x18, ptr)
        struct.pack_into("<I", blob, i * 0x18 + 0x10, serial)

    # owner/comp 段各长 0x140/0x200 字节，间隔拉到 0x1000 保证段互不重叠
    owners = [BASE + 0x301000, BASE + 0x302000, BASE + 0x303000]
    comps = [BASE + 0x401000, BASE + 0x402000, BASE + 0x403000]
    coords = [(1953.7, 1313.2, 850.2), (1954.7, 1315.2, 853.2),
              (1955.7, 1317.2, 856.2)]
    for k, (ow, cp, xyz) in enumerate(zip(owners, comps, coords)):
        slot(10 + k, ow, 100 + k)
        actor = bytearray(0x140)                  # actor：+0x10 类、+0x130 RootComponent
        struct.pack_into("<Q", actor, 0x10, NEUTRAL_CLS)
        struct.pack_into("<Q", actor, 0x130, cp)
        p.w(ow, actor)
        comp = bytearray(0x200)                   # RootComponent：+0x1c0 FTransform
        struct.pack_into("<4f", comp, 0x1c0, 0.0, 0.0, 0.0, 1.0)  # quat
        struct.pack_into("<3f", comp, 0x1c0 + 16, *xyz)           # 平移
        p.w(cp, comp)
    for i in range(NUM_ITEMS):                    # 其余槽：有 ptr 无段（类读 → None）
        if 10 <= i < 13:
            continue
        slot(i, BASE + 0x1000000 + i * 0x100, 7)
    p.w(CHUNK, bytes(blob))

    item_addr = t.make_item_addr(p)               # 真实布局解析（假内存）
    cal = {"targets": {10: owners[0], 11: owners[1], 12: owners[2]},
           "item_addr": item_addr,
           "root_off": 0x130, "xform_off": 0x1c0,
           "hud": HUD_CLS, "owner_classes": {}, "last_index": NUM_ITEMS,
           "tmt": None, "tmt_cdo": None,
           "blocks_rt": BASE + 0x700000,
           "hud_set": {HUD_CLS}, "family_roots": [], "family_set": set()}
    return cal, owners, coords


def mutate_later(p):
    """t≈4s：槽 5000 换成新的 TargetHudComponent（owner=新 actor）——
    模拟游戏中途出现新目标，验证差分线程真实发现路径（HUD → OwnerPrivate）。"""
    obj4 = BASE + 0x500400
    while time.time() - p.t0 < 4.0:
        time.sleep(0.05)
    hud = bytearray(0x30)                         # HUD 组件：类=HUD_CLS、owner=actor
    struct.pack_into("<Q", hud, 0x10, HUD_CLS)
    struct.pack_into("<Q", hud, 0x20, NEW_OWNER)
    actor = bytearray(0x140)
    struct.pack_into("<Q", actor, 0x10, NEUTRAL_CLS)
    struct.pack_into("<Q", actor, 0x130, NEW_COMP)
    comp = bytearray(0x200)
    struct.pack_into("<4f", comp, 0x1c0, 0.0, 0.0, 0.0, 1.0)
    struct.pack_into("<3f", comp, 0x1c0 + 16, *NEW_XYZ)
    # 顺序重要：先写对象段再改槽位（差分看到新槽位时对象必须已可读）
    p.w(obj4, hud)
    p.w(NEW_OWNER, actor)
    p.w(NEW_COMP, comp)
    p.w_mut(CHUNK + 5000 * 0x18, struct.pack("<Q", obj4))
    p.w_mut(CHUNK + 5000 * 0x18 + 0x10, struct.pack("<I", 88))


class _Capture:
    """线程安全的 stdout 捕获（run/差分/重扫多线程都在 print）。"""

    def __init__(self):
        self.buf = []
        self.lock = threading.Lock()

    def write(self, s):
        with self.lock:
            self.buf.append(s)

    def flush(self):
        pass

    def text(self):
        with self.lock:
            return "".join(self.buf)


def main():
    # time.time() 粒度探测：部分 Windows CPython 的 time.time 分辨率粗（1/15.6ms），
    # 会让 dt 统计退化（真实录制器同样受限）；粒度粗时只做帧数/速率/内容断言。
    probe = []
    pc_end = time.perf_counter() + 0.25
    while time.perf_counter() < pc_end:
        v = time.time()
        if not probe or v != probe[-1]:
            probe.append(v)
        time.sleep(0.001)
    dprobe = [b - a for a, b in zip(probe, probe[1:])]
    gran = min(dprobe) if dprobe else 1.0
    fine_clock = gran < 0.002
    print("[test] time.time 粒度 ≈ %.4f ms → %s"
          % (gran * 1e3, "dt 统计有效" if fine_clock else "dt 统计退化，跳过 dt 断言"))

    out_dir = tempfile.mkdtemp(prefix="poll_perf_")
    p = FakeProc()
    cal, owners, coords = build_world(p)
    # [flags 2026-10-05] 无标志位世界：hp/dc 恒 None（6 列条目）
    expect3 = [[owners[k], f32(coords[k][0]), f32(coords[k][1]), f32(coords[k][2]),
                None, None] for k in range(3)]
    expect4 = expect3 + [[NEW_OWNER, f32(NEW_XYZ[0]), f32(NEW_XYZ[1]),
                          f32(NEW_XYZ[2]), None, None]]

    cap = _Capture()
    result = {}
    old_stdout = sys.stdout
    sys.stdout = cap
    try:
        def sampler_body():
            p.sampler_ident = threading.get_ident()
            try:
                tp2.run(p, cal, HZ, False, out_dir)
            except BaseException as e:            # 跨线程取回意外异常
                result["err"] = e

        th = threading.Thread(target=sampler_body, name="sampler", daemon=True)
        th.start()
        mut = threading.Thread(target=mutate_later, args=(p,), name="mutator",
                               daemon=True)
        mut.start()
        th.join(timeout=RUN_SECS + 30)
    finally:
        sys.stdout = old_stdout

    assert "err" not in result, "采样循环抛出意外异常: %r" % (result.get("err"),)
    assert not th.is_alive(), "采样线程超时未退出"
    text = cap.text()

    def _fail(msg):
        raise AssertionError(msg + "\n---- 采样窗口捕获输出 ----\n" + text)

    if "[rescan] 后台完成" not in text:
        _fail("重扫线程未在采样窗口内完成一轮")
    if "+新目标(HUD)" not in text:
        _fail("差分线程未通过 chunk 差分发现动态新目标")
    if "中断等待重附着" in text or "本轮失败" in text:
        _fail("后台线程出现失败/熔断")

    out_files = [fn for fn in os.listdir(out_dir) if fn.endswith(".jsonl")]
    assert len(out_files) == 1, out_files
    path = os.path.join(out_dir, out_files[0])
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines, "输出为空"

    # ---- ② schema：clock_map + 逐帧字节级校验 ----
    cm = json.loads(lines[0])
    assert cm["ev"] == "clock_map" and isinstance(cm["t"], float) and "note" in cm
    # [flags 2026-10-05] mock 世界无反射机制 → flag_reflection=unavailable（诚实降级）
    assert cm.get("flag_reflection") == "unavailable"
    assert lines[0] == json.dumps(cm), "clock_map 非 json.dumps 规范形"
    frames = []
    for ln in lines[1:]:
        o = json.loads(ln)
        assert set(o) == {"ev", "t", "targets"}, "frame 行键集不符: %r" % o
        assert o["ev"] == "frame"
        assert isinstance(o["t"], float)
        assert ln == json.dumps(o), "frame 行非 json.dumps 规范形（字节级不兼容）"
        for tg in o["targets"]:
            # [flags 2026-10-05] targets 条目 [ptr,x,y,z,hp,dc]；mock 无标志位
            # → hp/dc 为 None（回退语义，cleaner/merge 只显式取 e[0..3] 不受影响）
            assert len(tg) == 6, tg
            assert isinstance(tg[0], int) and not isinstance(tg[0], bool)
            for v in tg[1:4]:
                assert isinstance(v, float)
            assert tg[4] is None and tg[5] is None, tg
        frames.append(o)
    assert frames, "无 frame 行"

    # ---- 内容正确性：逐帧目标集合与合成内存真值一致、4 目标切换只发生一次 ----
    n3 = sum(1 for fr in frames if fr["targets"] == expect3)
    n4 = sum(1 for fr in frames if fr["targets"] == expect4)
    assert n3 + n4 == len(frames), "存在与真值不符的帧（错读！）"
    first4 = next(i for i, fr in enumerate(frames) if len(fr["targets"]) == 4)
    assert all(len(fr["targets"]) == 3 for fr in frames[:first4])
    assert all(len(fr["targets"]) == 4 for fr in frames[first4:])
    assert first4 >= 1 and len(frames) - first4 >= 100, (first4, len(frames))

    # ---- ① 帧率 + ③ 无阻塞 ----
    ts = [fr["t"] for fr in frames]
    assert all(b >= a for a, b in zip(ts, ts[1:])), "t 非单调"
    span = ts[-1] - ts[0]
    rate = (len(frames) - 1) / span
    print("[test] out=%s" % path)
    print("[test] frames=%d (3目标 %d 帧 + 4目标 %d 帧) span=%.3fs"
          % (len(frames), n3, n4, span))
    print("[test] 帧率 = %.1f Hz（名义 %dHz，断言 ≥200Hz）" % (rate, HZ))
    assert len(frames) >= HZ * 9, "帧数不足: %d" % len(frames)
    assert rate >= 200.0, "帧率 %.1fHz < 200Hz" % rate
    if fine_clock:
        d = sorted(b - a for a, b in zip(ts, ts[1:]))
        med, p95, mx = d[len(d) // 2], d[int(len(d) * 0.95)], d[-1]
        print("[test] dt median=%.3fms p95=%.3fms max=%.3fms"
              % (med * 1e3, p95 * 1e3, mx * 1e3))
        assert med <= 1.0 / 200 + 0.0005, "dt 中位 %.3fms — 节拍未达 200Hz" % (med * 1e3)
        assert p95 <= 1.0 / 200 + 0.0015, "dt p95 %.3fms" % (p95 * 1e3)
        assert mx <= 0.030, ("最大帧停顿 %.1fms — 差分/重扫疑似阻塞采样"
                             % (mx * 1e3))
    print("[PASS] ①帧率≥200Hz ②schema字节级兼容+内容真值一致 ③重扫/差分不阻塞 —— 全部通过")


if __name__ == "__main__":
    main()
