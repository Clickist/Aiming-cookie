# -*- coding: utf-8 -*-
"""names.py — 外部解析 FName 名字（FNamePool 遍历），并给出类清单。

原理：
- UObject +0x18 = NamePrivate (FName: int32 index + int32 number)
- 4.23+ 名字全在 FNamePool：Entry = Blocks[index>>16] + (index&0xFFFF)*2
- Blocks[] 是 .data 里的指针数组，相邻块相差 1<<GNameBlockBits（默认 64KB）——
  用这个铁签名扫 .data 定位 Blocks[]，再解析任意 index。
- UClass 对象 = "其 ClassPrivate 等于众数类指针" 的对象；给每个 UClass 解析名字。

用法: python names.py [--find 关键词]   # 默认打印类清单 + 含关键词的类
"""
import struct
import sys

import tp1 as t

GNAME_BLOCK_BITS_CANDIDATES = (16, 13, 14, 15)
SCAN_LO, SCAN_HI = 0x3800000, 0x5A8D000   # 模块尾部（.data/.rdata 区域）


def read_class_name(p, blocks_addr, fname_idx):
    block_i = fname_idx >> 16
    off = (fname_idx & 0xFFFF) * 2
    if blocks_addr == BLOCK0_DIRECT:          # 单块退化模式：只有 block0 可解析
        if block_i != 0:
            return None
        block = blocks_addr
    else:
        block = p.u64(blocks_addr + block_i * 8)
        if not block:
            return None
    return read_entry(p, block, off)


BLOCK0_DIRECT = "BLOCK0_DIRECT"


def read_entry(p, block, off):
    hdr_raw = p.read(block + off, 2)
    if not hdr_raw:
        return None
    h = struct.unpack("<H", hdr_raw)[0]
    wide = h & 1
    ln = (h >> 6) & 0x3FF
    if ln <= 0 or ln > 96:
        return None
    raw = p.read(block + off + 2, ln + (ln + 1))  # 稍多读点
    if not raw:
        return None
    if wide:
        try:
            return raw[: ln * 2].decode("utf-16-le", errors="replace")
        except Exception:
            return None
    try:
        s = raw[:ln].decode("ascii")
    except Exception:
        return None
    return s if all(32 <= ord(c) < 127 for c in s) else None


def find_blocks(p):
    """定位 FNamePool.Entries.Blocks（.data 里的指针数组）：
    - 条目都是 64KB 对齐的堆指针（VirtualAlloc 粒度），连续 ≥3 个；
    - Blocks[0] 指向的块开头必是 index0='None'（len=4，非宽；注意头部含 5bit 探测哈希）；
    - 链验证 index1..4 = ByteProperty/IntProperty/BoolProperty/ObjectProperty。"""
    def is_block_ptr(v):
        return 0x10000000000 < v < 0x7FFFFFFFFFFF and (v & 0xFFFF) == 0 \
            and not (p.base <= v < p.base + SCAN_HI)

    runs = []
    chunk = 4 * 1024 * 1024
    overlap = 64
    for lo in range(SCAN_LO, SCAN_HI, chunk - overlap):
        blob = p.read(p.base + lo, min(chunk, SCAN_HI - lo))
        if not blob:
            continue
        n = len(blob) // 8
        if n < 4:
            continue
        vals = struct.unpack_from("<%dQ" % n, blob, 0)
        i = 0
        while i < n - 3:
            if is_block_ptr(vals[i]) and is_block_ptr(vals[i + 1]) and is_block_ptr(vals[i + 2]):
                j = i
                while j < n and is_block_ptr(vals[j]):
                    j += 1
                if j - i >= 3:
                    runs.append((lo + i * 8, j - i))
                i = j
            else:
                i += 1
    print("[names] 64KB 对齐堆指针链候选: %d" % len(runs))

    for rva, run_len in runs[:12]:
        v0 = p.u64(p.base + rva)
        if not v0:
            continue
        e0 = read_entry(p, v0, 0)
        if e0 != "None":
            continue
        # FName 索引=块内 2 字节槽号，与 EName 枚举值不同：None 占槽 0-2，
        # 故 ByteProperty=3、IntProperty=10、BoolProperty=17、FloatProperty=24
        ok = 0
        for idx, expect in ((3, "ByteProperty"), (10, "IntProperty"),
                            (17, "BoolProperty"), (24, "FloatProperty")):
            if read_entry(p, v0, idx * 2) == expect:
                ok += 1
        print("[names] 候选 Blocks @ RVA 0x%x: 'None'=%r 链 %d/4" % (rva, e0, ok))
        if ok == 4:
            print("[names] 命中！Blocks[0] 槽 RVA = 0x%x（链长 %d）" % (rva, run_len))
            return [(rva, run_len, 16)]
    return []


