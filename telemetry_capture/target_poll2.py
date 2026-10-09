# -*- coding: utf-8 -*-
"""target_poll2.py — v3：以 TargetHudComponent 的 Owner 锁定目标（不依赖目标类名）。

原理：每个训练目标 actor 都挂一个 TargetHudComponent（实测本构建 5 目标 → +5 实例）。
UObject::OwnerPrivate(+0x20) 即目标 actor。逐帧读 actor → RootComponent → ComponentToWorld。
用法:
    python target_poll2.py --run [--hz 200] [--wait]

[fix 2026-08-30b] 全量重扫从采样循环内联执行改为后台线程：原实现每次重扫阻塞采样
~3.1s（85k 对象全扫），晚间 0830 会话 96 次/325s（17% 占空）→ 死亡时间戳成块前移 +
重生跳速度稀释 → cleaner 切段崩坏（A2 81.83% FAIL）。重扫语义逐条保持（全槽位解析 +
cls_seen 动态重建族集 + Package/Default__ CDO 排除 + 槽位零化摘除），采样帧间隔不再
受重扫影响。跨线程依据：kernel32 句柄进程级（非线程亲和），ctypes CDLL 调用释放 GIL，
tp1.Proc.read 无共享可变状态（每次新建缓冲）。

[spd 2026-10-04] 采样提速 实测≈31Hz → 200Hz+（schema/内存链/发现摘除语义零改动）：
1. 31ms 帧间 dt 的根因是 Windows time.sleep 的 15.625ms 定时器量化（50Hz 名义
   sleep(20ms) 实际落在 2 个 tick=31.25ms，与 FORMAT §1.3-1 实测 dt 中位 31.0/p95
   32.0ms 吻合），不是 RPM 本身（5 目标 30 次小读仅 ~0.3ms）。改为 winmm
   timeBeginPeriod(1)（进程级，run() finally 归还）+ perf_counter 绝对节拍 + 混合
   等待（粗睡到剩 ~1.5ms 后自旋补齐）。帧时间戳仍取 time.time()-t0（epoch 差，语义不变）。
2. 0.5s 差分发现从采样循环内联（n%25）移入后台线程：200Hz 帧预算 5ms，全数组 chunk
   差分偶发 2~5ms 会周期性打穿预算。状态 snap/items 随迁差分线程私有；加目标仍收口
   st_lock（与 rescan 线程同协议，且差分只增不删——摘除仍由逐帧 serial 检查 + 后台
   重扫负责，并发面更小）。良性副作用：启动首帧不再被种子扫描阻塞（§1.3-1 的 1.05s
   首帧停顿消失），种子扫描期间的空窗帧由 calibrate 目标覆盖。
3. 平移 x,y,z 由 3 次 f32(4B) RPM 合并为一次 12B 批量读（FTransform 平移 12B 连续，
   同地址同字节，struct "<3f" ≡ 逐个 "<f"；读失败/短读整点跳过 ≡ 原任一分量 None
   跳过；NaN 通路不变）。每帧每目标 RPM 6→3 次。
4. 差分/活性探测/flush/scale 旁线由"按帧数取模"改为"按时间门控"——原取模周期绑定
   50Hz（n%25=0.5s 等），200Hz 下会被放大 4 倍；改为墙钟门控后 0.5s/2s/1s 语义保持。
5. main() 默认 hz 50→200（--hz 可覆盖）。
离线自测：test_poll_perf.py（无游戏，mock 内存层跑真实采样循环）。
"""
import ctypes
import json
import math
import os
import struct
import sys
import threading
import time
import traceback

import tp1 as t
import names as nm
import camera_probe as cp   # [flags 2026-10-05] is_ptr/fname_of/CHAIN_LAYOUT/anchor_offset_pos 复用

OUT_PATH = t.OUT_PATH

# [flags 2026-10-05] hp 读值有效性域（exp8 同款）：非有限（NaN/Inf）或出域判无效，
# 该帧无标志（不触发死亡事件）。实测存活 hp 1.0/54.0，1e6 上界裕量充足。
HP_MAX = 1e6

# [fix 2026-08-30] 全量重扫周期（秒）：兜底 chunk 差分的机制盲区（见 discovery 注释）
# [fix 2026-08-30b] 周期语义不变；执行移至后台线程，不再阻塞采样循环
RESCAN_SECS = 20.0

# [fix 2026-10-09] 盲窗防线（54109 案：serial 读失败误摘 + diff 对池化复用失明
# → 20.8s 盲窗丢 3 死）：
SERIAL_NONE_STREAK = 3         # serial 读失败容忍帧数（200Hz 下 15ms）：None 与真
                               # 变化同等提案会把瞬态 RPM 读失败的目标误摘出发现层；
                               # 连续超限才允许进入移除提案（提案仍走既有复核）
SERIAL_NONE_HARD_DROP_S = 15.0  # serial 连续读失败强制摘除阈（对齐 dead_streak 15s
                               # 熔断语义）：防提案被复核屡次挡回后僵尸槽位
                               # 每帧 3 次无效 RPM 永久空转
BLIND_STALE_S = 2.0            # 盲窗看门狗判失明阈：flag 目标 hp/dc 双 None 连续
                               # 该时长（serial 在册）→ 触发带外重扫。判别性：真死
                               # 尸体 hp=0 可读、垂死停更坐标冻结但旗标可读，均不触发
BLIND_RESCAN_COOLDOWN_S = 2.0  # 看门狗触发重扫限频（恢复上界 ≈ 2.0s 检测+~2.1s 扫描）

# [spd 2026-10-04] Windows 默认定时器分辨率 15.625ms：time.sleep(5ms) 会睡成 ~15.6ms、
# sleep(20ms) 落在 2 个 tick=31.25ms —— 这是旧版"名义 50Hz 实测 31Hz"的根因。采样期间
# 把本进程定时器分辨率提到 1ms（winmm，进程级；run() finally 归还），配合绝对节拍 +
# 混合等待，帧间隔抖动 ≪1ms。调用失败（非 Windows 等）静默降级：混合等待仍可用，
# 只是 sleep 段精度退回系统默认。
_winmm = None


def _timer_res_begin():
    global _winmm
    try:
        _winmm = ctypes.WinDLL("winmm")
        _winmm.timeBeginPeriod(1)
    except Exception:
        _winmm = None


def _timer_res_end():
    if _winmm is not None:
        try:
            _winmm.timeEndPeriod(1)
        except Exception:
            pass


def _wait_scroll(deadline):
    """混合等待到 perf_counter 绝对期限：剩 >1.5ms 先 time.sleep（让出 CPU），
    末段 ≤1.5ms 自旋补齐，deadline 前后误差 ≪1ms。自旋是 Python 字节码循环，
    Ctrl+C 可正常打断；单帧自旋上限 ~1.5ms（200Hz 预算 5ms 的 30%，
    专用采集进程可接受）。"""
    while True:
        rem = deadline - time.perf_counter()
        if rem <= 0:
            return
        if rem > 0.0015:
            time.sleep(rem - 0.001)


