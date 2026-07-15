# Flicking 处方手册（诊断 → 训练处方映射）

> **状态与边界**：处方证据与内容参考，不是产品范围、架构合同、当前实现状态或 active implementation plan。实际诊断、阈值和处方输出以当前代码与测试为准；社区和时间敏感内容在启用前必须重新核实。

> 日期 2026-06-29 · 处方层 deep research 产出。把"诊断信号 → 具体训练场景 + 编排 + 执行要点"落到可执行处方，并标注每条信源级别。
> 上游：`docs/coach-theory-foundation.md`（机制层：CI/Ericsson/guidance/KR-KP/Socratic）、`docs/coach-community-frontier.md`（地图层：Voltaic S5/顶级玩家/static clicking 三步）。本文是**处方层**，引用前两者、不重盖。
> research workflow：`wf_7793cb5f-265`（105 agents，97 claims → 25 验证 → 22 确认 / 3 否决 → 14 综合）。

## 0. 信源分级与判定规则

每条处方带 `source_level`：

| 级别 | 含义 | 可进诊断逻辑？ |
|---|---|---|
| `academic_peer_reviewed` | 同行评审主源 + 多源复现 | ✅ 可进 advice/diagnosis 规则 |
| `community_consensus` | Voltaic/r/FPSAimTrainer/KovaaK 等有规模社群共识 | ❌ 只进 narrator 文案 + profile 标签 + 处方理由（**不进诊断规则**，防随版本过时）|
| `personal_experience_unverified` | 个人经验/视频，未经验证 | ❌ 同上，且措辞要弱（"可尝试"）|

**社区判定 = 理论一致性**（非信源形式）：社区 claim 只要不与学术根基冲突就保留，只有"直接冲突"才排除。本轮 3 条被否决全是**学术过强 claim**（见 §6），社区经验层零排除。

## 1. 核心映射：诊断信号 → 处方

### 1.1 decel_frac 过高 / 减速段不规则 = 「制动代价」而非纯过冲修正

