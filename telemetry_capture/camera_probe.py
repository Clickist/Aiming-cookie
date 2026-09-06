# -*- coding: utf-8 -*-
"""camera_probe.py — 纯外部 RPM 读取 KovaaK's 玩家相机位姿（位置+旋转，50Hz）。

路线（见 event_layer_notes.md §1）：
  1) 枚举 GUObjectArray，按类名找 PlayerCameraManager 实例（names.py 解析 UClass 名）；
  2) 运行时反射自校准：沿 UClass 的 FField 属性链读 Offset_Internal——
     锚点 = AActor.RootComponent == 0x130（target_poll2 两轮轨迹验证过的本构建常量）；
     拿到 CameraCachePrivate 偏移后 POV = PCM + CameraCachePrivate + 4（+0 是 TimeStamp）；
  3) FMinimalViewInfo 内 Location/Rotation/FOV 用 UScriptStruct("MinimalViewInfo") 反射验证，
     失败则用 4.26 标准布局 Location=0x0 / Rotation=0xC(Pitch,Yaw,Roll) / FOV=0x18；
  4) --scan 提供备用路线：对 PCM 对象内存做时间差分，找“每帧变化”的区段人工确认。

用法:
    python camera_probe.py --calibrate            # 只打印校准结果
    python camera_probe.py --run [--hz 50]        # 采样 → camera_probe_out_*.jsonl
    python camera_probe.py --run --wait           # 等游戏进场（每 15s 重试）
    python camera_probe.py --scan [--secs 10]     # 差分扫描 PCM 内存（校准失败时用）

只读内存：所有读取走 RPM，不注入不写内存。读取失败/超界一律返回 None 并跳过该帧。
"""
import argparse
import json
import os
import struct
import sys
import time

import tp1 as t
import names as nm

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_probe_out")

# 4.26 标准布局（引擎源码约定），反射失败时的回退值 [inferred]
MVI_LOCATION = 0x0
MVI_ROTATION = 0xC   # FRotator: Pitch, Yaw, Roll (度)
MVI_FOV = 0x18
POV_TO_POV_FIELD = 0x4   # FCameraCacheEntry {float TimeStamp; FMinimalViewInfo POV;}
# FField 链内 Offset_Internal 的候选位置（本构建实测 0x4C：ArrayDim@0x38/ElementSize@0x3C/
# PropertyFlags@0x40(u64)；RootComponent=0x130 锚定，旧候选保留作游戏更新后回退）
OFFSET_CANDIDATES = (0x4C, 0x40, 0x44, 0x3C, 0x2C)

MAX_FIELDS = 500


def is_ptr(p, v, n=0x30):
    return v and 0x10000 < v < 0x7FFFFFFFFFFF and t.readable_range(p, v, n)


def fname_of(p, blocks_rt, obj):
    """对象/字段 +0x18(int32 FName idx) → 名字字符串。"""
    if not obj:
        return None
    fi = p.i32(obj + 0x18)
    if fi is None or fi <= 0 or fi >= (1 << 22):
        return None
    return nm.read_class_name(p, blocks_rt, fi)


def find_meta(p, obc):
    best = None
    for cp, cnt in obc.items():
        if p.u64(cp + 0x10) == cp and (best is None or cnt > best[1]):
            best = (cp, cnt)
    return best[0] if best else None


def find_named_class(p, blocks_rt, obc, meta, name):
    out = []
    for cp in obc:
        if cp == meta or p.u64(cp + 0x10) != meta:
            continue
        fi = p.i32(cp + 0x18)
        if fi and nm.read_class_name(p, blocks_rt, fi) == name:
            out.append(cp)
    return out


# 本构建两条链布局不同（2026-08-31 对运行进程实证；旧代码 name@+0x20/next@+0x18
# 两条链都不匹配，导致反射恒失败回退常量）：
#   Children(+0x48) = UFunction 链（UObject 布局）: Name@+0x18, Next@+0x28
#   ChildProperties(+0x50) = FField 链（FFieldVariant Owner 占 16B）: Name@+0x28, Next@+0x20
CHAIN_LAYOUT = {0x50: (0x28, 0x20), 0x48: (0x18, 0x28)}


