# Task 10R Cross-family Coach Knowledge Research

> 状态：research assessment completed on 2026-07-22。本文是 Task 10 的证据输入和覆盖审计，不是运行时 Knowledge Registry、产品范围或 analyzer 实现合同。产品范围仍以 `docs/PRD.md` 为准，稳定数据边界仍以 `docs/ARCHITECTURE.md` 与 active Complete Coach spec 为准。

## 1. 结论

旧 `knowledge/coach/registry.v1.json` 不足以直接支撑 dynamic clicking、continuous tracking 和 target switching 的上线级 Coach。问题不只是条目少，而是三层同时缺失：

1. **观察覆盖缺失**：dynamic clicking、switching、`reading`、`confirmation` 没有正式条目；tracking 只有早期 aggregate/error/mismatch 定义，缺少 Task 8 所需的 lag/gain/change response/correction/coherence/phase 等条件化解释。
2. **处方合同缺失**：现有 entry shape 没有独立的 quality prerequisite、expected direction、dose guardrail、matched retest 和 near-transfer retest 字段。
3. **证据层混合**：产品合同可以定义 metric，社区教练可以提供观察词和 cue，运动控制研究可以约束机制与验证；三者不能互相替代。

经过本轮研究，证据已经足够支持一个**保守的首发最小知识体系**，但不支持绝对阈值、身体根因、眼动/注意力事实、通用训练时长或主游戏迁移保证。Task 10 implementation 应把这些限制写进 schema 和每条 active entry，而不是靠 prompt 提醒。

## 2. 研究方法与证据等级

本轮按四类证据分别使用：

| 等级 | 可以回答什么 | 不能回答什么 |
|---|---|---|
| product contract / current code | 系统实际观测了什么、metric 如何定义、何时 unavailable | 人为什么这样做、什么训练一定有效 |
| academic peer-reviewed | 一般运动控制机制、测量限制、反馈/练习/迁移原则 | FPS 社区术语的唯一含义、KovaaK 专项绝对阈值 |
| official/community coach | 真人如何观察、命名、给 cue、拆练习、安排 follow-up | 医学事实、已证实因果、普适剂量和迁移保证 |
| anecdotal/personal | 候选模式、可测试假设、场景使用线索 | deterministic diagnosis、severity、profile fact |

社区经验不是低价值噪声。它是当前最丰富的 aiming-specific observation vocabulary 和 teaching cue 来源；但每条内容必须保留作者、日期、适用场景、分歧和 `community_organization | coach_first_party | community_consensus | personal_experience_unverified` 标签。单一作者或组织自述不能自动升级为 community consensus。

## 3. 当前可观测边界

### 3.1 可以形成正式事实

- canonical challenge time 与 source coverage；
- Raw Input 的 `dx/dy/buttons/timestamp` 及其派生 movement timing、speed、acceleration、path、direction reversal、correction/submovement 和 SPARC；
- 普通 KovaaK 画面中固定 viewport center 的 aim point；不得重新检测装饰性准星图案；
- MP4 本地预处理得到的 target center、visible radius/hitbox proxy、track identity、visibility、occlusion/re-entry、direction change 和 confidence；
- shot/hit/outcome events，以及 `directly_observed | validated_aligned | inferred | unavailable` 的 OutcomeAssociation；
- Stats/Performance 的规范化配置、kill/outcome timeline 和 aggregate facts；
- 每个 analyzer 的完整 ProcessedEventTable、MetricRecord、EvidenceSegment 和 safe derived SignalWindow。

### 3.2 当前不能形成正式事实

- 玩家注视位置、眼跳、余光使用或是否看清动画；
- 肌肉激活、握力、关节动作、手腕/手臂/手指贡献和身体张力；
- 玩家主观意图、信心、注意焦点、预测策略或“想选哪个目标”；
- Workshop 场景没有显式规则时的正确 target priority；
- 主游戏角色动画语义、掩体/地图约束、武器弹速/后坐力和玩家自身移动；
- `.perf` 低频 aggregate 推导的逐 shot-target 真值；
- 没有 calibration dataset 的健康线、好坏绝对阈值和跨设置像素比较。

### 3.3 质量 Gate

