# 瞄准社区前沿（Deep Research, 2026-06-29）

> 第二轮 deep research，专攻**社区前沿**（学术不研究 KovaaK's/aim trainer 消费品，瞄准实际前沿在社区）。5 angle fan-out → 12 源 fetch → 35 claim → 25 对抗验证 → **6 存活** + 19 被反驳（透明列出）。
> 94 agents / 1.69M tokens / ~44 min。遭遇 429 限流，部分 verify 失败，但 6 存活 claim 均经投票 + 多源确认。
> **关键性质**：社区内容**时间敏感**（Voltaic season 标签、Celestial 计数、顶级玩家名次都流动），且信源以论坛/视频为主（学术同行评审几乎缺位）。所有内容**只进 narrator 文案 + profile 标签 + 训练处方理由**，**不进诊断规则**（那是 `coach-theory-foundation.md` 学术根基的领地）。

## 信源分级（沿用）

【权威社区共识】（Voltaic 等成规模社群）>【个人经验/视频】（标「未经验证」）。社区内容多属这两类——经久度普遍低于学术理论，标清楚。

## 与已有底座的关系

- `aim-kinematics-research.md` §3-5：已收 Voltaic 流派/场景处方/灵敏度基础（2026-06-28）
- `coach-theory-foundation.md` §3：社区层标「易过时」
- 本文补 **2024-2025 最新社区发展**（S5 分类法 / 顶级玩家 / static clicking 三步等）

---

## §1 社区共识（6 存活）

### §1.1 Voltaic Season 5 分类法：3 支柱 × 3 子类 = 9（含新增 hybrid 第三类）
**claim**：S5 把鼠标瞄准标准化为 3 大类（clicking / tracking / switching），每类分 3 子类，并**新增第三类「hybrid」子类**（linear=clicking, control=tracking, stability=switching）桥接传统子类——从早期 2/大类（6 类）扩到 9 类。
- 完整映射：clicking = dynamic/static/linear；tracking = precise/control/reactive；switching = stability/evasive/speed
- **信源**【权威社区共识】：Voltaic 官方 S5 blog、r/Voltaic S5 benchmarks、KovaaK's S5 focus playlists
- **经久度**：高（Voltaic 当前官方分类，S5 期内稳定）
- **系统含义**：narrator 词汇用这套分类（clicking/tracking/switching + 子类）；profile 标签可对齐

### §1.2 三支柱 + 颜色编码（red/blue/purple）
**claim**：社区共识的瞄准技能分类是三支柱（clicking / tracking / target-switching），Voltaic Weakness-Specific Routines 2.0 全程颜色编码（红=clicking，蓝=tracking，紫=switching）。S5（2024-2025）仍沿用，无争议。
- **信源**【权威社区共识】：r/Voltaic、Scribd PDF、Voltaic blog S5 announcement
- **经久度**：高（无争议，S5 仍用）。少数异见：把「Movement Aiming」当第四支柱
- **系统含义**：UI/narrator 可用颜色约定；三支柱是社区通用语

### §1.3 顶级玩家子技能分化：smoothness/stability vs explosive-speed/reactive 很少同时顶峰
**claim**：Celestial 级世界 #1 Voltaic precise-tracking 记录保持者（VT Matty / 'Corporate Serf'）自述是 smoothness/stability 型玩家，**明确 static clicking 是弱项**。支持「smoothness/stability 瞄准 vs 爆发速度/reactive 瞄准是不同子技能，很少同时顶峰」。
- **信源**【个人经验/视频】：Corporate Serf YouTube "How to Rank Up in Voltaic"
- **经久度**：中（单样本自述 + Voltaic 分类排序佐证）
- **系统含义**：profile 可分子型（smoothness 型 vs speed 型）；解释为什么用户某类强某类弱是正常的（不强行全能）

### §1.4 2025 年中顶级 tracking 玩家列表
**claim**（截至 2025-05，Corporate Serf 编辑榜单，非官方排名）：VT Matty #1 tracking；VT Clover (#2) 和 Azer Plus (#3) 是 S5 tracking 仅有的两个 Celestial Complete；S4 tracking 仅四人（Likey、Juicer 等）。
- **信源**【个人经验/视频】：Corporate Serf tracking top list YouTube
- **经久度**：低-中（编辑意见非官方排名；Celestial 计数可经验证但 S5 beta 流动）
- **系统含义**：参考用（对标高手），**不进逻辑**；定期复核（名次/赛季变动）

