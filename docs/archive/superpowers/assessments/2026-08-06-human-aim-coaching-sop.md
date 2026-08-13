# 人类瞄准教练 SOP 调研与 Aiming Cookie Coach 落地共识

> 状态：assessment / session decision input
> 日期：2026-08-06
> 用途：交给后续 Coach agent 作为研究结论、产品约束和实现输入
> 边界：本文不直接授权代码实现，不修改 PRD、Architecture、active spec 或施工计划

## 1. 结论先行

本次调研最重要的结论不是“人类教练拥有更多数据”，而是：

> 人类教练的核心能力是把用户的主诉组织成一个可验证的工作假设，再用有目的的 probe 和复测逐步缩小问题；不是把一组指标翻译成一段静态建议。

因此 Aiming Cookie Coach 当前真正缺少的不是更多原始数据，也不是更多知识文章，而是一套可持续运行的 **Coaching Policy / Case State Machine**：

```text
用户目标与主诉
  -> 证据整理
  -> case formulation（观察、候选解释、反证）
  -> diagnostic probe（有目的的验证）
  -> 学员反馈与新证据
  -> 少量高优先级处方
  -> 主游戏/目标场景迁移
  -> retest
  -> 根据结果、体感、执行和平台期调整
```

这条链必须是 Coach 的默认工作方式。数据、确定性诊断、知识库和产品命令都服务于这条链，而不是各自向用户输出一份“看起来完整”的报告。

## 2. 本次 session 的产品共识

### 2.1 不预设产品信息少于人类教练

产品可以获取的证据不应被产品合同先验地限制为“少于人类教练”。如果产品实际能取得摄像头、动作轨迹、输入原始数据、训练结果、用户自述、疼痛/疲劳或环境设置，就应把它们纳入同一证据系统。

但“可获得”不等于“已经测量”，也不等于“已经证明因果”。每条证据必须标明来源和强度：

| evidence kind | 含义 | Coach 可以说什么 |
|---|---|---|
| `measured` | 产品直接测得的输入、事件、轨迹、时序或图像事实 | “检测到本次减速段更长” |
| `self_reported` | 用户主动描述的目标、体感、疼痛、疲劳、困惑或偏好 | “你描述训练 10 分钟后前臂开始紧” |
| `observed` | 从视频、截图、姿势或交互过程定位到的可见现象 | “这几个片段中准星在目标边缘来回修正” |
| `inferred` | 由多条证据推导出的候选解释，不是直接测量 | “这更像是刹车时机与目标读取共同造成的候选问题” |
| `external` | 研究、社区实践、教练经验或用户提供的外部资料 | “社区常用的一个练习是……” |

例如，摄像头可以观察到握持变化，但不能仅凭一帧姿势断言“肌肉张力导致 miss”；需要把它当作 `observed`，再通过追问、对照任务或复测验证。

当前阶段是否接入某种采集能力由产品/实施合同另行决定；本文只冻结消费这些证据时的推理纪律。

### 2.2 成功标准由用户定义

Coach 不预先规定所有人都应追求 PB、benchmark 排名、命中率或某个绝对阈值。首次进入诊断时，Coach 应明确询问并记录用户当前想改善的结果，例如：

- 主游戏迁移：对局中更快地完成第一发、少在目标边缘修正；
- 稳定性：发挥波动变小、疲劳后不崩；
- 动作质量：更早刹车、减少过冲、减少多余 submovement；
- 训练表现：完成某个 benchmark、提高 PB 或缩短达成时间；
- 体验与身体状态：降低紧张、疼痛、疲劳或训练挫败感；
- 其它用户明确提出且可被观察/复测的目标。

同一指标在不同目标下可能有不同优先级。Coach 的任务是帮助用户定义可观察的 outcome 和验证方式，而不是替用户决定“什么才算成功”。

### 2.3 允许用户依赖 Coach，但依赖形态要正确

Aiming Cookie 的目标是建立长期、持续的 Coach 关系。用户认为 Coach 有用、愿意回访、愿意把判断交给 Coach，是产品价值的一部分，不应为了“最终脱离 Coach”而削弱反馈。

