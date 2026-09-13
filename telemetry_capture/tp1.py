# -*- coding: utf-8 -*-
"""target_poll.py — 纯外部 ReadProcessMemory 读取 KovaaK's 目标遥测（零注入，零崩溃风险）。

用法（游戏副本运行中，任意时刻）:
    ~/AppData/Local/Programs/Python/Python39/python target_poll.py --calibrate   # 一次性自校准并打印偏移
    ~/AppData/Local/Programs/Python/Python39/python target_poll.py --run         # 50Hz 采目标 → target_poll_out.jsonl
    ~/AppData/Local/Programs/Python/Python39/python target_poll.py --run --hz 100

原理: 用 UE4SS 扫描留下的静态 RVA（见 ../ue4ss/scan_facts.md）+ 运行时模块基址
定位 GUObjectArray 和各 UClass 槽；枚举对象数组找 ATheMetaTrainerTarget 实例；
RootComponent 偏移与 ComponentToWorld 偏移在运行时用强签名自校准
（RootComponent: 指向 class==USceneComponent 的对象；FTransform: 单位四元数+有限平移）。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
rpm = k32.ReadProcessMemory
rpm.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
rpm.restype = wt.BOOL
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD,
                                       ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

PROCESS_ALL_ACCESS = 0x1F0FFF
TH32CS_SNAPPROCESS = 0x2
TARGET_EXE = "FPSAimTrainer-Win64-Shipping.exe"

# ---- 静态事实（analysis/ue4ss/scan_facts.md，对旧构建副本永久有效）----
# [v3 2026-09-01] RVA_* 不再写死单版：运行时按目标 exe sha256 从 offsets.json 选表
# （apply_offsets 覆写下列全局；未知版本 fail-fast）。下面的字面值仅为旧版兜底初值。
RVA_GUOBJECTARRAY = 0x53B4508
RVA_SLOTTARGET = 0x506FBC8        # ATheMetaTrainerTarget::StaticClass 槽 (sizeof=0x5e0)
RVA_SLOTS_SCENE = 0x51286A8       # USceneComponent::StaticClass 槽 (sizeof=0x200)

OFFSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offsets.json")


def apply_offsets(exe_path, proc=None):
    """[v4 2026-09-12] 偏移表四级分辨率链：包内表 → 用户缓存 → 云表 → 运行时自定位。
    全链不可用 → RuntimeError（fail-fast，绝不猜偏移）。链实现见 offset_resolver.py，
    自适应层说明见 RUNBOOK_OFFSETS.md §9。返回完整 sha256。"""
    global RVA_GUOBJECTARRAY, RVA_SLOTTARGET, RVA_SLOTS_SCENE, RVA_BLOCKS_EXPECT
    import offset_resolver as orr
    digest, ent, source = orr.resolve(exe_path, proc=proc)
    RVA_GUOBJECTARRAY = int(ent["rva_guobjectarray"], 16)
    slot_t = ent.get("rva_slot_target")
    slot_s = ent.get("rva_slot_scene")
    RVA_SLOTTARGET = int(slot_t, 16) if slot_t else None
    RVA_SLOTS_SCENE = int(slot_s, 16) if slot_s else None
    RVA_BLOCKS_EXPECT = int(ent["rva_blocks_expect"], 16) if ent.get("rva_blocks_expect") else None
    print("[offsets] 版本(%s): %s\n[offsets] GUObjectArray=0x%x SlotTarget=%s SlotScene=%s"
          % (source, ent.get("label", digest[:16]), RVA_GUOBJECTARRAY,
             ("0x%x" % RVA_SLOTTARGET) if RVA_SLOTTARGET else "缺(仅诊断校准用,不影响录制)",
             ("0x%x" % RVA_SLOTS_SCENE) if RVA_SLOTS_SCENE else "缺(仅诊断校准用,不影响录制)"))
    return digest


RVA_BLOCKS_EXPECT = None   # FNamePool.Blocks 期望 RVA（apply_offsets 填充；None=不对照）

# 兜底产物路径：打包后脚本目录是只读 MEIPASS，写那里会失败；产品路径恒传
# --out-dir，只有手工诊断会用到兜底，落到当前工作目录。
OUT_PATH = os.path.join(os.getcwd(), "target_poll_out.jsonl")


class Proc:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not self.h:
            raise OSError("OpenProcess(%d) failed err=%d" % (pid, ctypes.get_last_error()))
        self.base = self._module_base()
        self.module_path = self._module_path
        apply_offsets(self.module_path, proc=self)   # [v4] 四级链选表；未知版本可运行时自定位

    def _module_base(self):
        need = wt.DWORD(0)
        psapi.EnumProcessModulesEx(self.h, None, 0, ctypes.byref(need), 0x03)
        count = need.value // ctypes.sizeof(wt.HMODULE)
        arr = (wt.HMODULE * count)()
        got = wt.DWORD(0)
        if not psapi.EnumProcessModulesEx(self.h, arr, ctypes.sizeof(arr), ctypes.byref(got), 0x03):
            raise OSError("EnumProcessModulesEx failed")
        name = ctypes.create_unicode_buffer(512)
        for i in range(got.value // ctypes.sizeof(wt.HMODULE)):
            psapi.GetModuleFileNameExW(self.h, arr[i], name, 512)
            if os.path.basename(name.value).lower() == TARGET_EXE.lower():
                self._module_path = name.value
                return ctypes.cast(arr[i], ctypes.c_void_p).value
        raise OSError("module %s not found" % TARGET_EXE)

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        if not rpm(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def u8(self, a):  d = self.read(a, 1);  return d[0] if d else None
    def u32(self, a): d = self.read(a, 4);  return struct.unpack("<I", d)[0] if d else None
    def i32(self, a): d = self.read(a, 4);  return struct.unpack("<i", d)[0] if d else None
    def u64(self, a): d = self.read(a, 8);  return struct.unpack("<Q", d)[0] if d else None
    def f32(self, a): d = self.read(a, 4);  return struct.unpack("<f", d)[0] if d else None


def find_pid():
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", wt.WCHAR * 260)]
    e = PE(); e.dwSize = ctypes.sizeof(e)
    pid = None
    if k32.Process32FirstW(snap, ctypes.byref(e)):
        while True:
            if e.szExeFile == TARGET_EXE:
                pid = e.th32ProcessID
                break
            if not k32.Process32NextW(snap, ctypes.byref(e)):
                break
    k32.CloseHandle(snap)
    return pid


def readable_range(p, addr, n=8):
    """粗校验：地址非空且能读到 n 字节。"""
    return addr and p.read(addr, n) is not None


def chunk_layout(p):
    """返回 (chunklist绝对地址, per_chunk容量, stride, nume)——chunk 差分发现用。"""
    guoa = p.base + RVA_GUOBJECTARRAY
    for arr_base in (guoa + 0x10, guoa):
        chunkptr = p.u64(arr_base + 0x00)
        maxe = p.i32(arr_base + 0x10)
        nume = p.i32(arr_base + 0x14)
        maxc = p.i32(arr_base + 0x18)
        numc = p.i32(arr_base + 0x1C)
        if not chunkptr or not readable_range(p, chunkptr, 8):
            continue
        if not (0 < nume <= maxe <= 64_000_000) or not (0 < numc <= maxc <= 4096):
            continue
        per_chunk = maxe // maxc if maxc else 65536
        return chunkptr, per_chunk, 0x18, nume
    raise RuntimeError("chunk_layout: 布局未匹配")


def make_item_addr(p):
    """重跑布局判定，返回 item_addr(idx)——对象数组第 idx 槽的绝对地址（增量采样用）。"""
    guoa = p.base + RVA_GUOBJECTARRAY
    for arr_base in (guoa + 0x10, guoa):
        chunkptr = p.u64(arr_base + 0x00)
        maxe = p.i32(arr_base + 0x10)
        nume = p.i32(arr_base + 0x14)
        maxc = p.i32(arr_base + 0x18)
        numc = p.i32(arr_base + 0x1C)
        if not chunkptr or not readable_range(p, chunkptr, 8):
            continue
        if not (0 < nume <= maxe <= 64_000_000) or not (0 < numc <= maxc <= 4096):
            continue
        per_chunk = maxe // maxc if maxc else 65536
        stride = 0x18

        def item_addr(idx, _cl=chunkptr, _pc=per_chunk, _st=stride):
            chunk = p.u64(_cl + (idx // _pc) * 8)
            return chunk + (idx % _pc) * _st
        return item_addr
    raise RuntimeError("make_item_addr: 布局未匹配")


# ---------------- 对象数组解析（自校准） ----------------

def parse_object_array(p):
    """返回 (items_iter_fn, num_elements)。对 GUObjectArray 布局做经验校验。"""
    guoa = p.base + RVA_GUOBJECTARRAY
    candidates = []
    # UE4SS 给的可能是 FUObjectArray 也可能是其中 Objects 子结构；两种都试
    for arr_base, tag in ((guoa, "as-substruct"), (guoa + 0x10, "as-fuobjarray+0x10")):
        chunkptr = p.u64(arr_base + 0x00)
        maxe = p.i32(arr_base + 0x10)
        nume = p.i32(arr_base + 0x14)
        maxc = p.i32(arr_base + 0x18)
        numc = p.i32(arr_base + 0x1C)
        if not chunkptr or not readable_range(p, chunkptr, 8):
            continue
        if not (0 < nume <= maxe <= 64_000_000) or not (0 < numc <= maxc <= 4096):
            continue
        candidates.append((tag, arr_base, chunkptr, nume, maxc, numc))
    if not candidates:
        raise RuntimeError("GUObjectArray 布局未匹配（换 GUObjectArray 解读重试）")
    tag, arr_base, chunkptr, nume, maxc, numc = candidates[0]

    # chunk 指针数组：PreAllocated 内联在 +0x20 起，或 +0x0 是指向它的指针。
    # 直接经验判定：逐候选解释第一块，要求块内 item.Object 大多可读。
    def try_chunklist(cl_addr, stride_item):
        first = p.u64(cl_addr)
        if not first or not readable_range(p, first, stride_item):
            return None
        good = 0
        for i in range(min(nume, 64)):
            o = p.u64(first + i * stride_item)
            if o and readable_range(p, o, 0x30):
                good += 1
        return good / max(1, min(nume, 64))

    best = None
    for cl_addr, tag2 in ((chunkptr, "deref-chunkptr"),
                          (arr_base + 0x20, "inline-chunks@+0x20"),
                          (arr_base + 0x20, "inline-chunks@+0x20")):
        for stride in (0x18, 0x14, 0x10, 0x20):
            ratio = try_chunklist(cl_addr, stride)
            if ratio and ratio > 0.7:
                score = ratio
                if best is None or score > best[0]:
                    best = (score, cl_addr, stride, tag2)
    if not best:
        raise RuntimeError("对象数组 chunk/item 步长未校准通过")
    _, cl_addr, stride, tag2 = best
    print("[calib] array=%s chunklist=%s item_stride=0x%x valid_ratio=%.2f"
          % (tag, tag2, stride, best[0]))

    def items():
        # 每 chunk 容量 = maxe/maxc（本构建 16M/256=65536，与 UE 常量吻合）
        per_chunk = maxe // maxc if maxc else 0
        if not per_chunk or per_chunk > 10_000_000:
            per_chunk = 65536
        remaining = nume
        for c in range(numc):
            ch = p.u64(cl_addr + c * 8)
            if not ch:
                continue
            take = min(per_chunk, remaining)
            blob = p.read(ch, take * stride)
            if not blob:
                continue
            for i in range(take):
                off = i * stride
                obj = struct.unpack_from("<Q", blob, off)[0]
                yield obj
            remaining -= take

    return items, nume


# ---------------- 类识别 + 偏移自校准 ----------------

def read_class(p, obj):
    return p.u64(obj + 0x10)  # UObject::ClassPrivate (4.26)


def calibrate(p, items, nume):
    if not RVA_SLOTTARGET or not RVA_SLOTS_SCENE:
        raise RuntimeError("StaticClass 槽 RVA 缺失（自适应表只含录制必需的 GUObjectArray；"
                           "类槽仅诊断校准用，按 RUNBOOK_OFFSETS.md §3 重取后登记即恢复）")
    slot_t = p.u64(p.base + RVA_SLOTTARGET)
    slot_s = p.u64(p.base + RVA_SLOTS_SCENE)
    if not readable_range(p, slot_t, 0x30) or not readable_range(p, slot_s, 0x30):
        raise RuntimeError("StaticClass 槽读不到（游戏还在主菜单也应有值；检查 RVA/基址）")
    print("[calib] UClass(ATheMetaTrainerTarget)=0x%x  UClass(USceneComponent)=0x%x"
          % (slot_t, slot_s))

    # 枚举全部对象（新对象在数组尾部，必须全扫）
    targets = []
    seen = 0
    for obj in items():
        seen += 1
        if obj:
            cls = read_class(p, obj)  # 未读到的返回 None
            if cls == slot_t:
                targets.append(obj)
    print("[calib] scanned=%d, target_instances=%d" % (seen, len(targets)))
    if not targets:
        print("!! 当前场景没有目标实例（进一个训练场景再跑 --calibrate）")
        return None

    # RootComponent 偏移：目标对象上某偏移的指针，其 class == USceneComponent
    root_off = None
    for off in range(0x80, 0x240, 8):
        ok = 0
        for t in targets[:16]:
            ptr = p.u64(t + off)
            if ptr and readable_range(p, ptr, 0x30) and read_class(p, ptr) == slot_s:
                ok += 1
        if ok == min(16, len(targets)):
            root_off = off
            break
    if root_off is None:
        raise RuntimeError("RootComponent 偏移未校准到")
    print("[calib] RootComponent offset = 0x%x" % root_off)

    # ComponentToWorld（世界变换 FTransform）偏移：场景组件上单位四元数签名
    comp = p.u64(targets[0] + root_off)
    xform_off = None
    for off in range(0x40, 0x400, 4):
        q = [p.f32(comp + off + i * 4) for i in range(4)]
        if any(v is None or abs(v) > 1e6 for v in q):
            continue
        n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
        if 0.98 < n2 < 1.02 and abs(q[3]) < 1.0:  # 单位四元数（x,y,z,w）
            tr = [p.f32(comp + off + 16 + i * 4) for i in range(3)]
            if all(v is not None and abs(v) < 1e9 for v in tr) and any(abs(v) > 1e-3 for v in tr):
                xform_off = off
                break
    if xform_off is None:
        raise RuntimeError("ComponentToWorld 偏移未校准到")
    print("[calib] ComponentToWorld offset = 0x%x (q@+0, t@+0x10)" % xform_off)

    # 多实例位置一致性抽样（有移动目标时才有意义，只打印不强制）
    sample = {}
    for t in targets[:12]:
        comp2 = p.u64(t + root_off)
        if not comp2:
            continue
        pos = [p.f32(comp2 + xform_off + 16 + i * 4) for i in range(3)]
        sample[t] = pos
    print("[calib] sample positions:")
    for t, pos in list(sample.items())[:6]:
        print("    obj=0x%x pos=%s" % (t, ["%.1f" % v for v in pos]))
    return {"root_off": root_off, "xform_off": xform_off,
            "class_target": slot_t, "class_scene": slot_s}


def run(p, items, cal, hz):
    root_off, xo = cal["root_off"], cal["xform_off"]
    dt = 1.0 / hz
    print("[run] 采样 %dHz → %s（Ctrl+C 停止）" % (hz, OUT_PATH))
    f = open(OUT_PATH, "a", encoding="utf-8")
    t0 = time.time()
    n = 0
    try:
        while True:
            now = time.time() - t0
            arr = []
            for obj in items():
                if obj and readable_range(p, obj, 0x30):
                    if read_class(p, obj) == cal["class_target"]:
                        comp = p.u64(obj + root_off)
                        if comp:
                            pos = [p.f32(comp + xo + 16 + i * 4) for i in range(3)]
                            if all(v is not None for v in pos):
                                arr.append([obj, pos[0], pos[1], pos[2]])
            f.write(json.dumps({"ev": "frame", "t": round(now, 4), "targets": arr}) + "\n")
            n += 1
            if n % hz == 0:
                f.flush()
                print("    t=%6.1fs targets=%d" % (now, len(arr)))
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        f.flush(); f.close()
        print("[run] 结束，共 %d 帧写入 %s" % (n, OUT_PATH))


def main():
    mode = "--calibrate"
    hz = 50
    args = sys.argv[1:]
    if "--run" in args:
        mode = "--run"
    if "--hz" in args:
        hz = int(args[args.index("--hz") + 1])
    pid = find_pid()
    if not pid:
        print("!! 游戏副本未运行（先启动 Shipping exe）"); sys.exit(2)
    p = Proc(pid)
    print("[proc] pid=%d base=0x%x" % (pid, p.base))
    items, nume = parse_object_array(p)
    cal = calibrate(p, items, nume)
    if mode == "--run" and cal:
        run(p, items, cal, hz)


if __name__ == "__main__":
    main()