def walk_fields(p, blocks_rt, uobj, offset_pos=None):
    """遍历 UClass/UStruct 的 ChildProperties(+0x50) 与 Children(+0x48) 链。
    返回 [(name, offset_or_None)]；offset_pos=None 时只返回名字（用于锚定）。"""
    out = []
    for head_off in (0x50, 0x48):
        name_off, next_off = CHAIN_LAYOUT[head_off]
        cur = p.u64(uobj + head_off)
        n = 0
        while is_ptr(p, cur, 0x48) and n < MAX_FIELDS:
            n += 1
            fi = p.i32(cur + name_off)
            fname = nm.read_class_name(p, blocks_rt, fi) if fi else None
            off = None
            if offset_pos is not None:
                off = p.i32(cur + offset_pos)
            out.append((fname, off))
            nxt = p.u64(cur + next_off)
            if not is_ptr(p, nxt, 0x48) or nxt == cur:
                break
            cur = nxt
    return out


def anchor_offset_pos(p, blocks_rt, meta, pcm_cls):
    """沿 PCM 的 SuperStruct 链找到 Actor 类，用 RootComponent==0x130 锚定
    FField.Offset_Internal 的位置。返回 offset_pos 或 None。"""
    cls = pcm_cls
    actor_cls = None
    for _ in range(8):
        if not is_ptr(p, cls, 0x60):
            break
        cn = fname_of(p, blocks_rt, cls)
        if cn == "Actor":
            actor_cls = cls
            break
        sup = p.u64(cls + 0x40)
        if not is_ptr(p, sup, 0x60):
            break
        cls = sup
    if actor_cls is None:
        print("[calib] !! 未找到 Actor 基类，退回默认 Offset_Internal=0x40")
        return 0x40
    for pos in OFFSET_CANDIDATES:
        flds = walk_fields(p, blocks_rt, actor_cls, pos)
        for fnm, off in flds:
            if fnm == "RootComponent":
                if off == 0x130:
                    print("[calib] FField.Offset_Internal @ +0x%x（RootComponent=0x130 锚定）" % pos)
                    return pos
                break
    print("[calib] !! RootComponent 锚定失败，退回默认 0x40")
    return 0x40


def reflect_struct_fields(p, blocks_rt, obc, meta, struct_name, offset_pos):
    """找 UScriptStruct(name) 并反射其字段偏移 → {name: off}。"""
    for cp in find_named_class(p, blocks_rt, obc, meta, struct_name):
        flds = walk_fields(p, blocks_rt, cp, offset_pos)
        d = {fnm: off for fnm, off in flds if fnm and off is not None}
        if d:
            return d
    return {}