`academic_peer_reviewed` · 3-0 · Fradet, Lee & Dounskaia 2008（*Acta Psychologica*，[PMC2600723](https://pmc.ncbi.nlm.nih.gov/articles/PMC2600723/)）

**机制**：运动终止（motion termination）是与减速本身分离的控制成分——急速接近目标后需迅速将负加速度归零并稳定肢体，这一稳定过程在高速高负加速度动作中产生毛刺状速度波动（Type 1 子动作，速度过零）。**长而不规则的减速段部分是高速接近的"制动代价"。**

> 原文："the limb stabilization may be accompanied with small fluctuations, specifically during fast movements that require high negative acceleration while approaching the target and quick reduction of this acceleration to zero when the target has been achieved."

**处方**：
- 场景：1wall 6targets small（小目标训练精细制动）、Multiclick（连续制动-再启动）、Tile Frenzy（短距离快速制动）
- 执行要点：训练**有控制的制动**而非更大力气；制动是一次性归零，不是"蹭着减速"
- 编排：交错（见 §4.1）

### 1.2 submovement two-stage 必须先分类（Type 1 vs Type 2/3）

`academic_peer_reviewed` · 2-1 · Fradet et al. 2008 + Exp Brain Res 2024 "Type 1 Submovement Conundrum" + 老年研究 PMC2628348

**机制**：子动作至少三类、病因不同：
- **Type 1**（速度过零，gross）= 运动终止/稳定，**非精度修正**；离散动作中富集、往复动作中稀少
- **Type 2**（加速度过零）+ **Type 3**（jerk 过零，fine）= 与精度需求及低速相关

**诊断含义**：不能笼统读作"修正过多"。必须先分类不规则性类型——是制动控制差（终止问题，Type 1）还是速度-精度权衡波动（修正问题，Type 2/3），否则会把慢速 flick 误诊为共济失调性。

**处方（分支化）**：
- 终止型（Type 1 主导）→ Multiclick / Tile Frenzy 制动控制
- 修正型（Type 2/3 主导）→ 1w6ts 小目标精度子动作

> ⚠️ **落地前提**：Type 1/2/3 分类在 <300ms flick 信号上的可靠实现未解决（见 §9 开放问题 Q1）。当前 advice.py 的 `submovement_overlap` 是 trough-depth ratio proxy，不是 literal temporal overlap，只能给"两段式 vs 流体"的 experimental/info 粗分类；分支化处方需等分类算法落地。

### 1.3 SPARC 低 / 减速抖动 = 低速下的发力控制问题

`academic_peer_reviewed` · 3-0 · Fradet et al. 2008

**机制**：Fine 子动作（Type 2/3）发生率与峰值速度**强负相关**（Type 2: R²=0.71；Type 3: R²=0.75，p<0.05）——速度越低、不规则越多；Type 1 与速度无相关（R²=0.10）。机制为低速度下的运动单位放电变异（非必然是修正尝试）。

**处方**：低速场景（1w6ts、Tile Frenzy 短距）的减速抖动可能是**发力控制**问题，处方应转向"发力控制 + 外部焦点"，而非"更多修正训练"。

### 1.4 reverse_ratio 高 / 子动作计数高 —— ⚠️ 时长偏倚陷阱

`academic_peer_reviewed` · 2-0 · Cornec et al. 2024（*J NeuroEng Rehabil*，[PMC11134951](https://pmc.ncbi.nlm.nih.gov/articles/PMC11134951/)）

**陷阱**：时域子动作指标（nSUB、NARJ）与运动时长**强相关**（r_Spearman>0.8）但与运动直线度**无显著相关**——高子动作计数/高反向计数很大程度上是**更慢/更长动作的假象**而非纯修正信号。

**处方含义**：诊断 reverse_ratio 前**必须归一化运动时长**，否则会把慢 flick 误诊为共济失调性。

> ⚠️ **advice.py 改进点**：当前 `reverse_ratio` 阈值（0.20）未做时长归一化。理想是 `reverse_ratio / duration` 或限定同速度段比较。但 flicking.py 的切段已按 valley，时长差异有限——标为后续校准项，不阻塞 ④。

### 1.5 peak_speed / throughput 低于参考 = 发力不足（Fitts 律源于运动学结构）

`academic_peer_reviewed` · 2-0/2-0 · Hoffmann 2016（*J Motor Behavior*，DOI:10.1080/00222895.2015.1092939）

**机制**：Fitts 律可由少至 2 个子动作（初级弹道 + 一个修正）产生线性 MT-ID 关系。弹道段决定运动时间，修正子动作产生观察到的速度-精度权衡（ID 依赖）。

**处方**：提升吞吐量既可从**弹道距离覆盖段**（发力/峰值速度 → pasu、speed 类）也可从**修正段**（精度 → 1w6ts）入手；混合场景 Multiclick 兼顾。

### 1.6 SPARC 作为减速段质量主指标（诊断信号选择依据）

`academic_peer_reviewed` · 2-1 · Cornec et al. 2024 + Balasubramanian 2012/2015 + Bayle 2023

**结论**：对不受控时长的 reaching，SPARC 优于时域指标（LDLJ、nSUB、NARJ）——ICC>0.9 可靠性最好、CoV<10% 测量误差最小、受运动时长污染远小于 TDSM。**advice.py 已首选 SPARC，正确。**

> ⚠️ **范围警告**：原始研究为卒中康复人群（~2-2.6s 自定速 reach），KovaaK flick 是 <300ms 健康人弹道动作，频域估计在极短信号上可能退化——**可靠性数值不能直接迁移；旧 unversioned `-5.0` 只保留 legacy experimental/info 兼容，`native_flicking.sparc.v2` 与 `flicking_fair_summary.sparc.v2` 在真实产品数据校准前不触发绝对阈值 issue，旧版与 v2 数值不可直接比较**。

### 1.7 linearity 高 / 路径低效 —— 独立构造

`academic_peer_reviewed` · 被否决的 SPARC↔IoC 强相关版（0-3，见 §6）

**结论**：减速段平滑度（SPARC）与路径效率（path_efficiency）是**两个独立构造**。曾被试图用"SPARC↔IoC 强相关（r=0.64）"把 SPARC 当路径效率指标——**0-3 否决**，path_efficiency 必须独立测量。advice.py 当前分开处理 SPARC 与 path_efficiency，正确。

## 2. 执行要点层

### 2.1 外部注意焦点（external focus）

`academic_peer_reviewed` · 2-1/3-0 · Wulf 2013 综述 + Wulf et al. 2010（[PMC3153799](https://pmc.ncbi.nlm.nih.gov/articles/PMC3153799/)）+ Zachry 2005 + Vance 2004

**机制**：效果焦点（在运动效果上：准星/目标/命中点）相比内部焦点（在手/腕/前臂动作上）降低拮抗肌共收缩与 EMG、同时提升精度与峰值力/速度——**更省力的运动，张力浪费更少**。约束动作假说：外部焦点促进自动化控制，内部焦点引发有意识干预、产生多余肌肉活动。

**处方含义**：miss 段加速度密度高 / 解读为张力偏大（假设性）/ decel_frac 高 / 过度握紧的诊断应给外部焦点提示——"看准目标""顺过那个点"，可降低多余张力同时保住峰值速度与吞吐量。

> ⚠️ **任务依赖**：外部焦点对离散/瞄准动作稳健，但对 **<~200ms 纯弹道动作衰减**（意识无时间调制已发射的运动程序）——提示应在**准备/设定阶段**施加，不能指望它挽救已发射的弹道段。

### 2.2 对抗元认知过度自信（推交错时必带）

`academic_peer_reviewed` · 3-0 · Simon & Bjork 2001（经 Lee 2004 引）

**机制**：学习者系统性地是自身学习状态的差判官——块状练习者会感觉保留能力上"过度自信"，把"表现改善的感觉"误归为"学习进展的感觉"。

**处方含义**：磨单一 KovaaK 场景的用户会**感觉进步飞快、抵抗交错**，但这种感觉高估了真实学习。**AI 教练在推交错处方时必须主动对抗此偏置**——明确告知"感觉进步快 ≠ 长期记住"。这是 ④ plan 的 `interleave` adjustment 的 reason 必带措辞。

## 3. 社区经验层（`community_consensus`，理论一致，进 narrator + 处方理由，不进诊断规则）

| 社区 claim | 处方含义 | 理论一致性 |
|---|---|---|
| VDIM static clicking playlist 渐进分层（fingertip/precision → wide-flick+decel → reactive）| 新手按此顺序建模式 | 对应 §1.2 子动作分类 + §4 渐进 hybrid |
| 顶级 static clicking 由 per-target efficiency 决定（micro-correction + confirmation + 下一 flick 的流畅度），非 flick 速度或 raw accuracy | 进阶标准：流畅的 inter-target transition | 对应 Fitts 修正段（§1.5）|
| wrist/arm 同时控制 = 多任务成本（jack of both, master of neither）| isolate 一个 effector（块状）再结合 | 与 §4.2 hybrid 一致（先块状再交错）|
| 过度依赖 corrective micro（flick 过冲再修正）= 初始 flick 太快 | 处方：别 two-stage B-pill，直接落到目标 | 对应 §1.1 制动代价 + §1.2 修正型 |
| micro clicking 是独立子技能（KovaaK 官方 "Just The Micro Clicking" playlist）| micro 可单独成块 | 与 weakness-specific 一致 |
| VDIM 每日隔离一技术（block 编排）| 每日一个支柱 | **注意：这是块状编排，与 §4.1 交错有张力——见 §4.3 调和** |
| Voltaic weakness-specific 四类（smoothness/precision, speed, static clicking, reactivity）| 诊断→类别映射的社区版 | 与 advice 信号分类互补 |

## 4. 编排层（证据最强）

### 4.1 交错 > 块状（长期保留/迁移）【核心发现】

`academic_peer_reviewed` · 3-0 ×4 · Lee & Simon 2004 + Schorn & Knowlton 2021（[PMC8476370](https://pmc.ncbi.nlm.nih.gov/articles/PMC8476370/)）+ Nature Sci Rep 2024 三级元分析 + Dang/Parvin/Ivry 2023 + Shea & Morgan 1979

**结论**：交错（interleaved/random）练习在长期保留与迁移上稳定优于块状（blocked），即便全为内隐学习（无意识觉察）亦成立。块状在习得期表现更好、感觉进步更快，但学习更不灵活、在交错测试条件下失败。

**处方**：单一 session 内交错多个点击场景（pasu → 1w6ts → Multiclick → Tile Frenzy）期望比磨单一场景产生更好长期保留/迁移，**即便后者当下感觉更有效**。

### 4.2 渐进 hybrid（非二选一）【④ 编排依据】

`academic_peer_reviewed` · 3-0 · Lee & Simon 2004 + Al-Ameer & Toole 1993 + Simon et al. 2002

**结论**：存在混合编排（若干次块状后随机切换；或依表现 contingent 切换）兼顾块状习得收益与交错保留收益。**最佳实践是从块状（新技能习得/热身）→ 递增交错（保留/迁移）的渐进。**

**处方**：新接触某场景用块状几轮建立模式，随后转入跨场景交错。**④ plan-adjustment 的场景交错编排应据此渐进**——不是无脑全交错，新手/新场景先块状。

> 措辞限定：hybrid 是 "promising" 非 "proven"（原文 "show promise"）；切换阈值（几轮后切？）在 aim training 无直接证据，需经验性调参（§9 Q4）。

### 4.3 VDIM "每日隔离" vs "交错" 的调和

`community_consensus` VDIM 每日隔离一技术（块状）看似与 §4.1 交错冲突。调和：
- **跨 session**：每日隔离一技术（VDIM 式）= 合理的块状习得
- **session 内**：多个场景交错（§4.1）= 保留/迁移
- 两者不矛盾——块状用于首次习得某技术的"模式建立"，交错用于"巩固与迁移"。④ plan 的 `interleave` adjustment 针对 **session 内**编排。

### 4.4 KovaaK 落在 CI 实验室域（交错处方有据）

`academic_peer_reviewed` · 3-0/3-0 · Nature Sci Rep 2024 三级元分析（54 研究、2068 参与者）

CI 效应实验室域大且显著（pooled SMD=0.92, p<0.001），应用/现场域可忽略（SMD=0.23, p=0.24）。KovaaK 离散点击场景属受控简单离散刺激-响应任务（实验室域），恰是 CI 稳健区间。**交错处方在此用例理论上有据。**

> 限定：若交错场景共享同一 GMP（generalized motor program），CI 收益会缩水（Magill & Hall 1990）。pasu（弹道）vs 1w6ts（精度）vs Tile Frenzy（网格）跨不同 GMP，满足 CI 收益条件。

## 5. 反馈层

### 5.1 guidance hypothesis：减 KR / 汇总反馈促保留

`academic_peer_reviewed` · 3-0 ×3 · Salmoni, Schmidt & Walter 1984（PMID 6399752）+ Marschall/Bund/Wiemeyer 2007 元分析 + 2022 J Sports Sci 元分析

频繁/即时 KR 改善练习期表现但削弱无反馈时的长期保留（指导效应）。降低 KR 频率（每若干次 shot 才反馈、或 summary KR）促更稳健保留。

**处方**：实时逐 shot 反馈 ≠ 学到；处方应安排**无反馈的保留/迁移测试**检验真实习得；块状练习后用汇总反馈优于逐 shot。

> 任务依赖：对离散点击任务（pasu/1w6ts/Tile Frenzy）稳健，对复杂/连续任务及部分新手可能有害。

### 5.2 外部焦点 KR 可高频（例外）

`academic_peer_reviewed` · 3-0 · Wulf et al. 2010（PMC3153799）

当反馈诱导外部注意焦点时，高频反馈（100% 试次）反而比低频（33%）更能促进**运动形式**的学习。这是 guidance hypothesis 的已记录例外。

> ⚠️ 单研究、儿童足球掷界外球、N=48；效益在运动形式上而非精度；不应作 100% 频率定论。

## 6. 被否决 claim（必须排除出 advice.py）

| 被否决 claim | 票数 | 排除理由 |
|---|---|---|
| SPARC↔IoC 强相关（r=0.64）→ SPARC 可作路径效率指标 | 0-3 | path_efficiency 必须独立测量（§1.7）|
| 外部焦点**几乎无例外**优于内部焦点（~80 实验）| 0-3 | 过强；外部焦点有任务依赖（<200ms 衰减，§2.1）|
| distal > proximal 距离效应（远端焦点优于近端）| 1-2 | 证据不足，作倾向性而非绝对 |

## 7. caveats（外部效度边界）

1. **外部效度缺口**：所有学术来源用手臂指向/数字化板/卒中人群（~2-2.6s reach），非健康玩家 <300ms 鼠标 flick。子动作运动学效应器一般（可外推），但 **SPARC 绝对可靠性数值不能直接迁移**——advice.py 阈值应参数化、需真实校准。
2. **应用层外推**：CI/编排证据是实验室→KovaaK 理论一致外推，非直接测试。若场景共享同一 GMP 则 CI 收益缩水。
3. **任务依赖**：guidance hypothesis（减 KR）对离散点击稳健，复杂/连续/新手可能有害；外部焦点 <~200ms 衰减。
4. **单研究风险**：外部焦点+高频 KR 例外（§5.2）单研究、儿童、N=48。
5. **时间敏感**：社区前沿（Voltaic S5、顶级玩家方法）属 `coach-community-frontier.md` 范畴，易过时，需独立刷新。本报告社区层（§3）标 `community_consensus`，定期复核。

## 8. 落地建议（advice.py / narrator / ④）

### advice.py 处方表
- ✅ 当前 SPARC 主指标 + path_efficiency 独立 —— 正确（§1.6/§1.7）
- 🔧 `reverse_ratio` 诊断前理想做时长归一化（§1.4）——标后续校准，不阻塞 ④
- 🔧 submovement 处方分支化（终止型 Type 1 vs 修正型 Type 2/3，§1.2）——需分类算法，标后续
- ➕ 处方理由可引用外部焦点（§2.1）+ 元认知对抗（§2.2）

### narrator 知识库
- ➕ SYSTEM_PROMPT 补：外部焦点提示话术（"看准目标""顺过那个点"）、元认知对抗话术（"感觉进步快≠长期记住"）、交错 vs 块状的渐进逻辑
- ➕ 社区层（§3）作 narrator 内容，标 community_consensus

### ④ plan-adjustment（④ spec 开放问题的 research 回填）
- **Q2 stall 语义**：用 verdict（客观指标 vs baseline）有理论支撑——用户感觉不可靠（块状过度自信，§2.2），故用客观指标判 stall 而非用户感知。默认 verdict 成立。
- **编排**：④ 的 `interleave` adjustment 升级为"渐进 hybrid"（§4.2）——新手/新场景先块状几轮，再交错；reason 必带元认知对抗措辞（§2.2）
- **Q3 REST_GAP_DAYS / Q6 处方池**：处方池来自 §1 的映射（按诊断信号选场景）；REST_GAP_DAYS research 无具体数字，保持经验默认 + 标注需校准

## 9. 开放问题（research 留下的）

1. **Type 1/2/3 子动作分类在 <300ms flick 信号上如何可靠实现？** 速度/加速度/jerk 过零依赖信号质量与采样率，60fps 录屏是否够分辨率？决定 advice.py 能否落地分类诊断（§1.2）。
2. **SPARC 在健康人 <300ms 弹道 flick 上的实际可靠性？** 现有证据来自 ~2-2.6s 卒中 reach，需 KovaaK 数据集做测量特性验证（ICC/CoV/时长相关），否则阈值缺经验校准（§1.6）。
3. **社区"每场景技术要领+进阶标准+常见错误"**（pasu 用法、1w6ts 握法、Tile Frenzy 节奏、Multiclick 减速要点、speed 类发力）本批次未作独立 claim 验证——处方层"执行要点"的另一半，需单独 deep research pass 以 community_consensus 标准填充 narrator 知识库。
4. **hybrid 编排切换阈值**（几轮后切？依表现 contingent 判据？）在 aim training 无直接证据，④ 场景交错编排需经验性调参，可能需从保留测试数据反推（§4.2）。

## 10. 信源

**学术 primary（peer-reviewed）**：
- Fradet, Lee & Dounskaia 2008, *Acta Psychologica* — [PMC2600723](https://pmc.ncbi.nlm.nih.gov/articles/PMC2600723/)（子动作起源 / Type 1/2/3 / SPARC↔速度）
- Exp Brain Res 2024 "Type 1 Submovement Conundrum" — [Springer](https://link.springer.com/article/10.1007/s00221-024-06784-0)
- Hoffmann 2016, *J Motor Behavior* — DOI:10.1080/00222895.2015.1092939（Fitts 律源于运动学结构）
- Cornec et al. 2024, *J NeuroEng Rehabil* — [PMC11134951](https://pmc.ncbi.nlm.nih.gov/articles/PMC11134951/)（SPARC vs 时域指标 / 时长偏倚）
- Salmoni, Schmidt & Walter 1984 — PMID 6399752（guidance hypothesis）
- Wulf 2013 综述 + Wulf et al. 2010 [PMC3153799](https://pmc.ncbi.nlm.nih.gov/articles/PMC3153799/)（外部焦点）
- Lee & Simon 2004 + Schorn & Knowlton 2021 [PMC8476370](https://pmc.ncbi.nlm.nih.gov/articles/PMC8476370/) + Nature Sci Rep 2024 [s41598-024-65753-3](https://www.nature.com/articles/s41598-024-65753-3)（CI / 交错 vs 块状）
- Simon & Bjork 2001（元认知过度自信）

**社区（consensus/experience）**：VDIM guide (Lowgravity56, Scribd)、Voltaic weakness-specific routines (Scribd)、r/FPSAimTrainer 多帖、YouTube 教学视频、KovaaK 官方 playlist。
