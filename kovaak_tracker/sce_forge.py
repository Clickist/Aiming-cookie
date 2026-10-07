"""SCE 生成器本体（scenario-forge 写侧）：处方 → 合法 .sce → 回读自检 → 落盘。

产品模块。生成器与读回器（``kovaak_tracker.sce_reading``）闭环互证：每次生成
都用 sce_reading 解析回读，逐键比对处方，全对才放行；未通过自检的字节绝不落盘。

格式与知识出处（拷贝进本文件，不 import .zcode 下任何文件）：
- .sce 文本格式：逐键拷贝自本机真实场景 ``Humanoid Strafe.sce``
  （GameVersion 2.0.5.3，Reflex 文本版 [Map Data]），并对少量键做中性化覆盖
  （见 _CHARACTER_PROFILE_TEMPLATE 注释）；键序即该真实文件的键序。
- [Map Data] 几何与出生网格格式：拷贝自 ``1wall 6targets small.sce`` 的
  封闭立方体房间（内径 ±1024uu）与 PlayerSpawn 实体写法；CameraPath 块取自
  ``Reactive Flick.sce``（无悬空实体引用的形态）。
- 处方参数与难度递进语法：``.zcode/route-mining/generation-rules.md`` v0.3
  （规则 R1.1/R2.1/R3.6/R4.1/R9.3/R8.3/R9.5/R4.1-IR，出处以各预设函数注释标注）。
- 能力域命名：``.zcode/route-mining/capability-vocabulary.md`` v1.3。
- 空间分布档判据：parameter-distributions.md D6（宽 >1500uu=拉枪链 /
  窄 <500uu=快速微调 / 中等 500–1500uu；无出生网格由 Dodge dist 承担）。

键名勘误（配方书骨架名 → .sce 真实键，与 sce_reading 读回键一致）：
- 骨架名 ``Character Profile.GroundMaxSpeed`` → 真实键 ``MaxSpeed``
- 骨架名 ``Character Profile.GroundAcceleration`` → 真实键 ``Acceleration``

目录发现：本模块不硬编码任何机器路径。``forge_and_write`` 接受显式
``scenarios_dir`` 或 ``install_dir``；两者都缺时回退 ``KOVAAK_INSTALL_DIR``
env（与 ``webapp/backend/config.py::resolve_kovaak_install_dir`` 同一 override
契约）。安装根 → 场景目录的 join（``FPSAimTrainer/Saved/SaveGames/Scenarios``）
与 ``sce_reading.read_scenario_reading_from_dirs`` 现有发现逻辑同规。依赖方向
保持 webapp → kovaak_tracker（Coach 接线层后续负责传
``config.resolve_kovaak_install_dir()`` 的结果）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

from kovaak_tracker import sce_reading

FORGE_SCHEMA_VERSION = "sce_forge.v1"

AC_PREFIX = "AC"
#: 变向周期阶梯语法：每档 -0.1s（generation-rules 通用纪律 2 / R1.1）
DEFAULT_LR_STEP_PER_TIER = -0.1
#: 阶梯下限（反应极限带内，R1.1 末档 0.15；0.05 以下归读靶域）
LR_FLOOR = 0.05

# ---------------------------------------------------------------------------
# 文件内部 profile 引用名（非显示名；显示名 = .sce 文件名 = header Name）
# ---------------------------------------------------------------------------
_PLAYER_CHAR = "player"
_TARGET_CHAR = "target"
_TARGET_BOT = "target"
_DODGE_PROFILE = "ac strafe"
_WEAPON_PROFILE = "ac gun"

_SCENARIOS_SUBPATH = Path("FPSAimTrainer") / "Saved" / "SaveGames" / "Scenarios"


class ForgeError(ValueError):
    """处方不合法或目录来源缺失。"""


class SelfCheckError(ForgeError):
    """生成→回读自检未全对；带逐键明细。生成器绝不放行这种字节。"""


class ForgeWriteError(ForgeError):
    """写入阶段失败（目录来源缺失 / 目标文件已存在）。"""


# ---------------------------------------------------------------------------
# 处方输入结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForgePrescription:
    """生成一张 .sce 所需的完整处方。

    参数值即"预期写进文件并被 sce_reading 读回的值"（自检的比对基准）。
    难度档：``difficulty_tier``（1 起步），变向周期阶梯按 ``lr_step_per_tier``
    （默认 -0.1s/档）在预设函数内折算进 ``lr_time_change``；阶梯只对靶真的在动
    的处方有训练含义（静态靶 MaxSpeed=0 时 LR 档不生效，为惰性）。
    """

    # 身份
    name: str                     # 中文基名（显示名由 naming_scheme 组装）
    rule_id: str                  # 配方书规则 ID，如 "R1.1"
    domain: str                   # 能力域（capability-vocabulary v1.3 域名）
    difficulty_tier: int = 1
    lr_step_per_tier: float = DEFAULT_LR_STEP_PER_TIER

    # 目标（Character Profile 主靶）
    target_radius: float = 45.0
    target_shape: str = "Spheroid"          # Spheroid / Cylindrical / Cuboid
    target_height: float = 90.0             # Cylindrical/Cuboid 用；Spheroid 写直径
    target_has_head: bool = False
    target_head_radius: float = 0.0
    target_max_health: float = 300.0
    target_max_speed: float = 0.0           # 真实键 MaxSpeed（骨架名 GroundMaxSpeed）
    target_acceleration: float = 0.0        # 真实键 Acceleration（骨架名 GroundAcceleration）
    target_movement_type: str = "Base"      # Base / None
    target_gravity: float = 0.0
    target_jump_velocity: float = 0.0
    target_health_regen_per_sec: float = 0.0
    target_health_regen_delay: float = 0.0
    target_respawn: tuple[float, float] = (1.0, 1.0)   # Min/MaxRespawnDelay（序列接续节奏）

    # 变向（Dodge Profile）
    toggle_left_right: bool = True
    lr_time_change: tuple[float, float] = (0.4, 0.8)   # 已折算难度档的有效值
    fb_time_change: tuple[float, float] | None = None  # None=不处方（模板默认档）
    jump_frequency: float = 0.0
    crouch_frequency: float = 0.0
    damage_reaction: bool = False
    damage_reaction_threshold: float = 0.0
    target_distance: tuple[float, float] = (750.0, 2500.0)

    # 武器（玩家武器 Weapon Profile）
    weapon_category: str = "FullyAuto"      # FullyAuto / SemiAuto
    time_between_shots: float = 0.01
    damage_per_shot: float = 1.0
    magazine_max: int = 0
    ammo_per_shot: int = 1                  # 单发成本（改良换弹三键之一，纪律 10）
    ammo_reloaded_on_kill: int = 0
    headshot_multiplier: float = 2.0

    # 计分（header）
    score_per_kill: float = 0.0
    score_per_damage: float = 0.0
    score_per_hit: float = 0.0              # 按命中计分（R9.5 gauntlet，官方原文 §1.8）
    score_per_time: float = 0.0
    score_to_win: float = 0.0
    score_mult_accuracy: bool = False
    score_loss_per_miss: float = 0.0
    time_refilled_by_kill: float = 0.0
    invincible_bots: bool = False
    timelimit: float = 60.0
    timescale: float = 1.0

    # S5 基准型 FOV 头（纪律 19）：true → LockFOVRange=true/103.0/140.0/Clamped Horizontal
    lock_fov_s5: bool = False
    # 同屏多靶关碰撞（纪律 22）：≥2 同屏靶默认开；序列单活靶保持 false
    disable_character_collision: bool = False

    # 空间分布档（D6）：narrow / medium / wide / none
    space_tier: str = "none"
    bot_instances: int = 1

    # KovaaK 列表标签与说明
    game_tag: str = "Tracking"
    aim_type_tag: str = "Tracking"
    aim_sub_type_tag: str = "Both"
    description: str = ""

    # 自检预期（sce_reading 折算语义）
    expected_semantics: str = "continuous_tracking"

    @property
    def tier_label(self) -> str:
        return f"A{self.difficulty_tier}"


def _lr_ladder(
    base: tuple[float, float], tier: int, step: float = DEFAULT_LR_STEP_PER_TIER
) -> tuple[float, float]:
    """变向周期阶梯：每档 step（官方语法 -0.1s），下限 LR_FLOOR。"""
    shift = step * (max(tier, 1) - 1)
    min_lr = round(max(base[0] + shift, LR_FLOOR), 4)
    max_lr = round(max(base[1] + shift, min_lr), 4)
    return (min_lr, max_lr)


# ---------------------------------------------------------------------------
# .sce 模板（逐键拷贝自本机真实场景 Humanoid Strafe.sce，键序=原文件键序；
# %name% 形态为参数占位）
# ---------------------------------------------------------------------------

_HEADER_TEMPLATE: list[tuple[str, str]] = [
    ('Name', '%name%'),
    ('PlayerCharacters', 'player'),
    ('BotCharacters', '%bot_characters%'),
    ('IsChallenge', 'true'),
    ('Timelimit', '%timelimit%'),
    ('EndChallengeAfterKills', '0.0'),
    ('EndChallengeAfterDamage', '0.0'),
    ('PlayerProfile', 'player'),
    ('AddedBots', '%added_bots%'),
    ('PlayerMaxLives', '0'),
    ('BotMaxLives', '%bot_max_lives%'),
    ('PlayerTeam', '1'),
    ('BotTeams', '%bot_teams%'),
    ('MapName', 'cube_1wall_dense.map'),
    ('MapScale', '4.0'),
    ('BlockProjectilePredictors', 'true'),
    ('BlockCheats', 'true'),
    ('InvinciblePlayer', 'true'),
    ('InvincibleBots', '%invincible_bots%'),
    ('Timescale', '%timescale%'),
    ('BlockHealthbars', 'false'),
    ('TimeRefilledByKill', '%time_refilled_by_kill%'),
    ('ScoreToWin', '%score_to_win%'),
    ('ScorePerDamage', '%score_per_damage%'),
    ('ScorePerHit', '%score_per_hit%'),
    ('ScorePerKill', '%score_per_kill%'),
    ('ScorePerMidairDirect', '0.0'),
    ('ScorePerAnyDirect', '0.0'),
    ('ScorePerTime', '%score_per_time%'),
    ('ScoreLossPerDamageTaken', '0.0'),
    ('ScoreLossPerDeath', '0.0'),
    ('ScoreLossPerMidairDirected', '0.0'),
    ('ScoreLossPerAnyDirected', '0.0'),
    ('ScoreMultAccuracy', '%score_mult_accuracy%'),
    ('ScoreMultDamageEfficiency', 'false'),
    ('ScoreMultKillEfficiency', 'false'),
    ('GameTag', '%game_tag%'),
    ('WeaponHeroTag', '%weapon_hero_tag%'),
    ('AimTypeTag', '%aim_type_tag%'),
    ('AimSubTypeTag', '%aim_sub_type_tag%'),
    ('AimTypeFlicking', 'false'),
    ('AimTypeProjectile', 'false'),
    ('AimTypePlayerMovement', 'false'),
    ('DifficultyTag', '%difficulty_tag%'),
    ('AuthorsTag', 'Aiming Cookie'),
    ('BlockHitMarkers', 'false'),
    ('BlockHitSounds', 'false'),
    ('BlockMissSounds', 'false'),
    ('BlockFCT', 'false'),
    ('Description', '%description%'),
    ('GameVersion', '2.0.5.3'),
    ('ScorePerDistance', '0.0'),
    ('MBSEnable', 'false'),
    ('MBSTime1', '0.25'),
    ('MBSTime2', '0.5'),
    ('MBSTime3', '0.75'),
    ('MBSTime1Mult', '1.0'),
    ('MBSTime2Mult', '2.0'),
    ('MBSTime3Mult', '3.0'),
    ('MBSFBInstead', 'false'),
    ('MBSRequireEnemyAlive', 'false'),
    ('MaxDistanceTraveledScore', '0.0'),
    ('MaxMBSScore', '0.0'),
    ('DistanceScoreCondition', 'None'),
    ('DistScoreCondAcceptTime', '0.2'),
    ('ScoreLossPerMiss', '%score_loss_per_miss%'),
    ('MultSqrtAcc', 'false'),
    # FOV 锁定（纪律 19）：默认 false + 60/120（lock=false 模板默认对，写了未启用）；
    # lock_fov_s5=true → S5 基准型头 true/103.0/140.0/Clamped Horizontal（官方原文 §1.2）
    ('LockFOVRange', '%lock_fov_range%'),
    ('LockedFOVMin', '%locked_fov_min%'),
    ('LockedFOVMax', '%locked_fov_max%'),
    ('LockedFOVScale', 'Clamped Horizontal'),
    ('ScenarioVersion', 'Initial'),
]

_AIM_PROFILE_TEMPLATE: list[tuple[str, str]] = [
    ('Name', 'Default'),
    ('MinReactionTime', '0.3'),
    ('MaxReactionTime', '0.4'),
    ('MinSelfMovementCorrectionTime', '0.001'),
    ('MaxSelfMovementCorrectionTime', '0.05'),
    ('FlickFOV', '30.0'),
    ('FlickSpeed', '1.5'),
    ('FlickError', '15.0'),
    ('TrackSpeed', '3.5'),
    ('TrackError', '3.5'),
    ('MaxTurnAngleFromPadCenter', '75.0'),
    ('MinRecenterTime', '0.3'),
    ('MaxRecenterTime', '0.5'),
    ('OptimalAimFOV', '30.0'),
    ('OuterAimPenalty', '1.0'),
    ('MaxError', '40.0'),
    ('ShootFOV', '15.0'),
    ('VerticalAimOffset', '0.0'),
    ('MaxTolerableSpread', '5.0'),
    ('MinTolerableSpread', '1.0'),
    ('TolerableSpreadDist', '2000.0'),
    ('MaxSpreadDistFactor', '2.0'),
    ('AimingStyle', 'Original'),
    ('ScanSpeedMultiplier', '1.0'),
    ('MaxSeekPitch', '30.0'),
    ('MaxSeekYaw', '30.0'),
    ('AimingSpeed', '5.0'),
    ('MinShootDelay', '0.3'),
    ('MaxShootDelay', '0.6'),
]

_BOT_PROFILE_TEMPLATE: list[tuple[str, str]] = [
    ('Name', _TARGET_BOT),
    ('DodgeProfileNames', _DODGE_PROFILE),
    ('DodgeProfileWeights', '1.0'),
    ('DodgeProfileMaxChangeTime', '100.0'),
    ('DodgeProfileMinChangeTime', '100.0'),
    ('WeaponProfileWeights', '1.0;1.0;1.0;1.0;1.0;1.0;1.0;1.0'),
    ('AimingProfileNames', 'Default;Default;Default;Default;Default;Default;Default;Default'),
    ('WeaponSwitchTime', '3.0'),
    ('UseWeapons', 'false'),
    ('CharacterProfile', _TARGET_CHAR),
    ('SeeThroughWalls', 'false'),
    ('NoDodging', 'false'),
    ('NoAiming', 'false'),
    ('AbilityUseTimer', '0.1'),
    ('UseAbilityFrequency', '1.0'),
    ('UseAbilityFreqMinTime', '0.3'),
    ('UseAbilityFreqMaxTime', '0.6'),
    ('ShowLaser', 'false'),
    ('LaserRGB', 'X=1.000 Y=0.300 Z=0.000'),
    ('LaserAlpha', '1.0'),
    ('RandomizeDodgeProfiles', 'true'),
    ('RepeatDodgeProfileEntries', 'true'),
    ('UseMinimumRespawnTime', 'true'),
]

# 中性化覆盖（相对 Humanoid Strafe 敌方角色模板，理由）：
# - MeshHitDetection=false：命中判定走 MainBB 包络（true 会绕开 MainBBRadius，
#   靶半径处方失义）；WHJ SmoothStrafeSphere 靶角色同值。
# - CharacterModel=None + MainBBHide=false：几何靶形态（1w6ts/WHJ 靶同值）。
# - StrafeSpeedMult/ForwardSpeedBias=1.0、TerminalVelocity=0：去掉 HS 特有
#   加成，让 MaxSpeed 语义保真（自检按 MaxSpeed 直读）。
_CHARACTER_PROFILE_TEMPLATE: list[tuple[str, str]] = [
    ('Name', '%char_name%'),
    ('MaxHealth', '%max_health%'),
    ('WeaponProfileNames', '%weapon_profile_names%'),
    ('MinRespawnDelay', '%min_respawn_delay%'),
    ('MaxRespawnDelay', '%max_respawn_delay%'),
    ('StepUpHeight', '16.0'),
    ('CrouchHeightModifier', '0.5'),
    ('CrouchAnimationSpeed', '1.0'),
    ('CameraOffset', 'X=0.000 Y=0.000 Z=0.000'),
    ('HeadshotOnly', 'false'),
    ('DamageKnockbackFactor', '0.0'),
    ('MovementType', '%movement_type%'),
    ('MaxSpeed', '%max_speed%'),
    ('MaxCrouchSpeed', '390.0'),
    ('Acceleration', '%acceleration%'),
    ('Friction', '1.0'),
    ('BrakingFrictionFactor', '0.5'),
    ('JumpVelocity', '%jump_velocity%'),
    ('Gravity', '%gravity%'),
    ('AirControl', '0.25'),
    ('CanCrouch', 'false'),
    ('CanPogoJump', 'false'),
    ('CanCrouchInAir', 'false'),
    ('CanJumpFromCrouch', 'false'),
    ('EnemyBodyColor', 'X=1.000 Y=0.000 Z=0.000'),
    ('EnemyHeadColor', 'X=1.000 Y=1.000 Z=1.000'),
    ('TeamBodyColor', 'X=0.000 Y=0.000 Z=1.000'),
    ('TeamHeadColor', 'X=255.000 Y=255.000 Z=255.000'),
    ('BlockSelfDamage', 'false'),
    ('InvinciblePlayer', 'false'),
    ('InvincibleBots', 'false'),
    ('BlockTeamDamage', 'false'),
    ('AirJumpCount', '0'),
    ('AirJumpVelocity', '0.0'),
    ('MainBBType', '%main_bb_type%'),
    ('MainBBHeight', '%main_bb_height%'),
    ('MainBBRadius', '%main_bb_radius%'),
    ('MainBBHasHead', '%main_bb_has_head%'),
    ('MainBBHeadRadius', '%main_bb_head_radius%'),
    ('MainBBHeadOffset', '0.0'),
    ('MainBBHide', 'false'),
    ('ProjBBType', 'Cylindrical'),
    ('ProjBBHeight', '185.0'),
    ('ProjBBRadius', '37.0'),
    ('ProjBBHasHead', 'true'),
    ('ProjBBHeadRadius', '18.5'),
    ('ProjBBHeadOffset', '0.0'),
    ('ProjBBHide', 'true'),
    ('HasJetpack', 'false'),
    ('JetpackActivationDelay', '0.2'),
    ('JetpackFullFuelTime', '4.0'),
    ('JetpackFuelIncPerSec', '1.0'),
    ('JetpackFuelRegensInAir', 'false'),
    ('JetpackThrust', '6000.0'),
    ('JetpackMaxZVelocity', '400.0'),
    ('JetpackAirControlWithThrust', '0.25'),
    ('AbilityProfileNames', ''),
    ('HideWeapon', 'false'),
    ('AerialFriction', '0.0'),
    ('StrafeSpeedMult', '1.0'),
    ('BackSpeedMult', '1.0'),
    ('RespawnInvulnTime', '0.0'),
    ('BlockedSpawnRadius', '0.0'),
    ('BlockSpawnFOV', '0.0'),
    ('BlockSpawnDistance', '0.0'),
    ('RespawnAnimationDuration', '0.0'),
    ('AllowBufferedJumps', 'true'),
    ('BounceOffWalls', 'false'),
    ('LeanAngle', '0.0'),
    ('LeanDisplacement', '0.0'),
    ('AirJumpExtraControl', '0.0'),
    ('ForwardSpeedBias', '1.0'),
    ('HealthRegainedonkill', '0.0'),
    ('HealthRegenPerSec', '%health_regen_per_sec%'),
    ('HealthRegenDelay', '%health_regen_delay%'),
    ('JumpSpeedPenaltyDuration', '0.0'),
    ('JumpSpeedPenaltyPercent', '0.0'),
    ('ThirdPersonCamera', 'false'),
    ('TPSArmLength', '300.0'),
    ('TPSOffset', 'X=0.000 Y=150.000 Z=150.000'),
    ('BrakingDeceleration', '512.0'),
    ('TerminalVelocity', '0.0'),
    ('CharacterModel', 'None'),
    ('CharacterSkin', 'Default'),
    ('MeshHitDetection', 'false'),
    ('SpawnOffsetMin', 'X=0.000 Y=0.000 Z=0.000'),
    ('SpawnOffsetMax', 'X=0.000 Y=0.000 Z=0.000'),
    ('InvertBlockedSpawn', 'false'),
    ('ViewBobTime', '0.0'),
    ('ViewBobAngleAdjustment', '0.0'),
    ('ViewBobCameraZOffset', '0.0'),
    ('ViewBobAffectsShots', 'false'),
    ('IsFlyer', 'false'),
    ('FlightObeysPitch', 'false'),
    ('FlightVelocityUp', '800.0'),
    ('FlightAccelUp', '800.0'),
    ('FlightVelocityDown', '800.0'),
    ('FlightAccelDown', '800.0'),
    ('IsFlyUpOnJumpAndCrouch', 'false'),
    # 同屏多靶关碰撞（纪律 22；键序取自 VT Pasu Novice S5 真实文件同位置）
    ('DisableCharacterCollision', '%disable_character_collision%'),
    ('LifeStealPercent', '0.0'),
    ('AbilityGlobalCooldown', '0.0'),
    ('DragCoefficient', '10.0'),
    ('AmmoRegainedOnKill', '0'),
]

_DODGE_PROFILE_TEMPLATE: list[tuple[str, str]] = [
    ('Name', _DODGE_PROFILE),
    ('MaxTargetDistance', '%max_target_distance%'),
    ('MinTargetDistance', '%min_target_distance%'),
    ('ToggleLeftRight', '%toggle_left_right%'),
    ('ToggleForwardBack', '%toggle_forward_back%'),
    ('MinLRTimeChange', '%min_lr%'),
    ('MaxLRTimeChange', '%max_lr%'),
    ('MinFBTimeChange', '%min_fb%'),
    ('MaxFBTimeChange', '%max_fb%'),
    ('DamageReactionChangesDirection', '%damage_reaction%'),
    ('DamageReactionChanceToIgnore', '0.5'),
    ('DamageReactionMinimumDelay', '0.125'),
    ('DamageReactionMaximumDelay', '0.25'),
    ('DamageReactionCooldown', '1.0'),
    ('DamageReactionThreshold', '%damage_reaction_threshold%'),
    ('DamageReactionResetTimer', '0.1'),
    ('JumpFrequency', '%jump_frequency%'),
    ('CrouchInAirFrequency', '0.0'),
    ('CrouchOnGroundFrequency', '%crouch_frequency%'),
    ('TargetStrafeOverride', 'Ignore'),
    ('TargetStrafeMinDelay', '0.125'),
    ('TargetStrafeMaxDelay', '0.25'),
    ('MinProfileChangeTime', '0.0'),
    ('MaxProfileChangeTime', '0.0'),
    ('MinCrouchTime', '0.4'),
    ('MaxCrouchTime', '0.6'),
    ('MinJumpTime', '0.3'),
    ('MaxJumpTime', '0.6'),
    ('AlterateJumpCrouchInput', 'false'),
    ('ToggleUpDownMinTime', '0.2'),
    ('ToggleUpDownMaxTime', '0.5'),
    ('UpDownSwapPauseMinTime', '0.0'),
    ('UpDownSwapPauseMaxTime', '0.0'),
    ('LeftStrafeTimeMult', '1.0'),
    ('RightStrafeTimeMult', '1.0'),
    ('StrafeSwapMinPause', '0.0'),
    ('StrafeSwapMaxPause', '0.0'),
    ('BlockedMovementPercent', '0.5'),
    ('BlockedMovementReactionMin', '0.1'),
    ('BlockedMovementReactionMax', '0.1'),
    ('WaypointLogic', 'Ignore'),
    ('WaypointTurnRate', '200.0'),
    ('MinTimeBeforeShot', '0.15'),
    ('MaxTimeBeforeShot', '0.25'),
    ('IgnoreShotChance', '0.0'),
    ('ForwardTimeMult', '1.0'),
    ('BackTimeMult', '1.0'),
    ('DamageReactionChangesFB', 'false'),
    ('CooldownTime', '0.0'),
    ('DamageReactionTriggersProfileChange', 'false'),
    ('LOSReactType', 'None'),
    ('LOSReactInitMin', '0.175'),
    ('LOSReactInitMax', '0.25'),
    ('LOSReactChanceIgnore', '0.0'),
    ('LOSReactCooldownTime', '1.0'),
    ('LOSReactDurationMin', '1.0'),
    ('LOSReactDurationMax', '1.0'),
    ('LOSReactKillBot', 'false'),
    ('LOSReactKillBotTimerMin', '0.5'),
    ('LOSReactKillBotTimerMax', '0.75'),
]

# 武器弹道保真（R7.5/纪律 19）：Spread 全键留模板向量、UsePerShotRecoil=false、
# CanAimDownSight=false、HitscanRadius=0——非仿真场景一律默认关。
_WEAPON_PROFILE_TEMPLATE: list[tuple[str, str]] = [
    ('Name', _WEAPON_PROFILE),
    ('Type', 'Hitscan'),
    ('ShotsPerClick', '1'),
    ('DamagePerShot', '%damage_per_shot%'),
    ('KnockbackFactor', '0.0'),
    ('TimeBetweenShots', '%time_between_shots%'),
    ('Pierces', 'false'),
    ('Category', '%category%'),
    ('BurstShotCount', '1'),
    ('TimeBetweenBursts', '0.5'),
    ('ChargeStartDamage', '10.0'),
    ('ChargeStartVelocity', 'X=500.000 Y=0.000 Z=0.000'),
    ('ChargeTimeToAutoRelease', '2.0'),
    ('ChargeTimeToCap', '1.0'),
    ('ChargeMoveSpeedModifier', '1.0'),
    ('MuzzleVelocityMin', 'X=2000.000 Y=0.000 Z=0.000'),
    ('MuzzleVelocityMax', 'X=2000.000 Y=0.000 Z=0.000'),
    ('InheritOwnerVelocity', '0.0'),
    ('OriginOffset', 'X=0.000 Y=0.000 Z=0.000'),
    ('MaxTravelTime', '5.0'),
    ('MaxHitscanRange', '100000.0'),
    ('GravityScale', '1.0'),
    ('HeadshotCapable', 'false'),
    ('HeadshotMultiplier', '%headshot_multiplier%'),
    ('MagazineMax', '%magazine_max%'),
    ('AmmoPerShot', '%ammo_per_shot%'),
    ('ReloadTimeFromEmpty', '0.1'),
    ('ReloadTimeFromPartial', '0.1'),
    ('DamageFalloffStartDistance', '100000.0'),
    ('DamageFalloffStopDistance', '100000.0'),
    ('DamageAtMaxRange', '1.0'),
    ('DelayBeforeShot', '0.0'),
    ('ProjectileGraphic', 'Ball'),
    ('VisualLifetime', '0.00001'),
    ('BounceOffWorld', 'false'),
    ('BounceFactor', '0.5'),
    ('BounceCount', '0'),
    ('HomingProjectileAcceleration', '0.0'),
    ('ProjectileEnemyHitRadius', '1.0'),
    ('CanAimDownSight', 'false'),
    ('ADSZoomDelay', '0.0'),
    ('ADSZoomSensFactor', '0.7'),
    ('ADSMoveFactor', '1.0'),
    ('ADSStartDelay', '0.0'),
    ('ShootSoundCooldown', '0.0'),
    ('HitSoundCooldown', '0.0'),
    ('HitscanVisualOffset', 'X=0.000 Y=0.000 Z=-50.000'),
    ('ADSBlocksShooting', 'false'),
    ('ShootingBlocksADS', 'false'),
    ('KnockbackFactorAir', '0.0'),
    ('RecoilNegatable', 'false'),
    ('DecalType', '0'),
    ('DecalSize', '30.0'),
    ('DelayAfterShooting', '0.0'),
    ('BeamTracksCrosshair', 'true'),
    ('AlsoShoot', ''),
    ('ADSShoot', ''),
    ('StunDuration', '0.0'),
    ('CircularSpread', 'true'),
    ('SpreadStationaryVelocity', '0.0'),
    ('PassiveCharging', 'false'),
    ('BurstFullyAuto', 'true'),
    ('FlatKnockbackHorizontal', '0.0'),
    ('FlatKnockbackVertical', '0.0'),
    ('HitscanRadius', '0.0'),
    ('HitscanVisualRadius', '6.0'),
    ('TaggingDuration', '0.0'),
    ('TaggingMaxFactor', '1.0'),
    ('TaggingHitFactor', '1.0'),
    ('RecoilCrouchScale', '1.0'),
    ('RecoilADSScale', '1.0'),
    ('PSRCrouchScale', '1.0'),
    ('PSRADSScale', '1.0'),
    ('ProjectileAcceleration', '0.0'),
    ('AccelIncludeVertical', 'false'),
    ('AimPunchAmount', '0.0'),
    ('AimPunchResetTime', '0.2'),
    ('AimPunchCooldown', '0.5'),
    ('AimPunchHeadshotOnly', 'false'),
    ('AimPunchCosmeticOnly', 'false'),
    ('MinimumDecelVelocity', '0.0'),
    ('PSRManualNegation', 'false'),
    ('PSRAutoReset', 'true'),
    ('AimPunchUpTime', '0.05'),
    ('AmmoReloadedOnKill', '%ammo_reloaded_on_kill%'),
    ('CancelReloadOnKill', 'false'),
    ('FlatKnockbackHorizontalMin', '0.0'),
    ('FlatKnockbackVerticalMin', '0.0'),
    ('ADSScope', 'No Scope'),
    ('ADSFOVOverride', '104.0'),
    ('ADSFOVScale', 'Quake/Source'),
    ('ADSAllowUserOverrideFOV', 'false'),
    ('IsBurstWeapon', 'false'),
    ('ForceFirstPersonInADS', 'true'),
    ('ZoomBlockedInAir', 'false'),
    ('ADSCameraOffsetX', '0.0'),
    ('ADSCameraOffsetY', '0.0'),
    ('ADSCameraOffsetZ', '0.0'),
    ('QuickSwitchTime', '0.1'),
    ('WeaponModel', 'Asp'),
    ('WeaponAnimation', 'None'),
    ('UseIncReload', 'false'),
    ('IncReloadStartupTime', '0.1'),
    ('IncReloadLoopTime', '0.1'),
    ('IncReloadAmmoPerLoop', '1'),
    ('IncReloadEndTime', '0.1'),
    ('IncReloadCancelWithShoot', 'true'),
    ('WeaponSkin', 'Default'),
    ('ProjectileVisualOffset', 'X=0.000 Y=0.000 Z=-50.000'),
    ('SpreadDecayDelay', '0.0'),
    ('ReloadBeforeRecovery', 'true'),
    ('3rdPersonWeaponModel', 'None'),
    ('3rdPersonWeaponSkin', 'Default'),
    ('ParticleMuzzleFlash', 'None'),
    ('ParticleWallImpact', 'None'),
    ('ParticleBodyImpact', 'Beam'),
    ('ParticleProjectileTrail', ''),
    ('ParticleHitscanTrace', 'Tracer'),
    ('ParticleMuzzleFlashScale', '1.0'),
    ('ParticleWallImpactScale', '1.0'),
    ('ParticleBodyImpactScale', '0.3'),
    ('ParticleProjectileTrailScale', '1.0'),
    ('ADSCustomFOVAspectX', '16'),
    ('ADSCustomFOVAspectY', '9'),
    ('ADSCustomFOVScale', 'hML'),
    ('Explosive', 'false'),
    ('Radius', '500.0'),
    ('DamageAtCenter', '100.0'),
    ('DamageAtEdge', '0.0'),
    ('SelfDamageMultiplier', '0.5'),
    ('ExplodesOnContactWithEnemy', 'false'),
    ('DelayAfterEnemyContact', '0.0'),
    ('ExplodesOnContactWithWorld', 'false'),
    ('DelayAfterWorldContact', '0.0'),
    ('ExplodesOnNextAttack', 'false'),
    ('DelayAfterSpawn', '0.0'),
    ('BlockedByWorld', 'false'),
    ('ClearAttackersOnSelfDmg', 'false'),
    ('SpreadSSA', '0.0,0.1,0.0,0.0'),
    ('SpreadSCA', '0.0,0.1,0.0,0.0'),
    ('SpreadMSA', '1.0,1.0,-1.0,0.0'),
    ('SpreadMCA', '1.0,1.0,-1.0,0.0'),
    ('SpreadSSH', '1.0,1.0,-1.0,0.0'),
    ('SpreadSCH', '1.0,1.0,-1.0,0.0'),
    ('SpreadMSH', '1.0,1.0,-1.0,0.0'),
    ('SpreadMCH', '1.0,1.0,-1.0,0.0'),
    ('MaxRecoilUp', '0.0'),
    ('MinRecoilUp', '0.0'),
    ('MinRecoilHoriz', '0.0'),
    ('MaxRecoilHoriz', '0.0'),
    ('FirstShotRecoilMult', '1.0'),
    ('RecoilAutoReset', 'false'),
    ('TimeToRecoilPeak', '0.05'),
    ('TimeToRecoilReset', '0.35'),
    ('AAMode', '0'),
    ('AAPreferClosestPlayer', 'false'),
    ('AAAlpha', '0.05'),
    ('AAMaxSpeed', '1.0'),
    ('AADeadZone', '0.0'),
    ('AAFOV', '30.0'),
    ('AANeedsLOS', 'true'),
    ('TrackHorizontal', 'true'),
    ('TrackVertical', 'true'),
    ('AABlocksMouse', 'false'),
    ('AAOffTimer', '0.0'),
    ('AABackOnTimer', '0.0'),
    ('TriggerBotEnabled', 'false'),
    ('TriggerBotDelay', '0.0'),
    ('TriggerBotFOV', '1.0'),
    ('StickyLock', 'false'),
    ('HeadLock', 'false'),
    ('VerticalOffset', '0.0'),
    ('DisableLockOnKill', 'false'),
    ('UsePerShotRecoil', 'false'),
    ('PSRLoopStartIndex', '0'),
    ('PSRViewRecoilTracking', '0.45'),
    ('PSRCapUp', '9.0'),
    ('PSRCapRight', '4.0'),
    ('PSRCapLeft', '4.0'),
    ('PSRTimeToPeak', '0.175'),
    ('PSRResetDegreesPerSec', '40.0'),
    ('UsePerBulletSpread', 'false'),
    ('PBS0', '0.0,0.0'),
]


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------

def _bool(value: bool) -> str:
    return "true" if value else "false"


def _num(value: float | int) -> str:
    """数值格式化：repr(float) 保证字节→浮点精确回读（自检逐键比对的前提）。"""
    return repr(float(value))


def _render(template: list[tuple[str, str]], params: dict[str, str]) -> str:
    lines = []
    for key, value in template:
        if value.startswith("%") and value.endswith("%"):
            value = params[value[1:-1]]
        lines.append(f"{key}={value}")
    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# [Map Data]：封闭立方体房间 + 出生网格（几何写法拷贝自 1wall 6targets small）
# ---------------------------------------------------------------------------

#: 房间几何（1w6ts cube 内径 ±1024uu、壁厚 64、地板顶面 y=-1024；出生点 y=-32）
_FLOOR_TOP = -1024.0
_SPAWN_Y = -32.0
_ROOM = 1024.0
_WALL_OUT = 1088.0

#: 出生网格档（D6 判据：窄 <500 / 中等 500–1500 / 宽 >1500；跨度=max(xs)-min(xs)）。
#: 坐标为 X/Z 网格点（不含恒在原点的玩家出生点）。
_SPAWN_GRIDS: dict[str, list[tuple[float, float]]] = {
    "narrow": [(x, z) for x in (-120.0, 120.0) for z in (-120.0, 120.0)],       # span 240
    "medium": [(x, z) for x in (-480.0, 0.0, 480.0) for z in (-480.0, 0.0, 480.0)],  # span 960
    "wide": [(x, z) for x in (-920.0, -460.0, 0.0, 460.0, 920.0)
             for z in (-920.0, -460.0, 0.0, 460.0, 920.0)],                     # span 1840
}
_GRID_EXPECTED_SPAN = {"narrow": 240.0, "medium": 960.0, "wide": 1840.0}

_BRUSH_FACE_INDICES = ((0, 1, 2, 3), (6, 5, 4, 7), (2, 1, 5, 6), (0, 3, 7, 4), (3, 2, 6, 7), (1, 0, 4, 5))


def _box_brush(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> str:
    """轴对齐盒刷（顶点序与 6 面索引逐一拷贝自 1wall 6targets small 的刷块）。"""
    vertices = (
        (x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0),
        (x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0),
    )
    lines = ["\tbrush", "\t\tvertices"]
    lines += [f"\t\t\t{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in vertices]
    lines.append("\t\tfaces")
    lines += [
        f"\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 {a} {b} {c} {d} 0x00000000 "
        for a, b, c, d in _BRUSH_FACE_INDICES
    ]
    return "\r\n".join(lines)


def _spawn_entity(x: float, y: float, z: float) -> str:
    lines = [
        "\tentity",
        "\t\ttype PlayerSpawn",
        f"\t\tVector3 position {x:.6f} {y:.6f} {z:.6f}",
        "\t\tBool8 teamB 0",
        "\t\tBool8 modeCTF 0",
        "\t\tBool8 modeFFA 0",
        "\t\tBool8 modeTDM 0",
        "\t\tBool8 mode1v1 0",
        "\t\tBool8 modeRace 0",
        "\t\tBool8 mode2v2 0",
    ]
    return "\r\n".join(lines)


def _map_data_blob(space_tier: str) -> str:
    """生成 [Map Data] 段。space_tier=none → 0 出生点的无网格形态（D6：44% 场景，
    分布轴由 Dodge dist 承担；真实样本 Aimerz+ Week #7 - Suavetrack）。"""
    lines = ["[Map Data]", "reflex map version 8", "global"]
    # WorldSpawn（三张真实场景逐字同构）
    lines += [
        "\tentity",
        "\t\ttype WorldSpawn",
        "\t\tString32 targetGameOverCamera end",
        "\t\tUInt8 playersMin 1",
        "\t\tUInt8 playersMax 16",
    ]
    # 封闭房间：地板 + 天花板 + 四面全长墙（含角落，六刷密封）
    lines.append(_box_brush(-_ROOM, _ROOM, _FLOOR_TOP - 64.0, _FLOOR_TOP, -_ROOM, _ROOM))
    lines.append(_box_brush(-_ROOM, _ROOM, _ROOM, _WALL_OUT, -_ROOM, _ROOM))
    lines.append(_box_brush(-_WALL_OUT, -_ROOM, _FLOOR_TOP, _ROOM, -_WALL_OUT, _WALL_OUT))
    lines.append(_box_brush(_ROOM, _WALL_OUT, _FLOOR_TOP, _ROOM, -_WALL_OUT, _WALL_OUT))
    lines.append(_box_brush(-_ROOM, _ROOM, _FLOOR_TOP, _ROOM, -_WALL_OUT, -_ROOM))
    lines.append(_box_brush(-_ROOM, _ROOM, _FLOOR_TOP, _ROOM, _ROOM, _WALL_OUT))
    # CameraPath（Reactive Flick 形态：无悬空实体引用）
    lines += [
        "\tentity",
        "\t\ttype CameraPath",
        "\t\tUInt8 posLerp 2",
        "\t\tUInt8 angleLerp 2",
    ]
    # 出生网格：玩家恒在原点，靶位按档铺网格
    coords = [(0.0, 0.0)] + list(_SPAWN_GRIDS.get(space_tier, []))
    for x, z in coords:
        lines.append(_spawn_entity(x, _SPAWN_Y, z))
    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def _character_params(
    *, name: str, max_health: float, weapon_names: str, respawn: tuple[float, float],
    movement_type: str, max_speed: float, acceleration: float, bb_type: str,
    bb_height: float, bb_radius: float, has_head: bool, head_radius: float,
    regen_per_sec: float, regen_delay: float, gravity: float, jump_velocity: float,
    disable_collision: bool = False,
) -> dict[str, str]:
    return {
        "char_name": name,
        "max_health": _num(max_health),
        "weapon_profile_names": weapon_names,
        "min_respawn_delay": _num(respawn[0]),
        "max_respawn_delay": _num(respawn[1]),
        "movement_type": movement_type,
        "max_speed": _num(max_speed),
        "acceleration": _num(acceleration),
        "main_bb_type": bb_type,
        "main_bb_height": _num(bb_height),
        "main_bb_radius": _num(bb_radius),
        "main_bb_has_head": _bool(has_head),
        "main_bb_head_radius": _num(head_radius),
        "health_regen_per_sec": _num(regen_per_sec),
        "health_regen_delay": _num(regen_delay),
        "gravity": _num(gravity),
        "jump_velocity": _num(jump_velocity),
        "disable_character_collision": _bool(disable_collision),
    }