def nm_subclasses(p, root, cls_set, max_hops=8):
    """[fix 2026-08-30] root 类 + SuperStruct(+0x40) 链可达的全部子类。
    精确类相等匹配会漏 BP/原生子类——与 camera_probe 相机全 null 同源的坑
    （活实例是 MetaGameplayCameraManager 这类原生子类时逐帧校验恒 False）。"""
    out = {root}
    for cls in cls_set:
        if not cls or cls in out:
            continue
        s, hops = cls, 0
        while s and hops < max_hops:
            if s == root:
                out.add(cls)
                break
            s = p.u64(s + 0x40)
            hops += 1
    return out


def find_named_class(p, blocks_rt, obc, meta, name):
    for cp_ in obc:
        if cp_ == meta:
            continue
        c2 = p.u64(cp_ + 0x10)
        if not c2 or p.u64(c2 + 0x10) != meta:
            continue
        fi = p.i32(cp_ + 0x18)
        if fi and nm.read_class_name(p, blocks_rt, fi) == name:
            return cp_
    return None


# ---------------- [flags 2026-10-05] 目标死亡标志位（Health/DeathCount） ----------------
# 设计与活体实证见 .zcode/anchor-research-1005/REPORT.md FLAGS-FEASIBILITY：
#   FPSCharacter_C→MetaCharacter 类链带 Health(float,>0 活) 与 DeathCount(int,单调
#   死亡计数)。偏移按属性名经 UE4 运行时反射定位（camera_probe.walk_fields 同款
#   ChildProperties FField 链），per UClass 指针缓存——不写死偏移、不进 offsets.json，
#   游戏更新后属性名不变即自动重定位。类链无 MetaCharacter（计时靶/一枪死家族）
#   → 无标志，该目标回退坐标推导，不 fail-fast。

FLAG_FIELDS = ("Health", "DeathCount")

# [flags 2026-10-05c] dc 合理上限：身份换绑后字段类型错位（float 比特被当 i32 读）
# 时，读数 ≥ 0x3F800000(=1065353216，即 1.0f 的比特)——1e6 上界全覆盖此类垃圾
# （真实死亡计数会话内远小于此）。超限帧按不可信冻结处理（update_flag_state 对
# None 输入维持冻结语义）。cleaner 侧 DC_MAX 同值（模块独立，勿单边改动）。
DC_MAX = 1000000


def sanitize_flags(hp_raw, dc_raw):
    """[flags 2026-10-05] 错读防护①：hp 有限性/非负域门 + dc 非负/上限门。
    NaN/Inf/负/出域（hp>HP_MAX、dc>DC_MAX，自由内存/类型错位复读特征）→
    None（该帧无标志，不触发死亡事件）。dc 负值同理（死亡计数不可能为负）。"""
    hp = hp_raw if (hp_raw is not None and math.isfinite(hp_raw)
                    and 0.0 <= hp_raw <= HP_MAX) else None
    dc = dc_raw if (dc_raw is not None and 0 <= dc_raw <= DC_MAX) else None
    return hp, dc


def resolve_flags(p, blocks_rt, offset_pos, owner, cache):
    """owner 类链上按属性名反射 Health/DeathCount 偏移（per UClass 缓存）。
    返回 (hp_off, dc_off, class_name)，找不到的分量 None。offset_pos 为
    FField.Offset_Internal 在 FField 内的位置（camera_probe.anchor_offset_pos
    以 RootComponent==0x130 锚定）；None → 直接不可用。实现同研究脚本
    exp8_flag_watch.resolve_flags（真机已跑通）。"""
    ocls = p.u64(owner + 0x10) if owner else None
    if not ocls or offset_pos is None:
        return (None, None, None)
    if ocls in cache:
        return cache[ocls]
    cn0 = cp.fname_of(p, blocks_rt, ocls)
    offs = {}
    lvl = ocls
    for _ in range(8):          # 类链最深 8 级（实测 FPSCharacter_C→Actor 6 级）
        if not cp.is_ptr(p, lvl, 0x60):
            break
        name_off, next_off = cp.CHAIN_LAYOUT[0x50]   # ChildProperties FField 链
        cur = p.u64(lvl + 0x50)
        n = 0
        while cp.is_ptr(p, cur, 0x50) and n < 600:
            n += 1
            fi = p.i32(cur + name_off)
            fname = nm.read_class_name(p, blocks_rt, fi) if fi else None
            off = p.i32(cur + offset_pos)
            esize = p.i32(cur + 0x3C)    # ElementSize：float/int32 均为 4
            if fname and off is not None and fname in FLAG_FIELDS \
                    and fname not in offs and (esize or 4) == 4:
                offs[fname] = off
            nxt = p.u64(cur + next_off)
            if not cp.is_ptr(p, nxt, 0x50) or nxt == cur:
                break
            cur = nxt
        if len(offs) == len(FLAG_FIELDS):
            break
        sup = p.u64(lvl + 0x40)          # SuperStruct
        if not cp.is_ptr(p, sup, 0x60) or sup == lvl:
            break
        lvl = sup
    got = (offs.get("Health"), offs.get("DeathCount"), cn0)
    cache[ocls] = got
    return got


def update_flag_state(fstate, idx, hp, dc):
    """单目标死亡标志状态机（纯逻辑，test_death_flags 直测）。

    输入本帧已过有效性门（有限性/非负）的 hp/dc，None=无标志/读失败/无效。
    返回 (hp_out, dc_out, death)：
      hp_out/dc_out —— 身份存疑帧（见下）弃本帧标志返回 (None, None)；
      death         —— True 表示本帧产生一条 death 事件：主判据 = dc 恰 +1
                       （同一槽位一个 5ms 帧内物理上不可能死两次，跳变更大火者
                       是池化槽位身份更替/瞬态错读——重新播种基线不当死亡，
                       防 1005 冒烟局 dc=158 半路幻影边沿）；次判据 = hp 连续
                       2 帧==0（无 dc 的标志家族回退用，防错读确认）。
    dc 回退（dc<pdc）：hp 有效视为计数器复位（换图/重开）——重新播种基线；
    hp 不可信（None）判自由内存复读——弃本帧标志且状态不更新（原读法，防
    永久失灵只放行 hp 可信帧）。
    状态 fstate[idx] = [prev_dc, zero_streak, fired]；首次观察只播种不触发
    （防新附着时把附着前旧值当边沿）。"""
    st = fstate.get(idx)
    if hp is None and dc is None:
        return None, None, False
    if st is None:
        fstate[idx] = [dc, 1 if hp == 0.0 else 0, False]
        return hp, dc, False
    pdc, zst, fired = st
    if dc is not None and pdc is not None and dc < pdc:
        if hp is not None:
            fstate[idx] = [dc, 1 if hp == 0.0 else 0, False]
            return hp, dc, False    # 计数器复位且 hp 可信：重播种，不当死亡
        return None, None, False    # 自由内存复读：本帧标志不可信，状态不更新
    if dc is not None and pdc is not None and dc - pdc > 1:
        fstate[idx] = [dc, 1 if hp == 0.0 else 0, False]
        return hp, dc, False        # 跳变>1：身份更替/瞬态错读，重播种不当死亡
    death = False
    if dc is not None and pdc is not None and dc > pdc:
        death = True                # 主判据：DeathCount 恰 +1
        fired = True
        zst = 0
    if hp == 0.0:
        zst += 1
        if zst >= 2 and not fired:
            death = True                # 次判据：hp 连续 2 帧为 0
            fired = True
    elif hp is not None:
        zst = 0                         # hp 回到正数：重新武装次判据
        fired = False
    fstate[idx] = [dc, zst, fired]
    return hp, dc, death


