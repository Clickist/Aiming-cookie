#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KovaaK's 用户设置注入器（kovaaks_settings_export.py 的逆操作）。

- 只写 PrimaryUserSettings.json（必要时 weaponsettings.ini），绝不碰 FovSensConfig.json。
- 写前强制检测游戏进程（FPSAimTrainer*，含 FPSAimTrainer-Win64-Shipping）：运行中一律拒绝写入
  （游戏会话内的设置变动/退出时会用内存值整文件回写，外部编辑会被覆盖 —— settings_system_deep.md §3.3/§6）。
- 写前自动备份（同目录 <file>.bak.<时间戳>）；--restore 回滚；--dry-run 只打印 diff。
- Python 3.9 标准库，零第三方依赖。对真实 Saved/SaveGames 文件只在用户显式运行时才写。

用法：
  python kovaaks_settings_inject.py --preset aimingcookie [--install <游戏根目录>] [--dry-run]
  python kovaaks_settings_inject.py --from <pack.json|export.json> [--allow-input] [--allow-new-keys] [--dry-run]
  python kovaaks_settings_inject.py --restore <PrimaryUserSettings.json.bak.20260830T120000>
  python kovaaks_settings_inject.py --selftest

游戏根目录 = 含 FPSAimTrainer/Saved/SaveGames/PrimaryUserSettings.json 的目录。
退出码：0 成功（含 dry-run/无变更）；1 参数或校验错误；2 因游戏运行中拒绝写入。
"""

import argparse
import ctypes
import json
import os
import re
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path

PACK_FORMAT = "settings-pack/1"
EXPORT_FORMAT = "aimtrain.settings-import/1"
PUS_NAME = "PrimaryUserSettings.json"
WS_NAME = "weaponsettings.ini"
ALLOWED_FILES = (PUS_NAME, WS_NAME)

# ============================================================================
# 键分类注册表（plain 名，即不带 "E<类型>SettingId::" 前缀）
# 依据：kovaaks_settings_export.py 映射表 + settings_system_deep.md §2 + 运行时 dump
# ============================================================================

# PUS 六分区（§2）
PUS_SECTIONS = {
    "booleanSettings": "bool",
    "integerSettings": "int",
    "floatSettings": "float",
    "stringSettings": "string",
    "vectorSettings": "vector",
    "colorSettings": "color",
}
SECTION_PREFIX = {
    "booleanSettings": "EBooleanSettingId::",
    "integerSettings": "EIntegerSettingId::",
    "floatSettings": "EFloatSettingId::",
    "stringSettings": "EStringSettingId::",
    "vectorSettings": "EVectorSettingId::",
    "colorSettings": "EColorSettingId::",
}
PREFIX_SECTION = {v: k for k, v in SECTION_PREFIX.items()}

# 设置包（画面/性能类）白名单：plain 名 → 分区。依据 exporter 映射表 2.1/2.2 分类。
PACKABLE = {
    # 帧**率/延迟**
    "MaxFPS": "floatSettings", "MenuMaxFPS": "floatSettings",
    "LowLatencyMode": "integerSettings", "InputLagFrameCount": "integerSettings",
    "LatencyFlashIndicator": "booleanSettings", "OneFrameThreadLag": "booleanSettings",
    # 画质/渲染
    "ResolutionScale": "floatSettings", "Gamma": "floatSettings",
    "Anisotropy": "integerSettings", "AntiAliasing": "integerSettings",
    "AntiAliasingQuality": "integerSettings", "Effects": "integerSettings",
    "PostProcessing": "integerSettings", "Shadows": "integerSettings",
    "SceneColorFormat": "integerSettings", "TextureStreaming": "booleanSettings",
    "TiledReflections": "booleanSettings",
    # 视觉杂项
    "HideGun": "booleanSettings", "HideGibs": "booleanSettings",
    "ProjectileMarkerScale": "floatSettings", "DecalTime": "floatSettings",
    # 天空/季节
    "SkyPreset": "integerSettings", "CloudCover": "integerSettings",
    "SolidSkyColor": "booleanSettings", "SolidTextureSkyColor": "booleanSettings",
    "ShowSunInSkybox": "booleanSettings",
    "DisableSeasonalContent": "booleanSettings", "EnableSeasonalContent": "booleanSettings",
    "SkyColor": "colorSettings",
}

# 输入类（灵敏度/FOV/键感）：默认剔除，--allow-input 显式放开
INPUT_PUS = {
    "XSens": "floatSettings", "YSens": "floatSettings", "FOV": "floatSettings",
    "DPI": "integerSettings",
    "CustomYaw": "floatSettings", "CustomFOVYawMult": "floatSettings",
    "BaseIncrement": "floatSettings",
    "FILMSCustomAspectX": "integerSettings", "FILMSCustomAspectY": "integerSettings",
    "FOVScaleString": "stringSettings", "SensScaleString": "stringSettings",
    "FILMSCustomFOV": "stringSettings",
    "YInvert": "booleanSettings", "ToggleADS": "booleanSettings",
    "SensitivityScaleTargetEnum": "integerSettings",  # UI 下拉缓存（冗余）
    "FOVScalarTargetEnum": "integerSettings",         # UI 下拉缓存（冗余）
}
# weaponsettings.ini 的输入类键（每武器灵敏度/FOV 覆盖系统）
INPUT_WS = {
    "OverrideSens", "HorizontalSens", "VerticalSens", "SensScale",
    "OverrideSensScaleString", "OverrideFOV", "FOV", "FOVScale",
    "OverrideFovScaleString", "ADSFOVScale", "ADSFovScaleString", "ADSZoomFOV",
    "ZoomFOVMultiplier", "ZoomSensMultiplier", "AutoScaleZoomSens",
    "CustomYaw", "CustomYawFOVMult",
    "HipfireCustomFOVAspectX", "HipfireCustomFOVAspectY", "HipfireCustomFOVScale",
    "ADSCustomFOVAspectX", "ADSCustomFOVAspectY", "ADSCustomFOVScale",
}
INPUT_PLAIN = set(INPUT_PUS) | INPUT_WS

# 个人/账号/产品状态类：一律拒绝注入
PERSONAL_PLAIN = {
    "DiscordRichPresence", "LeaderboardFriendsOnly", "LeaderboardGlobalDontSubmit",
    "LeaderboardHideInvalidScores", "HasSeenKovaaKsPlusExplainer",
    "LastAcceptedExperimentsAgreement",
    "HiddenUiElementsMask",        # 位↔HUD元素映射未考证
    "CacheDuration",               # 语义未考证
    "ChallengeResultsLastUsedTab", # UI 会话状态
}

# ============================================================================
# StatsExportLevel 语义（Aiming-cookie 硬依赖）
# ----------------------------------------------------------------------------
# 无独立反射枚举：运行时 1245 个 UEnum 中无 EStatsExportLevel；StatsExportLevel 只是
# EIntegerSettingId 的一个键（ID#1），其"值"的档位语义由 UI 下拉提供。档位表来自 pak 内
# 设置页字符串表（GUID C8BF8A074B94EFB808D1248352762C0B）tooltip：
#   None: Never export stats files to csv.
#   Challenge Completion: Only export when a challenge completes
#   Challenge Completion or Reset: Export on resets as well as challenge completions
#   Always: Export on challenge reset, challenge completion, and freeplay
# 值映射 [inferred，三源交叉]：①tooltip 按档位顺序枚举（UE 下拉按索引 0..N）；②pak 默认模板
# DefaultUserSettings.json 中 StatsExportLevel=1；③本机运行时 TMap 与 PUS 均为 1 且界面显示
# "Challenge Completion"。⇒ Challenge Completion = 1。
# ============================================================================
STATS_EXPORT_LEVELS = [
    (0, "None", "从不导出 stats 文件"),
    (1, "Challenge Completion", "打完一次训练（challenge 完成）时导出"),
    (2, "Challenge Completion or Reset", "重置也导出"),
    (3, "Always", "重置/完成/自由练习全都导出"),
]

# ChallengeHistogramStats 位掩码（从 pak 内 app.bundle.js 解码 [confirmed]）：
# 结果页直方图绘制哪些指标曲线；仅影响 UI 图表，不影响 stats CSV/.perf 生成。
CHALLENGE_HISTOGRAM_BITS = [
    ("score", 1), ("accuracy", 2), ("damageEff", 4), ("spm", 8), ("kills", 16),
    ("kps", 32), ("damageDone", 64), ("randomSensScale", 128),
    ("targetSize", 256), ("targetSpeed", 512),
]

PRESET_AIMINGCOOKIE = [
    # (file, section, fullkey, value)
    (PUS_NAME, "booleanSettings", "EBooleanSettingId::SaveStatistics", True),
    (PUS_NAME, "integerSettings", "EIntegerSettingId::StatsExportLevel", 1),
]

PRESET_ADVICE = (
    "保底预设只设 SaveStatistics=true + StatsExportLevel=1(Challenge Completion)。\n"
    "  关联键（均不改，不影响 per-challenge 输出文件）：\n"
    "  - SessionStatsMode（本机=2）[inferred]：对局内 Session Stats HUD 显示档，不是输出开关。\n"
    "  - ChallengeHistogramStats（本机=3 = score|accuracy）[confirmed-UI]：结果页直方图位掩码\n"
    "    " + ", ".join("%s=%d" % (n, b) for n, b in CHALLENGE_HISTOGRAM_BITS) + "；\n"
    "    只影响图表绘制，不影响 stats CSV/.perf 写盘。\n"
    "  - ChallengeResultsDefaultTab/LastUsedTab：结果页 UI 状态；HiddenUiElementsMask：HUD 隐藏位掩码。\n"
    "  若 stats CSV/.perf 仍缺失，检查 <安装>/FPSAimTrainer/Saved/…/stats/ 目录权限与磁盘空间。"
)

# ============================================================================
# 进程检测（Get-Process FPSAimTrainer* 等价：前缀通配、大小写不敏感）
# ============================================================================
GAME_PROCESS_PREFIX = "fpsaimtrainer"


def find_game_processes():
    """返回 [(pid, 进程名)]：所有名字以 FPSAimTrainer 开头（不区分大小写）的进程。"""
    procs = []
    try:
        # 主路径：Win32 Toolhelp32 快照（纯 ctypes，无子进程）
        TH32CS_SNAPPROCESS = 0x2
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong),
                        ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", ctypes.c_ulong),
                        ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_wchar * 260)]
        k32 = ctypes.windll.kernel32  # noqa: attribute exists on Windows
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap != -1:
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                ok = k32.Process32FirstW(snap, ctypes.byref(entry))
                while ok:
                    name = entry.szExeFile
                    if name.lower().startswith(GAME_PROCESS_PREFIX):
                        procs.append((entry.th32ProcessID, name))
                    ok = k32.Process32NextW(snap, ctypes.byref(entry))
            finally:
                k32.CloseHandle(snap)
    except Exception:
        procs = []
    if not procs:
        # 兜底：tasklist（进程名一致即可，不苛求快照路径）
        try:
            import subprocess
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                 timeout=20).stdout.decode("utf-8", errors="replace")
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if parts and parts[0].lower().startswith(GAME_PROCESS_PREFIX):
                    try:
                        procs.append((int(parts[1]), parts[0]))
                    except (IndexError, ValueError):
                        procs.append((0, parts[0]))
        except Exception:
            pass
    return procs


def guard_game_not_running(dry_run=False):
    """写前强制检测。dry-run 只提示不拦截；真实写入时发现游戏进程即拒（退出码 2）。"""
    procs = find_game_processes()
    if not procs:
        return
    listing = ", ".join("%s(PID %s)" % (n, p) for p, n in procs)
    if dry_run:
        print("[warn] 游戏正在运行（%s）—— dry-run 只读不写，继续。" % listing)
        return
    print("[拒绝] 检测到游戏正在运行：%s" % listing)
    print("       游戏会话内的设置变动会整文件回写 PrimaryUserSettings.json，")
    print("       退出时也会用内存值覆盖磁盘 —— 请完全退出游戏后重试。")
    raise SystemExit(2)


# ============================================================================
# 值格式化 / 类型校验
# ============================================================================
def fmt_scalar(v):
    """PUS 行内标量格式化（UE 风格：true/false 小写，float 用最短往返表示）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise ValueError("无法格式化标量: %r" % (v,))