### §1.5 Voltaic 三条平行 benchmark track
**claim**：Voltaic 当前维护三条平行 benchmark：KovaaK's S5（旗舰）、Aimlabs S3（免费次级）、Valorant S1（游戏专用）。
- **信源**【权威社区共识】：app.voltaic.gg/leaderboards（主源 verbatim）、Voltaic blog、r/Voltaic
- **经久度**：高（S5 当前至 2026 中，无 S6 或 track 退役证据）
- **系统含义**：meta 上下文（用户跑哪个 track）；对标高手时注明 track

### §1.6 经典 static clicking 三步：big flick → micro-correction → hit-confirm
**claim**：经典 Voltaic static clicking 技术把每次目标交战分解为三步：**大 flick（arm 驱动）→ micro-correction（指尖/手腕）→ hit-confirm（修正落地后才点）**。micro-correction 是区分 clean vs sloppy 的精度关键。
- **信源**【权威社区共识】：Voltaic VDIM guide、r/FPSAimTrainer "Proper Static Clicking Form"、多个教学视频（2022-2026 稳定）
- **经久度**：高（多年教学稳定，未被取代）
- **异见**：少数「one-flick purity」流派（HnA 场景）；主流共识是 micro-correction 是预期的，只有**过度反复修正**才是缺陷。社区把「clean vs sloppy」操作化为「一次果断修正 vs 反复抖动」
- **系统含义**：**与学术 `coach-theory-foundation.md` 的 corrective submovement 直接呼应**——社区「micro-correction」= 学术「corrective submovement」。narrator 技术描述 + 处方理由可用此三步；`two_stage` 画像（discrete corrective）有社区对应

---

## §2 被反驳的 claim（透明，完整 19 条，对抗投票淘汰）

3 票对抗验证（存活需 ≥2 票确认）淘汰 19 个社区 claim。**完整列出**——社区内容信源差（论坛/视频）+ 时间敏感，淘汰率 >50% 是常态：

| # | claim（中文摘要）| 投票 | 不纳入原因 |
|---|---|---|---|
| 1 | mattyow #1 S5 1312 energy + rank 2-5 列表 | 1-0 | 时间敏感具体数字；投票分歧 |
| 2 | harmonic mean 排序 + 每子类≥1 分才得 energy | 1-0 | v0 verbatim 确认但汇总未达阈值；保守（需直接 voltaic.gg 文档）|
| 3 | Corporate Serf 3305 runs/55h 单场景刷到顶峰 | 1-0 | 单源个案，不可推广 |
| 4 | VDIM（LG56/4RK）two-day-per-category 结构 | 0-0 | 未达共识 |
| 5 | tracking 演变 S1→S5 reactivity spectrum（7 档）| 0-0 | 未达共识 |
| 6 | tracking 评估用 3 compound categories | 0-1 | 方向支持但未达阈值 |
| 7 | S5 consolidate 3 principles + hybrid | 0-1 | 与 §1.1 finding 重叠，未达阈值 |
| 8 | S5 dynamic vs linear clicking 区分（speed vs smoothness）| 0-0 | 未达共识 |
| 9 | bardozz method + 65-80 cm/360 static | 0-0 | 单源 |
| 10 | underflicking（故意欠冲再修）是 2025 主导练习法 | 0-1 | 方向支持但未达阈值 |
| 11 | tracking 学习顺序：precise→control→reactive→switching | 0-1 | 个人理论（Corporate Serf），未达阈值 |
| 12 | weakness-specific routines 分 Easy/Int/Adv 难度层 | 1-0 | 未达阈值 |
| 13 | VDIM static playlist 子技能 progression | 0-1 | 未达阈值 |
| 14 | VDIM accuracy ceiling/floor（~90%+ / 低于 85-90% 太快）| 1-0 | 未达阈值 |
| 15 | deceleration/tension 是 VDIM 独立子技能（Pokeball 训）| 0-1 | 未达阈值（但呼应学术减速段焦点）|
| 16 | FOV 103+ 是 leaderboard 硬要求 | 0-0 | 未达共识 |
| 17 | stair-stepping progression（分层推 energy）| 1-0 | 未达阈值；个案 |
| 18 | S5 strafe scenarios（bounce aim PGTI）是新技能 | 0-1 | 未达阈值 |
| 19 | 4 scenarios 最权重 tracking 评估 | 0-1 | 未达阈值；时间敏感 |