def calibrate(p):
    cands = nm.find_blocks(p)
    if not cands:
        raise RuntimeError("FNamePool Blocks 未找到")
    blocks_rt = p.base + cands[0][0]
    items, nume = t.parse_object_array(p)

    obc = {}
    for obj in items():
        if not obj:
            continue
        cls = p.u64(obj + 0x10)
        if cls:
            obc[cls] = obc.get(cls, 0) + 1
    meta = find_meta(p, obc)
    print("[calib] objects=%d classes=%d" % (nume, len(obc)))

    pcm_classes = find_named_class(p, blocks_rt, obc, meta, "PlayerCameraManager")
    if not pcm_classes:
        raise RuntimeError("未找到 PlayerCameraManager UClass（游戏未启动到主菜单之后？）")
    pcm_cls = pcm_classes[0]
    print("[calib] UClass(PlayerCameraManager)=0x%x" % pcm_cls)

    offset_pos = anchor_offset_pos(p, blocks_rt, meta, pcm_cls)

    # PCM 反射属性表
    flds = walk_fields(p, blocks_rt, pcm_cls, offset_pos)
    pcm_props = {fnm: off for fnm, off in flds if fnm and off is not None}
    for k in ("CameraCachePrivate", "LastFrameCameraCachePrivate", "DefaultFOV",
              "CameraStyle", "ViewTarget", "PendingViewTarget"):
        print("[calib] PCM.%-28s = %s" % (k, hex(pcm_props[k]) if k in pcm_props else "(未反射)"))

    cache_off = pcm_props.get("CameraCachePrivate")
    if cache_off is None:
        # 2026-08-30 --scan 差分实测验证值（POV=cache+4，含两个历史副本 0x1aec/0x20ec）
        cache_off = 0xe9c
        print("[calib] 反射失败，使用实测常量 CameraCachePrivate=0xe9c")

    mvi = reflect_struct_fields(p, blocks_rt, obc, meta, "MinimalViewInfo", offset_pos)
    loc_off = mvi.get("Location", MVI_LOCATION)
    rot_off = mvi.get("Rotation", MVI_ROTATION)
    fov_off = mvi.get("FOV", MVI_FOV)
    print("[calib] MinimalViewInfo: Location=%s Rotation=%s FOV=%s  %s"
          % (hex(loc_off), hex(rot_off), hex(fov_off),
             "(反射)" if mvi else "(4.26 标准回退)"))

    # PCM 实例（排除 Default__ CDO；含 BP 子类——SuperStruct 链可达即算）
    pcm_set = set([pcm_cls])
    for cp in obc:
        if cp in pcm_set:
            continue
        s, hops = cp, 0
        while s and hops < 8:
            if s == pcm_cls:
                pcm_set.add(cp)
                break
            s = p.u64(s + 0x40)
            hops += 1
    insts = []
    for obj in items():
        if obj and p.u64(obj + 0x10) in pcm_set:
            nm_ = fname_of(p, blocks_rt, obj)
            if nm_ and nm_.startswith("Default__"):
                continue
            insts.append(obj)
    print("[calib] PCM 实例=%d 个: %s" % (len(insts), [hex(x) for x in insts[:4]]))
    if not insts:
        raise RuntimeError("当前无 PlayerCameraManager 实例")

    # [fix 2026-08-31] 反射 CameraCachePrivate 必须过 POV 实测哨兵：本构建反射值 0x1ae0
    # 与运行时实测 0xe9c 不符（引擎魔改致 PCM 反射偏移陈旧；Actor 层 RootComponent=0x130
    # 反射与实测一致，故仅对 PCM 缓存偏移加门禁，不过哨兵即回退实测常量）。
    if insts and read_pov(p, insts[0] + cache_off + POV_TO_POV_FIELD,
                          loc_off, rot_off, fov_off) is None:
        print("[calib] !! 反射 CameraCachePrivate=%s 不过 POV 哨兵，回退实测常量 0xe9c"
              % hex(cache_off))
        cache_off = 0xe9c
    pov0 = insts[0] + cache_off + POV_TO_POV_FIELD
    sample = read_pov(p, pov0, loc_off, rot_off, fov_off)
    print("[calib] POV@0x%x 样例: %s" % (pov0, sample))
    ok = sample and all(v is not None for v in sample["pos"] + sample["rot"]) \
        and abs(sample["rot"][1]) <= 190.0
    print("[calib] 数值合理性: %s" % ("OK" if ok else "可疑（进场景后用 --run 观察 pos 是否随移动变化）"))

    return {"blocks_rt": blocks_rt, "pcm_cls": pcm_cls,
            "pcm_set": pcm_set,   # [fix 2026-08-30] 类族随 cal 下发，check_serial 用
            "cache_off": cache_off,
            "loc_off": loc_off, "rot_off": rot_off, "fov_off": fov_off,
            "insts": insts, "offset_pos": offset_pos, "item_addr": t.make_item_addr(p)}


def read_pov(p, pov_addr, loc_off, rot_off, fov_off):
    """读 POV：pos[3] rot[3](Pitch,Yaw,Roll) fov。任何一步失败返回 None。"""
    n = max(loc_off, rot_off, fov_off) + 12
    blob = p.read(pov_addr, n)
    if not blob or len(blob) < n:
        return None
    def f3(off):
        return list(struct.unpack_from("<3f", blob, off))
    try:
        pos = f3(loc_off)
        rot = f3(rot_off)
        fov = struct.unpack_from("<f", blob, fov_off)[0]
    except struct.error:
        return None
    if any(abs(v) > 1e9 for v in pos + rot + [fov]) or fov != fov or not (1.0 < fov < 179.0):
        return None
    return {"pos": [round(v, 3) for v in pos],
            "rot": [round(v, 3) for v in rot],
            "fov": round(fov, 3)}


def check_serial(p, cal, known):
    """粗防槽位复用：实例 ClassPrivate 仍属于 PCM 类族。
    [fix 2026-08-30] 原实现只比对原生 UClass(pcm_cls)，而本构建的活实例是原生子类
    MetaGameplayCameraManager（SuperStruct+0x40 一跳可达；活进程诊断
    runtime_dump_0830/diag_pipeline_0830_stdout.log 实测：现行为 False、族内判定 True，
    唯一非 CDO 实例 0x2804f352ae0 的 ClassPrivate=MetaGameplayCameraManager）——
    发现成功但逐帧校验恒 False → 全文件 null（即 FORMAT.md §2.4-1 的坑）。
    现改为比对 cal["pcm_set"]（发现逻辑同一套 SuperStruct 链可达类族）。"""
    pset = cal.get("pcm_set") or {cal["pcm_cls"]}
    return all(p.u64(o + 0x10) in pset for o in cal["insts"][:2])