def calibrate(p, items, nume):
    # [fix 2026-10-04] 表驱动优先 + 空 cands 守卫：原裸调 find_blocks 后直接
    # cands[0][0]，返回 [] 时抛 IndexError——而 main 重试循环只捕
    # (RuntimeError, OSError)，IndexError 穿透杀进程，第一次校准失败即零重试
    # 当场崩（1004 案 target 通道死因）。改 raise RuntimeError 走既有重试链。
    cands = nm.find_blocks_table_first(p)
    if not cands:
        raise RuntimeError("FNamePool Blocks 未找到（表直读与扫描均失败）")
    blocks_rt = p.base + cands[0][0]
    obc = {}
    for obj in items():
        if not obj:
            continue
        cls = p.u64(obj + 0x10)
        if cls:
            obc[cls] = obc.get(cls, 0) + 1
    meta = None
    best = -1
    for cls, cnt in obc.items():
        if p.u64(cls + 0x10) == cls and cnt > best:
            meta, best = cls, cnt

    hud = find_named_class(p, blocks_rt, obc, meta, "TargetHudComponent")
    if hud is None:
        raise RuntimeError("没找到 TargetHudComponent 类")
    print("[calib] TargetHudComponent = 0x%x" % hud)
    tmt = find_named_class(p, blocks_rt, obc, meta, "TheMetaTrainerTarget")
    print("[calib] TheMetaTrainerTarget = %s" % ("0x%x" % tmt if tmt else None))
    tmt_cdo_off = None
    tmt_cdo = None
    if tmt:
        for off in range(0x80, 0x480, 8):
            v = p.u64(tmt + off)
            if v and p.u64(v + 0x10) == tmt:
                tmt_cdo_off, tmt_cdo = off, v
                break
        print("[calib] 其 CDO @ UClass+0x%x = %s" % (tmt_cdo_off or 0, hex(tmt_cdo) if tmt_cdo else None))

    # [fix 2026-08-30] 类族匹配 + 挑战候选族。数据佐证（target_poll_out_0830_043716）：
    # 第一局 t=109.3 差分正常发现 6 目标、t=220.9 正常摘除；第二局 t≥304s（Enter 重开的
    # 挑战）全程 0 新增。差分与玩家朝向无关（全数组字节差分），故“门类太窄 / 目标 actor
    # 池化复用不产生任何数组事件”是仅有的自洽解释。extra=内存中已加载的目标族原生类
    # [inferred：挑战/机器人目标候选]。
    cls_set0 = set(obc.keys())
    hud_set = nm_subclasses(p, hud, cls_set0)
    family_roots = []
    if tmt:
        family_roots.append(tmt)
    for extra in ("TheMetaTrainerTargetPlatform", "TheMetaTrainerReloadTarget",
                  "CTargetNPC", "CSpawnTargetNPC"):
        cls = find_named_class(p, blocks_rt, obc, meta, extra)
        if cls:
            family_roots.append(cls)
    # 注：TheMetaTrainerTargetStart 不入族——地图常驻出生点标记，入族会引入永久幽灵
    family_set = set()
    for root in family_roots:
        family_set |= nm_subclasses(p, root, cls_set0)
    print("[calib] hud_set=%d family_roots=%d family_set=%d（含 SuperStruct 子类）"
          % (len(hud_set), len(family_roots), len(family_set)))

    # 全量扫：HUD 组件实例 → owner（目标 actor）
    targets = {}       # item_index -> owner_ptr
    owner_classes = {}  # owner -> 类名
    idx = 0
    for obj in items():
        if obj and p.u64(obj + 0x10) in hud_set:   # [fix 2026-08-30] 精确类 → 族匹配
            owner = p.u64(obj + 0x20)
            if owner:
                cls = p.u64(owner + 0x10)
                fi = p.i32(cls + 0x18) if cls else 0
                cn = nm.read_class_name(p, blocks_rt, fi) if (cls and fi) else None
                if cn and cn not in ("Package",):  # 排除 CDO 的野 owner
                    targets[idx] = owner
                    owner_classes[owner] = cn
        idx += 1
    # 去重（一个目标可能多个 HUD？保守按 owner 去重，保留最小 idx）
    seen_owner = {}
    for i in sorted(targets):
        o = targets[i]
        if o in seen_owner.values():
            del targets[i]
        else:
            seen_owner[i] = o
    print("[calib] 目标（HUD owner）: %d 个" % len(targets))
    for o, cn in owner_classes.items():
        print("    owner=0x%x class=%s" % (o, cn))
    if len(targets) < 3:
        print("!! owner 不足 3 个（菜单/残留态），等真正进场")
        return None

    # 偏移：本构建实测验证常量（2026-08-29 两轮真值轨迹验证），不再自动猜测
    root_off = 0x130
    xform_off = 0x1c0
    tl = list(targets.values())

    print("[calib] 使用验证偏移: RootComponent=0x130 ComponentToWorld=0x1c0")
    print("[calib] 样例:")
    samples = []
    for tt in tl[:6]:
        c2 = p.u64(tt + root_off)
        pos = [p.f32(c2 + xform_off + 16 + i * 4) for i in range(3)] if c2 else None
        samples.append(pos)
        print("    owner=0x%x pos=%s" % (tt, ["%.1f" % v for v in pos] if pos else None))
    # [v2.1] 偏移哨兵：样例位置须全部落在场景域内（|coord|≤8192，同 cleaner 野值界）
    # 且至少一个非零。全出域/全零 ⇒ 疑似游戏更新使 0x130/0x1c0 失效——fail-fast
    # 拒绝启动（宁可停采，不可静默产出错误坐标）。
    ok_samples = [s for s in samples if s and all(v is not None for v in s)]
    if ok_samples and not any(all(abs(v) <= 8192 for v in s) and
                              any(abs(v) > 1.0 for v in s) for s in ok_samples):
        raise RuntimeError(
            "偏移哨兵: ComponentToWorld 样例全部出域或为零 —— 疑似游戏更新使 "
            "0x130/0x1c0 失效，拒绝采样（更新后需重验偏移）")

    # [flags 2026-10-05] 死亡标志位偏移解析：运行时反射按属性名沿类链定位
    # （per UClass 缓存）。失败不 fail-fast（该目标回退坐标推导，录制继续），
    # 降级可观测：flag_reflection = ok(≥1 目标类双字段解析) | degraded(锚定
    # 机制在但零解析——类链无 MetaCharacter 或属性名被改) | unavailable(PCM/
    # Offset_Internal 锚定机制不可用)。同 camera_probe reflection=degraded 先例。
    flag_offset_pos = None
    try:
        pcm_cls = find_named_class(p, blocks_rt, obc, meta, "PlayerCameraManager")
        if pcm_cls:
            flag_offset_pos = cp.anchor_offset_pos(p, blocks_rt, meta, pcm_cls)
    except Exception as e:
        print("[calib] flag 反射锚定失败: %s" % e)
        flag_offset_pos = None
    flag_cache = {}     # UClass 指针 -> (hp_off, dc_off, class_name)，跨线程共享只增
    n_flagged = 0
    owners0 = set(targets.values())
    if flag_offset_pos is not None:
        for o in owners0:
            hp_o, dc_o, cn = resolve_flags(p, blocks_rt, flag_offset_pos, o,
                                           flag_cache)
            if hp_o is not None and dc_o is not None:
                n_flagged += 1
                print("[calib] flag: %s Health=%s DeathCount=%s"
                      % (cn, hex(hp_o), hex(dc_o)))
    if flag_offset_pos is None:
        flag_reflection = "unavailable"
    elif n_flagged > 0:
        flag_reflection = "ok"
    else:
        flag_reflection = "degraded"
        print("[calib] !! flag_reflection=degraded（目标类链无 MetaCharacter 或 "
              "Health/DeathCount 反射不到——属性名被改/重构的信号，需人工核对；"
              "死亡推导回退坐标路径）")
    print("[calib] flag_reflection=%s（%d/%d 目标类有标志位）"
          % (flag_reflection, n_flagged, len(owners0)))
    return {"targets": targets, "item_addr": t.make_item_addr(p),
            "root_off": root_off, "xform_off": xform_off,
            "hud": hud, "owner_classes": owner_classes, "last_index": idx,
            "tmt": tmt, "tmt_cdo": tmt_cdo,
            "blocks_rt": blocks_rt,   # [fix 2026-08-30] 家族路径读 CDO 名字用
            "hud_set": hud_set, "family_roots": family_roots,
            "family_set": family_set,   # [fix 2026-08-30] 族集随 cal 下发
            "flag_offset_pos": flag_offset_pos, "flag_cache": flag_cache,
            "flag_reflection": flag_reflection}   # [flags 2026-10-05]