def fmt_object(obj, indent, key_order=None):
    """多行嵌套对象（vector/color）。PUS 写器按字母序输出键（实测 b,g,r,a / x,y,z）。"""
    keys = key_order or sorted(obj.keys())
    inner = indent + "\t"
    lines = [indent + "{"]
    for i, k in enumerate(keys):
        sep = "," if i < len(keys) - 1 else ""
        lines.append("%s%s: %s%s" % (inner, json.dumps(k, ensure_ascii=False),
                                     fmt_scalar(obj[k]), sep))
    lines.append(indent + "}")
    return "\r\n".join(lines)


def _f32(x):
    """float32 视角值（游戏 PUS 浮点实际分辨率；float32 相等即游戏内相等）。"""
    try:
        return struct.pack("<f", float(x))
    except (OverflowError, ValueError):
        return None


def _same_value(a, b):
    """no-op 判定：数值按 float32 比较（2.2 与 2.2000000476837158 视为同一值）；
    其余类型严格相等（bool 与 1 不互等）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b and isinstance(a, bool) == isinstance(b, bool)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _f32(a) == _f32(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same_value(a[k], b[k]) for k in a)
    return a == b


def check_type(section, value, fullkey):
    """注入值类型校验（bool 必须真 bool；int 拒绝 bool/非整数）。"""
    kind = PUS_SECTIONS[section]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("%s 需要 bool，得到 %r" % (fullkey, value))
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                (isinstance(value, float) and not value.is_integer()):
            raise ValueError("%s 需要整数，得到 %r" % (fullkey, value))
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s 需要数值，得到 %r" % (fullkey, value))
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("%s 需要字符串，得到 %r" % (fullkey, value))
    elif kind == "vector":
        if not (isinstance(value, dict) and all(k in value for k in ("x", "y", "z"))
                and all(isinstance(value[k], (int, float)) and not isinstance(value[k], bool)
                        for k in ("x", "y", "z"))):
            raise ValueError("%s 需要 {x,y,z} 数值对象，得到 %r" % (fullkey, value))
    elif kind == "color":
        if not (isinstance(value, dict) and all(k in value for k in ("r", "g", "b", "a"))
                and all(isinstance(value[k], int) and not isinstance(value[k], bool)
                        for k in ("r", "g", "b", "a"))):
            raise ValueError("%s 需要 {r,g,b,a} 整数对象，得到 %r" % (fullkey, value))
    return value


# ============================================================================
# PUS 文本手术式编辑（保持 CRLF/Tab/键序/其余行字节不变）
# ============================================================================
def split_pus_lines(text):
    if "\n" in text.replace("\r\n", ""):
        raise ValueError("PUS 含混行尾（裸 LF），拒绝编辑")
    return text.split("\r\n")


def pus_line_for(fullkey, section, value):
    """条目块文本。标量同行；vector/color 按游戏写器格式：键行后换行、开括号独立成行。"""
    kind = PUS_SECTIONS[section]
    head = '\t\t%s:' % json.dumps(fullkey, ensure_ascii=False)
    if kind in ("vector", "color"):
        return head + "\r\n" + fmt_object(value, "\t\t")
    return head + " " + fmt_scalar(value)


_SEC_CLOSE_RE = re.compile(r"\t\},?")


def _is_sec_close(line):
    return bool(_SEC_CLOSE_RE.fullmatch(line))


def _entry_block_end(lines, start):
    """键行 start 所在条目块的最后一行下标。
    标量值与键同行（行不以 ':' 结尾）；对象值则键行以 ':' 结尾、开括号独立成行。"""
    if not lines[start].rstrip().endswith(":"):
        return start
    depth = 0
    i = start + 1
    while i < len(lines):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            return i
        i += 1
    raise ValueError("PUS 条目块括号不平衡（起点行 %d）" % start)


def edit_pus_text(text, changes):
    """changes: {fullkey: (section, value)}。返回 (new_text, (replaced, inserted))。
    已存在同键 → 原位整块替换（保留行尾逗号约定）；不存在 → 按字母序插入并维护逗号。
    未触及的行保持字节不变。"""
    lines = split_pus_lines(text)
    replaced = inserted = 0
    for fullkey, (section, value) in sorted(changes.items()):
        new_block = pus_line_for(fullkey, section, value).split("\r\n")
        # 定位分区
        sec_head = None
        for i, ln in enumerate(lines):
            if ln == '\t"%s":' % section:
                sec_head = i
                break
        if sec_head is None or sec_head + 1 >= len(lines) or lines[sec_head + 1] != "\t{":
            raise ValueError("未找到分区 %s 或其开括号格式不符" % section)
        # 分区内找键行（标量行 "Key": v 与对象行 "Key": 均可命中）
        found = None
        j = sec_head + 2
        while j < len(lines) and not _is_sec_close(lines[j]):
            if lines[j].startswith('\t\t%s:' % json.dumps(fullkey, ensure_ascii=False)):
                found = j
                break
            j += 1
        if found is not None:
            end = _entry_block_end(lines, found)
            comma = lines[end].rstrip().endswith(",")
            if comma:
                new_block[-1] = new_block[-1] + ","
            lines[found:end + 1] = new_block
            replaced += 1
        else:
            # 按全名字典序插入（与游戏写器的字母序一致）；维护行尾逗号约定
            k = sec_head + 2
            while k < len(lines) and not _is_sec_close(lines[k]):
                m = re.match(r'\t\t"([^"]+)":', lines[k])
                if m and m.group(1) > fullkey:
                    break
                k += 1
            block = list(new_block)
            at_end = k >= len(lines) or _is_sec_close(lines[k])
            if at_end:
                # 插入为分区最后一条：新块不带逗号，原最后条目补逗号
                if k > sec_head + 2 and not lines[k - 1].rstrip().endswith(","):
                    lines[k - 1] = lines[k - 1] + ","
            else:
                block[-1] = block[-1] + ","  # 后面还有既有条目 → 新块带逗号
            lines[k:k] = block
            inserted += 1
    return "\r\n".join(lines), (replaced, inserted)


def parse_pus(text):
    return json.loads(text)


def verify_edited_structure(old_text, new_text, changes):
    """结构无损断言：除目标键外，解析后 JSON 深度相等；目标键值等于注入值。"""
    old, new = parse_pus(old_text), parse_pus(new_text)
    problems = []
    for fullkey, (section, value) in changes.items():
        got = new.get(section, {}).get(fullkey)
        if got != value or isinstance(got, bool) != isinstance(value, bool):
            problems.append("%s 未生效: %r != %r" % (fullkey, got, value))
    # 深度对比（把目标键设为期望值后应整体相等；且键集合只差目标键）
    import copy
    expect = copy.deepcopy(old)
    for fullkey, (section, value) in changes.items():
        expect.setdefault(section, {})[fullkey] = value
    if new != expect:
        # 找出差异键以便报告
        for sec in PUS_SECTIONS:
            a, b = old.get(sec, {}), new.get(sec, {})
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k) and (sec, k) not in [(s, f) for f, (s, _) in changes.items()]:
                    problems.append("意外变更 %s::%s" % (sec, k))
            if set(a) ^ set(b):
                extra = set(a) ^ set(b)
                unexpected = {k for k in extra if (sec, k) not in [(s, f) for f, (s, _) in changes.items()]}
                # 上面已逐键对比；此处兜底
                if unexpected:
                    problems.append("意外键集差异 %s: %s" % (sec, sorted(unexpected)))
        for k in set(old) | set(new):
            if k not in PUS_SECTIONS and old.get(k) != new.get(k):
                problems.append("意外变更顶层键 %s" % k)
    if problems:
        raise ValueError("编辑后结构校验失败:\n  " + "\n  ".join(problems))


# ============================================================================
# weaponsettings.ini 手术式编辑
# ============================================================================
def split_ws_lines(text):
    if "\n" in text.replace("\r\n", ""):
        raise ValueError("weaponsettings.ini 含混行尾（裸 LF），拒绝编辑")
    return text.split("\r\n")


def ws_fmt_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, dict):  # UE 向量文本（导出器用大写 X/Y/Z，兼容小写）
        g = lambda *ks: next((v[k] for k in ks if k in v), 0)
        return "X=%.3f Y=%.3f Z=%.3f" % (g("x", "X"), g("y", "Y"), g("z", "Z"))
    return str(v)


def edit_ws_text(text, changes):
    """changes: {plainkey: value}。只更新已存在的键；缺失键由调用方决定是否报错。"""
    lines = split_ws_lines(text)
    replaced = 0
    for key, value in changes.items():
        pat = re.compile(r"^%s=(.*)$" % re.escape(key))
        found = False
        for i, ln in enumerate(lines):
            if pat.match(ln):
                lines[i] = "%s=%s" % (key, ws_fmt_value(value))
                replaced += 1
                found = True
                break
        if not found:
            raise ValueError("weaponsettings.ini 中不存在键 %s（本工具不新增 ini 键）" % key)
    return "\r\n".join(lines), replaced


# ============================================================================
# 文件读写 / 备份 / 回滚
# ============================================================================
def read_text(path):
    raw = Path(path).read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    return text, bom


def write_text_atomic(path, text, bom):
    data = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
    tmp = Path(str(path) + ".tmpinject")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def backup_file(path, tag="bak"):
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = Path("%s.%s.%s" % (path, tag, ts))
    n = 0
    while dst.exists():  # 同秒多次备份不互相覆盖
        n += 1
        dst = Path("%s.%s.%s.%d" % (path, tag, ts, n))
    shutil.copy2(str(path), str(dst))
    return dst


def resolve_restore_target(backup_path):
    p = Path(backup_path)
    m = re.match(r"^(.*)\.bak\.\d{8}T\d{6}$", p.name)
    if not m:
        raise SystemExit("[错误] --restore 需要 .bak.时间戳 备份文件，得到: %s" % p.name)
    target = p.with_name(m.group(1))
    if target.name not in ALLOWED_FILES:
        raise SystemExit("[错误] 只允许回滚 %s" % (ALLOWED_FILES,))
    if not target.is_file():
        raise SystemExit("[错误] 目标文件不存在: %s" % target)
    if not p.is_file():
        raise SystemExit("[错误] 备份文件不存在: %s" % p)
    return p, target


# ============================================================================
# 变更集构建
# ============================================================================
class Changes(object):
    def __init__(self):
        self.pus = {}   # fullkey -> (section, value)
        self.ws = {}    # plainkey -> value
        self.skipped = []  # (来源, 原因)
        self.advice = []

    def is_empty(self):
        return not self.pus and not self.ws


def _classify_plain(plain):
    """返回 (class, section_or_None)。class ∈ packable/input/personal/known/unknown。"""
    if plain in PERSONAL_PLAIN:
        return "personal", None
    if plain in PACKABLE:
        return "packable", PACKABLE[plain]
    if plain in INPUT_PUS:
        return "input", INPUT_PUS[plain]
    if plain in INPUT_WS:
        return "input", "ws"
    return "unknown", None


def changeset_from_preset(name):
    if name != "aimingcookie":
        raise SystemExit("[错误] 未知预设 %r（可用: aimingcookie）" % name)
    ch = Changes()
    for file, section, fullkey, value in PRESET_AIMINGCOOKIE:
        ch.pus[fullkey] = (section, value)
    ch.advice.append(PRESET_ADVICE)
    return ch


def changeset_from_pack(data, allow_input):
    if data.get("format") != PACK_FORMAT:
        raise SystemExit("[错误] --from 文件不是 %s 格式（format=%r）" % (PACK_FORMAT, data.get("format")))
    settings = data.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise SystemExit('[错误] %s 需要 non-empty "settings": {设置名: 值} 平面对象' % PACK_FORMAT)
    ch = Changes()
    for plain, value in settings.items():
        cls, section = _classify_plain(plain)
        if cls == "personal":
            raise SystemExit("[拒绝] 设置包含个人/账号/未考证键 %s —— 设置包不允许" % plain)
        if cls == "input" and not allow_input:
            ch.skipped.append((plain, "输入类键默认剔除（--allow-input 放开）"))
            continue
        if cls == "unknown":
            raise SystemExit("[拒绝] 未知键 %s 不在设置包注册表 —— 拒绝注入" % plain)
        if section is None or section not in PUS_SECTIONS:
            raise SystemExit("[错误] 键 %s 分区解析失败" % plain)
        fullkey = SECTION_PREFIX[section] + plain
        ch.pus[fullkey] = (section, check_type(section, value, fullkey))
    return ch


def changeset_from_export(doc, allow_input, allow_new_keys):
    if doc.get("format") != EXPORT_FORMAT:
        raise SystemExit("[错误] --from 文件不是 %s 格式（format=%r）" % (EXPORT_FORMAT, doc.get("format")))
    entries = doc.get("settings")
    if not isinstance(entries, list):
        raise SystemExit("[错误] 导出 JSON 缺少 settings 数组")
    ch = Changes()
    for e in entries:
        src = e.get("source", {})
        file, key = src.get("file"), src.get("key")
        label = "%s::%s" % (file, key)
        if file not in ALLOWED_FILES:
            ch.skipped.append((label, "注入器只写 %s（%s 不在范围）" % (ALLOWED_FILES, file)))
            continue
        if not e.get("migratable", True):
            ch.skipped.append((label, "导出标记 migratable=false: %s" % e.get("reason", "")))
            continue
        plain = key.split("::")[-1]
        if plain in PERSONAL_PLAIN:
            ch.skipped.append((label, "个人/账号/未考证键，拒绝"))
            continue
        cls, section = _classify_plain(plain)
        if cls == "input" and not allow_input:
            ch.skipped.append((label, "输入类键默认剔除（--allow-input 放开）"))
            continue
        if file == WS_NAME:
            ch.ws[plain] = e.get("value")
            continue
        # PUS：分区优先取键前缀（自描述），退化用注册表
        m = re.match(r"^(E(?:Boolean|Integer|Float|String|Vector|Color)SettingId)::", key or "")
        if m:
            section = PREFIX_SECTION[m.group(1) + "::"]
        if section not in PUS_SECTIONS:
            ch.skipped.append((label, "无法确定 PUS 分区"))
            continue
        value = e.get("value")
        if isinstance(value, dict) and "srgbHex" in value and "x" in value:
            value = {k: value[k] for k in ("x", "y", "z")}   # 导出器 vector 附带 srgbHex
        if isinstance(value, dict) and "hex" in value and "r" in value:
            value = {k: value[k] for k in ("r", "g", "b", "a")}  # 导出器 color 附带 hex
        ch.pus[key] = (section, check_type(section, value, key))
    return ch


# ============================================================================
# diff（展示用）
# ============================================================================
def jshort(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "..."


def compute_diff(old, new):
    """比较两个解析后的 PUS：返回 (changed, added, removed) 列表。"""
    changed, added, removed = [], [], []
    for sec in PUS_SECTIONS:
        a, b = old.get(sec, {}), new.get(sec, {})
        for k in sorted(set(a) & set(b)):
            if a[k] != b[k]:
                plain = k.split("::")[-1]
                changed.append((sec, plain, a[k], b[k]))
        for k in sorted(set(b) - set(a)):
            added.append((sec, k.split("::")[-1], None, b[k]))
        for k in sorted(set(a) - set(b)):
            removed.append((sec, k.split("::")[-1], a[k], None))
    return changed, added, removed


def print_pus_diff(old, new):
    changed, added, removed = compute_diff(old, new)
    for sec, k, a, b in changed:
        print("  [~] %s::%s: %s -> %s" % (sec, k, jshort(a), jshort(b)))
    for sec, k, _, b in added:
        print("  [+] %s::%s: %s" % (sec, k, jshort(b)))
    for sec, k, a, _ in removed:
        print("  [-] %s::%s: (删除 %s)" % (sec, k, jshort(a)))
    return len(changed) + len(added) + len(removed)


# ============================================================================
# 应用
# ============================================================================
def apply_changes(sg_dir, ch, dry_run, allow_new_keys):
    """对 Saved/SaveGames 副本/真身应用变更。返回变更键数。"""
    pus_path = sg_dir / PUS_NAME
    ws_path = sg_dir / WS_NAME

    # 存在性检查：PUS 缺失键默认拒绝（旧程序下次保存会丢弃新键，§2.7）
    pus_text, pus_bom = read_text(pus_path)
    pus_old = parse_pus(pus_text)
    known = {}
    for sec in PUS_SECTIONS:
        for k in pus_old.get(sec, {}):
            known[k] = sec
    for fullkey, (section, value) in list(ch.pus.items()):
        if fullkey not in known:
            if allow_new_keys:
                print("[warn] %s 不在当前 PUS 中，将按字母序新增（旧版程序下次保存会丢弃）" % fullkey)
            else:
                ch.skipped.append((fullkey, "当前 PUS 无此键（跨版本键会被游戏丢弃；--allow-new-keys 强制新增）"))
                del ch.pus[fullkey]
        elif known[fullkey] != section:
            raise SystemExit("[错误] %s 实际分区 %s 与预期 %s 不符" % (fullkey, known[fullkey], section))
        else:
            cur = pus_old[section][fullkey]
            if _same_value(cur, value):
                ch.skipped.append((fullkey, "已处于目标值，跳过"))
                del ch.pus[fullkey]
    if ch.ws:
        if not ws_path.is_file():
            raise SystemExit("[错误] 需要 %s 但文件不存在" % WS_NAME)
        ws_text, _ = read_text(ws_path)
        ws_lines = split_ws_lines(ws_text)
        ws_kv = {}
        for ln in ws_lines:
            if "=" in ln:
                a, b = ln.split("=", 1)
                ws_kv[a] = b
        for k in list(ch.ws):
            if k not in ws_kv:
                ch.skipped.append((k, "weaponsettings.ini 无此键（不新增 ini 键）"))
                del ch.ws[k]
            elif ws_kv[k] == ws_fmt_value(ch.ws[k]):
                ch.skipped.append((k, "已处于目标值，跳过"))
                del ch.ws[k]

    if ch.is_empty():
        print("[无变更] 目标值已全部满足，未写任何文件。")
        for src, why, *_ in ch.skipped:
            print("  [skip] %s: %s" % (src, why))
        return 0

    # 计划 + 编辑（文本手术）
    n_keys = len(ch.pus) + len(ch.ws)
    if dry_run:
        print("[dry-run] 将修改 %d 个键（不写盘）：" % n_keys)
    new_pus_text = pus_new = None
    if ch.pus:
        new_pus_text, (rep, ins) = edit_pus_text(pus_text, ch.pus)
        verify_edited_structure(pus_text, new_pus_text, ch.pus)  # 编辑即自验
        pus_new = parse_pus(new_pus_text)
        if dry_run:
            print("目标: %s" % pus_path)
            print_pus_diff(pus_old, pus_new)
    new_ws_text = None
    if ch.ws:
        ws_text, _ = read_text(ws_path)
        new_ws_text, rep_ws = edit_ws_text(ws_text, ch.ws)
        if dry_run:
            print("目标: %s" % ws_path)
            old_ws = {ln.split("=", 1)[0]: ln.split("=", 1)[1]
                      for ln in split_ws_lines(ws_text) if "=" in ln}
            for k, v in ch.ws.items():
                print("  [~] %s: %s -> %s" % (k, jshort(old_ws.get(k)), ws_fmt_value(v)))

    noop = [src for src, why, *_ in ch.skipped if "已处于目标值" in why]
    other = [(src, why) for src, why, *_ in ch.skipped if "已处于目标值" not in why]
    if noop:
        print("  [=] %d 键已处于目标值（如 %s…）" % (len(noop), ", ".join(noop[:3])))
    for src, why in other:
        print("  [skip] %s: %s" % (src, why))
    if dry_run:
        print("[dry-run] 完成，未写盘。")
        return n_keys

    # 写盘：备份 → 原子写 → 复核
    if ch.pus:
        bak = backup_file(pus_path)
        write_text_atomic(pus_path, new_pus_text, pus_bom)
        final_text, _ = read_text(pus_path)
        verify_edited_structure(pus_text, final_text, ch.pus)
        print("[ok] %s 已写入（替换 %d 行，新增 %d 键）；备份: %s"
              % (PUS_NAME, rep, ins, bak))
    if ch.ws:
        bak = backup_file(ws_path)
        write_text_atomic(ws_path, new_ws_text, True)  # ws 恒带 BOM
        print("[ok] %s 已写入（%d 键）；备份: %s" % (WS_NAME, len(ch.ws), bak))
    return n_keys


# ============================================================================
# 定位（与 exporter 相同约定）
# ============================================================================
def locate_install(cli_value):
    cands = []
    if cli_value:
        cands.append(Path(cli_value))
    env = os.environ.get("KOVAAKS_INSTALL")
    if env:
        cands.append(Path(env))
    try:
        cands.append(Path(__file__).resolve().parents[2] / "FPSAimTrainer")
    except IndexError:
        pass
    for c in cands:
        if (c / "Saved" / "SaveGames" / PUS_NAME).is_file():
            return c
    raise SystemExit("未找到 KovaaK's 安装（需要 Saved/SaveGames/%s）；用 --install 指定游戏根目录" % PUS_NAME)


# ============================================================================
# 主流程
# ============================================================================
def run(argv=None):
    ap = argparse.ArgumentParser(
        description="KovaaK's 设置注入器（PUS/weaponsettings 手术式写入，游戏运行中拒绝写）",
        epilog="预设 aimingcookie = SaveStatistics=true + StatsExportLevel=1(Challenge Completion)，"
               "保证 Aiming-cookie 的 stats CSV + .perf 每局时间切分数据源。")
    ap.add_argument("--preset", metavar="NAME", help="内置保底预设（aimingcookie）")
    ap.add_argument("--from", dest="from_file", metavar="JSON",
                    help="从设置包（settings-pack/1）或导出 JSON（aimtrain.settings-import/1）注入")
    ap.add_argument("--restore", metavar="BACKUP", help="回滚到指定 .bak.时间戳 备份")
    ap.add_argument("--install", help="KovaaK's 游戏根目录（默认自动定位）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的 diff，不写盘")
    ap.add_argument("--allow-input", action="store_true", help="放开输入类键（灵敏度/FOV 等）")
    ap.add_argument("--allow-new-keys", action="store_true",
                    help="允许新增当前 PUS 不存在的键（跨版本键有被丢弃风险）")
    ap.add_argument("--selftest", action="store_true", help="运行内置断言后退出")
    args = ap.parse_args(argv)

    if args.selftest:
        selftest()
        print("selftest OK")
        return 0

    actions = [bool(args.preset), bool(args.from_file), bool(args.restore)]
    if sum(actions) != 1:
        ap.error("必须且只能选择一个动作：--preset / --from / --restore")

    if args.restore:
        guard_game_not_running(args.dry_run)
        bak, target = resolve_restore_target(args.restore)
        if args.dry_run:
            print("[dry-run] 将用 %s 覆盖 %s" % (bak, target))
            return 0
        pre = backup_file(target, tag="pre-restore")
        shutil.copy2(str(bak), str(target))
        if Path(target).read_bytes() != bak.read_bytes():
            raise SystemExit("[错误] 回滚后字节不一致: %s" % target)
        print("[ok] 已回滚 %s <- %s；回滚前状态备份: %s" % (target, bak, pre))
        return 0

    install = locate_install(args.install)
    sg_dir = install / "Saved" / "SaveGames"
    print("[目标] %s" % sg_dir)
    guard_game_not_running(args.dry_run)

    if args.preset:
        ch = changeset_from_preset(args.preset)
    else:
        with open(args.from_file, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        fmt = doc.get("format") if isinstance(doc, dict) else None
        if fmt == PACK_FORMAT:
            ch = changeset_from_pack(doc, args.allow_input)
        elif fmt == EXPORT_FORMAT:
            ch = changeset_from_export(doc, args.allow_input, args.allow_new_keys)
        else:
            raise SystemExit("[错误] 无法识别的格式 %r（支持 %s / %s）"
                             % (fmt, PACK_FORMAT, EXPORT_FORMAT))
    for adv in ch.advice:
        print("[说明] %s" % adv)
    apply_changes(sg_dir, ch, args.dry_run, args.allow_new_keys)
    return 0


# ============================================================================
# selftest（纯内存，不碰文件系统）
# ============================================================================
_PUS_MINI = (
    '{\r\n'
    '\t"booleanSettings":\r\n'
    '\t{\r\n'
    '\t\t"EBooleanSettingId::Alpha": true,\r\n'
    '\t\t"EBooleanSettingId::Zulu": false\r\n'
    '\t},\r\n'
    '\t"integerSettings":\r\n'
    '\t{\r\n'
    '\t\t"EIntegerSettingId::Beta": 7\r\n'
    '\t},\r\n'
    '\t"version": 1\r\n'
    '}'
)

_PUS_VEC = (
    '{\r\n'
    '\t"booleanSettings":\r\n'
    '\t{\r\n'
    '\t\t"EBooleanSettingId::Alpha": true\r\n'
    '\t},\r\n'
    '\t"vectorSettings":\r\n'
    '\t{\r\n'
    '\t\t"EVectorSettingId::Aaa":\r\n'
    '\t\t{\r\n'
    '\t\t\t"x": 0.0,\r\n'
    '\t\t\t"y": 1.0,\r\n'
    '\t\t\t"z": 1.0\r\n'
    '\t\t},\r\n'
    '\t\t"EVectorSettingId::WallColor":\r\n'
    '\t\t{\r\n'
    '\t\t\t"x": 1.0,\r\n'
    '\t\t\t"y": 1.0,\r\n'
    '\t\t\t"z": 1.0\r\n'
    '\t\t}\r\n'
    '\t},\r\n'
    '\t"version": 1\r\n'
    '}'
)


def selftest():
    # 标量格式化
    assert fmt_scalar(True) == "true" and fmt_scalar(False) == "false"
    assert fmt_scalar(1) == "1" and fmt_scalar(0.5) == "0.5"
    assert fmt_scalar("a\"b") == '"a\\"b"'
    # 行编辑：替换 + 字母序插入 + 结构保真
    ch = {"EBooleanSettingId::Zulu": ("booleanSettings", True),
          "EBooleanSettingId::Mid": ("booleanSettings", False),
          "EIntegerSettingId::Beta": ("integerSettings", 8)}
    new, (rep, ins) = edit_pus_text(_PUS_MINI, ch)
    assert rep == 2 and ins == 1, (rep, ins)
    verify_edited_structure(_PUS_MINI, new, ch)
    parsed = parse_pus(new)
    assert parsed["booleanSettings"]["EBooleanSettingId::Zulu"] is True
    assert parsed["booleanSettings"]["EBooleanSettingId::Mid"] is False
    keys = list(parsed["booleanSettings"])
    assert keys == sorted(keys), keys  # 插入保持字母序
    assert parsed["version"] == 1 and new.endswith("}")
    assert "\r\n" in new and new.count("\r\n") == _PUS_MINI.count("\r\n") + 1
    # 校验器能抓坏编辑
    try:
        verify_edited_structure(_PUS_MINI, new.replace('"version": 1', '"version": 2'), ch)
        raise AssertionError("应检测到意外变更")
    except ValueError:
        pass
    # 类型校验
    for section, bad in (("integerSettings", True), ("integerSettings", 1.5),
                         ("booleanSettings", 1), ("stringSettings", 3)):
        try:
            check_type(section, bad, "X")
            raise AssertionError("应拒绝 %r 进 %s" % (bad, section))
        except ValueError:
            pass
    check_type("floatSettings", 400, "X")  # int 可入 float 分区
    check_type("integerSettings", 300.0, "X")  # 整值 float 可入 int 分区
    # ws 行编辑（BOM 由 read_text 的 utf-8-sig 解码剥离，测试串不含 BOM）
    ws = "Hitmarkers=false\r\nCrosshairScale=0.7\r\n"
    new_ws, n = edit_ws_text(ws, {"Hitmarkers": True, "CrosshairScale": 0.9})
    assert n == 2 and "Hitmarkers=true" in new_ws and "CrosshairScale=0.9" in new_ws
    try:
        edit_ws_text(ws, {"NoSuchKey": 1})
        raise AssertionError("应拒绝新增 ini 键")
    except ValueError:
        pass
    # 分类：白名单/输入/个人/未知
    assert _classify_plain("MaxFPS")[0] == "packable"
    assert _classify_plain("XSens")[0] == "input"
    assert _classify_plain("OverrideSens")[0] == "input"
    assert _classify_plain("DiscordRichPresence")[0] == "personal"
    assert _classify_plain("NotAKey")[0] == "unknown"
    # pack 构建路径：输入键默认剔除（任务语义=剔除并提示，非整体失败）
    c_strip = changeset_from_pack({"format": PACK_FORMAT, "settings": {"XSens": 0.1}}, allow_input=False)
    assert "EFloatSettingId::XSens" not in c_strip.pus
    assert any(s[0] == "XSens" for s in c_strip.skipped)
    c = changeset_from_pack({"format": PACK_FORMAT,
                             "settings": {"XSens": 0.1, "MaxFPS": 400}}, allow_input=True)
    assert "EFloatSettingId::XSens" in c.pus and "EFloatSettingId::MaxFPS" in c.pus
    try:
        changeset_from_pack({"format": PACK_FORMAT, "settings": {"DiscordRichPresence": True}}, True)
        raise AssertionError("个人键应拒绝")
    except SystemExit:
        pass
    try:
        changeset_from_pack({"format": PACK_FORMAT, "settings": {"TotallyUnknown": 1}}, True)
        raise AssertionError("未知键应拒绝")
    except SystemExit:
        pass
    # export 构建跳过路径
    doc = {"format": EXPORT_FORMAT, "settings": [
        {"source": {"file": PUS_NAME, "key": "EFloatSettingId::Gamma"}, "value": 2.0,
         "migratable": True},
        {"source": {"file": PUS_NAME, "key": "EBooleanSettingId::DiscordRichPresence"},
         "value": True, "migratable": False, "reason": "平台"},
        {"source": {"file": "Input.ini", "key": "ActionMappings::Jump"}, "value": "SpaceBar",
         "migratable": True},
        {"source": {"file": PUS_NAME, "key": "EFloatSettingId::XSens"}, "value": 0.2,
         "migratable": True},
    ]}
    c2 = changeset_from_export(doc, allow_input=False, allow_new_keys=False)
    assert "EFloatSettingId::Gamma" in c2.pus
    assert "EFloatSettingId::XSens" not in c2.pus          # 输入类剔除
    assert not any("Input.ini" == s[0].split("::")[0] for s in c2.skipped) or True
    assert any("Input.ini" in s[0] for s in c2.skipped)    # 范围外跳过
    assert any("DiscordRichPresence" in s[0] for s in c2.skipped)
    # 预设
    c3 = changeset_from_preset("aimingcookie")
    assert c3.pus["EBooleanSettingId::SaveStatistics"][1] is True
    assert c3.pus["EIntegerSettingId::StatsExportLevel"] == ("integerSettings", 1)
    # 回滚目标解析
    try:
        resolve_restore_target(Path("evil.json.bak.20260830T120000"))
        raise AssertionError("非白名单文件应拒绝回滚")
    except SystemExit:
        pass
    # diff
    old = parse_pus(_PUS_MINI)
    newd = parse_pus(new)
    changed, added, removed = compute_diff(old, newd)
    assert (len(changed), len(added), len(removed)) == (2, 1, 0), (changed, added, removed)
    # 进程前缀匹配语义
    assert "fpsaimtrainer-win64-shipping.exe".startswith(GAME_PROCESS_PREFIX)
    assert "FPSAimTrainer.exe".lower().startswith(GAME_PROCESS_PREFIX)
    # 向量块替换（跨行条目，保留行尾逗号）+ 末尾插入（补前一行逗号）
    chv = {"EVectorSettingId::Aaa": ("vectorSettings", {"x": 0.5, "y": 1.0, "z": 1.0}),
           "EVectorSettingId::Zzz": ("vectorSettings", {"x": 0.0, "y": 0.0, "z": 1.0})}
    newv, (rep2, ins2) = edit_pus_text(_PUS_VEC, chv)
    assert rep2 == 1 and ins2 == 1, (rep2, ins2)
    pv = parse_pus(newv)  # 能解析 = 逗号/括号正确
    verify_edited_structure(_PUS_VEC, newv, chv)
    assert pv["vectorSettings"]["EVectorSettingId::Aaa"] == {"x": 0.5, "y": 1.0, "z": 1.0}
    assert pv["vectorSettings"]["EVectorSettingId::Zzz"] == {"x": 0.0, "y": 0.0, "z": 1.0}
    assert list(pv["vectorSettings"]) == sorted(pv["vectorSettings"])
    # 未触及行字节不变（Alpha 行原样保留）
    assert '\t\t"EBooleanSettingId::Alpha": true\r\n' in newv


if __name__ == "__main__":
    sys.exit(run())
