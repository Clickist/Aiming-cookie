"""诊断读图语境层：读本机 .sce 全参数，生成"这张图练什么"语境描述符。

产品模块。解析核心移植自研究版提取器 `.zcode/route-mining/extract_full.py`
（拷贝改写，不 import .zcode 下任何文件）；对 .sce 只读。描述符内容全部从
参数 + 配方书映射派生，映射出处以注释标注：
- 能力域↔旋钮映射：`.zcode/route-mining/capability-vocabulary.md` v1.2
- 机制解读与生成纪律：`.zcode/route-mining/generation-rules.md` v0.2
- 两大计分阵营/档位统计事实：`.zcode/route-mining/parameter-distributions.md`

两层结构（用户裁决 2026-10-07：实现机制≠训练语义）：
- parameter_facts：配置了什么，如实报（含躺尸配置名单）；
- training/economy/space：玩家实际体验的训练语义，由折算判据从事实层推导。

fail-open：.sce 缺失/解析失败/名字不匹配 → availability=unavailable 的描述符，
绝不抛异常、绝不伪造默认值，不阻塞分析主流程。

引用链纪律（配置存在≠体验存在）：只有从 AddedBots（本局实际出生的 bot）出发
经引用闭包可达的配置才进入结论；BotCharacters 调色板里未被出生引用的配置记入
dormant_profiles（躺尸），其参数不参与任何推导。
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION = "scenario_reading_descriptor.v1"

# 与 webapp/backend/kovaak_run_store.py 的 _SCENARIO_DEFINITION_NAME 同规
_SCENARIO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,159}$")

MAX_LIST_LENGTH = 64
MAX_DORMANT = 32
MAX_TARGETS = 12
MAX_VALUE_CHARS = 40

PROFILE_FAMILIES = OrderedDict([
    ("Aim Profile", "aim_profiles"),
    ("Bot Profile", "bot_profiles"),
    ("Dodge Profile", "dodge_profiles"),
    ("Character Profile", "character_profiles"),
    ("Weapon Profile", "weapon_profiles"),
    ("Bot Rotation Profile", "bot_rotation_profiles"),
])
PROFILE_BUCKETS = tuple(PROFILE_FAMILIES.values())

# bot profile 键 → 引用的 profile 桶（移植自 extract_full.BOT_REF_KEYS）
BOT_REF_KEYS = OrderedDict([
    ("DodgeProfileNames", "dodge_profiles"),
    ("AimingProfileNames", "aim_profiles"),
    ("WeaponsProfileNames", "weapon_profiles"),
    ("CharacterProfile", "character_profiles"),
])

_VEC3 = re.compile(r"^\s*Vector3\s+position\s+(\S+)\s+(\S+)\s+(\S+)")


class SceParseError(ValueError):
    """ .sce 结构性损坏，无法提取任何 header/profile。"""


def _split_names(value: str | None) -> list[str]:
    """拆 .sce 引用列表（"A;B;;C"）。移植自 extract_full.split_names。"""
    if value is None:
        return []
    return [s.strip() for s in str(value).split(";") if s.strip()]


def _truthy(value: str | None) -> bool:
    return str(value).strip().casefold() == "true"


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed


def _norm_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _parse_sections(text: str) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """解析 .sce 为 (header, [(段名, 键值字典), ...])。移植自
    extract_full.parse_sections：每个段实例独立保留，Map Data 段跳过。"""
    header: dict[str, str] = OrderedDict()
    sections: list[tuple[str, dict[str, str]]] = []
    cur: dict[str, str] | None = None
    in_map = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            name = s[1:-1].strip()
            cur = OrderedDict()
            sections.append((name, cur))
            in_map = name == "Map Data"
            continue
        if in_map or "=" not in s or not s:
            continue
        key, _, value = s.partition("=")
        if cur is None:
            header[key.strip()] = value.strip()
        else:
            cur[key.strip()] = value.strip()
    if not header and not sections:
        raise SceParseError("no header or sections recognized")
    return header, sections


def _parse_map_data(text: str) -> dict[str, Any]:
    """解析 [Map Data] Reflex 块的实体统计。移植自 extract_full.parse_map_data，
    只保留产品需要的：实体计数 + PlayerSpawn/Target 坐标 bbox。"""
    in_map = False
    counts: dict[str, int] = OrderedDict()
    cur_type: str | None = None
    spawn_coords: list[list[float]] = []
    target_coords: list[list[float]] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_map = s[1:-1].strip() == "Map Data"
            cur_type = None
            continue
        if not in_map:
            continue
        if s.startswith("type "):
            cur_type = s[5:].strip()
            counts[cur_type] = counts.get(cur_type, 0) + 1
            continue
        if cur_type is None:
            continue
        m = _VEC3.match(line)
        if not m:
            continue
        xyz = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
        if cur_type == "PlayerSpawn":
            spawn_coords.append(xyz)
        elif cur_type == "Target":
            target_coords.append(xyz)

    def bbox(coords: list[list[float]]) -> dict[str, Any]:
        if not coords:
            return {"count": 0, "span": None}
        xs, ys, zs = zip(*coords)
        spans = [max(vals) - min(vals) for vals in (xs, ys, zs)]
        return {"count": len(coords), "span": spans}

    return {
        "entity_counts": counts,
        "player_spawns": bbox(spawn_coords),
        "map_targets": bbox(target_coords),
    }


class _SceDocument:
    """一次解析的 .sce 文档：header + 各桶 profile 实例 + 引用闭包。"""

    def __init__(self, data: bytes) -> None:
        try:
            text = data.decode("utf-8-sig", errors="replace")
        except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover - replace 不抛
            raise SceParseError(f"decode failed: {exc}") from exc
        self.header, sections = _parse_sections(text)
        self.map_data = _parse_map_data(text)
        self.buckets: dict[str, list[dict[str, str]]] = {b: [] for b in PROFILE_BUCKETS}
        self.abilities: list[dict[str, str]] = []
        for sec_name, kv in sections:
            bucket = PROFILE_FAMILIES.get(sec_name)
            if bucket is not None:
                entry = dict(kv)
                entry["profile_name"] = kv.get("Name", "")
                self.buckets[bucket].append(entry)
            elif sec_name in {
                "Weapon Ability Profile", "Movement Ability Profile",
                "Melee Ability Profile", "Sprint Ability Profile",
            }:
                entry = dict(kv)
                entry["section"] = sec_name
                self.abilities.append(entry)

        # —— 引用闭包（配置存在≠体验存在）——
        # 活动种子 = AddedBots 出生条目（.bot→bot profile，.rot→rotation）+ 玩家
        # 角色链（header PlayerCharacters）。AddedBots 无有效出生时回退
        # BotCharacters（记 activation_basis，fail-open）。
        self._index: dict[str, dict[str, list[dict[str, str]]]] = {}
        for bucket in PROFILE_BUCKETS:
            idx: dict[str, list[dict[str, str]]] = {}
            for entry in self.buckets[bucket]:
                idx.setdefault(entry["profile_name"], []).append(entry)
            self._index[bucket] = idx
        self.activation_basis = "added_bots"
        added = self._resolve_spawn_entries(self.header.get("AddedBots", ""))
        if not added["bot_names"] and not added["rotation_names"]:
            added = self._resolve_spawn_entries(self.header.get("BotCharacters", ""))
            self.activation_basis = "bot_characters_fallback"
        self._active: dict[str, set[str]] = {b: set() for b in PROFILE_BUCKETS}
        for name in added["bot_names"]:
            self._active["bot_profiles"].add(name.casefold())
        for name in added["rotation_names"]:
            self._active["bot_rotation_profiles"].add(name.casefold())
        for name in _split_names(self.header.get("PlayerCharacters", "")):
            self._active["character_profiles"].add(name.casefold())
        self._close_references()
        self.bot_instances = added["bot_instances"]
        self.bot_spawn_names = added["spawn_stems"]

    def _resolve_spawn_entries(self, raw: str) -> dict[str, Any]:
        bot_names: list[str] = []
        rotation_names: list[str] = []
        instances = 0
        for item in _split_names(raw):
            stem, ext = os.path.splitext(item)
            if ext == ".bot":
                instances += 1
                if self._resolve("bot_profiles", stem):
                    bot_names.append(stem)
            elif ext == ".rot":
                instances += 1
                if self._resolve("bot_rotation_profiles", stem):
                    rotation_names.append(stem)
        return {
            "bot_names": bot_names,
            "rotation_names": rotation_names,
            "bot_instances": instances,
            # 逐实例的 bot profile 名（含重复），供事实层按实例聚合
            "spawn_stems": [
                os.path.splitext(item)[0]
                for item in _split_names(raw)
                if os.path.splitext(item)[1] in (".bot", ".rot")
            ],
        }

    def _resolve(self, bucket: str, name: str) -> list[dict[str, str]]:
        """按名解析 profile 实例；先精确后大小写不敏感（移植自 extract_full._resolve）。"""
        hits = self._index[bucket].get(name)
        if hits:
            return hits
        low = name.casefold()
        return [
            entry
            for key, entries in self._index[bucket].items()
            if key.casefold() == low
            for entry in entries
        ]

    def _close_references(self) -> None:
        """不动点闭包：活动 bot → 其引用的各桶；活动角色 → 其武器；
        活动 rotation → 其 bot。只在活动集合内传播（躺尸配置不外溢）。"""
        for _ in range(len(self.buckets) * MAX_LIST_LENGTH):
            changed = False
            for name in list(self._active["bot_profiles"]):
                for bot in self._resolve("bot_profiles", name):
                    for key, bucket in BOT_REF_KEYS.items():
                        for ref in _split_names(bot.get(key)):
                            if ref.casefold() not in self._active[bucket]:
                                if self._resolve(bucket, ref):
                                    self._active[bucket].add(ref.casefold())
                                    changed = True
            for name in list(self._active["character_profiles"]):
                for char in self._resolve("character_profiles", name):
                    for ref in _split_names(char.get("WeaponProfileNames")):
                        if ref.casefold() not in self._active["weapon_profiles"]:
                            if self._resolve("weapon_profiles", ref):
                                self._active["weapon_profiles"].add(ref.casefold())
                                changed = True
            for name in list(self._active["bot_rotation_profiles"]):
                for rot in self._resolve("bot_rotation_profiles", name):
                    for ref in _split_names(rot.get("ProfileNames")):
                        if ref.casefold() not in self._active["bot_profiles"]:
                            if self._resolve("bot_profiles", ref):
                                self._active["bot_profiles"].add(ref.casefold())
                                changed = True
            if not changed:
                break

    def is_active(self, bucket: str, entry: dict[str, str]) -> bool:
        return entry["profile_name"].casefold() in self._active[bucket]

    def active_entries(self, bucket: str) -> list[dict[str, str]]:
        return [e for e in self.buckets[bucket] if self.is_active(bucket, e)]

    def dormant_entries(self) -> list[tuple[str, dict[str, str]]]:
        result: list[tuple[str, dict[str, str]]] = []
        for bucket in PROFILE_BUCKETS:
            for entry in self.buckets[bucket]:
                if not self.is_active(bucket, entry):
                    result.append((bucket, entry))
        return result


# ---------------------------------------------------------------------------
# 事实层装配
# ---------------------------------------------------------------------------

def _character_facts(char: dict[str, str]) -> dict[str, Any]:
    respawn_min = _num(char.get("MinRespawnDelay"))
    respawn_max = _num(char.get("MaxRespawnDelay"))
    return {
        "name": char.get("profile_name", ""),
        "max_speed": _num(char.get("MaxSpeed")),
        "max_health": _num(char.get("MaxHealth")),
        "main_bb_radius": _num(char.get("MainBBRadius")),
        "main_bb_type": char.get("MainBBType") or None,
        "movement_type": char.get("MovementType") or None,
        "gravity": _num(char.get("Gravity")),
        "jump_velocity": _num(char.get("JumpVelocity")),
        "is_flyer": _truthy(char.get("IsFlyer")),
        "respawn_delay_s": [respawn_min, respawn_max],
        "health_regen_per_sec": _num(char.get("HealthRegenPerSec")),
    }


def _dodge_facts(dodge: dict[str, str] | None) -> dict[str, Any] | None:
    if dodge is None:
        return None
    return {
        "name": dodge.get("profile_name", ""),
        "toggle_left_right": _truthy(dodge.get("ToggleLeftRight")),
        "lr_time_change_s": [
            _num(dodge.get("MinLRTimeChange")), _num(dodge.get("MaxLRTimeChange")),
        ],
        "fb_time_change_s": [
            _num(dodge.get("MinFBTimeChange")), _num(dodge.get("MaxFBTimeChange")),
        ],
        "jump_frequency": _num(dodge.get("JumpFrequency")),
        "crouch_on_ground_frequency": _num(dodge.get("CrouchOnGroundFrequency")),
        "target_strafe_override": dodge.get("TargetStrafeOverride") or None,
        "target_distance": [
            _num(dodge.get("MinTargetDistance")), _num(dodge.get("MaxTargetDistance")),
        ],
    }


def _weapon_facts(weapon: dict[str, str]) -> dict[str, Any]:
    magazine = _num(weapon.get("MagazineMax"))
    return {
        "name": weapon.get("profile_name", ""),
        "type": weapon.get("Type") or None,
        "category": weapon.get("Category") or None,
        "time_between_shots_s": _num(weapon.get("TimeBetweenShots")),
        "shots_per_click": _num(weapon.get("ShotsPerClick")),
        "damage_per_shot": _num(weapon.get("DamagePerShot")),
        "magazine_max": int(magazine) if magazine is not None else None,
        "ammo_reloaded_on_kill": int(
            bounce
        ) if (bounce := _num(weapon.get("AmmoReloadedOnKill"))) is not None else None,
        "reload_time_from_empty_s": _num(weapon.get("ReloadTimeFromEmpty")),
        "headshot_multiplier": _num(weapon.get("HeadshotMultiplier")),
    }


def _scoring_facts(header: dict[str, str]) -> dict[str, Any]:
    timescale = _num(header.get("Timescale"))
    return {
        "score_per_kill": _num(header.get("ScorePerKill")),
        "score_per_damage": _num(header.get("ScorePerDamage")),
        "score_per_time": _num(header.get("ScorePerTime")),
        "score_to_win": _num(header.get("ScoreToWin")),
        "score_mult_accuracy": _truthy(header.get("ScoreMultAccuracy")),
        "score_mult_kill_efficiency": _truthy(header.get("ScoreMultKillEfficiency")),
        "score_loss_per_miss": _num(header.get("ScoreLossPerMiss")),
        "score_loss_per_damage_taken": _num(header.get("ScoreLossPerDamageTaken")),
        "time_refilled_by_kill_s": _num(header.get("TimeRefilledByKill")),
        "time_limit_s": _num(header.get("Timelimit")),
        "timescale": timescale,
        "invincible_bots": _truthy(header.get("InvincibleBots")),
    }


def _targets_facts(doc: _SceDocument) -> list[dict[str, Any]]:
    # 同名 bot 多实例只报一份（instances 计数）——同屏 bot 配置数语义见
    # parameter-distributions D6 补遗（配置数是同屏上限近似）。
    seen: dict[str, dict[str, Any]] = {}
    for name in doc.bot_spawn_names:
        bot = doc._resolve("bot_profiles", name)  # noqa: SLF001 - 模块内部使用
        if not bot:
            continue
        profile = bot[0]
        key = profile["profile_name"].casefold()
        if key in seen:
            seen[key]["instances"] += 1
            continue
        char_name = next(iter(_split_names(profile.get("CharacterProfile"))), None)
        char_hits = doc._resolve("character_profiles", char_name) if char_name else []
        char = char_hits[0] if char_hits else None
        dodge_name = next(iter(_split_names(profile.get("DodgeProfileNames"))), None)
        dodge_hits = doc._resolve("dodge_profiles", dodge_name) if dodge_name else []
        dodge = dodge_hits[0] if dodge_hits else None
        target = {
            "bot_profile": profile["profile_name"],
            "spawned_via": "AddedBots",
            "instances": 1,
            "character": _character_facts(char) if char else None,
            "dodge": _dodge_facts(dodge),
        }
        # movement_inert：变向配置挂着但主靶 MaxSpeed<=0 → 移动不会发生，
        # 呈现为"击杀后换位"（实现机制≠训练语义折算判据的参数锚）。
        max_speed = (target["character"] or {}).get("max_speed") or 0.0
        movement_none = (
            (target["character"] or {}).get("movement_type", "Base") == "None"
        )
        target["dodge"]["movement_inert"] = bool(
            target["dodge"]
        ) and (max_speed <= 0 or movement_none)
        seen[key] = target
    return list(seen.values())[:MAX_TARGETS]


# ---------------------------------------------------------------------------
# 训练语义折算（用户裁决 2026-10-07）
# ---------------------------------------------------------------------------

def _training(facts: dict[str, Any]) -> dict[str, Any]:
    """折算判据：区分"活着持续移动让你跟"与"以换位/出现为目的的移动"。

    - 靶不动（MaxSpeed=0 / MovementType=None）：静态点击；若击杀计分，判
      reposition_style（速死+击杀计分 → 移动配置服务"死后新位置出现"）。
    - 靶在动：按火（全自动/≤0.02s）+伤害或无敌语境 → 连续跟踪；否则（半自动
      +击杀计分典型如 pasu 系）→ 动态点击（确认与速度匹配，C91）。
    判读出处：capability-vocabulary.md 域1边界/域2/域5，C88/C91 链。
    """
    targets = facts["targets"]
    if not targets:
        return {
            "semantics": "unknown",
            "semantics_basis": "no_active_targets",
            "primary_domain": None,
            "domains": [],
            "rationale": "本机 .sce 无被出生引用的活动靶配置，无法折算训练语义。",
        }
    chars = [t["character"] for t in targets if t["character"]]
    moves = any(
        (c.get("max_speed") or 0.0) > 0 and c.get("movement_type") != "None"
        for c in chars
    )
    scoring = facts["scoring"]
    kill_camp = (scoring["score_per_kill"] or 0.0) > 0
    damage_camp = (
        (scoring["score_per_damage"] or 0.0) > 0 or scoring["invincible_bots"]
    )
    weapon = facts["player_weapon"]
    hold_fire = bool(weapon) and (
        weapon.get("category") == "FullyAuto"
        or (weapon.get("time_between_shots_s") is not None
            and weapon["time_between_shots_s"] <= 0.02)
    )
    if not moves:
        if kill_camp:
            basis = "reposition_style"
            primary = "static_positioning"
            domains = ["static_positioning"]
            rationale = (
                "主靶挂变向配置但 MaxSpeed=0、击杀计分：移动配置服务于"
                "\u201c点完一个、另一个换位置出现\u201d——训练语义=静态点击（换位式）。"
            )
        else:
            basis = "static_targets"
            primary = "static_positioning"
            domains = ["static_positioning"]
            rationale = "主靶静止（MaxSpeed=0 且无击杀计分结构）：静态点击。"
    elif hold_fire and (damage_camp or not kill_camp):
        basis = "sustained_hold_fire"
        primary = "smooth_tracking"
        domains = ["smooth_tracking"]
        rationale = (
            "靶在动 + 持续按火（全自动/极短射击间隔）+ 伤害语境：输出=持续在靶时间，"
            "训练语义=连续跟踪（平滑与速度匹配）。"
        )
    else:
        basis = "moving_target_clicking"
        primary = "confirm_timing"
        domains = ["confirm_timing", "smooth_tracking"]
        rationale = (
            "靶在动 + 点击式开火 + 击杀计分：练移动靶点击的确认与速度匹配"
            "（长变向周期段提供可读窗口，先对齐方向与速度再 commit）。"
        )
    if primary == "static_positioning":
        # 域4（微型靶档 r4–24 把微调变显性主任务）+ 域5（限弹+杀后回弹逼确认）
        small_target = any(
            (c.get("main_bb_radius") is not None and 0 < c["main_bb_radius"] <= 24)
            for c in chars
        )
        if small_target:
            domains.append("micro_adjust")
        if "ammo_economy" in (facts.get("_economy_structures") or []):
            domains.append("confirm_timing")
    return {
        "semantics": {
            "reposition_style": "static_clicking",
            "static_targets": "static_clicking",
            "moving_target_clicking": "dynamic_clicking",
            "sustained_hold_fire": "continuous_tracking",
        }[basis],
        "semantics_basis": basis,
        "primary_domain": primary,
        "domains": list(dict.fromkeys(domains)),
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# 经济结构 / 空间分布 / 证据链
# ---------------------------------------------------------------------------

def _economy(facts: dict[str, Any]) -> dict[str, Any]:
    """计分阵营/乘数/惩罚形态。出处：parameter-distributions D5 两大阵营；
    generation-rules 纪律5（惩罚键罕用）、纪律10（回弹两键辨析：
    弹药回弹=Weapon Profile.AmmoReloadedOnKill，时间回补=header.TimeRefilledByKill）。"""
    scoring = facts["scoring"]
    if (scoring["score_per_kill"] or 0.0) > 0:
        camp = "kill"
    elif (scoring["score_per_damage"] or 0.0) > 0:
        camp = "damage"
    elif (scoring["score_per_time"] or 0.0) > 0:
        camp = "time"
    else:
        camp = "none"
    multipliers = []
    if scoring["score_mult_accuracy"]:
        multipliers.append("accuracy")
    if scoring["score_mult_kill_efficiency"]:
        multipliers.append("kill_efficiency")
    multiplier = "+".join(multipliers) if multipliers else "none"
    penalties = []
    if (scoring["score_loss_per_miss"] or 0.0) > 0:
        penalties.append("miss")
    if (scoring["score_loss_per_damage_taken"] or 0.0) > 0:
        penalties.append("damage_taken")
    penalty = "+".join(penalties) if penalties else "none"
    weapon = facts["player_weapon"] or {}
    magazine = weapon.get("magazine_max") or 0
    bounce = weapon.get("ammo_reloaded_on_kill") or 0
    time_refill = scoring["time_refilled_by_kill_s"] or 0.0
    structures: list[str] = []
    ammo: dict[str, Any] | None = None
    if magazine > 0:
        ammo = {
            "magazine_max": magazine,
            "ammo_reloaded_on_kill": bounce,
            "time_refilled_by_kill_s": time_refill,
        }
        if bounce > 0:
            structures.append("ammo_economy")  # 弹药经济型：命中即续航
    if time_refill > 0:
        structures.append("time_refill")  # 击杀回时间（罕用档）
    if multiplier != "none":
        structures.append("multiplier_dilution")  # 乘数稀释型：贪快稀释分数
    if penalty != "none":
        structures.append("penalty")
    return {
        "scoring_camp": camp,
        "multiplier": multiplier,
        "penalty": penalty,
        "structures": structures,
        **({"ammo": ammo} if ammo is not None else {}),
    }


def _space(doc: _SceDocument, facts: dict[str, Any]) -> dict[str, Any]:
    """空间分布档（词汇表域3 v1.1：宽 >1500uu=拉枪链 / 窄 <500uu=快速微调 /
    中等 500–1500uu；无出生网格图（44%）分布轴由 Dodge 的 Min/MaxTargetDistance
    承担——parameter-distributions D6）。"""
    spawns = doc.map_data["player_spawns"]
    span = spawns["span"]
    if spawns["count"] > 0 and span is not None:
        max_span = max(span)
        if max_span > 1500:
            tier = "wide"
        elif max_span >= 500:
            tier = "medium"
        else:
            tier = "narrow"
        return {
            "tier": tier,
            "max_span_uu": max_span,
            "spawn_count": spawns["count"],
            "basis": "player_spawn_grid",
        }
    band = None
    for target in facts["targets"]:
        dodge = target.get("dodge")
        if dodge and all(v is not None for v in dodge.get("target_distance") or []):
            band = dodge["target_distance"]
            break
    return {
        "tier": "unknown",
        "max_span_uu": None,
        "spawn_count": 0,
        "basis": "dodge_target_distance",
        **({"target_distance_band": band} if band is not None else {}),
    }


def _evidence(doc: _SceDocument, facts: dict[str, Any]) -> list[dict[str, str]]:
    """机制依据：每个结论挂键名+值。键名用 full_extract 真实键（段名.键名）。"""
    rows: list[dict[str, str]] = []

    def add(claim: str, key: str, value: Any) -> None:
        text = "" if value is None else str(value)
        rows.append({"claim": claim, "key": key, "value": text[:MAX_VALUE_CHARS]})

    weapon = facts["player_weapon"]
    if weapon:
        if weapon.get("category"):
            add(
                "点 vs 摁：半自动=点击计价，全自动=持续按火"
                "（射速 0.01/0.1 双峰是最强家族判别键，D7）",
                "Weapon Profile.Category", weapon["category"],
            )
        if weapon.get("time_between_shots_s") is not None:
            add("射击间隔", "Weapon Profile.TimeBetweenShots", weapon["time_between_shots_s"])
        if (weapon.get("magazine_max") or 0) > 0:
            add(
                "限弹：乱甩变弹药损失（域3/域4 限弹行）",
                "Weapon Profile.MagazineMax", weapon["magazine_max"],
            )
        if (weapon.get("ammo_reloaded_on_kill") or 0) > 0:
            add(
                "弹药击杀回弹=命中即续航（两键辨析：非 header.TimeRefilledByKill）",
                "Weapon Profile.AmmoReloadedOnKill", weapon["ammo_reloaded_on_kill"],
            )
    for target in facts["targets"]:
        char = target["character"] or {}
        dodge = target["dodge"] or {}
        if dodge:
            tag = "（配置挂着但主靶 MaxSpeed=0，不生效）" if dodge.get("movement_inert") else ""
            add(
                f"变向周期 LR {dodge['lr_time_change_s'][0]}–"
                f"{dodge['lr_time_change_s'][1]}s{tag}（域1：越短变向越频；"
                "长周期档删变向变量归平滑/确认）",
                "Dodge Profile.MinLRTimeChange", dodge["lr_time_change_s"][0],
            )
            add("变向周期上限", "Dodge Profile.MaxLRTimeChange", dodge["lr_time_change_s"][1])
            if (dodge.get("jump_frequency") or 0) > 0:
                add(
                    "跳频：垂直分量打断腕段（域1 垂直行）",
                    "Dodge Profile.JumpFrequency", dodge["jump_frequency"],
                )
        if char:
            add(
                "主靶速度：0=换位式呈现 / >0=在靶期间持续移动",
                "Character Profile.MaxSpeed", char.get("max_speed"),
            )
            if (char.get("max_health") or 0) == 1:
                add(
                    "一发杀：速死靶，微调窗口由计分而非血量提供（D2）",
                    "Character Profile.MaxHealth", char["max_health"],
                )
            gravity = char.get("gravity") or 0.0
            jump_v = char.get("jump_velocity") or 0.0
            if char.get("is_flyer") or (0 < gravity <= 1 and jump_v >= 900):
                add(
                    "跳跃浮空实现（pasu 球滞空：低 Gravity+高 JumpVelocity，D11 形态1）",
                    "Character Profile.Gravity", char.get("gravity"),
                )
                add("跳跃浮空实现", "Character Profile.JumpVelocity", char.get("jump_velocity"))
            if (char.get("respawn_delay_s") or [None])[0] is not None:
                add(
                    "重生节奏（D9 三档：0.001 高密度刷靶 / 0.1 快 / 1–5 仿真）",
                    "Character Profile.MinRespawnDelay", char["respawn_delay_s"][0],
                )
    scoring = facts["scoring"]
    add(
        "计分阵营（D5 两大阵营：纯击杀≈clicking / 纯伤害≈tracking）",
        "header.ScorePerKill", scoring["score_per_kill"],
    )
    if (scoring["score_per_damage"] or 0.0) > 0:
        add("伤害计分：输出=持续在靶时间", "header.ScorePerDamage", scoring["score_per_damage"])
    if scoring["score_mult_accuracy"]:
        add(
            "准确率乘数：贪快稀释分数（乘数稀释型；frogClick 出入警示："
            "这是间接计价）",
            "header.ScoreMultAccuracy", "true",
        )
    if (scoring["score_loss_per_miss"] or 0.0) > 0:
        add(
            "miss 罚：抢拍直接扣分（重旋钮，仅个别场景，纪律5）",
            "header.ScoreLossPerMiss", scoring["score_loss_per_miss"],
        )
    timescale = scoring.get("timescale")
    if timescale is not None and timescale != 1.0:
        add(
            "倍速剂量（域9：0.5–0.94 学 pattern / 1.5–2.0 压反应）",
            "header.Timescale", timescale,
        )
    space = facts["space"]
    if space["basis"] == "player_spawn_grid":
        add(
            f"出生网格跨度 → {space['tier']} 分布档"
            "（域3：宽=拉枪链 / 窄=快速微调 / 中等=过渡带）",
            "map_data.player_spawn_summary.span", space.get("max_span_uu"),
        )
    else:
        add(
            "无出生网格：分布轴由 Dodge 的 Min/MaxTargetDistance 承担（D6）",
            "Dodge Profile.MinTargetDistance",
            (space.get("target_distance_band") or [None])[0],
        )
    return rows


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def reading_unavailable(display_name: str, reason: str) -> dict[str, Any]:
    """构造 unavailable 描述符（fail-open 的统一形态，供外部调用方复用）。"""
    return {
        "schema_version": SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION,
        "display_name": display_name,
        "availability": "unavailable",
        "reason": reason,
    }


_unavailable = reading_unavailable  # 模块内部沿用短名


def build_scenario_reading_descriptor(data: bytes, *, display_name: str) -> dict[str, Any]:
    """从 .sce 字节构建语境描述符。任何失败都返回 unavailable 描述符（fail-open）。"""
    try:
        doc = _SceDocument(data)
    except SceParseError:
        return _unavailable(display_name, "sce_parse_failed")
    except Exception:  # noqa: BLE001 - 读图绝不阻塞分析主流程
        return _unavailable(display_name, "sce_parse_failed")
    header_name = doc.header.get("Name", "")
    if header_name and _norm_name(header_name) != _norm_name(display_name):
        return _unavailable(display_name, "display_name_mismatch")
    facts: dict[str, Any] = {
        "activation_basis": doc.activation_basis,
        "bot_count": doc.bot_instances,
        "targets": _targets_facts(doc),
        "player_weapon": None,
        "scoring": _scoring_facts(doc.header),
        "rotation_profiles": [
            e["profile_name"] for e in doc.active_entries("bot_rotation_profiles")
        ],
        "dormant_profiles": [
            {"profile": e["profile_name"], "family": bucket}
            for bucket, e in doc.dormant_entries()[:MAX_DORMANT]
        ],
    }
    player_name = next(
        iter(_split_names(doc.header.get("PlayerCharacters", ""))), None,
    )
    if player_name:
        player_hits = doc._resolve("character_profiles", player_name)
        if player_hits:
            weapon_name = next(
                iter(_split_names(player_hits[0].get("WeaponProfileNames"))), None,
            )
            if weapon_name:
                weapon_hits = doc._resolve("weapon_profiles", weapon_name)
                if weapon_hits:
                    facts["player_weapon"] = _weapon_facts(weapon_hits[0])
    facts["space"] = _space(doc, facts)
    facts["_economy_structures"] = _economy(facts)["structures"]
    training = _training(facts)
    economy = _economy(facts)
    descriptor: dict[str, Any] = {
        "schema_version": SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION,
        "display_name": display_name,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "availability": "available",
        "parameter_facts": {
            key: facts[key]
            for key in (
                "activation_basis", "bot_count", "targets", "player_weapon",
                "scoring", "rotation_profiles", "dormant_profiles",
            )
        },
        "training": training,
        "mechanism_evidence": _evidence(doc, facts),
        "economy": economy,
        "space": {
            key: facts["space"][key]
            for key in ("tier", "max_span_uu", "spawn_count", "basis")
            if key in facts["space"]
        }
        | (
            {"target_distance_band": facts["space"]["target_distance_band"]}
            if "target_distance_band" in facts["space"] else {}
        ),
        "knowledge_refs": [
            "capability-vocabulary.md v1.2",
            "generation-rules.md v0.2",
            "parameter-distributions.md D5/D6/D7/D9/D11",
        ],
    }
    return descriptor


def _scenario_file_pools(install_dir: Path) -> list[tuple[str, str | None, Path]]:
    """优先级有序的 .sce 搜索池（local 在前，workshop 按目录名排序）。

    与 read_scenario_reading_from_dirs 的候选布局同规：本地 Scenarios 优先，
    其次 <steamapps>/workshop/content/824270/<id>/。返回 (origin, workshop_id,
    目录)，目录可能不存在（调用方逐个容错）。
    """
    pools: list[tuple[str, str | None, Path]] = [
        (
            "local_scenarios",
            None,
            install_dir / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios",
        ),
    ]
    # workshop 布局：<steamapps>/workshop/content/824270/<workshop_id>/<name>.sce，
    # install 根 = <steamapps>/common/<KovaaK 根> → parents[1] = <steamapps>
    parents = install_dir.parents
    if len(parents) >= 2:
        workshop_root = parents[1] / "workshop" / "content" / "824270"
        if workshop_root.is_dir():
            for child in sorted(workshop_root.iterdir())[:2000]:
                if child.is_dir():
                    pools.append(("workshop", child.name, child))
    return pools


def _spawned_bot_instances(doc: _SceDocument) -> list[str]:
    """逐出生槽展开 bot profile 名（含重复实例），供 bb 半径做逐实例统计。

    - AddedBots 基：.bot 直引 bot profile；.rot 经 rotation 的 ProfileNames
      展开为多个 bot（一条 rotation 出生槽 = 其引用的全部 bot）。
    - BotCharacters 回退基：出生名就是 bot profile 名。
    仅保留能解析到 profile 的实例（悬空引用不产半径）。
    """
    if doc.activation_basis == "bot_characters_fallback":
        return [n for n in doc.bot_spawn_names if doc._resolve("bot_profiles", n)]
    instances: list[str] = []
    for item in _split_names(doc.header.get("AddedBots", "")):
        stem, ext = os.path.splitext(item)
        if ext == ".bot":
            if doc._resolve("bot_profiles", stem):
                instances.append(stem)
        elif ext == ".rot":
            for rot in doc._resolve("bot_rotation_profiles", stem):
                for ref in _split_names(rot.get("ProfileNames")):
                    if doc._resolve("bot_profiles", ref):
                        instances.append(ref)
    return instances


def _scenario_bb_bots(doc: _SceDocument) -> list[dict[str, Any]]:
    """出生实例 → 逐实例 bot 半径条目（radius_cm=None 表示该实例解析不出）。"""
    bots: list[dict[str, Any]] = []
    for name in _spawned_bot_instances(doc):
        profile = doc._resolve("bot_profiles", name)[0]
        char_name = next(iter(_split_names(profile.get("CharacterProfile"))), None)
        char_hits = doc._resolve("character_profiles", char_name) if char_name else []
        char = char_hits[0] if char_hits else None
        bots.append({
            "profile": profile.get("profile_name", ""),
            "character": char.get("profile_name", "") if char else None,
            "bb_type": (char.get("MainBBType") or None) if char else None,
            "radius_cm": _num(char.get("MainBBRadius")) if char else None,
        })
    return bots


def extract_bounding_radius_from_bytes(data: bytes) -> dict[str, Any]:
    """解析 .sce 字节 → 出生实例级主靶 bb 半径（半径表生成器与本地层共用核心）。

    返回 {availability, header_name, timescale, bots, radii_cm}；损坏 →
    unavailable（reason=sce_parse_failed）。radius_cm=None 的实例保留（如实
    报告悬空引用），但不进 radii_cm。
    """
    try:
        doc = _SceDocument(data)
    except SceParseError:
        return {"availability": "unavailable", "reason": "sce_parse_failed"}
    except Exception:  # noqa: BLE001 - 单文件损坏绝不拖垮调用方
        return {"availability": "unavailable", "reason": "sce_parse_failed"}
    bots = _scenario_bb_bots(doc)
    return {
        "availability": "available",
        "header_name": doc.header.get("Name", ""),
        "timescale": _num(doc.header.get("Timescale")),
        "bots": bots,
        "radii_cm": [b["radius_cm"] for b in bots if b["radius_cm"] is not None],
    }


def resolve_scenario_bounding_radius(
    scenario: object,
    *,
    install_dir: str | Any,
) -> dict[str, Any]:
    """场景名 → 本机 .sce → 逐出生实例的主靶 bb 半径（bb_fill 的本地层）。

    两级匹配：文件名主干（游戏按文件名取 .sce，快路径，命中不校验 Name）→
    .sce 的 Name= 字段（stats 场景名可能与文件名不同）。两级都大小写/空白
    不敏感，语义同研究版 sce_bb.find_sce。fail-open：无效名/找不到/解析失败/
    无半径 → unavailable，绝不抛异常。对 .sce 只读。
    """
    display_name = scenario if isinstance(scenario, str) else ""

    def _unavailable(reason: str) -> dict[str, Any]:
        return {
            "availability": "unavailable",
            "scenario": display_name,
            "reason": reason,
        }

    if not isinstance(scenario, str) or not _SCENARIO_NAME_RE.fullmatch(scenario):
        return _unavailable("invalid_scenario_name")
    want = _norm_name(scenario)
    stem_hits: list[tuple[str, str | None, Path, bool]] = []
    others: list[tuple[str, str | None, Path, bool]] = []
    for origin, workshop_id, pdir in _scenario_file_pools(Path(install_dir)):
        try:
            files = sorted(pdir.glob("*.sce"))
        except OSError:
            continue
        for path in files:
            entry = (origin, workshop_id, path, _norm_name(path.stem) == want)
            (stem_hits if entry[3] else others).append(entry)
    saw_match_without_radius = False
    for origin, workshop_id, path, stem_hit in stem_hits + others:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        parsed = extract_bounding_radius_from_bytes(data)
        if parsed.get("availability") != "available":
            continue
        if not stem_hit and _norm_name(parsed.get("header_name", "")) != want:
            continue
        if not parsed["radii_cm"]:
            saw_match_without_radius = True
            continue
        return {
            "availability": "available",
            "scenario": scenario,
            "sce_file": path.name,
            "sce_path": str(path),
            "origin": origin,
            **({"workshop_id": workshop_id} if workshop_id else {}),
            "match_level": "file_stem" if stem_hit else "header_name",
            "timescale": parsed.get("timescale"),
            "bots": parsed.get("bots"),
            "radii_cm": parsed.get("radii_cm"),
        }
    return _unavailable("no_active_radius" if saw_match_without_radius else "sce_not_found")


def read_scenario_reading_from_dirs(
    scenario: object,
    *,
    install_dir: str | Any,
) -> dict[str, Any]:
    """读本机 .sce（本地 Scenarios 优先，其次 workshop），返回语境描述符。

    场景名无效 / 文件不存在 → unavailable（reason=sce_not_found 等），
    绝不抛异常。对 .sce 只读。
    """
    display_name = scenario if isinstance(scenario, str) else ""
    if not isinstance(scenario, str) or not _SCENARIO_NAME_RE.fullmatch(scenario):
        return _unavailable(display_name, "invalid_scenario_name")
    root = Path(install_dir)
    candidates: list[tuple[str, str | None, Path]] = [
        ("local_scenarios", None, root / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios" / f"{scenario}.sce"),
    ]
    # workshop 布局：<steamapps>/workshop/content/824270/<workshop_id>/<name>.sce，
    # install 根 = <steamapps>/common/<KovaaK 根> → parents[1] = <steamapps>
    parents = root.parents
    if len(parents) >= 2:
        workshop_root = parents[1] / "workshop" / "content" / "824270"
        if workshop_root.is_dir():
            for child in sorted(workshop_root.iterdir())[:2000]:
                if child.is_dir():
                    candidates.append(("workshop", child.name, child / f"{scenario}.sce"))
    for origin, workshop_id, path in candidates:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        descriptor = build_scenario_reading_descriptor(data, display_name=scenario)
        if descriptor["availability"] == "available":
            descriptor["source"] = {
                "origin": origin,
                **({"workshop_id": workshop_id} if workshop_id else {}),
            }
        return descriptor
    return _unavailable(scenario, "sce_not_found")


__all__ = [
    "SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION",
    "SceParseError",
    "build_scenario_reading_descriptor",
    "extract_bounding_radius_from_bytes",
    "read_scenario_reading_from_dirs",
    "reading_unavailable",
    "resolve_scenario_bounding_radius",
]