def main():
    keyword = ""
    if "--find" in sys.argv:
        keyword = sys.argv[sys.argv.index("--find") + 1].lower()
    pid = t.find_pid()
    if not pid:
        print("!! 游戏未运行"); sys.exit(2)
    p = t.Proc(pid)
    print("[proc] pid=%d base=0x%x" % (pid, p.base))

    cands = find_blocks(p)
    print("[names] Blocks[] 候选（addr, run, bits）:", [(hex(a + p.base), r, b) for a, r, b in cands][:8])
    if not cands:
        print("!! 未找到 FNamePool Blocks 链"); sys.exit(3)

    # 逐候选验证：解析几个对象名，取“可读名字占比”最高者
    items, nume = t.parse_object_array(p)
    probes = []
    for obj in items():
        if not obj:
            continue
        idx = p.i32(obj + 0x18)
        if idx and idx < (1 << 22):
            probes.append(idx)
        if len(probes) >= 200:
            break
    best = None
    for addr, run, bits in cands:
        ok = sum(1 for i in probes if read_class_name(p, p.base + addr, i))
        ratio = ok / max(1, len(probes))
        if best is None or ratio > best[0]:
            best = (ratio, addr, run, bits)
    ratio, blocks_addr, run, bits = best
    print("[names] 选定 Blocks @ RVA 0x%x（run=%d, %dKB 块, 探针可读率 %.0f%%）"
          % (blocks_addr, run, 1 << bits >> 10, ratio * 100))
    if ratio < 0.6:
        print("!! 可读率过低，候选可疑"); sys.exit(4)
    blocks_rt = p.base + blocks_addr

    # 全量统计：每个类指针的实例数
    objects_by_class = {}
    seen = 0
    for obj in items():
        seen += 1
        if not obj:
            continue
        cls = p.u64(obj + 0x10)
        if cls:
            objects_by_class[cls] = objects_by_class.get(cls, 0) + 1

    # 元类：UClass::StaticClass 自引用（ClassPrivate==自身），且被所有 UClass 指向
    meta = None
    meta_best = -1
    for cls_ptr, cnt in objects_by_class.items():
        if p.u64(cls_ptr + 0x10) == cls_ptr and cnt > meta_best:
            meta, meta_best = cls_ptr, cnt
    print("[names] 共 %d 对象；UClass 元类指针=0x%x (inst=%d)" % (seen, meta, meta_best))

    classes = []
    for cls_ptr, cnt in objects_by_class.items():
        if cls_ptr == meta:
            continue
        if p.u64(cls_ptr + 0x10) != meta:
            continue
        fi = p.i32(cls_ptr + 0x18)
        num = p.i32(cls_ptr + 0x1C)
        name = read_class_name(p, blocks_rt, fi) if fi else None
        if name:
            classes.append((name, num, cls_ptr, cnt))
    classes.sort(key=lambda c: -c[3])
    print("[names] 已命名类 %d 个；实例数 Top20:" % len(classes))
    for name, num, ptr, cnt in classes[:20]:
        print("    %-44s inst=%-6d class=0x%x" % (name, cnt, ptr))
    if keyword:
        print("[names] 名字含 %r 的类:" % keyword)
        for name, num, ptr, cnt in classes:
            if keyword in name.lower():
                print("    %-44s inst=%-6d class=0x%x" % (name, cnt, ptr))


if __name__ == "__main__":
    main()
