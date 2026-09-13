# -*- coding: utf-8 -*-
"""offset_resolver.py — 偏移表四级分辨率链（2026-09-12 自适应层）。

解析顺序（tp1.apply_offsets 调用）：
  1. 包内表 offsets.json（随仓库/打包分发，人工策源）
  2. 用户缓存 offsets.local.json（自动定位成功后写入；打包版内嵌表只读，
     缓存必须落用户数据目录——服务经 AIMING_COOKIE_OFFSETS_CACHE 指到 DATA_ROOT）
  3. 云表（默认开：env 缺省用 DEFAULT_CLOUD_URL=offsets.aimingcookie.com；
     设 AIMING_COOKIE_OFFSETS_URL=<base> 指向自建，设 "0" 显式关闭。
     只读公开、人工策源，客户端永不上传）
  4. 运行时自定位 GUObjectArray（本 POC 实证：主菜单态签名扫描全镜像唯一命中，
     无需旧 exe、无需进对局，见 RUNBOOK_OFFSETS.md §9）

不变量：**绝不静默猜偏移**。第 4 级必须通过验收判据（布局常量 + FName 探针可读率
≥60%）才算数；全链失败维持原 fail-fast 文案并指向 RUNBOOK。类槽（SlotTarget/
SlotScene）不在录制主路径上（仅 tp1 v1 诊断校准消费），自动推导表允许缺失，
缺槽时 tp1.calibrate 报错指回 RUNBOOK。
"""
import hashlib
import json
import os
import struct

# 每次附着的浅校验上限：云表/缓存条目也要过布局判读才敢用（proc 在场时）
CLOUD_TIMEOUT_SECONDS = 3.0
FNAME_PROBE_MIN = 0.60
FNAME_PROBE_MIN_SAMPLES = 20
SCAN_FALLBACK_LO, SCAN_FALLBACK_HI = 0x3F0E000, 0x5800000

DEFAULT_CLOUD_URL = "https://offsets.aimingcookie.com"   # ac-offsets worker（只读、人工策源）