def forge_sce_text(prescription: ForgePrescription, *, name: str) -> bytes:
    """从处方生成 .sce 字节（UTF-8 无 BOM、CRLF，与真实 .sce 一致）。只生成不写盘。"""
    if prescription.space_tier not in ("narrow", "medium", "wide", "none"):
        raise ForgeError(f"未知空间分布档: {prescription.space_tier!r}")
    if prescription.difficulty_tier < 1:
        raise ForgeError(f"难度档必须 >=1，收到 {prescription.difficulty_tier}")
    if prescription.bot_instances < 1:
        raise ForgeError(f"bot 实例数必须 >=1，收到 {prescription.bot_instances}")
    if prescription.disable_character_collision and prescription.bot_instances < 2:
        raise ForgeError(
            "disable_character_collision 是同屏多靶纪律（generation-rules v0.3 纪律 22）："
            f"仅对同屏 >=2 靶有意义，收到 bot_instances={prescription.bot_instances}"
        )

    bots = [f"{_TARGET_BOT}.bot"] * prescription.bot_instances
    if prescription.lock_fov_s5:
        # S5 基准型 FOV 头：6/6 在库 S5 场景实测 103–140 Clamped Horizontal（纪律 19）
        fov_params = {"lock_fov_range": "true", "locked_fov_min": "103.0", "locked_fov_max": "140.0"}
    else:
        # 默认不锁：60/120 是 lock=false 的模板默认对（写了未启用，勿当特化档引用）
        fov_params = {"lock_fov_range": "false", "locked_fov_min": "60.0", "locked_fov_max": "120.0"}
    header_params = {
        "name": name,
        "bot_characters": ";".join(bots),
        "timelimit": _num(prescription.timelimit),
        "added_bots": ";".join(bots),
        "bot_max_lives": ";".join(["0"] * prescription.bot_instances),
        "bot_teams": ";".join(["2"] * prescription.bot_instances),
        "invincible_bots": _bool(prescription.invincible_bots),
        "timescale": _num(prescription.timescale),
        "time_refilled_by_kill": _num(prescription.time_refilled_by_kill),
        "score_to_win": _num(prescription.score_to_win),
        "score_per_damage": _num(prescription.score_per_damage),
        "score_per_hit": _num(prescription.score_per_hit),
        "score_per_kill": _num(prescription.score_per_kill),
        "score_per_time": _num(prescription.score_per_time),
        "score_mult_accuracy": _bool(prescription.score_mult_accuracy),
        "score_loss_per_miss": _num(prescription.score_loss_per_miss),
        **fov_params,
        "game_tag": prescription.game_tag,
        "weapon_hero_tag": "",
        "aim_type_tag": prescription.aim_type_tag,
        "aim_sub_type_tag": prescription.aim_sub_type_tag,
        "difficulty_tag": "3",
        "description": (
            prescription.description
            or f"Aiming Cookie 处方场景 {prescription.rule_id} "
               f"{prescription.tier_label}档（sce_forge）"
        ),
    }

    fb = prescription.fb_time_change or (1.5, 1.5)  # 模板默认档（不处方时）
    dodge_params = {
        "max_target_distance": _num(prescription.target_distance[1]),
        "min_target_distance": _num(prescription.target_distance[0]),
        "toggle_left_right": _bool(prescription.toggle_left_right),
        "toggle_forward_back": "false",
        "min_lr": _num(prescription.lr_time_change[0]),
        "max_lr": _num(prescription.lr_time_change[1]),
        "min_fb": _num(fb[0]),
        "max_fb": _num(fb[1]),
        "damage_reaction": _bool(prescription.damage_reaction),
        "damage_reaction_threshold": _num(prescription.damage_reaction_threshold),
        "jump_frequency": _num(prescription.jump_frequency),
        "crouch_frequency": _num(prescription.crouch_frequency),
    }
    weapon_params = {
        "category": prescription.weapon_category,
        "time_between_shots": _num(prescription.time_between_shots),
        "damage_per_shot": _num(prescription.damage_per_shot),
        "magazine_max": str(int(prescription.magazine_max)),
        "ammo_per_shot": str(int(prescription.ammo_per_shot)),
        "ammo_reloaded_on_kill": str(int(prescription.ammo_reloaded_on_kill)),
        "headshot_multiplier": _num(prescription.headshot_multiplier),
    }
    target_char = _character_params(
        name=_TARGET_CHAR,
        max_health=prescription.target_max_health,
        weapon_names=";;;;;;;",
        respawn=prescription.target_respawn,
        movement_type=prescription.target_movement_type,
        max_speed=prescription.target_max_speed,
        acceleration=prescription.target_acceleration,
        bb_type=prescription.target_shape,
        bb_height=prescription.target_height,
        bb_radius=prescription.target_radius,
        has_head=prescription.target_has_head,
        head_radius=prescription.target_head_radius,
        regen_per_sec=prescription.target_health_regen_per_sec,
        regen_delay=prescription.target_health_regen_delay,
        gravity=prescription.target_gravity,
        jump_velocity=prescription.target_jump_velocity,
        disable_collision=prescription.disable_character_collision,
    )
    player_char = _character_params(
        name=_PLAYER_CHAR,
        max_health=100.0,
        weapon_names=f"{_WEAPON_PROFILE};;;;;;;",
        respawn=(0.1, 0.1),
        movement_type="Base",
        max_speed=0.0,   # 站桩站位（1w6ts 玩家角色同形态）
        acceleration=0.0,
        bb_type="Cylindrical",
        bb_height=185.0,
        bb_radius=37.0,
        has_head=False,
        head_radius=0.0,
        regen_per_sec=0.0,
        regen_delay=0.0,
        gravity=0.0,
        jump_velocity=0.0,
        disable_collision=False,   # 玩家角色恒 false（S5 真实文件同值）
    )

    parts = [_render(_HEADER_TEMPLATE, header_params)]
    for section_name, body in (
        ("Aim Profile", _render(_AIM_PROFILE_TEMPLATE, {})),
        ("Bot Profile", _render(_BOT_PROFILE_TEMPLATE, {})),
        ("Character Profile", _render(_CHARACTER_PROFILE_TEMPLATE, player_char)),
        ("Character Profile", _render(_CHARACTER_PROFILE_TEMPLATE, target_char)),
        ("Dodge Profile", _render(_DODGE_PROFILE_TEMPLATE, dodge_params)),
        ("Weapon Profile", _render(_WEAPON_PROFILE_TEMPLATE, weapon_params)),
    ):
        parts.append(f"[{section_name}]\r\n{body}")
    if prescription.space_tier != "none":
        parts.append(_map_data_blob(prescription.space_tier))
    return ("\r\n\r\n".join(parts) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------------------
# dry-run 自检（铁律）：生成字节 → sce_reading 回读 → 逐键比对处方
# ---------------------------------------------------------------------------

def self_check(data: bytes, prescription: ForgePrescription, *, name: str) -> dict[str, Any]:
    """回读比对。全对 → 报告 dict（ok=True）；任何一键不符 → SelfCheckError 带明细。"""
    checks: list[dict[str, Any]] = []

    def check(key: str, expected: Any, actual: Any) -> None:
        ok = expected == actual
        checks.append({"key": key, "expected": expected, "actual": actual, "ok": ok})

    descriptor = sce_reading.build_scenario_reading_descriptor(data, display_name=name)
    if descriptor["availability"] != "available":
        raise SelfCheckError(
            f"[{name}] 回读失败（availability={descriptor['availability']}，"
            f"reason={descriptor.get('reason')}）；未写盘"
        )

    facts = descriptor["parameter_facts"]
    check("bot_count", prescription.bot_instances, facts["bot_count"])
    targets = facts["targets"]
    if not targets:
        raise SelfCheckError(f"[{name}] 回读无活动靶配置；未写盘")
    character = targets[0]["character"] or {}
    dodge = targets[0]["dodge"] or {}

    # —— 靶参数（Character Profile）——
    check("target.main_bb_radius", float(prescription.target_radius), character.get("main_bb_radius"))
    check(
        "target.main_bb_type",
        str(prescription.target_shape).casefold(),
        str(character.get("main_bb_type") or "").casefold(),
    )
    check("target.max_health", float(prescription.target_max_health), character.get("max_health"))
    check("target.max_speed", float(prescription.target_max_speed), character.get("max_speed"))
    check("target.movement_type", prescription.target_movement_type, character.get("movement_type"))
    check(
        "target.health_regen_per_sec",
        float(prescription.target_health_regen_per_sec),
        character.get("health_regen_per_sec") or 0.0,
    )
    check(
        "target.respawn_delay_s",
        [float(prescription.target_respawn[0]), float(prescription.target_respawn[1])],
        character.get("respawn_delay_s"),
    )
    # 注：sce_reading 事实层未暴露的键不入回读比对面（HealthRegenDelay 先例），
    # 由 tests/test_sce_forge.py 文本级断言覆盖：HealthRegenDelay、ScorePerHit、
    # AmmoPerShot、LockFOVRange 系（103–140 Clamped Horizontal）、
    # DisableCharacterCollision。

    # —— 变向（Dodge Profile）——
    check("dodge.toggle_left_right", prescription.toggle_left_right, dodge.get("toggle_left_right"))
    check(
        "dodge.lr_time_change",
        [float(prescription.lr_time_change[0]), float(prescription.lr_time_change[1])],
        dodge.get("lr_time_change_s"),
    )
    check("dodge.jump_frequency", float(prescription.jump_frequency), dodge.get("jump_frequency") or 0.0)
    check(
        "dodge.crouch_frequency",
        float(prescription.crouch_frequency),
        dodge.get("crouch_on_ground_frequency") or 0.0,
    )
    check(
        "dodge.target_distance",
        [float(prescription.target_distance[0]), float(prescription.target_distance[1])],
        dodge.get("target_distance"),
    )

    # —— 武器（玩家武器链）——
    weapon = facts["player_weapon"] or {}
    check("weapon.category", prescription.weapon_category, weapon.get("category"))
    check(
        "weapon.time_between_shots",
        float(prescription.time_between_shots),
        weapon.get("time_between_shots_s"),
    )
    check("weapon.damage_per_shot", float(prescription.damage_per_shot), weapon.get("damage_per_shot"))
    check("weapon.magazine_max", int(prescription.magazine_max), weapon.get("magazine_max"))
    check(
        "weapon.ammo_reloaded_on_kill",
        int(prescription.ammo_reloaded_on_kill),
        weapon.get("ammo_reloaded_on_kill"),
    )
    check(
        "weapon.headshot_multiplier",
        float(prescription.headshot_multiplier),
        weapon.get("headshot_multiplier"),
    )

    # —— 计分（header）——
    scoring = facts["scoring"]
    check("scoring.score_per_kill", float(prescription.score_per_kill), scoring["score_per_kill"])
    check("scoring.score_per_damage", float(prescription.score_per_damage), scoring["score_per_damage"])
    check("scoring.score_per_time", float(prescription.score_per_time), scoring["score_per_time"])
    check("scoring.score_to_win", float(prescription.score_to_win), scoring["score_to_win"])
    check("scoring.score_mult_accuracy", prescription.score_mult_accuracy, scoring["score_mult_accuracy"])
    check(
        "scoring.score_loss_per_miss",
        float(prescription.score_loss_per_miss),
        scoring["score_loss_per_miss"],
    )
    check(
        "scoring.time_refilled_by_kill",
        float(prescription.time_refilled_by_kill),
        scoring["time_refilled_by_kill_s"],
    )
    check("scoring.invincible_bots", prescription.invincible_bots, scoring["invincible_bots"])
    check("scoring.timelimit", float(prescription.timelimit), scoring["time_limit_s"])
    check("scoring.timescale", float(prescription.timescale), scoring["timescale"])

    # —— 空间分布档（D6）——
    space = descriptor["space"]
    if prescription.space_tier == "none":
        check("space.basis", "dodge_target_distance", space["basis"])
        check(
            "space.target_distance_band",
            [float(prescription.target_distance[0]), float(prescription.target_distance[1])],
            space.get("target_distance_band"),
        )
    else:
        expected_span = _GRID_EXPECTED_SPAN[prescription.space_tier]
        check("space.tier", prescription.space_tier, space["tier"])
        check("space.max_span_uu", expected_span, space["max_span_uu"])
        check("space.spawn_count", len(_SPAWN_GRIDS[prescription.space_tier]) + 1, space["spawn_count"])

    # —— 训练语义闭环（生成器×读回器互证）——
    check("training.semantics", prescription.expected_semantics, descriptor["training"]["semantics"])

    failed = [c for c in checks if not c["ok"]]
    report = {
        "schema_version": FORGE_SCHEMA_VERSION,
        "ok": not failed,
        "name": name,
        "rule_id": prescription.rule_id,
        "domain": prescription.domain,
        "difficulty_tier": prescription.tier_label,
        "space_tier": prescription.space_tier,
        "expected_semantics": prescription.expected_semantics,
        "reader": "kovaak_tracker.sce_reading.build_scenario_reading_descriptor",
        "passed": len(checks) - len(failed),
        "failed": [
            {"key": c["key"], "expected": c["expected"], "actual": c["actual"]} for c in failed
        ],
        "checks": checks,
    }
    if failed:
        detail = "\n".join(
            f"  {c['key']}: 预期 {c['expected']!r} ≠ 实际 {c['actual']!r}" for c in failed
        )
        raise SelfCheckError(
            f"[{name}] 自检 {len(failed)}/{len(checks)} 项不符，字节未放行：\n{detail}"
        )
    return report


def forge_sce_text_checked(
    prescription: ForgePrescription, *, name: str
) -> tuple[bytes, dict[str, Any]]:
    """生成 + 自检一体入口：自检不过直接 raise（供调用方在写盘前使用）。"""
    data = forge_sce_text(prescription, name=name)
    report = self_check(data, prescription, name=name)
    return data, report


# ---------------------------------------------------------------------------
# 命名规范（AC- 前缀；三候选样式）
# ---------------------------------------------------------------------------

#: rule_id → (ASCII 短码, 中文名)。中文基名即 ForgePrescription.name。
RULE_LABELS: dict[str, tuple[str, str]] = {
    "R1.1": ("VarResp", "变向响应"),
    "R2.1": ("UniStrafe", "单向平滑"),
    "R3.6": ("SpaceWide", "空间拉枪"),
    "R4.1": ("MicroDrift", "微调慢漂"),
    "R9.3": ("RegenPress", "回血压输出"),
    "R8.3": ("SurvTime", "生存计分"),
    # v0.3 新增：R9.5=gauntlet 计时器；R4.1-IR=R4.1 弹药经济的官方改良换弹变体
    # （-IR 后缀锚定 R4.1/纪律 10 三键辨析，非独立新规则编号）
    "R9.5": ("Gauntlet", "gauntlet 计时"),
    "R4.1-IR": ("ImprReload", "改良换弹"),
}

_NAMING_NOTES = {
    "code": "ASCII 短码（AC-VarResp-A1）：KovaaK 列表按字母序聚在 AC- 前缀下，"
            "与现有产品场景名正则（^[A-Za-z0-9][A-Za-z0-9 _.-]*$）兼容，检索/遥测最稳。",
    "cn_tier": "中文+档位（AC 变向响应 A1档）：KovaaK 列表里一眼可读规则与难度，"
               "同名重复生成会冲突（需先删旧档）。",
    "cn_dated": "中文+档位+日期（AC 变向响应 A1档·20261007）：同处方可重复生成不互撞，"
                "列表按日期区分训练批次。",
}


def naming_scheme(
    candidate: ForgePrescription, style: str = "code", *, date: date_cls | None = None
) -> str:
    """处方 → KovaaK 显示名（= .sce 文件名 stem）。默认 code 样式。"""
    code, cn = RULE_LABELS.get(candidate.rule_id, ("Custom", candidate.name or "处方"))
    tier = candidate.tier_label
    if style == "code":
        return f"{AC_PREFIX}-{code}-{tier}"
    if style == "cn_tier":
        return f"{AC_PREFIX} {cn} {tier}档"
    if style == "cn_dated":
        day = date or date_cls.today()
        return f"{AC_PREFIX} {cn} {tier}档·{day:%Y%m%d}"
    raise ForgeError(f"未知命名样式: {style!r}（可选 code / cn_tier / cn_dated）")


def naming_candidates(
    candidate: ForgePrescription, *, date: date_cls | None = None
) -> list[dict[str, str]]:
    """三个候选命名样例（code / cn_tier / cn_dated），各附 KovaaK 列表效果说明。"""
    return [
        {"style": style, "name": naming_scheme(candidate, style, date=date), "note": _NAMING_NOTES[style]}
        for style in ("code", "cn_tier", "cn_dated")
    ]


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _resolve_scenarios_dir(
    *, scenarios_dir: Path | str | None, install_dir: Path | str | None
) -> Path:
    """目录来源优先级：显式 scenarios_dir > 显式 install_dir > KOVAAK_INSTALL_DIR env。

    不做任何机器路径硬编码；安装根→场景目录的 join 与
    sce_reading.read_scenario_reading_from_dirs 的现有发现逻辑同规。Coach 接线层
    传 webapp.backend.config.resolve_kovaak_install_dir() 的结果即可。
    """
    if scenarios_dir:
        return Path(scenarios_dir)
    root = Path(install_dir) if install_dir else None
    if root is None:
        override = os.environ.get("KOVAAK_INSTALL_DIR", "").strip()
        if override:
            root = Path(override)
    if root is None:
        raise ForgeWriteError(
            "未提供 KovaaK 场景目录来源：请传 scenarios_dir 或 install_dir"
            "（Coach 接线层传 config.resolve_kovaak_install_dir() 的结果），"
            "或设置 KOVAAK_INSTALL_DIR 环境变量"
        )
    return root / _SCENARIOS_SUBPATH


def forge_and_write(
    prescription: ForgePrescription,
    *,
    scenarios_dir: Path | str | None = None,
    install_dir: Path | str | None = None,
    name_style: str = "code",
    date: date_cls | None = None,
) -> dict[str, Any]:
    """生成 → 回读自检 → 写入 KovaaK 场景目录（AC- 前缀命名，已存在拒绝覆盖）。

    返回 ``{"path", "name", "self_check_report"}``。任何一步失败都不产生文件。
    """
    name = naming_scheme(prescription, name_style, date=date)
    data, report = forge_sce_text_checked(prescription, name=name)

    target_dir = _resolve_scenarios_dir(scenarios_dir=scenarios_dir, install_dir=install_dir)
    path = target_dir / f"{name}.sce"
    if path.exists():
        raise ForgeWriteError(
            f"拒绝覆盖已存在的场景文件：{path}（如需更换请先手动删除旧档，"
            "生成器不做静默覆盖）"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "name": name, "self_check_report": report}


# ---------------------------------------------------------------------------
# 内置配方书规则（generation-rules.md v0.3 拷贝改写；出处见各函数注释）
# ---------------------------------------------------------------------------

def forge_R1_1(difficulty_tier: int = 1) -> ForgePrescription:
    """R1.1 变向密度降档（跟不上变向的第一处方）。

    出处：generation-rules v0.3 G1/R1.1【坐实链 C60】【档位统计 -0.1s/档 主导语法】。
    骨架典型值（Humanoid Strafe / Ground Plaza / rA STRAFETRACK）：MinLR 0.4/MaxLR 0.8
    起步、每档 -0.1s 递进；HP300+伤害计分让"在靶时间"成为唯一得分路径；
    受击变向 false 保节奏纯净（纪律 4：不用运行时自适应）。
    速度/加速骨架未指定 → 取 tracking 中速带 v1100（D2 p50）+ 常用 a9000。
    """
    return ForgePrescription(
        name=RULE_LABELS["R1.1"][1],
        rule_id="R1.1",
        domain="变向响应",
        difficulty_tier=difficulty_tier,
        target_radius=45.0,
        target_shape="Spheroid",
        target_height=90.0,
        target_max_health=300.0,
        target_max_speed=1100.0,
        target_acceleration=9000.0,
        lr_time_change=_lr_ladder((0.4, 0.8), difficulty_tier),
        target_distance=(750.0, 2500.0),
        weapon_category="FullyAuto",
        time_between_shots=0.01,
        damage_per_shot=1.0,
        score_per_damage=3.0,      # ScorePerDamage=3+Win1000 = tracking 打分标准型（纪律 7）
        score_to_win=1000.0,
        timelimit=60.0,            # 60s 基准（纪律 6）
        space_tier="none",         # tracking 半数无出生网格（D6）
        game_tag="Tracking",
        aim_type_tag="Tracking",
        aim_sub_type_tag="Reactivity",
        description=(
            f"Aiming Cookie R1.1 变向阶梯 A{difficulty_tier}档："
            f"LR {_lr_ladder((0.4, 0.8), difficulty_tier)} 每档-0.1s；"
            "伤害计分3.0/Win1000；hp300 r45 v1100。出处 generation-rules v0.3 R1.1"
        ),
        expected_semantics="continuous_tracking",
    )


def forge_R2_1(difficulty_tier: int = 1) -> ForgePrescription:
    """R2.1 单向化（删除变向变量）。

    出处：generation-rules v0.3 G2/R2.1【坐实链 C51 Smooth Thin LR1000-1000 / SYW】。
    长周期档 LR 1000-1000 把变向变量删掉，误差只剩速度匹配与张力。
    阶梯不适用（变向已删除，tier 只作标记）；血量取 SYW 变体 hp200（Thin 系 hp1
    一发杀会引入击杀重置干扰，取同规则 SYW 形态）。
    """
    return ForgePrescription(
        name=RULE_LABELS["R2.1"][1],
        rule_id="R2.1",
        domain="平滑跟枪",
        difficulty_tier=difficulty_tier,
        target_radius=20.0,
        target_shape="Spheroid",
        target_height=40.0,
        target_max_health=200.0,
        target_max_speed=850.0,
        target_acceleration=9000.0,
        lr_time_change=(1000.0, 1000.0),   # 长周期档写法；60/99 同义
        target_distance=(750.0, 2500.0),
        weapon_category="FullyAuto",
        time_between_shots=0.01,
        damage_per_shot=1.0,
        score_per_damage=1.0,
        timelimit=60.0,
        space_tier="none",
        game_tag="Tracking",
        aim_type_tag="Tracking",
        aim_sub_type_tag="Smoothness",
        description=(
            "Aiming Cookie R2.1 单向化：LR 1000-1000 删除变向变量，"
            "只考速度匹配与张力。出处 generation-rules v0.3 R2.1（C51/SYW）"
        ),
        expected_semantics="continuous_tracking",
    )


def forge_R3_6(difficulty_tier: int = 1) -> ForgePrescription:
    """R3.6 空间分布分岔——宽分布分支（拉枪链）。

    出处：generation-rules v0.3 G3/R3.6【档位统计 space_axis_stats；坐实链 C22】。
    宽分布（span>1500uu）小靶=长距离拉枪链考纲；载体形态取 1wall 6targets small
    （hp1 一发杀+kill10×准确率乘算+6 靶位+SemiAuto 0.1s 点击计价），
    出生网格 span 1840uu（1w6ts 系 1856–1920 同档）。LR 阶梯对静态靶惰性
    （MaxSpeed=0 变向不生效），保留 Mimic 形态基档。
    """
    return ForgePrescription(
        name=RULE_LABELS["R3.6"][1],
        rule_id="R3.6",
        domain="静态定位",
        difficulty_tier=difficulty_tier,
        target_radius=60.0,
        target_shape="Spheroid",
        target_height=120.0,
        target_max_health=1.0,     # 一发杀档（纪律 8：hp1 配 dmg1）
        target_max_speed=0.0,
        target_acceleration=0.0,
        lr_time_change=_lr_ladder((0.2, 0.5), difficulty_tier),  # 惰性（静态靶）
        target_distance=(750.0, 2500.0),   # 1w6ts Mimic 带（与网格距离一致）
        weapon_category="SemiAuto",
        time_between_shots=0.1,
        damage_per_shot=1.0,
        score_per_kill=10.0,
        score_to_win=1.0,          # 无终点计时赛写法（纪律 11）
        score_mult_accuracy=True,  # kill10×准确率：落点质量计价
        timelimit=60.0,
        space_tier="wide",         # 宽分布=拉枪链考纲（span 1840 > 1500）
        bot_instances=6,           # 1w6ts AddedBots 6 靶形态
        game_tag="Mouse Control",
        aim_type_tag="Clicking",
        aim_sub_type_tag="Precision",
        description=(
            "Aiming Cookie R3.6 空间分岔·宽分布：span1840 拉枪链+kill10×准确率+一发杀。"
            "出处 generation-rules v0.3 R3.6（C22/1w6ts 形态）"
        ),
        expected_semantics="static_clicking",
    )


def forge_R4_1(difficulty_tier: int = 1) -> ForgePrescription:
    """R4.1 纯指尖场（慢漂+多发杀+限弹杀后回弹）。

    出处：generation-rules v0.3 G4/R4.1【坐实链 C123/XN42 Floating Heads Timing
    400%】。LR 10-10 单向慢漂 + v300/a100000 即停即走 + r20/hp36 多发杀 +
    mag3+AmmoReloadedOnKill=3（弹药击杀回弹真键）——微调窗口被计分结构强制存在。
    空间分布按 R4.5 纪律取窄网格（微调场默认窄分布形态，Reactive Flick
    dist 0/0 形态）；击杀计分取最小 kill1。
    """
    return ForgePrescription(
        name=RULE_LABELS["R4.1"][1],
        rule_id="R4.1",
        domain="微调",
        difficulty_tier=difficulty_tier,
        target_radius=20.0,
        target_shape="Spheroid",
        target_height=40.0,
        target_max_health=36.0,
        target_max_speed=300.0,
        target_acceleration=100000.0,
        lr_time_change=_lr_ladder((10.0, 10.0), difficulty_tier),
        target_distance=(0.0, 0.0),   # 窄网格形态：靶位全由出生网格承担（Reactive Flick 同款）
        weapon_category="SemiAuto",
        time_between_shots=0.1,
        damage_per_shot=1.0,
        magazine_max=3,
        ammo_reloaded_on_kill=3,
        score_per_kill=1.0,
        timelimit=60.0,
        space_tier="narrow",          # 窄簇 span 240 < 500（R4.5 纪律）
        game_tag="Mouse Control",
        aim_type_tag="Clicking",
        aim_sub_type_tag="Micro Adjust",
        description=(
            "Aiming Cookie R4.1 纯指尖场：LR10-10 慢漂+r20/hp36 多发杀+mag3 杀后回弹3。"
            "出处 generation-rules v0.3 R4.1（C123 FHT 形态）"
        ),
        expected_semantics="dynamic_clicking",
    )


def forge_R9_3(difficulty_tier: int = 1) -> ForgePrescription:
    """R9.3 回血压输出密度（持续性计价）。

    出处：generation-rules v0.3 G9/R9.3【坐实键值 C81/KL20/KL46，原册 0.3s 笔误
    已修正】。真实键值：HealthRegenDelay=0.03s + HealthRegenPerSec=62.5/s +
    hp55/r27.5（domiSwitch Easy）——回血快于单发伤害就必须连续命中。
    运动/计分骨架未指定 → tracking 标准型（v700 + ScorePerDamage3+Win1000 +
    全自动激光），LR 阶梯同 R1.1 语法。
    """
    return ForgePrescription(
        name=RULE_LABELS["R9.3"][1],
        rule_id="R9.3",
        domain="压力与节奏",
        difficulty_tier=difficulty_tier,
        target_radius=27.5,
        target_shape="Spheroid",
        target_height=55.0,
        target_max_health=55.0,
        target_max_speed=700.0,
        target_acceleration=9000.0,
        target_health_regen_per_sec=62.5,
        target_health_regen_delay=0.03,
        lr_time_change=_lr_ladder((0.4, 0.8), difficulty_tier),
        target_distance=(750.0, 2500.0),
        weapon_category="FullyAuto",
        time_between_shots=0.01,
        damage_per_shot=1.0,
        score_per_damage=3.0,
        score_to_win=1000.0,
        timelimit=60.0,
        space_tier="none",
        game_tag="Tracking",
        aim_type_tag="Tracking",
        aim_sub_type_tag="Reactivity",
        description=(
            "Aiming Cookie R9.3 回血压输出：hp55+回血0.03s/62.5每秒（真实键值，"
            "原册0.3s系笔误），停顿即见回血缺口。出处 generation-rules v0.3 R9.3"
        ),
        expected_semantics="continuous_tracking",
    )


def forge_R8_3(difficulty_tier: int = 1) -> ForgePrescription:
    """R8.3 生存型计分（反应跟枪基础）。

    出处：generation-rules v0.3 G8/R8.3【坐实链 KL12/C60 Ground Plaza
    ScorePerTime；档位统计 时间×1+Win1000】。ScorePerTime=1.0+ScoreToWin=1000+
    无敌靶多 bot——离开=立即停分，考"持续接触"。
    运动/靶参数取 Ground Plaza 形态的 tracking 中速带基档；3 bot 同屏
    （多 bot=视线分配变异位，D6 补遗）；空间取中等网格。
    """
    return ForgePrescription(
        name=RULE_LABELS["R8.3"][1],
        rule_id="R8.3",
        domain="读靶",
        difficulty_tier=difficulty_tier,
        target_radius=45.0,
        target_shape="Spheroid",
        target_height=90.0,
        target_max_health=300.0,   # 无敌靶，血量不参与结算
        target_max_speed=1100.0,
        target_acceleration=9000.0,
        lr_time_change=_lr_ladder((0.4, 0.8), difficulty_tier),
        target_distance=(750.0, 2500.0),
        weapon_category="FullyAuto",
        time_between_shots=0.01,
        damage_per_shot=1.0,
        score_per_time=1.0,
        score_to_win=1000.0,
        invincible_bots=True,
        timelimit=60.0,
        space_tier="medium",
        bot_instances=3,
        game_tag="Tracking",
        aim_type_tag="Tracking",
        aim_sub_type_tag="Reactivity",
        description=(
            "Aiming Cookie R8.3 生存型计分：ScorePerTime1.0+Win1000+无敌靶，"
            "离开=停分。出处 generation-rules v0.3 R8.3（Ground Plaza 形态）"
        ),
        expected_semantics="continuous_tracking",
    )


def forge_R9_5(difficulty_tier: int = 1, target_duration_s: float = 19.0) -> ForgePrescription:
    """R9.5 gauntlet 计时器（负回血=固定时长靶，v0.3 新增）。

    出处：generation-rules v0.3 G9/R9.5【官方原文：Voltaic S5 blog §1.8——
    "the duration of each target is fixed, with the target's health decaying
    automatically and the player being scored on the number of hits … not have
    any filler targets"】＋【对表：VT PGT Novice S5 .sce 原文逐键核对
    （intent-crosscheck §1.1 #8）】。
    机关：HealthRegenPerSec=-100（负回血=计时器）× MaxHealth=100×target_duration_s
    → 每靶存活时长=可指定的 target_duration_s（官方 Novice 档 1900÷100=恰 19s）；
    ScorePerHit=1.0 按命中计分；respawn 1.48s 序列接续。
    靶行为取 PGT Bounce 1：r32/v800/a900/固定JV1750/grav1.0/LR3–4/jumpF1.0/
    dist1300–2000；LR 阶梯按 -0.1s/档从该基档递进。
    S5 基准型 FOV 头（103–140 Clamped Horizontal）默认开启（纪律 19）；
    序列制单活靶 → 碰撞保持 false（纪律 22 同屏/序列分工）。
    """
    if target_duration_s <= 0:
        raise ForgeError(f"每靶存活时长必须 >0，收到 {target_duration_s}")
    regen = -100.0
    return ForgePrescription(
        name=RULE_LABELS["R9.5"][1],
        rule_id="R9.5",
        domain="压力与节奏",
        difficulty_tier=difficulty_tier,
        target_radius=32.0,        # PGT Bounce 1（小于同层 Aether/Ground，佐证"small goats"）
        target_shape="Spheroid",
        target_height=64.0,
        target_max_health=round(abs(regen) * target_duration_s, 4),   # 时长=血量÷|regen|
        target_max_speed=800.0,
        target_acceleration=900.0,
        target_gravity=1.0,
        target_jump_velocity=1750.0,                                   # 固定 JV 档（min=max）
        target_health_regen_per_sec=regen,
        target_health_regen_delay=0.0,
        target_respawn=(1.48, 1.48),   # PGT 原文键：序列接续节奏
        lr_time_change=_lr_ladder((3.0, 4.0), difficulty_tier),        # PGT Long Strafes Jumping
        jump_frequency=1.0,
        target_distance=(1300.0, 2000.0),
        weapon_category="FullyAuto",
        time_between_shots=0.01,
        damage_per_shot=1.0,
        score_per_hit=1.0,         # 按命中计分（命中数=唯一得分路径）
        score_to_win=1.0,          # 无终点计时赛写法（纪律 11）
        timelimit=60.0,
        lock_fov_s5=True,          # S5 基准型 FOV 头（纪律 19 官方原文 §1.2）
        space_tier="none",         # PGT 靶位由地图 json 提供；forge 侧分布轴由 dist 承担（D6）
        game_tag="Tracking",
        aim_type_tag="Tracking",
        aim_sub_type_tag="Precision",
        description=(
            f"Aiming Cookie R9.5 gauntlet 计时器 A{difficulty_tier}档：负回血-100/s×"
            f"血量{round(abs(regen) * target_duration_s, 4)}=每靶{target_duration_s}s，"
            "ScorePerHit 按命中计分。出处 generation-rules v0.3 R9.5"
            "（官方原文 S5 blog §1.8 + PGT S5 指纹逐值）"
        ),
        expected_semantics="continuous_tracking",
    )


def forge_R4_1_IR(difficulty_tier: int = 1) -> ForgePrescription:
    """R4.1-IR 改良换弹（R4.1 弹药经济的官方高阶形态，三键组占位）。

    出处：generation-rules v0.3 纪律 10（弹药经济三键辨析）＋【官方原文：
    Voltaic S5 blog §1.1——"you start with 100 ammo in a clip … it costs 30
    ammo to shoot, and a hit refunds 37 ammo … at least two consecutive misses
    is always required to trigger a reload"（VT ww5t Intermediate S5）】。
    弹药三键=官方值：MagazineMax=100 / AmmoPerShot=30 / AmmoReloadedOnKill=37
    （回补>成本）。**改良换弹要求 MagazineMax>0 才生效**——mag=0 时 AmmoPerShot/
    AmmoReloadedOnKill 是躺尸对（S5 Pasu/Pentashot 陷阱，纪律 10 方法论警示）。
    载体=ww5t 型静态点击（宽墙 5 靶同屏，§1.7 多靶关碰撞）；ScoreMultAccuracy
    保持 false——官方声明改良换弹与平方根精度计分二选一（§1.1）。
    非弹药参数（r50/clicking p50、hp1 一发杀、kill10）为 1w6ts 系骨架档，
    待 S5 ww5t 文件到手回填（intent-crosscheck 裁决案 #5）。
    """
    return ForgePrescription(
        name=RULE_LABELS["R4.1-IR"][1],
        rule_id="R4.1-IR",
        domain="静态定位",
        difficulty_tier=difficulty_tier,
        target_radius=50.0,        # clicking 靶 p50（D3）；ww5t "small to medium" 待实值回填
        target_shape="Spheroid",
        target_height=100.0,
        target_max_health=1.0,     # 一发杀档（纪律 8：hp1 配 dmg1）
        target_max_speed=0.0,
        target_acceleration=0.0,
        lr_time_change=_lr_ladder((0.2, 0.5), difficulty_tier),   # 惰性（静态靶）
        target_distance=(750.0, 2500.0),
        weapon_category="SemiAuto",
        time_between_shots=0.1,
        damage_per_shot=1.0,
        magazine_max=100,          # 官方 clip 值（blog §1.1）
        ammo_per_shot=30,          # miss 净扣 30%
        ammo_reloaded_on_kill=37,  # hit 净返 7%（回补>成本）
        score_per_kill=10.0,
        score_to_win=1.0,
        score_mult_accuracy=False, # 改良换弹与平方根计分二选一（blog §1.1）
        timelimit=60.0,
        lock_fov_s5=True,          # S5 基准家族 6/6 锁 103–140（纪律 19 官方源头）
        disable_character_collision=True,   # 同屏 5 靶 → 关碰撞（纪律 22，blog §1.7）
        space_tier="wide",         # ww5t 宽墙（span>1500 拉枪链档）
        bot_instances=5,
        game_tag="Mouse Control",
        aim_type_tag="Clicking",
        aim_sub_type_tag="Speed",
        description=(
            "Aiming Cookie R4.1-IR 改良换弹：mag100/耗30/返37（至少两 miss 才换弹），"
            "宽墙 5 靶关碰撞。出处 generation-rules v0.3 纪律 10"
            "（官方原文 S5 blog §1.1 + §1.7）"
        ),
        expected_semantics="static_clicking",
    )


#: 规则 ID → 预设工厂
FORGE_PRESETS: dict[str, Callable[..., ForgePrescription]] = {
    "R1.1": forge_R1_1,
    "R2.1": forge_R2_1,
    "R3.6": forge_R3_6,
    "R4.1": forge_R4_1,
    "R9.3": forge_R9_3,
    "R8.3": forge_R8_3,
    "R9.5": forge_R9_5,
    "R4.1-IR": forge_R4_1_IR,
}

__all__ = [
    "FORGE_SCHEMA_VERSION",
    "AC_PREFIX",
    "DEFAULT_LR_STEP_PER_TIER",
    "LR_FLOOR",
    "ForgeError",
    "ForgePrescription",
    "ForgeWriteError",
    "SelfCheckError",
    "FORGE_PRESETS",
    "RULE_LABELS",
    "forge_sce_text",
    "forge_sce_text_checked",
    "self_check",
    "naming_scheme",
    "naming_candidates",
    "forge_and_write",
    "forge_R1_1",
    "forge_R2_1",
    "forge_R3_6",
    "forge_R4_1",
    "forge_R9_3",
    "forge_R8_3",
    "forge_R9_5",
    "forge_R4_1_IR",
]