需要区分两种依赖：

- **允许并鼓励**：用户依赖 Coach 做跨次综合判断、保持训练上下文、管理计划、解释平台期和提出下一步；
- **需要控制节奏**：用户是否在每个鼠标动作前等待实时提示，这属于反馈延迟、训练设计和交互策略问题，不能让即时提示淹没任务本身。

因此 Coach 可以保持高价值的持续陪伴和主动回访，不需要刻意把用户训练成不再需要 Coach 的状态。

### 2.4 Training Plan 是 living plan

训练计划不是一次生成后长期不变的静态报告，而是一个带版本和依据的工作假设。每次调整都应记录：

- 调整原因：执行反馈、体感、客观变化、目标变化、平台期或新证据；
- 依据：相关 Analysis、metric、diagnosis、prescription、knowledge 和用户反馈引用；
- 变化内容：场景、cue、剂量、难度、顺序、迁移任务或休息安排；
- 验证目标：下次怎样比较，预期朝哪个方向变化，什么情况算证据不足；
- 下一次 review 时间或触发条件。

Coach 不应因为一次低分就全盘换计划，也不应因为计划已经保存就忽略用户的新问题。

## 3. 公开人类教练 SOP 调研

### 3.1 调研范围与证据等级

本次查看了公开教练服务页、课程页、VOD review 说明、训练视频和教练市场页面。公开页面通常能说明服务流程、交付物和教练如何描述问题，但其中的成功率、客户评价和“适合所有人”等内容属于 marketing claim，不作为效果证据。视频/课程中的示范用于提取流程和语言，不代表其阈值或处方已被 Aiming Cookie 验证。

来源按用途分为：

| 来源层 | 可支持的结论 | 不可支持的结论 |
|---|---|---|
| 服务页/课程页 | intake 字段、交付方式、复训与回访形式 | 真实效果大小、因果证明 |
| 教练公开课/VOD review | 问题拆解顺序、观察语言、probe 和 cue 示例 | 对所有玩家普适的诊断阈值 |
| 教练市场页 | 教练类型、服务包装、用户购买路径 | 教学质量排名 |
| 研究/正式测量 | 指标定义和可能机制 | 直接替代个体诊断 |

### 3.2 公开来源