def run(p, cal, hz, secs=0.0, out_dir=None):
    """[fix 2026-08-30] 新增 secs 参数：>0 时采样 secs 秒后自动停止（--run --secs N
    定长验证用）；0 = 原行为，直到 Ctrl+C。"""
    loc_off, rot_off, fov_off = cal["loc_off"], cal["rot_off"], cal["fov_off"]
    pov = [o + cal["cache_off"] + POV_TO_POV_FIELD for o in cal["insts"]]
    out_path = OUT_PATH + "_" + time.strftime("%m%d_%H%M%S") + ".jsonl"
    if out_dir:
        out_path = os.path.join(out_dir, os.path.basename(out_path))
    print("[run] %dHz → %s（%s）（Ctrl+C 停止）"
          % (hz, out_path, ("限时 %.0fs" % secs) if secs > 0 else "不限时"))
    f = open(out_path, "a", encoding="utf-8")
    f.write(json.dumps({"ev": "clock_map", "t": time.time(),
                        "povs": [hex(x) for x in pov],
                        "offsets": {"cache": cal["cache_off"], "loc": loc_off,
                                    "rot": rot_off, "fov": fov_off}}) + "\n")
    dt = 1.0 / hz
    t0 = time.time()
    n = 0
    fail_streak = 0
    try:
        while True:
            if secs > 0 and time.time() - t0 >= secs:
                print("[run] 已达 --secs=%.0fs，停止" % secs)
                break
            now = time.time() - t0
            rec = None
            if check_serial(p, cal, None):
                for i, pa in enumerate(pov):
                    d = read_pov(p, pa, loc_off, rot_off, fov_off)
                    if d:
                        rec = {"ev": "cam", "t": round(now, 4), "i": i,
                               "pos": d["pos"], "rot": d["rot"], "fov": d["fov"]}
                        break   # 多实例时取第一个有效（单机只有 1 个）
            f.write(json.dumps(rec) + "\n")
            n += 1
            # [v2.1] 持续失联熔断：连续 >15s 全 null（游戏退出/PCM 实例失效）→
            # 中断本段交由 main 的重附着循环接管。此前会无限写 null（FORMAT §2.4-5）。
            fail_streak = fail_streak + 1 if rec is None else 0
            if fail_streak > hz * 15:
                print("[run] PCM 持续失联 %.0fs，中断等待重附着" % (fail_streak / hz))
                break
            if n % (hz * 2) == 0:
                f.flush()
                print("    t=%6.1fs %s" % (now, ("" if rec is None else
                      "pos=%s rot=%s fov=%s" % (rec["pos"], rec["rot"], rec["fov"]))))
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        f.flush(); f.close()
        print("[run] 结束，%d 帧 → %s" % (n, out_path))