def run(p, cal, hz, scale=False, out_dir=None):
    """[v2.1] scale=True 时每秒每目标额外读 FTransform.Scale3D（ComponentToWorld
    内 quat@0x0/平移@0x10/Scale3D@0x20），写 {"ev":"scale","t","addr","s":[sx,sy,sz]}
    旁线——目标尺寸缺口的内存侧验证/兜底路线（.sce 配置路线见 sce_bb.py）。
    cleaner 配套补丁会跳过非 frame 行，帧 schema 不变。"""
    root_off, xo = cal["root_off"], cal["xform_off"]
    item_addr = cal["item_addr"]
    tmt_cdo = cal.get("tmt_cdo")
    blocks_rt = cal["blocks_rt"]     # [fix 2026-08-30] 家族路径读 CDO 名字用
    # [fix 2026-08-30] 可热重建的族集（rescan 时按当前内存类集动态重建，
    # 新加载的场景 BP 子类自动纳入）；hud/tmt 精确类指针不再直接用于匹配
    sets = {"hud": set(cal["hud_set"]), "family": set(cal["family_set"])}
    targets = dict(cal["targets"])   # item_index -> owner
    owners = set(targets.values())   # 去重用
    serials = {i: p.u32(item_addr(i) + 0x10) for i in targets}
    # [spd 2026-10-04] chunk 差分的状态（snap/items）与布局解析随差分线程私有
    # （原先在采样主线程），采样循环内不再有任何全数组级工作
    # [fix 2026-08-30b] targets/owners/serials 由采样主线程与后台重扫线程双方读写，
    # 变更一律收进 st_lock；sets 单写者（后台线程），读侧靠 GIL 原子取值
    st_lock = threading.Lock()
    stop = threading.Event()
    # [fix 2026-10-09] 盲窗看门狗 → 带外重扫请求：flag 目标 hp/dc 双 None 连续
    # BLIND_STALE_S（serial 在册=目标应可读）判「疑似失明」，置位请求重扫线程
    # 立即全量重扫（恢复上界 2.0s 检测 + ~2.1s 扫描 ≈ 4s，替代 20s 盲等）
    rescan_req = threading.Event()
    # [flags 2026-10-05] 标志位偏移与死亡边沿状态（与 serials 同生命周期：
    # 发现时解析、摘除时清理）：
    #   flg    idx -> (hp_off, dc_off)，None 分量=该类无标志（回退坐标推导）
    #   fstate idx -> [prev_dc, zero_streak, fired]（update_flag_state 状态机）
    # 写入方：calibrate 初装（下）、差分/重扫线程发现新目标时（st_lock 内）；
    # 采样线程只读 flg、私有 fstate（单线程消费）。反射复用函数头 blocks_rt。
    flag_offset_pos = cal.get("flag_offset_pos")
    flag_cache = cal.get("flag_cache")
    if not isinstance(flag_cache, dict):
        flag_cache = {}
    flg = {}
    fstate = {}
    for _i, _o in targets.items():
        flg[_i] = (resolve_flags(p, blocks_rt, flag_offset_pos, _o, flag_cache)[:2]
                   if flag_offset_pos is not None else (None, None))
    out_path = OUT_PATH.replace(".jsonl", "_" + time.strftime("%m%d_%H%M%S") + ".jsonl")
    if out_dir:
        out_path = os.path.join(out_dir, os.path.basename(out_path))
    print("[run] %dHz → %s（Ctrl+C 停止）；chunk 差分发现开启；全量重扫=后台线程"
          % (hz, out_path))
    f = open(out_path, "a", encoding="utf-8")
    # [v2.1] 首行 clock_map：本文件绝对 epoch 锚（此前只有文件名墙钟 ±1s）。
    # 先写锚再取 t0 ⇒ 帧 epoch ≈ clock_map.t + t（<1ms，与 camera_probe 同序约定）。
    # cleaner 配套：跳过非 frame 行，并把锚透传为 rounds_index 的 t0_epoch。
    # [flags 2026-10-05] 附 flag_reflection（ok/degraded/unavailable），downstream
    # 审计与 merge manifest 透传用（同 camera reflection=degraded 先例）。
    f.write(json.dumps({"ev": "clock_map", "t": time.time(),
                        "note": "epoch anchor; frame t = time.time()-t0, "
                                "t0 taken right after this line",
                        "flag_reflection":
                            cal.get("flag_reflection") or "unavailable"}) + "\n")
    t0 = time.time()
    n = 0
    # [fix 2026-08-31] 进程死亡熔断：RPM 对已退出进程返回 None 而不抛异常，run()
    # 原先会永远写空帧、main() 的重附着循环永远不触发（实测 0831 重启验证发现；
    # FORMAT §1.3-8 的"confirmed"只覆盖抛异常的死法）。每 0.5s 读一次对象数组头
    # 做活性探测（[spd 2026-10-04] 起为时间门控，原为每 25 帧搭差分周期），
    # 连续 >15s 失败即中断本段交由 main 重附着。
    dead_streak = 0
    # [fix 2026-10-09] serial 读失败容忍 + 盲窗看门狗状态（idx 键，随摘除清理）
    none_streak = {}     # idx -> 连续 serial None 帧数
    none_since = {}      # idx -> 首个 None 帧的 now（硬摘除计时）
    blind_since = {}     # idx -> 首个 hp/dc 双 None 帧的 now
    blind_suspect = set()  # 已判疑似失明并触发过重扫的 idx
    last_blind_rescan = 0.0

    def read_num():
        return (p.i32(p.base + t.RVA_GUOBJECTARRAY + 0x24),
                p.i32(p.base + t.RVA_GUOBJECTARRAY + 0x10 + 0x1C))

    def discover_diff(snap, items, cl, per_chunk, stride):
        """[fix 2026-08-30b] 差分发现。原 discovery() 的 force 路径拆出到后台
        rescan_worker；差分路径逻辑与 [fix 2026-08-30] 一致（族匹配 + Package 野
        owner 排除 + Default__ CDO 排除）。共享态变更收敛到函数末尾的 st_lock 内。
        [spd 2026-10-04] 由采样主线程内联（n%25≈0.5s）改为 diff_worker 后台线程
        调用；snap/items 由调用方持有（差分线程私有）；本函数只增不删——摘除仍由
        采样 serial 检查 + 后台重扫负责。"""
        nume, numc = read_num()
        if not nume or not numc:
            return  # 游戏退出/读数失败：本轮跳过
        # [fix 2026-08-30b] 首轮差分只播种基线（snap/items），不做类判定：
        # 冷基线逐槽类读取 = 主线程内联 ~85k 次 RPM（实测 2.7s 空洞，0830 晚间
        # 数据 0→3.22s 同源的"首帧全量解析"既有开销）。附着时已存在对象的发现
        # 由 calibrate + 后台重扫首轮覆盖；此后正常差分不受影响。
        seed = not snap
        cand = {}          # idx -> (ptr, cls, sn)：待类判定槽位（统一在第二阶段处理）
        for c in range(numc):
            ca = p.u64(cl + c * 8)
            if not ca:
                continue
            take = min(per_chunk, max(0, nume - c * per_chunk))
            blob = p.read(ca, take * stride)
            if not blob:
                continue
            if snap.get(c) == blob:
                continue
            m = len(blob) // stride
            base_i = c * per_chunk
            for i in range(m):
                off = i * stride
                ptr = struct.unpack_from("<Q", blob, off)[0]
                sn = struct.unpack_from("<I", blob, off + 0x10)[0]
                idx = base_i + i
                if seed:
                    items[idx] = (ptr, sn)
                    continue
                old = items.get(idx, (0, 0))
                if ptr == old[0] and sn == old[1]:
                    continue
                items[idx] = (ptr, sn)
                if ptr == 0:
                    continue   # 摘除交给采样循环的 serial 检查 + 后台重扫
                cls = p.u64(ptr + 0x10)
                cand[idx] = (ptr, cls, sn)
            snap[c] = blob
        hud_set, fam_set = sets["hud"], sets["family"]
        adds = []          # (idx, sn, 对象指针, 是否 HUD 路径)
        for idx, (ptr, cls, sn) in cand.items():
            if cls in hud_set:
                owner = p.u64(ptr + 0x20)
                if not owner:
                    continue
                # [fix 2026-08-30] 与 calibrate 对齐：owner 类=Package 的野 owner 排除
                # （rescan 实测发现 Default__TargetHudComponent CDO 的 owner 是
                # Package 对象 0x28031735600，会被全量重扫捞入）
                ocls = p.u64(owner + 0x10)
                fi2 = p.i32(ocls + 0x18) if ocls else None
                ocn = nm.read_class_name(p, blocks_rt, fi2) if fi2 else None
                if ocn == "Package":
                    continue
                adds.append((idx, sn, owner, True))
            elif cls in fam_set and ptr != tmt_cdo:
                # 家族直采：CDO/子类 CDO 一并排除（名字前缀 Default__，覆盖子类 CDO）
                fi = p.i32(ptr + 0x18)
                nm_ = nm.read_class_name(p, blocks_rt, fi) if fi else None
                if nm_ and nm_.startswith("Default__"):
                    continue
                adds.append((idx, sn, ptr, False))
        if not adds:
            return
        # [flags 2026-10-05] 新 owner 的标志位偏移解析（锁外执行——链反射是几十次
        # 小读，不能进 st_lock 挡采样快照；per UClass 缓存命中近零开销）
        flag_new = None
        if flag_offset_pos is not None:
            flag_new = {}
            for _idx, _sn, o, _hud in adds:
                if o not in flag_new:
                    flag_new[o] = resolve_flags(p, blocks_rt, flag_offset_pos,
                                                o, flag_cache)[:2]
        with st_lock:
            for idx, sn, o, is_hud in adds:
                if o in owners:
                    continue
                targets[idx] = o
                serials[idx] = sn
                owners.add(o)
                # [flags] None 分量=该类无标志；flg 与 targets 同生共死
                flg[idx] = flag_new.get(o) if flag_new is not None else (None, None)
                if is_hud:
                    print("    +新目标(HUD) owner=0x%x (idx=%d)" % (o, idx))
                else:
                    print("    +新目标(族) obj=0x%x (idx=%d)" % (o, idx))

    def diff_worker():
        """[spd 2026-10-04] 差分发现线程：绝对节拍 0.5s（与原 n%25@50Hz 等周期）。
        200Hz 采样帧预算 5ms，全数组 chunk 差分（~85k 槽 ~3MB 读+比对）偶发 2~5ms
        在采样循环内会周期性打穿预算，故移入后台。snap/items 本线程私有；加目标
        收口 st_lock（同 rescan 协议）。异常容忍 3 次连续失败后线程退出——原内联
        实现遇到异常会炸掉整段采样走重附着；线程化后由重扫兜底发现 + 采样活性熔断
        （进程真死时 15s 内中断本段）收敛，语义不劣化。"""
        try:
            d_cl, d_pc, d_st, _nume0 = t.chunk_layout(p)
        except Exception as e:
            print("    [diff] chunk 布局解析失败，差分线程退出: %s" % e)
            return
        d_snap = {}    # chunk_idx -> bytes
        d_items = {}   # item_index -> (ptr, serial)
        due = 0.0
        fails = 0
        while not stop.wait(max(0.0, due - (time.time() - t0))):
            try:
                discover_diff(d_snap, d_items, d_cl, d_pc, d_st)
                fails = 0
            except Exception as e:
                fails += 1
                print("    [diff] 本轮失败(%d/3): %s" % (fails, e))
                if fails >= 3:
                    print("    [diff] 连续失败，差分线程退出（重扫兜底仍在）")
                    return
            # 绝对节拍：周期保持 0.5s；被超过时不追补连扫
            due = max(due + 0.5, time.time() - t0)

    def rescan_worker():
        """[fix 2026-08-30b] 后台全量重扫线程。原 force 路径内联在采样循环里，
        每次阻塞 ~3.1s（85k 对象全扫 + 族链走查），一晚 96 次 → A2 81.83% FAIL；
        现移到独立线程，采样帧间隔不再受影响。重扫语义与 [fix 2026-08-30] force
        路径逐条保持：全部槽位解析（忽略差分快照）+ cls_seen 动态重建族集 +
        owner=Package 排除 + Default__ CDO 排除 + 槽位零化摘除 + owners 去重。
        跨线程依据：kernel32 句柄进程级可跨线程；ctypes CDLL 调用释放 GIL；
        tp1.Proc.read 每次新建缓冲，无共享可变状态。
        线程私有缓存：
          w_prev — idx→ptr（上一轮非零槽位），供"槽位零化摘除"提案（apply 时复核）；
          w_cls  — idx→((ptr,sn),cls)，(ptr,serial) 未变的槽位复用类指针读取
                   （UE 对象 ClassPrivate 构造后不变，同 (ptr,sn) 必同类），
                   把每轮 ~85k 次类指针 RPM 压到仅新增/变化槽位。"""
        w_prev = {}
        w_cls = {}
        # [spd 2026-10-04] 布局解析移入各线程私有（原先在 run() 作用域共享；
        # 差分线程化后重扫线程改用本地副本，值只读、语义不变）
        try:
            r_cl, r_pc, r_st, _nume0 = t.chunk_layout(p)
        except Exception as e:
            print("    [rescan] chunk 布局解析失败，后台重扫线程退出: %s" % e)
            return
        due = 0.0
        fails = 0
        while not stop.is_set():
            # [fix 2026-10-09] 等待条件 = 周期到 OR 盲窗看门狗触发（0.2s 粒度
            # 轮询）。触发式重扫不追补不重置周期锚：due 推进公式保持绝对节拍。
            triggered = rescan_req.is_set()
            if triggered:
                rescan_req.clear()
            wait_s = 0.0 if triggered else max(
                0.0, min(due - (time.time() - t0), 0.2))
            if stop.wait(wait_s):
                break
            if not triggered and (time.time() - t0) < due:
                continue
            tc = time.time()
            try:
                nume, numc = read_num()
                if not nume or not numc:
                    raise RuntimeError("nume/numc 读失败（游戏退出？）")
                cand = {}          # idx -> (ptr, cls, sn)
                cls_seen = set()   # 当前类集 → 动态重建族集
                dead_props = []    # 槽位零化摘除提案（apply 时复核）
                for c in range(numc):
                    ca = p.u64(r_cl + c * 8)
                    if not ca:
                        continue
                    take = min(r_pc, max(0, nume - c * r_pc))
                    blob = p.read(ca, take * r_st)
                    if not blob:
                        continue
                    m = len(blob) // r_st
                    base_i = c * r_pc
                    for i in range(m):
                        off = i * r_st
                        ptr = struct.unpack_from("<Q", blob, off)[0]
                        sn = struct.unpack_from("<I", blob, off + 0x10)[0]
                        idx = base_i + i
                        if ptr == 0:
                            if w_prev.pop(idx, None) is not None:
                                dead_props.append(idx)
                            w_cls.pop(idx, None)
                            continue
                        w_prev[idx] = ptr
                        ent = w_cls.get(idx)
                        if ent is not None and ent[0] == (ptr, sn):
                            cls = ent[1]
                        else:
                            cls = p.u64(ptr + 0x10)
                            w_cls[idx] = ((ptr, sn), cls)
                        cls_seen.add(cls)
                        cand[idx] = (ptr, cls, sn)
                # 族集重建（同原 force：基于当前内存类集；空类集时保留旧集，
                # 防止整轮读失败把好集打穿——原 `if force and cls_seen` 语义）
                if cls_seen:
                    nh = nm_subclasses(p, cal["hud"], cls_seen)
                    fam = set()
                    for root in cal["family_roots"]:
                        fam |= nm_subclasses(p, root, cls_seen)
                else:
                    nh = fam = None
                add_hud = []   # (idx, sn, owner)
                add_fam = []   # (idx, sn, ptr)
                for idx, (ptr, cls, sn) in cand.items():
                    if cls in nh:
                        owner = p.u64(ptr + 0x20)
                        if not owner:
                            continue
                        # owner 类=Package 的野 owner 排除（同差分路径/原 force）
                        ocls = p.u64(owner + 0x10)
                        fi2 = p.i32(ocls + 0x18) if ocls else None
                        ocn = nm.read_class_name(p, blocks_rt, fi2) if fi2 else None
                        if ocn == "Package":
                            continue
                        add_hud.append((idx, sn, owner))
                    elif cls in fam and ptr != tmt_cdo:
                        # 家族直采：CDO/子类 CDO 排除（Default__ 前缀）
                        fi = p.i32(ptr + 0x18)
                        nm_ = nm.read_class_name(p, blocks_rt, fi) if fi else None
                        if nm_ and nm_.startswith("Default__"):
                            continue
                        add_fam.append((idx, sn, ptr))
                # sets 单写者（本线程）：键赋值 GIL 原子，采样侧读到新旧皆有效
                if nh is not None:
                    sets["hud"], sets["family"] = nh, fam
                # [flags 2026-10-05] 新 owner 标志位解析（锁外，同差分路径）
                flag_new = None
                if flag_offset_pos is not None:
                    flag_new = {}
                    for _idx, _sn, o in add_hud:
                        if o not in flag_new:
                            flag_new[o] = resolve_flags(
                                p, blocks_rt, flag_offset_pos, o, flag_cache)[:2]
                    for _idx, _sn, o in add_fam:
                        if o not in flag_new:
                            flag_new[o] = resolve_flags(
                                p, blocks_rt, flag_offset_pos, o, flag_cache)[:2]
                with st_lock:
                    for idx in dead_props:
                        if idx in targets:
                            owners.discard(targets[idx])
                            del targets[idx]
                            flg.pop(idx, None)      # [flags] 摘除同步清理
                            fstate.pop(idx, None)
                    for idx, sn, owner in add_hud:
                        if owner in owners:
                            continue
                        targets[idx] = owner
                        serials[idx] = sn
                        owners.add(owner)
                        flg[idx] = flag_new.get(owner) if flag_new is not None \
                            else (None, None)       # [flags]
                        print("    +新目标(HUD) owner=0x%x (idx=%d) [后台重扫]"
                              % (owner, idx))
                    for idx, sn, ptr in add_fam:
                        if ptr in owners:
                            continue
                        targets[idx] = ptr
                        serials[idx] = sn
                        owners.add(ptr)
                        flg[idx] = flag_new.get(ptr) if flag_new is not None \
                            else (None, None)       # [flags]
                        print("    +新目标(族) obj=0x%x (idx=%d) [后台重扫]"
                              % (ptr, idx))
                print("    [rescan] 后台完成 用时 %.2fs hud候选=%d 族候选=%d 摘除=%d"
                      % (time.time() - tc, len(add_hud), len(add_fam),
                         len(dead_props)))
                fails = 0
            except Exception as e:
                fails += 1
                print("    [rescan] 本轮失败(%d/3): %s" % (fails, e))
                if fails >= 3:
                    print("    [rescan] 连续失败，后台重扫线程退出")
                    return
            # 绝对节拍：周期保持 RESCAN_SECS；周期被超过时不追补连扫
            due = max(due + RESCAN_SECS, time.time() - t0)

    worker = threading.Thread(target=rescan_worker, name="rescan-worker",
                              daemon=True)
    worker.start()
    dworker = threading.Thread(target=diff_worker, name="diff-worker",
                               daemon=True)
    dworker.start()

    # [spd 2026-10-04] 绝对节拍：deadline 基于 perf_counter（单调高精度），帧时间戳
    # 仍用 time.time()-t0（epoch 差，schema 语义不变）。单帧落后超过一个周期即重对齐，
    # 不突发补帧。三个节拍（探测 0.5s / flush 2s / scale 1s）从"帧数取模"改"墙钟门控"，
    # 语义按原 50Hz 意图保持（原取模在 200Hz 下周期会被放大 4 倍）。
    _timer_res_begin()
    period = 1.0 / hz
    next_dl = time.perf_counter() + period
    last_probe = -1.0
    last_flush = 0.0
    last_scale = 0.0
    try:
        while True:
            _wait_scroll(next_dl)
            pc_now = time.perf_counter()
            if pc_now - next_dl > period:
                next_dl = pc_now
            next_dl += period
            now = time.time() - t0
            if now - last_probe >= 0.5:   # [fix 2026-08-31] 活性探测（0.5s 不变）
                last_probe = now
                nume, numc = read_num()
                if not nume or not numc:
                    dead_streak += 1
                    if dead_streak > 30:   # 30 × 0.5s = 15s
                        print("[run] 对象数组连续 %.0fs 读失败（进程退出？），"
                              "中断等待重附着" % (dead_streak * 0.5))
                        break
                else:
                    dead_streak = 0
            with st_lock:            # 快照后 RPM 读取在锁外（不阻塞后台合并）
                tg = list(targets.items())
                ser = dict(serials)
            arr = []
            scale_rows = []       # [v2.1] --scale：每秒每目标一条旁线
            scale_due = scale and (now - last_scale >= 1.0)
            if scale_due:
                last_scale = now
            dead = []
            death_rows = []   # [flags 2026-10-05] 本帧死亡事件行（随帧落盘）
            flag_rows = []    # [flags 2026-10-05b] 坐标缺席帧的 flag 观测行（dc 减法口径用）
            blind_rows = []   # [fix 2026-10-09] 盲窗看门狗旁线行（suspect/rescan/recovered）
            for i, owner in tg:
                ia = item_addr(i)
                sn = p.u32(ia + 0x10)
                if sn is None:
                    # [fix 2026-10-09] serial 读失败（RPM 瞬态）≠ 槽位复用：
                    # None 与真变化同等提案会把瞬态读失败的目标误摘出发现层
                    # （54109 盲窗根因：483.23 一帧 hp/dc/serial 多路读失败即
                    # 误摘，0.5s diff 对池化复用结构性失明，恢复只能等 20s
                    # 重扫）。容忍 SERIAL_NONE_STREAK 帧后允许提案（走既有
                    # 复核）；停摆超 SERIAL_NONE_HARD_DROP_S 强制摘除防僵尸
                    # 槽位空转。真变化（int 且 != 期望）照旧立即提案。
                    streak = none_streak.get(i, 0) + 1
                    none_streak[i] = streak
                    first = none_since.setdefault(i, now)
                    if streak == SERIAL_NONE_STREAK or (
                            streak > SERIAL_NONE_STREAK
                            and now - first >= SERIAL_NONE_HARD_DROP_S):
                        dead.append(i)
                        if streak > SERIAL_NONE_STREAK:
                            none_streak.pop(i, None)
                            none_since.pop(i, None)
                    continue
                if none_streak.pop(i, None) is not None:
                    none_since.pop(i, None)   # 读恢复，容忍计数清零
                if sn != ser.get(i):
                    dead.append(i)   # 槽位被复用 = 原对象已销毁
                    continue
                comp = p.u64(owner + root_off)
                coord_ok = False
                px = py = pz = None
                if comp:
                    # [spd 2026-10-04] 平移 12B 连续：一次 RPM 替代 3 次 f32。
                    # 同地址同字节同解法（"<3f" ≡ 逐个 "<f"）；读失败/短读整点跳过
                    # ≡ 原来任一分量 None 跳过；NaN 通路不变（cleaner 照旧剔除）。
                    blob = p.read(comp + xo + 16, 12)
                    if blob is not None and len(blob) >= 12:
                        px, py, pz = struct.unpack_from("<3f", blob, 0)
                        coord_ok = True
                # [flags 2026-10-05b] flag/dc 读取脱离坐标分支：死亡计数不依赖
                # 坐标（RootComponent 悬空/transform 读失败的槽位照常计 dc），
                # 每目标每帧仍恰 +2 次 4B RPM，不破坏 200Hz 帧预算。有效性门见
                # sanitize_flags（有限性/非负）；单调性/身份更替在 update_flag_state；
                # 指针有效性由读失败→None 与既有 serial 检查兜底。
                hp = dc = None
                fo = flg.get(i)
                if fo is not None:
                    if fo[0] is not None:
                        d = p.read(owner + fo[0], 4)
                        if d and len(d) >= 4:
                            hp = struct.unpack("<f", d)[0]
                    if fo[1] is not None:
                        d = p.read(owner + fo[1], 4)
                        if d and len(d) >= 4:
                            dc = struct.unpack("<i", d)[0]
                    hp, dc = sanitize_flags(hp, dc)
                hp, dc, died = update_flag_state(fstate, i, hp, dc)
                # [fix 2026-10-09] 盲窗看门狗：flag 目标（dc 分量在册）hp/dc 双
                # None 连续 BLIND_STALE_S 且 serial 在册 → 判「疑似失明」，触发
                # 带外重扫（恢复上界 ~4s 替代 20s 盲等）。判别性：真死亡尸体
                # hp=0 可读、垂死停更坐标冻结但旗标可读，均不触发；只对「注册
                # 在表但完全读不到」的目标触发。旁线行进 jsonl（cleaner 对非
                # frame 行跳过，零下游破坏）。
                blind = (fo is not None and fo[1] is not None
                         and hp is None and dc is None)
                if blind:
                    first = blind_since.setdefault(i, now)
                    if now - first >= BLIND_STALE_S and i not in blind_suspect:
                        blind_suspect.add(i)
                        blind_rows.append({"ev": "blind_watch",
                                           "t": round(now, 4), "addr": owner,
                                           "phase": "suspect"})
                        if (now - last_blind_rescan
                                >= BLIND_RESCAN_COOLDOWN_S):
                            last_blind_rescan = now
                            rescan_req.set()
                            blind_rows.append({"ev": "blind_watch",
                                               "t": round(now, 4),
                                               "addr": owner,
                                               "phase": "rescan"})
                elif i in blind_since:
                    blind_since.pop(i, None)
                    if i in blind_suspect:
                        blind_suspect.discard(i)
                        blind_rows.append({"ev": "blind_watch",
                                           "t": round(now, 4), "addr": owner,
                                           "phase": "recovered"})
                if coord_ok:
                    arr.append([owner, px, py, pz, hp, dc])
                elif fo is not None and (hp is not None or dc is not None):
                    # 坐标缺席但 flag 在：发 flag 行补 dc 观测（cleaner 的
                    # deaths_summary 减法口径对采样空洞免疫的依据）。坐标路径
                    # 帧不重复发（dc 已在帧条目第 5/6 列）。
                    flag_rows.append({"ev": "flag", "t": round(now, 4),
                                      "addr": owner, "hp": hp, "dc": dc})
                if died and dc is not None:   # dc 未读到时不产行（行 schema 恒 int dc）
                    death_rows.append({"ev": "death", "t": round(now, 4),
                                       "addr": owner, "dc": dc})
                if coord_ok and scale_due:
                    s3 = [p.f32(comp + xo + 32 + k * 4) for k in range(3)]
                    if all(v is not None and 0.01 <= abs(v) <= 1000.0 for v in s3):
                        scale_rows.append({"ev": "scale", "t": round(now, 4),
                                           "addr": owner,
                                           "s": [round(v, 4) for v in s3]})
            if dead:
                with st_lock:
                    for i in dead:
                        # [fix 2026-08-30b] 复核：快照到合并之间该槽位可能已被
                        # 差分/后台重扫换绑（新对象新 serial），复核避免误摘
                        if i in targets and serials.get(i) != p.u32(item_addr(i) + 0x10):
                            owners.discard(targets[i])
                            del targets[i]
                            flg.pop(i, None)        # [flags] 摘除同步清理
                            fstate.pop(i, None)
                            # [fix 2026-10-09] 容忍/看门狗状态随摘除清理
                            none_streak.pop(i, None)
                            none_since.pop(i, None)
                            blind_since.pop(i, None)
                            blind_suspect.discard(i)
            f.write(json.dumps({"ev": "frame", "t": round(now, 4), "targets": arr}) + "\n")
            for drow in death_rows:   # [flags 2026-10-05] 死亡事件行（cleaner 消费）
                f.write(json.dumps(drow) + "\n")
            for frow in flag_rows:    # [flags 2026-10-05b] 坐标缺席帧的 dc 观测行
                f.write(json.dumps(frow) + "\n")
            for brow in blind_rows:   # [fix 2026-10-09] 盲窗看门狗旁线行
                f.write(json.dumps(brow) + "\n")
            for srow in scale_rows:   # [v2.1] scale 旁线（cleaner 跳过）
                f.write(json.dumps(srow) + "\n")
            n += 1
            if now - last_flush >= 2.0:
                last_flush = now
                f.flush()
                print("    t=%6.1fs targets=%d" % (now, len(arr)))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()          # [fix 2026-08-30b] 停掉后台线程再退出（含差分线程）
        worker.join(timeout=8)
        dworker.join(timeout=2)
        _timer_res_end()
        f.flush(); f.close()
        print("[run] 结束，共 %d 帧 → %s" % (n, OUT_PATH))