- [Voltaic Amped Coaching](https://voltaic.gg/coaching)：以目标、基线和个性化训练反馈包装长期教练服务。
- [Rambo Coaching Lessons](https://www.fpscoach.com/lessons)：展示 FPS 一对一课程、问题定位和训练指导的服务入口。
- [Rambo Masterclass / Training Video](https://www.fpscoach.com/trainingvideo)：公开课程式讲解，适合观察“现象 -> 解释 -> 练习”的叙事结构。
- [Sam Coaching](https://bysam.github.io/coaching/)：公开说明目标收集、分析和训练反馈的教练服务流程。
- [Metafy KovaaK's Aim Trainer Coaches](https://metafy.gg/kovaaks-aim-trainer/sessions)：教练市场中的 session、VOD review 和按需指导形态。
- [Krascsi](https://krascsi.com/)：面向瞄准/竞技表现的教练服务、课程与个性化训练表达。
- [Shotty](https://coachshotty.com/)：FPS 教练服务、VOD 分析和训练建议的公开包装。
- [Petr Coaching](https://www.petr-coaching.com/)：长期训练、复盘和个体化指导的服务表达。
- [MattyOW VOD Review](https://rawinput.net/resources/vod%20review)：以 VOD 为中心的观察、暂停、问题解释和练习迁移。
- [MattyOW / EliGE case](https://www.youtube.com/watch?v=14K94qHO5ww)：公开案例中把实际对局行为与专项练习联系起来。
- [Viscose full method](https://www.youtube.com/watch?v=yqyy4j32hWk)：完整方法论示范，适合提取问题分类、练习和复测的顺序。

### 3.3 跨来源稳定的 SOP

#### Step 1: Intake / 目标与主诉

教练先问“你想改变什么”，而不是马上解释一张分数表。常见 intake 字段：

- 主游戏、角色/武器、段位和典型对局；
- 训练目标、时间预算、当前 routine 和希望的反馈方式；
- 用户自己认为的问题、何时出现、在什么压力/疲劳条件下出现；
- 灵敏度、DPI、cm/360、FOV、显示设备和其它相关设置；
- VOD、训练记录、最近变化、身体不适和恢复情况。

#### Step 2: 双重基线

优秀教练通常同时看两种基线：

1. **真实游戏/VOD 基线**：确认问题是否迁移到真实目标读取、移动、站位、压力和决策。
2. **隔离任务/aim trainer 基线**：用可重复任务观察控制、速度、刹车、修正、追踪或切换等局部能力。

必要时加 handcam、姿势或设置检查，但这些是证据源，不是自动因果结论。

#### Step 3: 模式识别与问题分类

教练寻找重复出现的模式，而不是挑一个最夸张的片段。常见分类：

- 鼠标控制：起手、加速、刹车、过冲/欠冲、修正和路径；
- 视觉/目标读取：确认时机、目标优先级和反应；
- 移动与射击协同：停枪、反向移动、peek 时机；
- 决策与站位：角度、暴露、追目标、资源管理；
- 压力与状态：疲劳、紧张、节奏、过度追求速度；
- 设备/设置：只有在证据足够且与用户目标相关时才进入候选解释。

#### Step 4: 优先级

把多个现象压缩成少数高杠杆问题。优先级依据通常包括：

- 是否频繁、是否在用户关心的场景中反复出现；
- 对用户目标的影响；
- 是否有可执行的短期干预；
- 是否能通过低成本 probe 区分候选解释；
- 是否存在疼痛、过劳或其它需要先处理的风险。

一次 session 通常只保留一个主问题和少量次问题，避免把处方变成全量指标清单。

#### Step 5: 现场验证 / Diagnostic probe

教练会暂停、追问体感、改变任务难度或 cue、让用户重新跑一小段，再比较结果。Probe 的目的不是“再收集更多数据”，而是区分具体候选解释，例如：

- 降低速度后过冲是否消失；
- 明确“先停再点”的 cue 后 stopping/settle 是否改变；
- 同一任务在疲劳前后是否出现不同模式；
- 隔离练习改善但真实对局不改善时，问题是否在迁移、目标读取或决策层。

#### Step 6: 处方

处方应明确：练什么场景、注意什么 cue、为什么练、剂量/时长、如何进阶、何时停止或调整，以及如何迁移回主游戏。只说“多练刹车”“放松一点”“提高准确率”不构成完整处方。

#### Step 7: 复测与回访

教练通常通过下一段 VOD、weekly/biweekly session、异步反馈或训练日志检查执行情况。复测要保持可比条件，同时允许用户反馈“这个 cue 让我更紧/更慢/更容易理解”。主观反馈不是噪声，而是调整处方的重要证据。

#### Step 8: 总结、交接与持续关系

session 结束时，教练会留下当前工作假设、下一步 routine、自我观察点和下次 review 触发条件。长期关系中，计划会因新证据、平台期、目标变化和执行困难持续版本化。

## 4. 人类教练如何把数据变成“人话”

人类教练并不直接把 `decel_frac = 0.42` 交给用户，而是经过以下转换：

```text
metric/event/distribution
  -> 可复述的具体现象
  -> 对用户任务的影响
  -> 一个或多个候选解释
  -> 明确限制与反证
  -> 一个可执行的 probe 或处方
  -> 复测时看什么
```

推荐 Coach 的表达顺序：

1. **先回应主诉**：“你说自己在小目标上总是急着点，尤其是连续目标。”
2. **给可定位的观察**：“这几局里，起手速度没有明显问题，但到目标附近后出现两次以上反向修正。”
3. **给低强度诊断**：“所以当前更像是刹车/确认节奏的问题，不足以证明是灵敏度或握持导致。”
4. **给一个动作**：“先做 5 分钟只允许自己在准星稳定后点击的练习。”
5. **给成功信号**：“下一次不只看分数，观察 corrective count 是否下降，并记录这个 cue 是否让你更紧。”
6. **约定复测**：“如果修正下降但真实对局仍慢，我们再检查目标读取和迁移，而不是继续堆 aim trainer 难度。”

语言要求：短句、具体、带上下文、承认不确定性、每次只推动一个下一步。避免把术语、指标名和来源名堆成报告口吻。

## 5. 建议的 Coach Case State Machine

以下状态是后续 agent 应实现或映射到现有 runtime 的逻辑概念；不是要求立即创建同名数据库表。

```text
intake
  -> baseline
  -> formulation
  -> probe
  -> intervention
  -> retest
  -> adjustment
  -> follow_up
       ^       |
       |       +-- 新主诉/新证据 -> intake 或 formulation
       +---------- 平台期/执行困难 -> adjustment
```

### 5.1 `intake`

必须回答：用户要改变什么、当前最困扰的现象是什么、在哪些场景发生、可投入多少训练时间、有哪些身体/设备/环境约束。已有 profile 或历史记录可以预填，但 Coach 要让用户纠正。

### 5.2 `baseline`

选择与目标相关的最小证据集：真实游戏/VOD、输入原生 trace、Stats/Performance、aim trainer 任务、用户自述或摄像头观察。明确每条证据的时间、来源、覆盖范围、质量和可比性。

### 5.3 `formulation`

形成结构化工作假设，而不是直接生成定论：

```text
coach_case_formulation.v1
  player_problem          # 用户主诉/可观察现象
  desired_outcome         # 用户定义的成功结果
  relevant_observations[] # metric/event/video/self-report refs
  candidate_explanations[]
  counterevidence[]
  confidence              # 仅表示当前假设强度，不是诊断真值
  priority_problem
```

候选解释必须能被某个 probe 或后续复测区分；只有“看起来合理但无法验证”的解释不得直接成为强处方。

### 5.4 `probe`

```text
coach_diagnostic_probe.v1
  probe_id
  question_to_discriminate
  changed_condition       # cue / difficulty / scenario / pacing
  procedure
  expected_signals[]
  stop_or_safety_rule?
  result
  learner_response
```

`learner_response` 至少应能记录：执行是否完成、用户感受到什么、cue 是否易懂、是否出现疼痛/疲劳/紧张、客观信号如何变化，以及用户是否接受这个训练方向。Coach 不得把“用户没有按计划执行”解释成“用户不自律”，要先检查剂量、理解、场景和反馈是否合适。

### 5.5 `intervention` 与 `retest`

每个处方至少包含：

```text
prescription
  scenario
  cue
  purpose
  dosage?
  progression?
  stop_or_adjust_rule?
  target_metrics[]
  expected_direction[]
  transfer_task?
  retest_after
```

复测需同时包含：

- 客观信号：目标 metric/event/distribution 的变化；
- 用户目标：主游戏或用户定义 outcome 是否改善；
- 主观反馈：理解度、体感、疲劳、疼痛、训练意愿；
- 可比性：场景、设置、输入模式和样本量是否足以比较；
- 证据不足行为：不可比时说“目前无法判断”，不硬给成功/失败。

### 5.6 `adjustment` 与 living plan

调整不是把旧计划静默覆盖，而是生成新版本，保存 adjustment reason、依据 refs 和新的 verification targets。调整触发包括：

- 目标已经改变；
- 用户执行困难或 cue 不可理解；
- 客观指标改善但主游戏没有迁移；
- 客观指标没有改善；
- 用户体感或身体状态变差；
- 平台期、新证据或新的候选解释出现。

当证据支持不足时，Coach 应优先缩小问题、追加 probe 或降低剂量，而不是自动增加训练量或改换整套 routine。

## 6. 原始数据与真实问题的连接规则

Coach 每次使用数据时，内部应能回答下面四个问题：

| 问题 | 示例 |
|---|---|
| 这条数据观察到了什么？ | “目标附近的反向修正次数在本次 40 个事件中偏高。” |
| 它与用户哪个目标有关？ | “你想减少对局中第一发后的二次修正。” |
| 还有什么可能解释或反证？ | “也可能是目标读取慢；当前 trace 没有目标视觉时间戳。” |
| 下一步怎样验证？ | “用相同场景做低速确认 cue，再看修正是否下降并询问体感。” |

推荐的证据组织不是“所有数据塞进 prompt”，而是按当前 case 检索：

```text
player_problem
  -> relevant evidence refs
  -> deterministic observations
  -> candidate explanations / counterevidence
  -> probe or prescription
  -> verification targets
```

指标、研究、社区经验和用户自述的职责必须分开：

- 指标说明“发生了什么”；
- 规则说明“哪些证据组合更符合什么问题”；
- 研究/社区材料提供机制、cue 或候选练习；
- 用户目标与体感决定“为什么现在值得处理”和“结果是否有用”；
- Coach 负责把这些东西组织成一条可执行的下一步。

## 7. 反模式清单

- 只按 PB、benchmark 排名或单次高分决定用户是否成功；
- 把所有指标按严重程度排序后一次性输出；
- 用“手部紧张”“灵敏度不对”“姿势导致问题”等未经 probe 的因果句；
- 看到某个 metric 就自动生成固定 routine，不询问用户主诉和训练时间；
- 把用户没执行计划归因于意志力，不检查剂量、cue、理解和身体状态；
- 计划生成后不记录版本和调整原因；
- 只复测 aim trainer 分数，不检查主游戏迁移或用户定义 outcome；
- 过度使用术语、英文缩写和来源名，用户无法复述下一步；
- 为了“培养独立性”减少 Coach 的长期上下文和回访价值；
- 把营销页的客户评价、成功率或排名当成效果证明。

## 8. 给后续 agent 的实现输入

后续 agent 开工前应先在现有代码/active plan 中找到对应的 context、prompt、plan 和命令合同，再决定字段落点。建议按以下顺序验证：

1. 能否在 Coach turn 中稳定取得 `desired_outcome` 和 `player_problem`；
2. 能否把 measured / self_reported / observed / inferred / external 证据分开，并保留 refs；
3. 能否先生成 formulation，再在证据不足时提出一个 probe，而不是直接处方；
4. 能否用简洁白话输出“现象 -> 影响 -> 假设 -> 下一步 -> 复测”；
5. 能否把 learner response 写回 case，并触发 living plan 的版本化 adjustment；
6. 能否在用户目标变化、平台期、不可比数据和身体不适时停止自动强推；
7. 能否让 Coach 持续引用跨次档案，同时不把关系绑定到单个可删除 Analysis。

这些是设计验收条件，不是对当前施工顺序的替代。任何新增 schema、migration、状态、默认剂量、阈值或写命令，都必须回到对应的 active implementation plan 或由产品/架构负责人明确授权。

## 9. 最小验收清单

- 给定“我在真实对局第一发后经常修正，但训练分数还在涨”的主诉，Coach 能优先讨论迁移问题，而不是只报 benchmark；
- 给定同一指标的两种用户目标，Coach 能给出不同优先级或验证方式；
- 缺少目标视觉证据时，Coach 不把输入 trace 说成目标读取的直接测量；
- 用户报告疼痛/疲劳时，Coach 会记录并调整/暂停，而不是继续增加剂量；
- probe 后用户说 cue 让自己更紧，下一版处方会记录该反馈并改变策略；
- 复测不可比时，Coach 输出证据不足，不伪造成功或失败；
- 计划调整保留旧版本、原因、依据和新 verification targets；
- 跨次回访仍能找回用户目标、主问题、已尝试 cue、结果和未解决假设；
- 最终回复短、具体、像人在说话，并明确下一步动作和复测时机。

## 10. 研究材料与事实边界

本文总结公开服务和教学材料中的共同流程，不为任何教练、课程或训练法背书。公开材料经常省略失败案例、用户选择偏差和长期效果，因此只能作为 SOP 设计输入。Aiming Cookie 的指标定义、阈值、因果解释和训练剂量仍需在本产品数据与用户目标上单独校准。