def scan(p, cal_cache_off_missing=True, secs=10.0):
    """备用路线：差分 PCM 对象内存，找随时间变化的 float 区段（人工确认 POV）。"""
    cands = nm.find_blocks(p)
    blocks_rt = p.base + cands[0][0]
    items, nume = t.parse_object_array(p)
    obc = {}
    for obj in items():
        if obj:
            cls = p.u64(obj + 0x10)
            if cls:
                obc[cls] = obc.get(cls, 0) + 1
    meta = find_meta(p, obc)
    pcm_classes = find_named_class(p, blocks_rt, obc, meta, "PlayerCameraManager")
    if not pcm_classes:
        raise RuntimeError("未找到 PlayerCameraManager UClass")
    native_pcm = pcm_classes[0]
    # 子类扩展：SuperStruct(+0x40) 链上能到达原生 PCM 的类（如 BP_PlayerCameraManager_C）
    pcm_set = set([native_pcm])
    for cp in obc:
        if cp in pcm_set:
            continue
        s, hops = cp, 0
        while s and hops < 8:
            if s == native_pcm:
                pcm_set.add(cp)
                break
            s = p.u64(s + 0x40)
            hops += 1
    insts = []
    for obj in items():
        if obj and p.u64(obj + 0x10) in pcm_set:
            nm_ = fname_of(p, blocks_rt, obj)
            if nm_ and not nm_.startswith("Default__"):
                insts.append(obj)
    if not insts:
        raise RuntimeError("无 PCM 实例")
    inst = insts[0]
    N = 0x8000
    print("[scan] PCM=0x%x 读 0x%x 字节，每 0.5s 一次，共 %.0fs（请在游戏里转动视角/移动）"
          % (inst, N, secs))
    snaps = []
    t0 = time.time()
    while time.time() - t0 < secs:
        blob = p.read(inst, N)
        if blob:
            snaps.append(blob)
        time.sleep(0.5)
    if len(snaps) < 3:
        print("[scan] 样本不足"); return
    ref = snaps[0]
    changing = []
    for off in range(0, N - 4, 4):
        vals = [struct.unpack_from("<f", s, off)[0] for s in snaps]
        if any(v != v or abs(v) > 1e12 for v in vals):
            continue
        if len(set(vals)) > 1:
            changing.append((off, vals))
    print("[scan] 变化中的 float 区段 %d 个；疑似 POV（rotation 在旁 + fov 合理）:" % len(changing))
    byoff = dict(changing)
    for off, vals in changing:
        # 猜 POV 起点：pos=off-4? rot=pos+0xC fov=+0x18 的组合签名
        cand_pov = None
        for pov in range(max(0, off - 0x18), off + 4, 4):
            try:
                rot = [struct.unpack_from("<f", snaps[0], pov + 0xC + k * 4)[0] for k in range(3)]
                fov = struct.unpack_from("<f", snaps[0], pov + 0x18)[0]
                rv = [[struct.unpack_from("<f", s, pov + 0xC + k * 4)[0] for k in range(3)] for s in snaps]
                fv = [struct.unpack_from("<f", s, pov + 0x18)[0] for s in snaps]
                rot_ch = any(any(a != b for a, b in zip(r1, r2)) for r1, r2 in zip(rv, rv[1:]))
                fov_ok = all(1.0 < v < 179.0 for v in fv) and len(set(fv)) >= 1
                if rot_ch and fov_ok:
                    cand_pov = pov
                    break
            except struct.error:
                continue
        if cand_pov is not None and abs(off - cand_pov) < 0x1C:
            cache = cand_pov - POV_TO_POV_FIELD
            print("  → 疑似 POV=0x%x (CameraCachePrivate=0x%x)  首帧 pos/rot: %s"
                  % (cand_pov, cache, byoff.get(cand_pov + 0xC, byoff.get(off))))
    print("[scan] 提示：真正的 CameraCachePrivate 偏移 = POV - 4；把它和 --calibrate 的反射值对照。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--hz", type=int, default=50)
    ap.add_argument("--secs", type=float, default=0.0,
                    help="[fix 2026-08-30] --run 限时秒数（0=不限时）；--scan 未显式给时回退 10s")
    ap.add_argument("--wait", action="store_true", help="循环等待（每 15s 重试）")
    ap.add_argument("--out-dir", default=None,
                    help="[产品化 2026-09-06] 产物目录（默认仍脚本目录）")
    args = ap.parse_args()

    pid = t.find_pid()
    if pid is None:
        if not args.wait:
            print("!! 游戏副本未运行（先启动 FPSAimTrainer-Win64-Shipping.exe）")
            sys.exit(2)
        # [v2.1] --wait 时初始也等游戏出现（此前只重试校准，不等进程）
        print("[wait] 游戏未运行，每 10s 检查（Ctrl+C 退出）...")
        while pid is None:
            time.sleep(10)
            try:
                pid = t.find_pid()
            except Exception:
                pid = None
    p = t.Proc(pid)
    print("[proc] pid=%d base=0x%x" % (pid, p.base))

    scan_secs = args.secs if args.secs > 0 else 10.0
    if args.scan:
        scan(p, True, scan_secs)
        return

    cal = None
    attempts = 40 if args.wait else 1
    for i in range(attempts):
        try:
            cal = calibrate(p)
        except (RuntimeError, OSError) as e:
            print("[calib] 失败: %s" % e)
            cal = None
        if cal:
            break
        if args.wait and i < attempts - 1:
            print("[wait] 15s 后重试...")
            time.sleep(15)
    if not cal:
        sys.exit(3)
    if args.scan:
        scan(p, True, scan_secs)
    elif args.run:
        # [v2.1] 重附着循环（对齐 target_poll2 main；此前采样中断即退出，FORMAT §2.4-5）。
        # 每次重新附着 = 新文件新 clock_map；--secs>0 定长验证模式不循环。
        while True:
            run(p, cal, args.hz, args.secs, args.out_dir)
            if args.secs > 0:
                break
            cal = None
            while cal is None:
                pid = None
                while pid is None:
                    print("[wait] 等待游戏进程（每 10s 检查，Ctrl+C 退出）...")
                    time.sleep(10)
                    try:
                        pid = t.find_pid()
                    except Exception:
                        pid = None
                try:
                    p = t.Proc(pid)
                    print("[proc] 重新附着 pid=%d base=0x%x" % (pid, p.base))
                    cal = calibrate(p)
                except Exception as e:
                    print("[wait] 重新附着失败: %s" % e)
    else:
        print("[done] 校准完成（--run 采样 / --scan 差分复核）")


if __name__ == "__main__":
    main()