def sha256_of_file(path, buf=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            d = f.read(buf)
            if not d:
                break
            h.update(d)
    return h.hexdigest()


def bundled_table_path(exe_path_unused=None):
    override = os.environ.get("AIMING_COOKIE_OFFSETS_BUNDLED", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "offsets.json")


def user_cache_path():
    override = os.environ.get("AIMING_COOKIE_OFFSETS_CACHE", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "offsets.local.json")


def cloud_base_url():
    raw = os.environ.get("AIMING_COOKIE_OFFSETS_URL")
    if raw is None:
        return DEFAULT_CLOUD_URL
    raw = raw.strip()
    return "" if raw in ("", "0") else raw.rstrip("/")


def load_table(path):
    """读一张偏移表，返回 {sha256: entry}；缺失/损坏 → None（让位下一级）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            table = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict):
        return None
    return {k: v for k, v in table.items() if not k.startswith("_") and isinstance(v, dict)}


def entry_structural_ok(entry):
    """结构校验：rva_guobjectarray 必须是可解析 hex；其余字段可缺。"""
    if not isinstance(entry, dict):
        return False
    try:
        int(entry["rva_guobjectarray"], 16)
    except (KeyError, TypeError, ValueError):
        return False
    for k in ("rva_slot_target", "rva_slot_scene", "rva_blocks_expect"):
        v = entry.get(k)
        if v is not None:
            try:
                int(v, 16)
            except (TypeError, ValueError):
                return False
    return True


def save_cache_entry(path, digest, entry):
    """原子写用户缓存表（tmp+replace）。失败只打印，不影响录制。"""
    table = load_table(path) or {}
    table = {k: v for k, v in table.items() if entry_structural_ok(v)}
    table[digest] = entry
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        print("[offsets] 已缓存自适应表项 → %s" % path)
    except OSError as e:
        print("[offsets] 缓存写入失败（忽略）: %s" % e)


def cloud_fetch(digest, base_url):
    """GET {base}/{sha256} → entry 或 None。任何失败静默让位（离线不阻塞录制）。"""
    import urllib.request
    req = urllib.request.Request(
        "%s/%s" % (base_url, digest),
        headers={"User-Agent": "AimingCookie-Telemetry/1.0"},   # CF 1010 拦默认 python UA
    )
    try:
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT_SECONDS) as resp:
            entry = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("[offsets] 云表不可达（跳过）: %s" % e)
        return None
    if entry_structural_ok(entry):
        return entry
    print("[offsets] 云表条目结构不合规（跳过）")
    return None


# ---------------- 第 4 级：运行时自定位 ----------------

def _scan_range_from_pe(exe_path):
    """从 PE 节表推扫描窗：最低数据节 VA → SizeOfImage；解析失败用兜底常量。"""
    try:
        with open(exe_path, "rb") as f:
            head = f.read(4096)
        e = struct.unpack_from("<I", head, 0x3C)[0]
        coff = e + 4
        nsec = struct.unpack_from("<H", head, coff + 2)[0]
        opt_size = struct.unpack_from("<H", head, coff + 16)[0]
        opt = coff + 20
        size_of_image = struct.unpack_from("<I", head, opt + 56)[0]
        st = opt + opt_size
        data_vas = []
        for i in range(nsec):
            o = st + i * 40
            chars = struct.unpack_from("<I", head, o + 36)[0]
            va = struct.unpack_from("<I", head, o + 12)[0]
            if chars & 0x40000000:   # IMAGE_SCN_MEM_READ
                data_vas.append(va)
        if data_vas and size_of_image:
            return min(data_vas), size_of_image
    except (OSError, struct.error):
        pass
    return SCAN_FALLBACK_LO, SCAN_FALLBACK_HI


def locate_guoa(p, exe_path):
    """运行时定位 GUObjectArray RVA。返回 entry dict 或 None（不达标绝不猜）。

    签名（FixedUObjectArray @ S，8 字节对齐）：u64(S)=chunkptr 可读模块外堆指针、
    i32(S+0x10)==0x1000000、i32(S+0x14) in (0,maxe]、i32(S+0x18)==0x100、
    i32(S+0x1C) in (0,maxc]。GUOA 表键 = S-0x20（fuobj+0x10 解读）。
    验收：parse 同布局 + FName 探针可读率 ≥60%（≥20 样本）。
    """
    import numpy as np
    import names as nm   # 懒加载：names→tp1，模块级会成环

    lo, hi = _scan_range_from_pe(exe_path)
    blocks = nm.find_blocks(p)
    if not blocks:
        print("[offsets] 自定位前提缺失：FNamePool 自扫描未命中（引擎级变化）")
        return None
    blocks_rt = p.base + blocks[0][0]

    hits = []
    chunk = 4 << 20
    for start in range(lo, min(hi, 0x40000000), chunk):
        end = min(start + chunk, hi)
        try:
            blob = p.read(p.base + start, end - start)
        except Exception:
            continue
        if not blob or len(blob) < 64:
            continue
        buf = blob + b"\0" * 16
        x = np.frombuffer(buf, dtype="<u4", count=(len(buf) // 4))
        n = len(blob) // 4
        maxe_cand = np.nonzero((x[:n] == 0x1000000) & (x[2:n + 2] == 0x100)
                               & (x[1:n + 1] > 0) & (x[1:n + 1] <= 0x1000000)
                               & (x[3:n + 3] > 0) & (x[3:n + 3] <= 0x100))[0]
        for i in maxe_cand:
            off = start + int(i) * 4
            if off % 8 == 0:
                hits.append(off)
    print("[offsets] 自定位：布局签名命中 %d 处" % len(hits))

    for s in hits:
        s_addr = p.base + s
        chunkptr = p.u64(s_addr - 0x10)
        if not chunkptr or not (p.read(chunkptr, 8)):
            continue
        if p.base <= chunkptr < p.base + hi:
            continue
        guoa_rva = s - 0x20
        if _guoa_probe_ok(p, p.base + guoa_rva, blocks_rt, nm):
            print("[offsets] 自定位成功：GUObjectArray=0x%x（探针验收通过）" % guoa_rva)
            return {"label": "自动定位 %s" % time_str(),
                    "rva_guobjectarray": "0x%X" % guoa_rva,
                    "rva_slot_target": None, "rva_slot_scene": None,
                    "rva_blocks_expect": "0x%X" % blocks[0][0]}
    print("[offsets] 自定位失败：无候选通过验收（fail-fast，按 RUNBOOK_OFFSETS.md §3 人工重取）")
    return None


def _guoa_probe_ok(p, guoa_addr, blocks_rt, nm):
    """对候选 GUOA 做布局判读 + FName 探针。判读复用 parse_object_array 同款经验规则。"""
    for arr_base in (guoa_addr + 0x10, guoa_addr):
        chunkptr = p.u64(arr_base)
        maxe = p.i32(arr_base + 0x10)
        nume = p.i32(arr_base + 0x14)
        maxc = p.i32(arr_base + 0x18)
        numc = p.i32(arr_base + 0x1C)
        if not chunkptr or not p.read(chunkptr, 8):
            continue
        if not (0 < nume <= maxe <= 64_000_000) or not (0 < numc <= maxc <= 4096):
            continue
        per_chunk = maxe // maxc if maxc else 65536
        probes = readable = 0
        for c in range(numc):
            if probes >= 200:
                break
            ch = p.u64(chunkptr + c * 8)
            if not ch or not p.read(ch, 8):
                continue
            take = min(per_chunk, nume - c * per_chunk, 200 - probes)
            if take <= 0:
                # 布局校验不约束 numc ≤ ceil(nume/per_chunk)：垃圾内存候选可令
                # nume - c*per_chunk < 0。chunk c 覆盖 [c*per_chunk,(c+1)*per_chunk)，
                # 此后 chunk 只会更负且无剩余元素——钳到 0 并 break，绝不用负数
                # 长度调 p.read（会抛 ValueError 穿透打崩采集子进程）。
                break
            blob = p.read(ch, take * 0x18)
            if not blob:
                continue
            for k in range(take):
                obj = struct.unpack_from("<Q", blob, k * 0x18)[0]
                if obj and p.read(obj, 0x20):
                    idx = p.i32(obj + 0x18)
                    if idx and 0 < idx < (1 << 22):
                        probes += 1
                        if nm.read_class_name(p, blocks_rt, idx):
                            readable += 1
        rate = readable / probes if probes else 0.0
        if probes >= FNAME_PROBE_MIN_SAMPLES and rate >= FNAME_PROBE_MIN:
            return True
    return False


def time_str():
    import time
    return time.strftime("%Y-%m-%d %H:%M")


# ---------------- 分辨率链入口 ----------------

def resolve(exe_path, digest=None, proc=None):
    """按四级链解析偏移表项。成功返回 (digest, entry, source)；全败 raise RuntimeError。

    source ∈ {"bundled", "cache", "cloud", "auto"}。proc 仅第 4 级需要。
    """
    if digest is None:
        digest = sha256_of_file(exe_path)

    for source, path in (("bundled", bundled_table_path()),
                         ("cache", user_cache_path())):
        table = load_table(path)
        if table is None:
            continue
        ent = table.get(digest)
        if ent is not None:
            if entry_structural_ok(ent):
                return digest, ent, source
            print("[offsets] %s 表含坏条目（跳过）: %s" % (source, path))

    base = cloud_base_url()
    if base:
        ent = cloud_fetch(digest, base)
        if ent is not None:
            return digest, ent, "cloud"

    if proc is not None:
        ent = locate_guoa(proc, exe_path)
        if ent is not None:
            save_cache_entry(user_cache_path(), digest, ent)
            return digest, ent, "auto"

    known = load_table(bundled_table_path()) or {}
    labels = "; ".join((v.get("label", k[:16]) if isinstance(v, dict) else k)
                       for k, v in known.items())
    raise RuntimeError(
        "不支持的 exe 版本 sha256=%s…（已知: %s）。"
        "自适应定位失败或不可用（需游戏运行中），请按 RUNBOOK_OFFSETS.md 重取偏移并加入 offsets.json"
        % (digest[:16], labels))