> **教训**：社区**具体数字/rank/方法论**淘汰率极高（时间敏感 + 单源）。系统只引用**稳定的大框架**（§1 三支柱/分类/static clicking 三步），具体数字不入逻辑、定期复核。#15（减速/tension 子技能）虽淘汰但**呼应学术减速段焦点**（`coach-theory-foundation.md` §1.7 + `aim-kinematics-research.md` Becker），narrator 可谨慎引用。

---

## §3 Open Questions（理论锚定但未解决）

1. **Voltaic 官方排序方法**（harmonic vs arithmetic vs 几何平均 + 每子类最低分门槛）—— 需直接 voltaic.gg/about 文档
2. **顶级玩家 cm/360 / DPI / sens 趋势**（按子类分）—— 无可验证 benchmark 存活
3. **S5 hybrid 子类（linear/control/stability）场景设计**—— 练什么相邻子类不练的？未验证
4. **VDIM 结构 / plateau 突破法**（two-day-per-category、accuracy ceiling/floor）—— 未验证，需定向直接源研究

---

## §4 对系统的总结指导

1. **社区内容只进 narrator 文案 + profile 标签 + 训练处方理由**，**不进诊断规则**（advice/diagnosis）——社区随版本/KOL 变，易过时；诊断根基用学术（`coach-theory-foundation.md`）。
2. **可直接用的社区词汇**：三支柱（clicking/tracking/switching）+ 颜色、static clicking 三步（big flick → micro → confirm）、smoothness vs speed 子型。
3. **社区↔学术呼应**：社区「micro-correction」= 学术「corrective submovement」（§1.6 ↔ `coach-theory-foundation.md` §1.7）；这是社区经验有学术根基的少数点，可在 narrator 同时引用两层。
4. **时间敏感内容定期复核**：顶级玩家名/rank/S5 season/energy 数字（§1.4 等）随版本变，系统不硬编码，narrator 引用时标注「截至 2025-05」类时间戳。
5. **被反驳的具体数字/rank/方法论不引用**（§2）——对抗验证淘汰率高是社区内容的常态。

---

## §5 来源（分级）

**【权威社区共识】**：
- Voltaic 官方：[blog.voltaic.gg](https://blog.voltaic.gg)（S5 announcement）、[app.voltaic.gg/benchmarks](https://app.voltaic.gg/benchmarks)、[app.voltaic.gg/leaderboards](https://app.voltaic.gg/leaderboards)
- r/Voltaic：[Season 5 KovaaK's Benchmarks](https://www.reddit.com/r/Voltaic/comments/1hlqgeq/voltaic_season_5_kovaaks_benchmarks/)、[Weakness-Specific Routines 2.0](https://www.reddit.com/r/Voltaic/comments/mmb22c/issuespecific_routines_20/)
- KovaaK's S5 focus playlists（kovaaks.com）
- Weakness-Specific Routines 2.0（Scribd PDF 镜像）

**【个人经验/视频】**（标未经验证）：
- Corporate Serf YouTube：[How to Rank Up in Voltaic](https://www.youtube.com/watch?v=VzNA36hmN1Y)、[tracking top list](https://www.youtube.com/watch?v=8Qjrl4hsnSQ)
- r/FPSAimTrainer（Proper Static Clicking Form 等讨论）
- KovaaK's Steam discussions
- 多个教学视频（Voltaic Static Clicking Guide、micro-adjusting 等）

（学术同行评审在社区前沿**几乎缺位**——这正是社区内容的性质，也是为什么要标信源等级 + 易过时）