任何 target-relative claim 至少要求：

- canonical time/alignment 可用；
- visual producer profile 已人工审核，runtime visual domain compatible；
- center/radius/identity/re-entry/coverage 达到对应 metric family 门槛；
- 需要 outcome 的 claim 有直接观测或已验证对齐的 OutcomeAssociation；
- predictable mechanism claim 另有 segment-level MotionPredictabilityEvidence；
- occlusion、frame gap、identity crossing、results UI 和低 confidence 区段被排除或显式 limited。

## 4. 真人教练的共同教学结构

不同教练没有统一执照或标准教材。公开页面能够直接支持的共同部分只有：

```text
intake / goal
  -> benchmark, live observation or VOD assessment
  -> personalized plan / advice
  -> follow-up, adjustment and recap
```

证据：

- [Voltaic Amped](https://voltaic.gg/coaching)（Voltaic，未标发布日期，访问于 2026-07-22）公开列出 onboarding、initial assessment、personalized plan、weekly sessions、follow-up adjustment 和 post-analysis；
- [KellerFPS](https://metafy.gg/aim-lab/sessions)（KellerFPS 的 marketplace 自述，动态页面，访问于 2026-07-22）公开描述 live benchmark、逐 task 观察和同步定制 playlist；
- [Rambo](https://www.fpscoach.com/lessons/)（Ron "Rambo" Kim，未标发布日期，访问于 2026-07-22）要求 webcam 观察 grip/aiming arm，并结合 VOD、recap 和 improvement plan；
- [Krascsi](https://krascsi.com/)（品牌自述，未标发布日期，访问于 2026-07-22）提供 live posture/grip/mechanics assessment、evolving plan、async VOD 和周期 follow-up；
- [WizardHyeong curriculum](https://wizardhyeong-coaching.teachable.com/p/the-definitive-aim-guide)（Shrugger / WizardHyeong，持续更新课程目录，访问于 2026-07-22）把 movement、tracking、speed matching、click timing、confirmation、posture、prediction/reading、VOD review 和 pro analysis 分成独立教学单元。

“重复 pattern -> 候选原因与反例 -> 单一主问题 -> 隔离 cue -> 延迟 matched/near-transfer retest”不是上述页面共同证明的真人教练标准流程，而是本产品把社区工作流、运动学习验证原则和数据可追溯要求组合后的**产品教学合同**。Coach 不能只返回“指标偏高 + 知识段落”；它需要维护候选假设、反例、当前主问题、用户执行和复测结果。正式 profile 和 plan 仍只能由确定性 facts 与产品命令更新。

## 5. 核心教练术语到数据的映射

| 教练术语 | 可支持的 processed facts | Coach 可以说 | 替代解释 / 反证 | 禁止说 |
|---|---|---|---|---|
| movement reading | validated target change-point；change 前后 target velocity/acceleration；crosshair response direction/latency；post-change error/loss/recovery；不同 motion condition 的 matched distribution | “你在这些已验证变向/加速条件后更容易丢失目标；reading 是一个候选训练方向” | alignment/capture latency、visual quality、目标更小/更快、遮挡、预测策略、动作回稳慢 | “反应神经慢”“没有看目标”“眼睛没跟上” |
| animation reading | 只有主游戏 animation cue 标注、眼动/行为或可审核语义时才有证据；普通球形 aim-trainer target 不具备 | 当前只能说明 aim-trainer acceleration/change response，不能说明主游戏动画读取 | 角色、skin、FOV、场景、地图和熟悉度差异 | 从 KovaaK 球体轨迹宣称动画阅读好坏 |
| speed matching | target/crosshair relative velocity；signed velocity gain；position error；time in radius；steady-segment coverage | “在这些稳定长 strafe 中，你的相对速度持续偏快/偏慢” | intentional edge tracking/lead、alignment offset、target depth/size、短片段、reacquisition phase | 单凭 mismatch 推断 tension 或视觉能力 |
| change response / reactivity | validated direction/speed change；response onset/sign；overshoot/under-response；reacquisition；post-change stability | “变向后的回到目标和回稳成本较高” | change 不可读、frame gap、系统延迟、提前预测、短 strafe 不允许稳定估计 | 把总 lag 直接叫 human reaction time |
| pre-shot confirmation | first entry into hitbox/radius；settle/dwell；relative speed/error at click；shot outcome | “点击前在目标范围内等待更久/更短；是否过度要结合命中和目标状态” | 小目标、目标加速、武器/shot rule、deliberate pacing、association 不完整 | 从 dwell 直接推断犹豫、信心或注视 |
| kill/outcome confirmation | previous validated hit/kill/damage 到 leave/switch 的时间；early leave before outcome；post-outcome dwell | “有些链在结果确认前离开，或结果后仍停留较久” | TTK、projectile delay、reload、continuous fire、结果不可观测 | 无逐事件 outcome 时评价 kill confirmation |
| acquisition | target identity/candidate visible；movement start；first entry；settle/fire；path/distance | “转入目标的 transport、到达和点击阶段中，主要成本在 X” | initial viewport geometry、target size/speed、selection unavailable | 把所有 acquisition 都叫 flick 或 selection error |
| reacquisition | loss interval；re-entry；first stable return；post-return error/coverage | “离靶次数”和“每次回位慢”分开描述 | occlusion、identity loss、target disappearance、frame gap | 把视觉 detector 丢失算用户 loss |
| smoothness | versioned SPARC/correction burden，加上 error/coverage/path 和相同 window/filter | “在误差没有恶化的前提下，运动更/不那么连续” | 正常 intermittent correction、目标变向密度、sampling/filter、intentional fast correction | smoothness 单独作为总分；correction 存在即坏 |
| prediction / lead | signed target-frame error/relative velocity；accepted MotionPredictabilityEvidence；repeated/periodic/script fit；outcome | “该可预测片段存在稳定 lead descriptor，与结果共同支持候选 predictive strategy” | local velocity extrapolation、alignment bias、target direction bias、random luck | 只有 ScenarioProfile `predictable` 就说玩家在预测 |
| tension management | 仅 kinematic symptom + user report + one-variable cue experiment + matched retest | “这个 pattern 也可能与发力/释放方式有关，可以做一次低风险单变量实验” | reading、speed match、摩擦、sens、疲劳、压力、姿势、measurement noise | “检测到张力过高/过低”“某个肌群锁死” |
| selection / roadmapping | simultaneous candidate tracks、chosen next target、distance/layout；只有规则提供 expected target 时才能判断 selection | “你实际选择了哪个目标、路径和后续位置”；有规则时才比较 expected | 未观测 attention、未知 priority、目标 viability、边界/TTK | nearest target 不是自动的正确 target；不猜意图 |

社区来源对术语的支持：

- [Voltaic Amped](https://voltaic.gg/coaching)（Voltaic，访问于 2026-07-22）将 target reading、confirmation、acquisition、click timing、reactivity 和 switching 列为不同教学对象；[Voltaic Season 5](https://blog.voltaic.gg/announcing-the-voltaic-season-5-aiming-benchmarks-beta-for-kovaaks/)（Voltaic，2024-12-25）把 clicking、tracking、switching 及 linear/control/stability 等子类作为 benchmark taxonomy，但明确 raw aim 只是游戏表现的一部分；
- [Movement Reading](https://rawinput.net/resources/reaction-time)（Viscose，2025-02-08）区分 reaction、prediction、animation cue 和 movement reading；
- [Speed Matching](https://rawinput.net/resources/speedmatching)（MattyOW，2025-05-23）把 relative speed continuity、reading 和 tension cue 联系起来，但其 tension 归因保持 community hypothesis；
- [Tension Management](https://rawinput.net/resources/tension)（Viscose，2025-06-16）提供 release/lockout/tension-budget 教学语言，不构成 EMG 或 grip-force 测量；
- [Smoothness](https://rawinput.net/resources/smoothness)（pinguefy / Viscose，2024-06-24）把 smoothness 作为跨 family 的社区教学对象；其机制归因不能脱离 error、coverage 和任务条件；
- [How to Pasu](https://rawinput.net/resources/pasu)（MattyOW，2022-08-04）将 dynamic clicking 拆成 target reading、acquisition-to-click、deliberate click、pacing、leading、target viability 和 roadmapping；
- [Pure Reactivity vs Reactive Tracking](https://rawinput.net/resources/purreactivity)（MattyOW，2023-06-25）显示 gradual-acceleration 与 instant-acceleration 条件需要不同策略；
- [One Tip for Every Category](https://rawinput.net/resources/tipforallcats)（MattyOW，2025-04-13）展示 dynamic/linear/elusive clicking、precise/control/reactive tracking 和 speed/evasive/stability switching 的社区 cue 差异。

## 6. Dynamic clicking 最小知识覆盖

### 6.1 条件分类

首发至少区分：

- `linear/predictable`：较长稳定方向或明确 bounce/script；
- `arc/bounce`：速度和竖直状态随轨迹阶段变化；
- `reactive/evasive`：短 strafe、快速或不可预测 change；
- `unknown`：没有足够 profile/evidence，不生成 predictive claim。

### 6.2 Observation coverage matrix

| Observation | 质量前提 | 解释与方向 | Alternatives | Cue / dose guardrail | Retest |
|---|---|---|---|---|---|
| normalized click error / miss vector | center+radius accepted；click time aligned；association available | 只在相同 target size/speed/change condition 下使用 self distribution；沿运动方向 signed error 与横向 error 分开 | detector error、target change at click、click transport latency、shot rule | 选择较大/较慢同类目标，先让 click error 与 miss spread 稳定，再提高 speed；不追单局 PB | matched：同 profile；near：未见过的 size/distance |
| target-state conditioned outcome | direct/validated association；每个 state 有足够 events | 比较 steady/accelerating/decelerating/post-change 条件，不把 aggregate accuracy 当 state truth | sampling imbalance、hard-state exposure、target priority | 一次只降低一个 motion dimension；保持 outcome guardrail | matched motion distribution；near：未见 speed/change sequence |
| acquisition-to-click duration | target identity、acquire definition、click event完整 | 长时间只说明观察/等待成本；结合 error/outcome 判断 over-confirmation candidate | deliberate pacing、小目标、刚发生 change、target viability | easier dynamic；要求清楚读到方向后再点击，不规定通用毫秒线 | 同 target condition；near：相同 motion 不同 layout |
| relative velocity before click | trajectory quality、统一坐标、足够 pre-click window | 持续 signed mismatch 支持 speed-matching/lead-lag descriptor | terminal decel、planned lead、edge hit、frame gap | long readable strafe，cue 为匹配方向和速度后完成 click | matched speed；near：不同 target speed |
| post-change error and response | validated change point、alignment 可分离 | change 后 error/loss 上升支持 change-reading/response candidate | capture offset、prediction miss、target too small | gradual acceleration/reversal easy variant，先看变化再完成一次连续响应 | matched change type；near：unseen sequence/acceleration |
| stable lead/lag | accepted predictability evidence；signed target-frame metric | successful stable lead 可是有效策略；无 predictability 只描述位置偏差 | local velocity extrapolation、system bias、random direction bias | predictable linear/bounce 条件；禁止“猜下一次随机变向” | matched script；near：phase/speed variation |
| repeated click / pacing | shot events complete；reload/scoring rule known | miss burst、re-click recovery、rate/accuracy joint trend 描述 pacing | weapon rule、reload、spawn drought、selection | 调低 condition 难度，练 deliberate click；不采用通用 75/80/90% 阈值 | 同 scoring profile；near：不同 spawn/layout |

### 6.3 学术边界

- 在 van Donkelaar 等人的手臂 moving-target 实验中，predictable 与 unpredictable target velocity 引出了不同的初始响应与后续修正策略：[Control strategies in directing the hand to moving targets](https://pubmed.ncbi.nlm.nih.gov/1301368/)。
- 在 Soechting 与 Flanders 的手部截获范式中，visual motion extrapolation 更依赖动作开始附近的局部 motion cue，而不是完整轨迹模型：[Extrapolation of Visual Motion for Manual Interception](https://doi.org/10.1152/jn.90308.2008)。
- 在 Kreyenmeier 等人的眼动、遮挡与手部截获范式中，人可以跟踪加速目标，却未必准确利用 acceleration 预测未来 time-to-contact；因此 acceleration-conditioned miss 不能仅凭运动学归因为 reading 缺陷：[Humans Can Track But Fail to Predict Accelerating Objects](https://pmc.ncbi.nlm.nih.gov/articles/PMC9469915/)。

这些研究支持 condition-specific metrics 和多个候选机制，不提供 KovaaK absolute threshold。

## 7. Continuous tracking 最小知识覆盖

### 7.1 Predictable tracking

| Observation | 解释与方向 | Alternatives | Cue / verification |
|---|---|---|---|
| position error + time in radius | error 更低且 coverage 不降才是改善；edge strategy 要看 hitbox coverage | target size、intentional edge track、visual domain | matched script；cue 为稳定贴合而非追 center-only |
| lag + velocity gain | 只在长、近似平稳片段解释；lag 接近 0、gain 接近 task-appropriate band 不是无条件目标 | alignment offset、predictive lead、depth/scale change | constant/slowly varying speed；near retest 改 phase/speed |
| coherence / phase | 只用于足够长、近似平稳、版本固定的窗口 | nonstationarity、change points、short window | matched periodic script；不可用于 reactive 随机片段 |
| loss/reacquisition | loss count、duration、return latency 分开 | occlusion、identity failure、radius error | easier/large target；分别降低 loss 与回位时间 |

### 7.2 Reactive tracking

| Observation | 解释与方向 | Alternatives | Cue / verification |
|---|---|---|---|
| direction-change response | validated change 后正确方向启动和回到目标的条件分布 | system latency、anticipation、change magnitude | gradual/clear changes 起步；matched random sequence + unseen sequence |
| overshoot / under-response | target-frame signed error 与 response gain 联合解释 | target reversed again、edge strategy、alignment | cue 为一次连续响应后回稳，不追求最早 movement onset |
| post-change stability | change 后固定窗口 error/coverage/corrections | window overlap、target size、occlusion | 先降低 change density，再逐步恢复 |
| premature reversal | 在 target 尚未 change 时反向，且多次出现；只能叫 predictive reversal descriptor | track reset、mouse reposition、identity swap | unpredictable condition 中等待可观测 change；与 predictable 条件对照 |

### 7.3 Control / smoothness tracking

| Observation | 解释与方向 | Alternatives | Cue / verification |
|---|---|---|---|
| correction burden | correction 是正常 control 行为；只有与 error/coverage/energy proxy 的联合恶化才是候选问题 | target change density、normal intermittent control、noise | easier continuous target，减少不必要大幅 chase；保留 error guardrail |
| SPARC / continuity | 同 window/filter/sampling/version 比较；更 smooth 但 error 更差不算改善 | temporal scaling、filter、measurement noise | matched smooth condition；near retest 改 target size/speed |
| speed/acceleration mismatch | steady 与 change window 分开；signed mismatch 比绝对 aggregate 更可教 | lag、lead、edge strategy、reacquisition | long strafe speed match；change cue 独立训练 |

### 7.4 学术边界

- 多种 visuo-manual tracking 实验观察到 intermittent corrections；在本产品里，correction 存在本身因此不能作为缺陷判据：[Miall et al. 1993](https://pubmed.ncbi.nlm.nih.gov/12730041/)、[visuo-manual intermittent control](https://pmc.ncbi.nlm.nih.gov/articles/PMC5663819/)。
- 小样本 joystick/visuomotor 实验显示 delayed visual feedback 会改变 correction frequency 和 tracking behavior；这支持产品把系统 alignment 与 human-response hypothesis 分开，不提供通用的人类反应时阈值：[Visuomotor tracking with delayed visual feedback](https://doi.org/10.1016/0306-4522(85)90189-7)。
- SPARC 是 kinematic smoothness measure，受 window、频率范围、filter 和任务类型约束，不能替代 accuracy：[On the analysis of movement smoothness](https://pmc.ncbi.nlm.nih.gov/articles/PMC4674971/)。

## 8. Target switching 最小知识覆盖

### 8.1 条件分类

- `speed`：transport 与快速 acquisition 为主；
- `evasive`：到达后还需短 tracking/change response；
- `stability`：arrival control、稳定跟随和 selection/layout 更突出；
- `unknown/unclassified discrete acquisition`：identity/outcome/selection 不足时的安全降级。

### 8.2 Observation coverage matrix

| Observation | 质量前提 | 解释与方向 | Alternatives | Cue / retest |
|---|---|---|---|---|
| previous outcome -> leave | previous outcome direct/validated；TTK/scoring known | early leave 与 post-outcome dwell 分开 | projectile/damage delay、continuous fire、reload | 大目标/稳定 TTK 练完成结果后连续离开；matched TTK + near different TTK |
| inter-target transition | stable identities、distance/direction、leave/acquire events | distance-conditioned time 和 path efficiency 联合；快但 arrival error 大不算改善 | layout、mouse reset、target motion | 先练 clean transport，再提高 speed；matched layout + near distance/direction |
| arrival / acquire / settle | hitbox accepted、target moving state known | transport 与 terminal control 分开 | target changed direction、small target、occlusion | larger/slower targets，cue 为平滑进入可跟随状态 |
| first shot/damage | outcome association available | first-shot error 与 acquire duration 联合 | weapon fire rate、spread、damage model | matched weapon/scoring；near target speed/size |
| carry-over overshoot | previous movement direction、next target frame、stable chain | previous momentum 延续导致 next-target terminal cost的候选 descriptor | chosen path geometry、next target moved、identity error | alternate direction/distance 条件；观察 arrival error 不只 switch speed |
| pre-alignment | next target visible、selected identity known | 描述 movement start 前 aim point 与 chosen target 的关系 | attention/intent unknown、candidate appeared late | 只作 descriptive；不自动评价 selection |
| selection / roadmapping | simultaneous candidates + explicit expected rule | 只有 rule-defined expected target 才能算 selection error | viability、future layout、priority、attention unknown | matched rule/layout；否则只展示 actual choice |

Target switching 不能被简化成连续 flick。Sensorimotor review 说明多个 movement goal 的选择、动作成本与在线改向可能相互作用：[Decision-making in sensorimotor control](https://pmc.ncbi.nlm.nih.gov/articles/PMC6107066/)。在 Kurtzer 等人的 target-split reaching 范式中，两个候选相对单一 replacement 增加了 selection time，选择也受附近选项偏置：[Reaching movements are automatically redirected to nearby options](https://doi.org/10.1152/jn.00336.2020)。这些研究只支持把 selection 作为独立分析维度，不提供 FPS 或游戏内正确目标优先级。

## 9. Training cue、剂量与验证合同

### 9.1 Cue 原则

- 每次只给一个 primary cue；其它机制保持 alternatives；
- 优先 external/task-effect cue，例如“让相对速度稳定”“变向后重新进入目标范围”，而不是未经验证的肌群指令；
- cue 必须指向可复测 metric 和 condition；
- community cue 可以使用，但标来源等级，并允许 Coach 在 counterexample 后撤回；
- Aim Lab 的一项 37 人 FPS 实验没有观察到 external-focus 的显著即时表现优势；单项研究既不能否定其它任务中的 external-focus 效果，也说明不能把它写成无条件定律：[Lamers James & O'Connor 2023](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0288937)。

### 9.2 Difficulty 和 dose guardrail

没有证据支持统一的“每天 N 分钟”“保持 X% accuracy”适用于所有 scenario、family 和水平。首发 entry 的 dose guardrail 应是规则而不是魔法数字：

- 使用用户当前可持续 schedule 和 baseline，先记录实际完成量；
- learning block 采用能看清目标模式并执行 cue 的较低 functional difficulty；hard variant 用作 stress test，不替代技术学习；
- 一次只改变 target size、speed、change density、distance/layout 或 exposure duration 中一个主要变量；
- 持续出现与 cue 无关的质量崩溃、注意力下降或主观疲劳上升时停止该 block 或降难度；
- 疼痛、麻木、刺痛、无力或持续不适立即停止，不用“坚持完成剂量”；
- 分数没有下降不能证明没有疲劳。在 20 名参与者完成六个 5 分钟 Aim Lab bout 的研究中，主观与 EMG wrist-extensor fatigue 指标上升，而所测 aiming performance 未显著下降：[Impact of repetitive mouse aiming on muscle fatigue](https://doi.org/10.1016/j.jelekin.2025.102992)；
- precision demand 和 mental pressure 可增加 muscle activity、grip/click force，但当前产品没有 EMG/force sensor，不能从 kinematics 反推：[Visser et al. 2004](https://doi.org/10.1080/00140130310001617967)。

Challenge Point Framework 提出并预测 task difficulty 与 learner skill 共同决定练习信息量，而不是越难越好；它没有直接验证 FPS 剂量：[Guadagnoli & Lee 2004](https://pubmed.ncbi.nlm.nih.gov/15130871/)。社区上“easy variant refinement、hard variant stress test”的做法可作为 community implementation，与此框架相容但不是已验证 FPS 剂量公式。

### 9.3 Retest

每条处方必须同时定义：

1. **delayed matched retest**：相同 ScenarioProfile、设置、visual/metric version 和目标 condition；
2. **near-transfer retest**：只改变一个关键维度，例如 speed、change sequence、target size、distance/direction、layout 或 TTK；
3. **main-game homework（可选、用户报告）**：只作 transfer evidence，不写回 deterministic aim metric；
4. **stop/adjust rule**：matched 无改善、near-transfer 反向或 discomfort 上升时降低 claim、换 cue 或终止实验。

Motor learning 必须区分 acquisition performance、retention 和 transfer；contextual interference 对 transfer 的平均效应不能被直接换算成单个用户处方：[2024 meta-analysis](https://pmc.ncbi.nlm.nih.gov/articles/PMC11349744/)。一项仅含 10 名 FPS 玩家和四个 KovaaK 任务的 pilot 支持部分任务的 test-retest reliability，但没有测量主游戏 transfer；因此它不能建立“benchmark improvement = in-game improvement”：[KovaaK reliability pilot](https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2024.1309991/full)。

## 10. Candidate diagnosis 合同

每条 candidate issue 至少需要：

```text
observation refs
quality prerequisites and accepted condition refs
expected direction or comparison-only rule
supporting rows
counterexample rows
plausible mechanisms
alternative explanations
forbidden inferences
knowledge refs + source/claim levels
cue
dose guardrail
matched retest
near-transfer retest
stop/adjust rule
```

Coach 可以接受、降低或拒绝 deterministic candidate issue，但不能覆盖 measured facts。Coach 的最终解释是可修订 hypothesis state；只有后续 comparable retest 才能提高或降低候选机制可信度。

建议的响应顺序：

1. 先用白话说最稳定的 observation；
2. 给 1 个主要解释和 1-2 个 alternatives；
3. 指出哪个 evidence segment/row 支持、哪个反例限制；
4. 给一个 cue 和小范围练习条件；
5. 给 matched + near-transfer retest；
6. 明确什么结果会推翻当前建议。

## 11. Runtime Knowledge 结构缺口

当前 `coach_knowledge_registry.v1` entry 只有通用 `text/limitations/counterevidence/supported_uses`，无法让 validator 强制本研究矩阵。由于 Python 和 TypeScript 都 exact-key validate v1 shape，直接加入 required fields 是 wire-contract change，不能只改 JSON。

Task 10 implementation 应先做版本裁决，推荐方案是：

- 保留 `registry.v1.json` 和 v1 reader 解析历史 refs；
- 新建 `coach_knowledge_registry.v2` / `registry.v2.json`，active production retrieval 使用 v2；
- v2 entry 增加结构化字段：
  - `family_scope[]`
  - `observation_refs[]`
  - `quality_prerequisites[]`
  - `expected_direction`
  - `alternative_explanations[]`
  - `forbidden_inferences[]`
  - `cue`
  - `dose_guardrail[]`
  - `matched_retest`
  - `near_transfer_retest`
  - `stop_adjust_rule[]`
- `definition`、mechanism、direction、cue、dose 和 retest 等 claim-bearing section 各自保存 `claim_level` 与 `source_refs[]`；不能用 entry 级单一 claim ceiling 把“学术机制”和“社区 cue”混成同一证据等级；
- v2 community source 至少保存 `title`、`author_or_org`、`published_at?`、`retrieved_at`、`locator`、`applicability[]` 与它实际支持的 section；单一教练本人材料标为 `coach_first_party/community_practice`，不得冒充 `community_consensus`；
- v1 entry refs 继续可解析但不伪装成已满足 v2 prescription chain；
- migration audit 逐条说明 `carry_forward | rewrite | split | retire | reject`；
- active retrieval 只读一个 canonical v2 Registry；历史 trace 以 `registry_version + entry_ref` 解析对应不可变版本；
- Python/TypeScript retrieval 和 Pi trace 继续只保存 registry/entry/section/source/claim refs，不保存全文到 SQLite。

兼容扩展 v1 也技术可行，但会让相同 `schema_version` 表示两个不同 wire contracts，并迫使所有旧 active entry 原地补字段。除非现有 stable trace/read contract证明完全不需要读取旧 shape，否则不推荐。

## 12. Task 10 implementation 的冻结输入

### 12.1 必须覆盖

- dynamic clicking：6.2 的全部 observation；
- predictable/reactive/control tracking：7.1-7.3 的全部 observation；
- speed/evasive/stability switching：8.2 的全部 observation；
- reading、speed matching、pre-shot confirmation、kill confirmation、acquisition、reacquisition、smoothness、prediction、tension、selection 的术语映射；
- movement aiming outcome-only 的 `cue/dose/retest = not_applicable`；
- 每条 active v2 entry 的 definition/scope/quality/direction/limitations/alternatives/cue/dose/matched/near-transfer/stop rule；
- academic/community/personal source claim ceiling；
- exact retrieval、最多 3 条、零命中不全库 fallback、Python/TS parity 和 historical refs。

### 12.2 不得进入首发确定性逻辑

- universal cm/360、FOV、mouse weight、accuracy 或 session-minute threshold；
- `reading = reaction time`；
- `smoothness high = accuracy high` 或 `correction = bad`；
- kinematics -> tension/grip/muscle/posture；
- ScenarioProfile `predictable` -> 玩家使用 prediction；
- nearest target -> correct selection；
- benchmark improvement -> main-game transfer；
- score maintained -> no fatigue；
- community top-player technique -> universal optimal technique。

## 13. 剩余 Open Questions

以下不阻塞最小 Task 10 implementation，但必须在后续 Task/Gate 中继续 fail closed：

- 各 metric 的真实 Run calibration distribution 和 absolute severity；
- MP4 visual producer 的真实 center/radius/identity/re-entry quality profile；
- 2026-07-22 已补齐 `OutcomeAssociation.association_kind`、availability、outcome kind、refs、confidence 与 limitations 的组合校验；`inferred + available`、shot-as-outcome 等反例已由测试拒绝。Task 7-9/12 仍须保留该回归 Gate；
- 2026-07-22 real-run capability audit 已确认 normal/timescale/restart 四件套存在，Task 6 不应等待 capture receipt v2；当前 worker 对 seven-field `observed_visual_domain` 的硬依赖是待纠正的 provisional contract。真实剩余 Gate 是 annotation/producer/quality profile，详见 [`2026-07-22-real-run-analysis-capability-audit.md`](2026-07-22-real-run-analysis-capability-audit.md)；
- animation reading、eye focus、player movement、weapon/TTK 和 map semantics；
- subjective tension/fatigue/pain/adherence 的产品 intake 和 execution record；
- Coach recommendation 与真人教练标注的一致性；
- matched improvement 是否在 near-transfer 和 main game 中保留；
- 不同 family、水平和用户 schedule 的最小有效剂量。

## 14. 最终 Gate

本 research assessment 只证明“有足够证据写保守知识合同”，不证明 analyzer、Registry v2 或 Coach 已实现。Task 10 implementation 完成必须另外证明：

- schema/registry validation 和 migration 完整；
- 每个 launch observation 有 active entry；
- 每条 entry 的 quality、alternatives、cue、dose、retest 可机器校验；
- community/personal claim 不越级；
- v1 historical refs 可读；
- Python/TypeScript/Pi parity；
- knowledge 不产生 measured fact、absolute severity 或自动身体根因；
- Task 7-9 从第一条正式 issue 起能引用对应 v2 knowledge ref。
