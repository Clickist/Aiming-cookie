# -*- coding: utf-8 -*-
"""names.find_blocks / find_blocks_at / is_block_ptr 离线单测（mock Proc，不依赖真进程）。

[fix 2026-10-04] FNamePool 表驱动修复（1004 4080 案）配套——此前 tests/ 对
find_blocks/is_block_ptr 零覆盖。覆盖面按评审 §5.2：
- is_block_ptr：低地址（<1TB）64KB 对齐指针正例（被旧 1TB 下限误杀的形态）；
  高位垃圾 / 未对齐 / 模块区内 / 过小 负例；
- find_blocks_at：真表 RVA（None+四链齐全）命中；假表 RVA（读不到 / 非块
  指针 / 假 None / 断链）四态负例；
- find_blocks：低地址块指针 run 可被扫出（下限放宽回归）；>12 个垃圾 run
  排在真品之前仍命中（去 runs[:12] 截断回归）；
- find_blocks_table_first：表直读优先 / 表假阴落回扫描 / 无表值直走扫描；
- target_poll2 / camera_probe 空 cands 守卫：find_blocks 返回 [] 时 calibrate
  抛 RuntimeError（清晰错误，走既有重试链）而非 IndexError（原零重试暴毙路径）。
"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telemetry_capture"))

import camera_probe as cp    # noqa: E402
import names as nm           # noqa: E402
import target_poll2 as tp2   # noqa: E402
import tp1 as t              # noqa: E402

BASE = 0x7FF600000000            # 模块基址（典型 Shipping 加载址）
LOW_HEAP = 0x0000000300000000    # 1TB 以下的低地址堆（1004 4080 案形态），64KB 对齐
TABLE_RVA = 0x53C3350            # offsets.json 对 build 25582706 的真实 rva_blocks_expect
SCAN_CHUNK = 4 * 1024 * 1024      # 与 names.find_blocks 同款分块参数
SCAN_OVERLAP = 64
GARBAGE_HEAP = 0x0000000400000000  # 垃圾 run 指向的低地址堆（内容非 'None'）
FAKE_HEAP = 0x0000000500000000     # 假表值指向的堆（内容非 'None'，与真堆分开防段重叠）


class FakeProc:
    """duck-type tp1.Proc 读接口：分段内存、整段全有或全无（贴近 RPM 语义），
    读不到返回 None；u64/u32/i32/f32 与真 Proc 同语义。不碰任何真进程。"""

    def __init__(self, base=BASE):
        self.base = base
        self.segs = []   # [(start_addr, bytearray)]，按添加顺序查找

    def add_seg(self, start, data):
        self.segs.append((start, bytearray(data)))

    def data_seg(self, start, size):
        """取/建起始地址 start、长 size 的可写数据段（同段复用，可多次覆写）。"""
        for s, buf in self.segs:
            if s == start:
                return buf
        buf = bytearray(size)
        self.segs.append((start, buf))
        return buf

    def read(self, addr, n):
        if n <= 0:
            return b""
        for start, buf in self.segs:
            if start <= addr and addr + n <= start + len(buf):
                off = addr - start
                return bytes(buf[off:off + n])
        return None

    def u8(self, a):
        d = self.read(a, 1)
        return d[0] if d else None

    def u32(self, a):
        d = self.read(a, 4)
        return struct.unpack("<I", d)[0] if d else None

    def i32(self, a):
        d = self.read(a, 4)
        return struct.unpack("<i", d)[0] if d else None

    def u64(self, a):
        d = self.read(a, 8)
        return struct.unpack("<Q", d)[0] if d else None

    def f32(self, a):
        d = self.read(a, 4)
        return struct.unpack("<f", d)[0] if d else None


def fname_entry(name):
    """编码 FNamePool 窄字符表项（头部 u16：wide bit0 + len<<6）。"""
    b = name.encode("ascii")
    return struct.pack("<H", len(b) << 6) + b + b"\x00" * 2


def make_block0(names=None):
    """合法 Blocks[0]：'None' + Byte/Int/Bool/Float 四链（names 可覆盖以造断链/假 None）。"""
    slots = names if names is not None else {
        0: "None", 3: "ByteProperty", 10: "IntProperty",
        17: "BoolProperty", 24: "FloatProperty"}
    blk = bytearray(0x10000)
    for slot, name in slots.items():
        raw = fname_entry(name)
        blk[slot * 2:slot * 2 + len(raw)] = raw
    return bytes(blk)


def add_data_chunk(p, rva, writes):
    """把 writes {偏移: bytes} 写进覆盖 rva 的那个 4MB 扫描分块（整块可读，
    否则 FakeProc 全有或全无语义会让 find_blocks 跳过该块；同块复用可多次覆写）。"""
    step = SCAN_CHUNK - SCAN_OVERLAP
    chunk_lo = nm.SCAN_LO + ((rva - nm.SCAN_LO) // step) * step
    size = min(SCAN_CHUNK, nm.SCAN_HI - chunk_lo)
    seg = p.data_seg(p.base + chunk_lo, size)
    for off, data in writes.items():
        rel = rva - chunk_lo + off
        seg[rel:rel + len(data)] = data


def add_heap_blocks(p, heap=LOW_HEAP, n=3, block0=None):
    """低地址堆上 n 个 64KB 连续块，首块内容可指定（默认合法 None+四链）。"""
    p.add_seg(heap, block0 if block0 is not None else make_block0())
    for i in range(1, n):
        p.add_seg(heap + i * 0x10000, b"\x00" * 0x10000)


def ptr(v):
    return struct.pack("<Q", v)


def build_valid_site(p, table_rva=TABLE_RVA, heap=LOW_HEAP, n_blocks=3):
    """真表现场：低地址堆上合法 block0 + .data 表指向 n_blocks 个连续块。"""
    add_heap_blocks(p, heap, n_blocks)
    add_data_chunk(p, table_rva,
                   {0: b"".join(ptr(heap + i * 0x10000) for i in range(n_blocks))})


# ---------------- is_block_ptr：下限放宽正/负例 ----------------

class TestIsBlockPtr:
    def p(self):
        return FakeProc()

    def test_low_address_aligned_passes(self):
        # 1TB 以下、64KB 对齐、模块区外 → 过（旧 1TB 下限会误杀的形态）
        assert nm.is_block_ptr(self.p(), LOW_HEAP) is True
        assert nm.is_block_ptr(self.p(), 0x20000) is True   # 新下限之上的极小地址

    def test_high_garbage_rejected(self):
        assert nm.is_block_ptr(self.p(), 0x800000000000) is False
        assert nm.is_block_ptr(self.p(), 0xFFFFFFFFFFFF) is False

    def test_misaligned_rejected(self):
        assert nm.is_block_ptr(self.p(), LOW_HEAP + 0x40) is False
        assert nm.is_block_ptr(self.p(), LOW_HEAP | 0x8) is False

    def test_module_range_excluded(self):
        p = self.p()
        # 模块镜像区内（64KB 对齐）仍被排除（模块区排除保留）
        assert nm.is_block_ptr(p, p.base + 0x5000000) is False
        assert nm.is_block_ptr(p, p.base) is False

    def test_tiny_or_zero_rejected(self):
        p = self.p()
        assert nm.is_block_ptr(p, 0) is False
        assert nm.is_block_ptr(p, 0x10000) is False   # 下限为开区间（> 0x10000）
        assert nm.is_block_ptr(p, 0x1000) is False


# ---------------- find_blocks_at：表直读两态 ----------------

class TestFindBlocksAt:
    def test_real_table_rva_hits(self):
        p = FakeProc()
        build_valid_site(p)
        assert nm.find_blocks_at(p, TABLE_RVA) == [(TABLE_RVA, 3, 16)]

    def test_run_len_counts_consecutive_blocks(self):
        p = FakeProc()
        build_valid_site(p, n_blocks=5)
        assert nm.find_blocks_at(p, TABLE_RVA) == [(TABLE_RVA, 5, 16)]

    def test_unreadable_rva_returns_none(self):
        p = FakeProc()   # 内存全空
        assert nm.find_blocks_at(p, TABLE_RVA) is None

    def test_non_block_ptr_returns_none(self, capsys):
        p = FakeProc()
        add_data_chunk(p, TABLE_RVA, {0: ptr(0x12345)})  # 未对齐垃圾
        assert nm.find_blocks_at(p, TABLE_RVA) is None
        assert "非块指针" in capsys.readouterr().out   # 门级日志可定因

    def test_fake_none_returns_none(self, capsys):
        p = FakeProc()
        p.add_seg(LOW_HEAP, make_block0({0: "Object"}))   # 假 None：首表项不是 'None'
        add_data_chunk(p, TABLE_RVA, {0: ptr(LOW_HEAP)})
        assert nm.find_blocks_at(p, TABLE_RVA) is None
        assert "≠ 'None'" in capsys.readouterr().out

    def test_broken_chain_returns_none(self, capsys):
        p = FakeProc()
        p.add_seg(LOW_HEAP, make_block0({                # None 对，但 24 号槽断链
            0: "None", 3: "ByteProperty", 10: "IntProperty",
            17: "BoolProperty", 24: "DoubleProperty"}))
        add_data_chunk(p, TABLE_RVA, {0: ptr(LOW_HEAP)})
        assert nm.find_blocks_at(p, TABLE_RVA) is None
        out = capsys.readouterr().out
        assert "四链 3/4" in out and "FloatProperty='DoubleProperty'" in out


# ---------------- find_blocks：扫描放宽回归 ----------------

class TestFindBlocks:
    def test_low_heap_run_found(self):
        # 下限放宽回归：块指针全在 1TB 以下，旧 1TB 门下 runs 会是 0
        p = FakeProc()
        build_valid_site(p)
        assert nm.find_blocks(p) == [(TABLE_RVA, 3, 16)]

    def test_no_truncation_after_garbage_runs(self):
        # 去 runs[:12] 截断回归：13 个垃圾 run 按 RVA 升序排在真品之前，仍须命中
        p = FakeProc()
        garbage = b""
        for i in range(13):
            for j in range(3):   # 每个垃圾 run 3 个连续块指针（指向不可读堆 → None 门拒）
                garbage += ptr(GARBAGE_HEAP + i * 0x30000 + j * 0x10000)
            garbage += ptr(0)    # 0 分隔，保证 13 个 run 互相独立
        add_heap_blocks(p)       # 真品（低地址堆，None+四链齐全）
        real = b"".join(ptr(LOW_HEAP + i * 0x10000) for i in range(3))
        add_data_chunk(p, TABLE_RVA, {0: garbage, len(garbage): real})
        assert nm.find_blocks(p) == [(TABLE_RVA + len(garbage), 3, 16)]

    def test_empty_memory_returns_empty(self):
        assert nm.find_blocks(FakeProc()) == []

    def test_candidate_rejection_logged(self, capsys):
        # 失败现场可一行定因：候选被 None 门拒绝时日志带指针值与首表项内容
        p = FakeProc()
        p.add_seg(LOW_HEAP, make_block0({0: "Object"}))   # 假 None
        add_data_chunk(p, TABLE_RVA, {0: b"".join([
            ptr(LOW_HEAP), ptr(LOW_HEAP + 0x10000), ptr(LOW_HEAP + 0x20000)])})
        assert nm.find_blocks(p) == []
        out = capsys.readouterr().out
        assert "首表项='Object' ≠ 'None'" in out and ("0x%x" % LOW_HEAP) in out


# ---------------- find_blocks_table_first：走查顺序 ----------------

class TestTableFirst:
    def test_table_direct_read_preferred(self, monkeypatch):
        # 表值有效时即使扫描窗完全空（扫描必败）也命中——证明走的是直读
        p = FakeProc()
        add_heap_blocks(p)
        # 表放在扫描窗之外的 RVA（0x6000000 > SCAN_HI），find_blocks 永远看不见
        table_ptrs = b"".join(ptr(LOW_HEAP + i * 0x10000) for i in range(3))
        p.add_seg(p.base + 0x6000000, table_ptrs)
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", 0x6000000)
        assert nm.find_blocks_table_first(p) == [(0x6000000, 3, 16)]

    def test_fake_table_falls_back_to_scan(self, monkeypatch):
        # 表值假阴（首表项非 'None'）→ 落回扫描并命中真品（扫描窗内另一个 RVA）
        p = FakeProc()
        p.add_seg(FAKE_HEAP, make_block0({0: "Object"}))          # 表值指向的假块
        build_valid_site(p, table_rva=TABLE_RVA + 0x10000)       # 扫描可命中的真品
        add_data_chunk(p, TABLE_RVA, {0: ptr(FAKE_HEAP)})        # 后写覆盖：假表值
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", TABLE_RVA)
        assert nm.find_blocks_table_first(p) == [(TABLE_RVA + 0x10000, 3, 16)]

    def test_none_table_goes_straight_to_scan(self, monkeypatch):
        p = FakeProc()
        build_valid_site(p)
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", None)
        assert nm.find_blocks_table_first(p) == [(TABLE_RVA, 3, 16)]

    def test_all_fail_returns_empty(self, monkeypatch):
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", None)
        assert nm.find_blocks_table_first(FakeProc()) == []


# ---------------- target_poll2 / camera_probe 空 cands 守卫回归 ----------------

class TestEmptyCandsGuard:
    def test_target_poll2_calibrate_raises_runtime_error_not_index_error(self, monkeypatch):
        # 原 cands[0][0] 在 [] 上抛 IndexError，穿透 main 的 (RuntimeError, OSError)
        # 重试循环零重试当场崩；现必须是清晰 RuntimeError
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", None)
        with pytest.raises(RuntimeError, match="FNamePool Blocks 未找到"):
            tp2.calibrate(FakeProc(), None, 0)

    def test_camera_probe_calibrate_keeps_clear_error(self, monkeypatch):
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", None)
        with pytest.raises(RuntimeError, match="FNamePool Blocks 未找到"):
            cp.calibrate(FakeProc())

    def test_camera_probe_scan_guards_empty_cands(self, monkeypatch):
        # 原裸取 cands[0] → IndexError；现同款清晰 RuntimeError
        monkeypatch.setattr(t, "RVA_BLOCKS_EXPECT", None)
        with pytest.raises(RuntimeError, match="FNamePool Blocks 未找到"):
            cp.scan(FakeProc(), True, 0.1)