def main():
    hz = 200    # [spd 2026-10-04] 默认 200Hz（原 50；--hz 可覆盖）
    wait = False
    scale = False          # [v2.1] --scale：每秒每目标读 Scale3D 旁线
    out_dir = None         # [产品化 2026-09-06] --out-dir：产物落会话目录（默认仍脚本目录）
    args = sys.argv[1:]
    if "--hz" in args:
        hz = int(args[args.index("--hz") + 1])
    wait = "--wait" in args
    scale = "--scale" in args
    if "--out-dir" in args:
        out_dir = args[args.index("--out-dir") + 1]
    # [fix 2026-09-30] 初次路径加固（对齐 camera_probe 同款修复；B机实证 target
    # rc=1 死于此段）：find_pid / Proc 构造 / parse_object_array 原先裸奔——
    # OpenProcess 竞态 OSError、模块枚举失败、apply_offsets fail-fast、
    # GUObjectArray 布局未匹配 RuntimeError 任何一个都直接炸进程。Proc 附着
    # 重试 3 次×2s；全败回落等游戏循环（本脚本原本就无论 --wait 都等游戏）。
    pid = None
    try:
        pid = t.find_pid()
    except Exception as e:
        print("[pid] 进程枚举失败: %s" % e)
    p = None
    if pid is not None:
        for attempt in range(3):
            try:
                p = t.Proc(pid)
                break
            except Exception as e:
                print("[proc] 附着失败(%d/3): %s" % (attempt + 1, e))
                if attempt < 2:
                    time.sleep(2)
    while p is None:
        print("[wait] 游戏未运行，每 10s 检查（Ctrl+C 退出）...")
        time.sleep(10)
        try:
            pid = t.find_pid()
        except Exception:
            pid = None
        if pid is None:
            continue
        try:
            p = t.Proc(pid)
        except Exception as e:
            print("[wait] 附着失败: %s" % e)
    print("[proc] pid=%d base=0x%x" % (pid, p.base))
    cal = None
    attempts = 40 if wait else 1
    for i in range(attempts):
        try:
            # [fix 2026-09-30] parse_object_array 挪进 try：游戏启动窗口期布局
            # 校验失败是瞬态，原先在 try 外直接 rc=1 死。
            items, nume = t.parse_object_array(p)
            cal = calibrate(p, items, nume)
        except (RuntimeError, OSError) as e:
            print("[calib] 失败: %s" % e)
            cal = None
        if cal:
            break
        if wait and i < attempts - 1:
            print("[wait] 15s 后重试...")
            time.sleep(15)
    if not ("--run" in sys.argv):
        return
    while True:
        if cal:
            # [fix 2026-09-30] 热自旋防护 + traceback 落盘：本段采样存活 <5s
            # （附着即崩）→ 下次重试前强制 sleep ≥10s，防"附着→秒崩→紧循环
            # 疯狂 calibrate"每圈产一个垃圾 jsonl + 一次全量校准；异常先打印
            # traceback 摘要再走既有的一行提示。
            seg_t0 = time.time()
            try:
                run(p, cal, hz, scale, out_dir)
            except Exception as e:
                traceback.print_exc()
                print("[run] 采样中断（%s），可能是游戏退出" % e)
            seg_alive = time.time() - seg_t0
            if seg_alive < 5.0:
                print("[wait] 本段仅存活 %.1fs，10s 后再重附着（防热自旋）" % seg_alive)
                time.sleep(10)
        print("[wait] 等待游戏进程出现（每 10s 检查，Ctrl+C 退出）...")
        pid = None
        while pid is None:
            time.sleep(10)
            try:
                pid = t.find_pid()
            except Exception:
                pid = None
        try:
            p = t.Proc(pid)
            print("[proc] 重新附着 pid=%d base=0x%x" % (pid, p.base))
            items, nume = t.parse_object_array(p)
            cal = calibrate(p, items, nume)  # 菜单态/异常返回 None 时外循环继续等
        except Exception as e:
            print("[wait] 重新附着失败: %s" % e)
            cal = None


if __name__ == "__main__":
    main()
