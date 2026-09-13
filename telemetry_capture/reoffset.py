# -*- coding: utf-8 -*-
"""reoffset.py — 游戏更新后偏移锚定链一键重验（纯 RPM 只读，零写入零注入）。

目的：游戏更新后"十分钟恢复"，取代"一晚上考古"。对运行中的游戏逐项验证
external 采集管线（target_poll2 / camera_probe / shot_probe）依赖的整条锚定链，
逐项 PASS/FAIL + 实测值（供人肉比对基线；某项 FAIL 时把新实测值回填各脚本基线）：

  1. 进程+模块基址          进程名通配 FPSAimTrainer*（或 --pid 指定）
  2. FNamePool 内容锚定     'None'+属性名链 4/4 ⇒ Blocks 槽 RVA（期望 0x5387350）
  3. GUObjectArray 解析     offsets.json 按进程 exe 哈希选表的 RVA 布局解析 + FName 探针可读率
  4. 类名解析               TheMetaTrainerTarget / TargetHudComponent / PlayerCameraManager
                            / SceneComponent 按名字可解析
  5. RootComponent=0x130    全量对象宽池：+0x130 → USceneComponent 族根组件
                            （AActor 引擎层偏移，任何摆放过的 actor 均可验证；
                            实例发现含 BP 子类，Default__ CDO / /Script 包对象排除）
  6. ComponentToWorld=0x1c0 单位四元数 + 平移域哨兵(|v|≤8192 非全零) + 互异位置 ≥2
                            （同位置 actor（pawn/controller/组件）属正常，判据按去重位置数）
  7. 相机 POV=0xea0         PCM 族实例 +0xea0 数值哨兵(fov∈(1,179)/有限/在域)
  8. POV 镜像同构           PCM 实例后段动态扫 LastFrame 缓存副本（本构建基线
                            0x1af0/0x20f0，camera_probe 0830 --scan 实测）——fov 一致
                            且平移相近 ⇒ POV 结构咬合非孤证

  注：FField/UField 属性名反射交叉验证（camera_probe.walk_fields 路线）在本构建
  不可用——其属性链节点名在 Children(+0x48) 上可解析（如 WasRecentlyRendered）
  但属性不全（Actor 链 131 节点无 RootComponent），camera_probe 自身也因此回退
  实测常量 0xe9c。故第 8 项改用 POV 镜像副本同构做交叉验证。

域值哨兵：样例坐标全出域/全零 ⇒ 该项 FAIL（fail-fast：宁可报错，不接受静默错坐标）。

用法（在 analysis/external/ 下，用 py -3.9）:
    py -3.9 reoffset.py                # 自动找进程 FPSAimTrainer*
    py -3.9 reoffset.py --pid 18068    # 指定 PID
退出码: 0=全 PASS, 1=有 FAIL/SKIP, 2=进程未找到或打不开。

只读声明：全部读取走 ReadProcessMemory；OpenProcess 只申请
PROCESS_QUERY_INFORMATION|PROCESS_VM_READ。不写内存、不注入、不动进程状态。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import fnmatch
import os
import struct
import sys
import time

import tp1 as t
import names as nm
from camera_probe import find_meta, find_named_class, fname_of, is_ptr

PROC_PAT = "fpsaimtrainer*"     # 家族坑经验：进程查找必须支持通配
PROC_EXACT = "fpsaimtrainer-win64-shipping.exe"
# [v3] Blocks 槽期望 RVA 移入 offsets.json（按 exe 哈希键控，经 t.apply_offsets 装载）
POV_EXPECT = 0xea0              # PCM + CameraCachePrivate(0xe9c) + 4（TimeStamp）
MIRROR_BASELINE = (0x1af0, 0x20f0)   # POV LastFrame 镜像副本基线（camera_probe 0830 实测）
MIRROR_SCAN = (0xf00, 0x2800)   # 镜像动态扫描窗（避开 POV 本体，覆盖两份历史副本）
ROOT_OFF, XFORM_OFF = 0x130, 0x1c0
DOMAIN = 8192.0                 # 场景域界（同 target_poll2 偏移哨兵 / cleaner 野值界）
ACCESS_RO = 0x0400 | 0x0010     # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ（只读句柄）


class Proc:
    """只读版 tp1.Proc：OpenProcess 仅申请 QUERY|VM_READ；读接口与 tp1.Proc 完全一致，
    tp1.parse_object_array / names.* 可直接使用。"""

    def __init__(self, pid):
        self.pid = pid
        self.h = t.k32.OpenProcess(ACCESS_RO, False, pid)
        if not self.h:
            raise OSError("OpenProcess(%d) failed err=%d" % (pid, ctypes.get_last_error()))
        self.base = self._module_base()
        # [v3] 按 exe 哈希选偏移表（与 tp1.Proc 同源）；[v4] 传 proc=self 启用第 4 级
        # 运行时自定位——“游戏更新后十分钟恢复”正是自定位最能救场的场景，reoffset
        # 的只读 Proc 已满足 locate_guoa 的读取接口。
        t.apply_offsets(self.module_path, proc=self)

    def _module_base(self):
        need = wt.DWORD(0)
        t.psapi.EnumProcessModulesEx(self.h, None, 0, ctypes.byref(need), 0x03)
        count = need.value // ctypes.sizeof(wt.HMODULE)
        arr = (wt.HMODULE * count)()
        got = wt.DWORD(0)
        if not t.psapi.EnumProcessModulesEx(self.h, arr, ctypes.sizeof(arr),
                                            ctypes.byref(got), 0x03):
            raise OSError("EnumProcessModulesEx failed")
        name = ctypes.create_unicode_buffer(512)
        fallback = None
        for i in range(got.value // ctypes.sizeof(wt.HMODULE)):
            t.psapi.GetModuleFileNameExW(self.h, arr[i], name, 512)
            bn = os.path.basename(name.value).lower()
            if bn == PROC_EXACT:
                self.module_path = name.value
                return ctypes.cast(arr[i], ctypes.c_void_p).value
            if fallback is None and fnmatch.fnmatch(bn, PROC_PAT):
                self.module_path = name.value
                fallback = ctypes.cast(arr[i], ctypes.c_void_p).value
        if fallback:
            return fallback
        raise OSError("module %s not found" % PROC_EXACT)

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        if not t.k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n,
                                       ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def u8(self, a):  d = self.read(a, 1);  return d[0] if d else None
    def u32(self, a): d = self.read(a, 4);  return struct.unpack("<I", d)[0] if d else None
    def i32(self, a): d = self.read(a, 4);  return struct.unpack("<i", d)[0] if d else None
    def u64(self, a): d = self.read(a, 8);  return struct.unpack("<Q", d)[0] if d else None
    def f32(self, a): d = self.read(a, 4);  return struct.unpack("<f", d)[0] if d else None


def find_pid():
    """通配进程查找（tp1.find_pid 是精确匹配，这里支持 FPSAimTrainer*；
    多个匹配时优先精确 Shipping 名）。返回 (pid, exe) 或 (None, None)。"""
    snap = t.k32.CreateToolhelp32Snapshot(0x2, 0)

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", wt.WCHAR * 260)]
    e = PE(); e.dwSize = ctypes.sizeof(e)
    exact = wild = None
    if t.k32.Process32FirstW(snap, ctypes.byref(e)):
        while True:
            low = e.szExeFile.lower()
            if low == PROC_EXACT and exact is None:
                exact = (e.th32ProcessID, e.szExeFile)
            elif wild is None and fnmatch.fnmatch(low, PROC_PAT):
                wild = (e.th32ProcessID, e.szExeFile)
            if not t.k32.Process32NextW(snap, ctypes.byref(e)):
                break
    t.k32.CloseHandle(snap)
    return exact or wild or (None, None)


def super_family(p, root, cls_set, max_hops=8):
    """root 类 + SuperStruct(+0x40) 链可达的全部子类（target_poll2.nm_subclasses
    同逻辑：精确类相等匹配会漏 BP/原生子类）。"""
    out = {root}
    for cp in cls_set:
        if not cp or cp in out:
            continue
        s, hops = cp, 0
        while s and hops < max_hops:
            if s == root:
                out.add(cp)
                break
            s = p.u64(s + 0x40)
            hops += 1
    return out


def read_pov(p, pov):
    """读 FMinimalViewInfo：pos@0 / rot@0xC(Pitch,Yaw,Roll) / fov@0x18（4.26 布局，
    camera_probe 同）。读失败或出现 NaN/超界返回 None。"""
    pos = [p.f32(pov + i * 4) for i in range(3)]
    rot = [p.f32(pov + 0xC + i * 4) for i in range(3)]
    fov = p.f32(pov + 0x18)
    vals = pos + rot + [fov]
    if any(v is None or v != v or abs(v) > 1e9 for v in vals):
        return None
    return pos, rot, fov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=0,
                    help="指定进程 PID（默认自动查找 FPSAimTrainer*）")
    args = ap.parse_args()

    rows = []

    def rec(name, status, detail):
        rows.append((name, status, detail))
        print("[%d/%d] %-24s %-4s %s" % (len(rows), 8, name, status, detail))

    t0 = time.time()

    # ---- 1. 进程 + 模块基址 ----
    if args.pid:
        pid, exe = args.pid, "(--pid 指定)"
    else:
        pid, exe = find_pid()
    if not pid:
        rec("进程+模块基址", "FAIL", "未找到 %s 进程" % PROC_PAT)
        _summary(rows, t0); sys.exit(2)
    try:
        p = Proc(pid)
    except (OSError, RuntimeError) as e:
        # RuntimeError：未知 exe 版本且四级链（含自定位）全败。干净的 FAIL 行
        # 而非裸 traceback 崩溃。
        rec("进程+模块基址", "FAIL", str(e))
        _summary(rows, t0); sys.exit(2)
    rec("进程+模块基址", "PASS", "pid=%d base=0x%x (%s)" % (pid, p.base, exe))

    # ---- 2. FNamePool 内容锚定（'None' + 属性名链 4/4）----
    blocks_rt = None
    cands = nm.find_blocks(p)
    if not cands:
        rec("FNamePool内容锚定", "FAIL", "无 'None'+属性名链 4/4 候选（名字表布局已变？）")
    else:
        rva = cands[0][0]
        blocks_rt = p.base + rva
        expect = t.RVA_BLOCKS_EXPECT   # [v3] 从 offsets.json 来（None=该版无对照值）
        rec("FNamePool内容锚定", "PASS" if expect in (None, rva) else "FAIL",
            "Blocks槽RVA=0x%x%s 链4/4"
            % (rva, "" if expect is None else " (期望 0x%x)" % expect))

    # ---- 3. GUObjectArray 解析 + 一次全量遍历（类计数 + (obj,cls) 复用）----
    items = None
    nume = 0
    probes, obc, pairs = [], {}, []
    try:
        items, nume = t.parse_object_array(p)
        for obj in items():
            if not obj:
                continue
            cls = p.u64(obj + 0x10)
            if not cls:
                continue
            obc[cls] = obc.get(cls, 0) + 1
            pairs.append((obj, cls))
            if len(probes) < 200:
                fi = p.i32(obj + 0x18)
                if fi and fi < (1 << 22):
                    probes.append(fi)
    except Exception as e:
        rec("GUObjectArray解析", "FAIL", "RVA=0x%x 布局未匹配: %s"
            % (t.RVA_GUOBJECTARRAY, e))
    if items is not None:
        ratio = 0.0
        if blocks_rt and probes:
            ratio = sum(1 for i in probes if nm.read_class_name(p, blocks_rt, i)) \
                / float(len(probes))
        rec("GUObjectArray解析", "PASS" if ratio >= 0.6 else "FAIL",
            "RVA=0x%x nume=%d FName探针可读率 %.0f%%"
            % (t.RVA_GUOBJECTARRAY, nume, ratio * 100))

    # ---- 4. 类名解析 ----
    named = {}
    scene_fam = set()
    pcm_inst = None
    ok4 = False
    if blocks_rt is None or items is None:
        rec("类名解析", "SKIP", "前级 FAIL，跳过")
    else:
        meta = find_meta(p, obc)
        if meta is None:
            rec("类名解析", "FAIL", "元类（UClass::StaticClass 自引用）未找到")
        else:
            want = ("TheMetaTrainerTarget", "TargetHudComponent",
                    "PlayerCameraManager", "SceneComponent")
            for w in want:
                lst = find_named_class(p, blocks_rt, obc, meta, w)
                named[w] = lst[0] if lst else None
            missing = [w for w in want if not named[w]]
            ok4 = not missing
            if ok4:
                cls_set = set(obc)
                scene_fam = super_family(p, named["SceneComponent"], cls_set)
                pcm_fam = super_family(p, named["PlayerCameraManager"], cls_set)
                for obj, cls in pairs:      # PCM 实例（含 BP/原生子类，排 CDO）
                    if cls in pcm_fam:
                        nm_ = fname_of(p, blocks_rt, obj)
                        if nm_ and not nm_.startswith("Default__"):
                            pcm_inst = obj
                            break
            rec("类名解析", "PASS" if ok4 else "FAIL",
                " ".join("%s=0x%x" % (w, named[w]) if named[w] else "%s=×" % w
                         for w in want)
                + ("  缺:" + ",".join(missing) if missing else ""))

    # ---- 5. RootComponent=0x130（宽池：全量对象 +0x130 → SceneComponent 族）----
    # 注意 owner/对象名过滤要两类都看：类名=Package（target_poll2 的野 owner 排除）
    # 与自身名 Default__//Script（CDO 与包对象）。0x130/0x1c0 是 AActor/USceneComponent
    # 引擎层偏移，任何摆放过的 actor 都能验证，不依赖特定目标类在场。
    roots = []          # [(obj, comp)]
    if not ok4:
        rec("RootComponent=0x130", "SKIP", "前级 FAIL，跳过")
    else:
        for obj, cls in pairs:
            comp = p.u64(obj + ROOT_OFF)
            if not (comp and is_ptr(p, comp, XFORM_OFF + 0x20)):
                continue
            if p.u64(comp + 0x10) not in scene_fam:
                continue
            nm_ = fname_of(p, blocks_rt, obj)
            if nm_ and (nm_.startswith("Default__") or nm_.startswith("/Script/")):
                continue
            roots.append((obj, comp))
        rec("RootComponent=0x130", "PASS" if len(roots) >= 3 else "FAIL",
            "扫描 %d 对象 → +0x130 命中 SceneComponent 族 %d 个"
            % (len(pairs), len(roots)))

    # ---- 6. ComponentToWorld=0x1c0（单位四元数 + 域哨兵 + 互异位置 ≥2）----
    if not ok4:
        rec("ComponentToWorld=0x1c0", "SKIP", "前级 FAIL，跳过")
    elif not roots:
        rec("ComponentToWorld=0x1c0", "FAIL", "无可采样组件（见上一项）")
    else:
        unit, positioned, uniques = 0, [], []
        for obj, comp in roots:
            q = [p.f32(comp + XFORM_OFF + i * 4) for i in range(4)]
            if all(v is not None and v == v and abs(v) <= 1e6 for v in q) \
                    and 0.98 < sum(v * v for v in q) < 1.02:
                unit += 1
            tr = [p.f32(comp + XFORM_OFF + 0x10 + i * 4) for i in range(3)]
            if not all(v is not None and v == v and abs(v) <= DOMAIN for v in tr) \
                    or not any(abs(v) > 1.0 for v in tr):
                continue    # 出域/全零：未定位对象（池化/默认位），只剔除不判 FAIL
            positioned.append(tr)
            if all(max(abs(tr[k] - u[k]) for k in range(3)) > 1.0 for u in uniques):
                uniques.append(tr)
        ok6 = (unit == len(roots) and len(uniques) >= 2)
        rec("ComponentToWorld=0x1c0", "PASS" if ok6 else "FAIL",
            "quat单位 %d/%d 定位样本 %d 互异位置 %d（同位 actor 属正常）"
            % (unit, len(roots), len(positioned), len(uniques)))

    # ---- 7. 相机 POV=0xea0（数值哨兵）----
    pov_data = None
    if not ok4:
        rec("相机POV=0xea0", "SKIP", "前级 FAIL，跳过")
    elif pcm_inst is None:
        rec("相机POV=0xea0", "FAIL", "无 PlayerCameraManager 族实例")
    else:
        pov_data = read_pov(p, pcm_inst + POV_EXPECT)
        if pov_data is None:
            rec("相机POV=0xea0", "FAIL",
                "POV@+0x%x 读失败/超界（实例 0x%x）" % (POV_EXPECT, pcm_inst))
        else:
            pos, rot, fov = pov_data
            ok7 = (1.0 < fov < 179.0 and abs(rot[1]) <= 190.0
                   and all(abs(v) <= DOMAIN for v in pos)
                   and any(abs(v) > 1.0 for v in pos))
            rec("相机POV=0xea0", "PASS" if ok7 else "FAIL",
                "pos=%s rot=%s fov=%.1f"
                % ([round(v, 1) for v in pos], [round(v, 1) for v in rot], fov))

    # ---- 8. POV 镜像同构（LastFrame 缓存副本，动态扫描）----
    if pov_data is None:
        rec("POV镜像同构", "SKIP", "前级无有效 POV，跳过")
    else:
        pos_ref, _, fov_ref = pov_data
        hits = []
        for off in range(MIRROR_SCAN[0], MIRROR_SCAN[1], 4):
            d = read_pov(p, pcm_inst + off)
            if d is None:
                continue
            if abs(d[2] - fov_ref) <= 0.5 \
                    and max(abs(a - b) for a, b in zip(d[0], pos_ref)) <= 1000.0:
                hits.append(off)
        ok8 = len(hits) >= 2
        rec("POV镜像同构", "PASS" if ok8 else "FAIL",
            "镜像命中 %s（基线 0x%x/0x%x）⇒ POV 结构咬合%s"
            % ([hex(o) for o in hits], MIRROR_BASELINE[0], MIRROR_BASELINE[1],
               "" if all(h in MIRROR_BASELINE for h in hits) else "，镜像基线已漂移"))

    _summary(rows, t0)
    sys.exit(0 if all(s == "PASS" for _, s, _ in rows) else 1)


def _summary(rows, t0):
    print("-" * 78)
    for name, status, detail in rows:
        print("  %-4s %-24s %s" % (status, name, detail))
    npass = sum(1 for _, s, _ in rows if s == "PASS")
    fails = [n for n, s, _ in rows if s != "PASS"]
    print("总结论: %d/%d PASS，用时 %.1fs%s"
          % (npass, len(rows), time.time() - t0,
             "" if not fails else "  未过项: " + ", ".join(fails)))


if __name__ == "__main__":
    main()
