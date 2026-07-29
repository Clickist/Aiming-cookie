# Coach Guided Teaching Loop v1 - Design Contract

> 状态：active。本文冻结 Coach 从“解释分析”升级为“主动带用户完成一次训练与复测”的局部交互合同；实施仍只由 active plan 中被点点明确授权的单个 Task 推进。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`../../frontend-uiux-design.md`](../../frontend-uiux-design.md)。本合同展开 PRD 已定义的完整 Coach 闭环、长期训练计划和复测目标，不改变产品阶段或数据边界。
> 相关合同：[`2026-07-20-complete-coach-analysis-context-design.md`](2026-07-20-complete-coach-analysis-context-design.md)、[`2026-07-13-coach-product-commands-explanations-provider-design.md`](2026-07-13-coach-product-commands-explanations-provider-design.md)、[`2026-07-14-versioned-coach-knowledge-registry-design.md`](2026-07-14-versioned-coach-knowledge-registry-design.md)。

## 1. 目标与成功语义

Coach 的首要教学单位不是一篇报告或一张静态 playlist，而是一个可验证的小循环：

```text
理解目标与当前约束
  -> 观察可重复 pattern
  -> 提出一个主要候选解释和最多两个 alternatives
  -> 教一个 mental model 与一个 cue
  -> 用户正常接受则直接练；出现明确疑问或误解时只澄清一次
  -> 完成一个单变量练习 block
  -> 用户确认完成量、感受和不适
  -> matched retest
  -> 保留、降低或拒绝候选解释
  -> 安排 delayed / near-transfer 下一步
```

一次成功循环必须让用户知道：

- Coach 观察到了什么；
- 当前为什么只把某个原因当作候选假设；
- 这一组只改变什么；
- 什么结果支持或推翻当前解释；
- 完成后下一步是什么。

“给出训练建议”“生成训练计划”或“用户回复知道了”均不单独构成教学完成。

## 2. v1 范围与非目标

### 2.1 v1 范围

- 复用当前 Coach conversation、analysis context、Knowledge Registry、Training Plan、execution 和 retest store；
- 使用独立、owner/thread-scoped `TeachingSession` 保存当前 lesson 的受限过程状态，并为每轮生成不可变 `TeachingTurnContract`；
- 让 Coach 通过现有 owner-scoped product-command bridge 准备 plan item、execution 和 retest 写入；
- 复用现有结构化 confirmation UI，用户事实未经确认不得落库；
- 在当前会话中强制执行单问题追问、单 cue、单变量练习和复测更新；不把复述当作开始训练的门槛；
- matched immediate retest、delayed matched retest 与 near-transfer retest 在措辞和记录中保持不同含义；
- 无 Provider 时不伪装成 Coach，现有确定性 Analysis、History 和本地建议保持不变。

### 2.2 非目标

- 不在 v1 接入 handcam、任意文件附件、主游戏 VOD 分析或玩家移动遥测；
- 不增加外设商品目录、购买链接、联盟推荐或品牌排序；
- 不把身体、视觉、认知、握持、姿势、张力或疼痛变成确定性测量；
- 不要求前端新增页面、复杂卡片、表单式 intake 或新的 confirmation 交互；
- 不用提示词替代既有 grounding、owner scope、reachable refs、idempotency 或 confirmation 校验。

## 3. 对话式教学协议

### 3.1 Orient / Intake

- Coach 优先复用当前 Analysis、profile、active plan、历史比较和用户已确认资料；不得重复询问已有事实。
- 缺少会改变当前训练分支的信息时，每次只问一个最能区分候选原因的问题。
- 首批高价值问题限于：训练目标、可用时间、当前主观体验、疲劳/不适，以及“没看到变化 / 看到了但动作没跟上 / 动作过大”的可区分反馈。
- Intake 不是一次性长表单。没有分析上下文时可以给低风险通用方向，但不得生成伪精确剂量或身体归因。

### 3.2 Observe / Hypothesize

- Coach 先说明最稳定、可重复的 observation，再提出一个主要候选解释和最多两个 alternatives。
- reading、speed matching、张力、灵敏度、摩擦、疲劳和设备只能在对应证据边界内作为 candidate hypothesis。
- 相同抖动或反复修正不得自动解释为张力；没有用户报告、handcam、force/EMG 或单变量复测时不得说“检测到张力”。
- 每个候选解释必须带一个可执行的 discriminator：追问、条件对照或 matched retest。

### 3.3 Teach / Clarify when needed

- 每轮只教一个 mental model 和一个可在当前任务中执行的 cue。
- cue 优先描述目标/任务效果，例如“看到目标减速时让自己的移动也开始减速”，不直接要求未经测量的肌肉控制。
- Coach 讲清一个 cue 后默认直接进入练习准备；用户说“好”“明白了”“开始吧”不再触发理解检查。
- 只有用户明确提问、复述成另一个动作要求或暴露明显误解时，Coach 才围绕原 cue 澄清一次；澄清后继续练习，不要求再次复述，也不增加第二个 cue。
- 用户明确只要报告或解释时可以先回答，但不得把长报告冒充已完成教学。

### 3.4 Practice / Check-in

- 一个 block 只改变一个主要变量：cue、目标速度、变化密度、目标尺寸、距离/布局或暴露时长之一。
- Coach 必须说明 planned dose 的来源；没有用户 schedule、baseline 或知识支持时不得生成精确数字。
- 完成后 Coach 只收集最小必要用户事实：实际完成量、completed/partial/skipped 和主观体验。只有用户主动报告身体不适时才响应，不在普通训练回合主动询问或提示症状清单。
- Coach 可以预填 execution 写入，但必须展示将记录的事实并等待结构化确认；不得从沉默、分数或聊天语气推断完成。
- 用户主动报告疼痛、麻木、刺痛、无力或持续不适时，当前训练建议停止；用户可见回复只需自然说明“先别练这组，休息一下，别硬撑”，不作医疗判断，也不触发商品推荐。

### 3.5 Retest / Revise

- immediate matched retest 只判断 cue 是否改变当前表现，不称为学习或长期改善。
- delayed matched retest 使用相同 scenario、设置、metric/version 和关键条件，验证保留；near-transfer 只改变一个关键任务维度。
- Coach 可以预填 retest 写入，但 comparability、结果和用户反馈必须由确定性 Analysis 或用户结构化确认提供。
- 复测后 Coach 必须明确选择 `retain / lower / reject` 当前候选解释之一，并说明依据；没有可比结果时保持 unresolved。
- 新 TeachingSession 发起的 `training_plan.retest.record` 复用既有 `result` 字段，但只允许写入版本化结果 `coach_retest_outcome.v1:improved | unchanged | worsened | mixed_or_inconclusive`。历史自由文本继续可读，但不得被追溯解释为这四种结果。
- 只有本次 confirmation 的 idempotency 结果精确指向的 retest fact 才能推进教学状态；不得读取同一 item 的“最新一条”代替本次确认。`comparable + improved -> retain`，`comparable + unchanged/mixed_or_inconclusive -> lower`，`comparable + worsened -> reject`；`not_comparable / unavailable / 未识别旧结果` 保持 unresolved，不产生调整决定。
- immediate、delayed 与 near-transfer 的决定只回答各自范围：immediate 不称为学会，delayed 才讨论保留，near-transfer 不外推到主游戏。`retain / lower / reject` 先更新教学假设；本合同不把它伪装成 Training Plan item 已经被修改。
- 当复测同时引用按 `[baseline, current]` 排列的两条 Analysis 时，backend 必须先复用现有 `compare_analysis_results()` 判断两次是否使用同一场景、设置、metric/version、单位、校准和质量条件。可比性与“变化是否足够大”是两件事：无版本化 metric-change policy 时，非零 delta 只能记录为 `mixed_or_inconclusive`，不得仅凭正负号或统一百分比称为进步/退步；数值相同只表示本次没有观察到变化。单条 Analysis 或用户主观事实继续走结构化确认，不伪装成确定性比较。
- 复测决定必须实际更新现有 Training Plan item：确认 `completed / partial` 执行后 item 进入 `active`，`skipped` 或用户主动报告不适不激活 item；`retain` 保持 `active` 并沿用 item 已保存的 review date、matched 与 near-transfer retest；`lower` 将 item 放回 `planned`，让下一轮优先检查其他解释；`reject` 将 item 置为 `cancelled`。缺少 metric-change policy 造成的 `mixed_or_inconclusive` 保持 unresolved，不得降低 item。创建替代 item 仍需一个完整、grounded 的处方并走既有 `training_plan.item.add` confirmation；没有完整备选时不得由 Provider 临场编造。
- 主游戏表现只作为独立 transfer evidence。v1 没有主游戏输入时不得把 aim-trainer improvement 表述成游戏能力提升。

### 3.6 数值展示与教学输出

- ratio 可以以保留原语义的数学等价形式展示；例如有明确来源与单位的 ratio 可以显示为百分比。该转换不是教学错误，也不单独触发 repair；
- Coach 不得把数值格式转换扩展为未提供的发生频率、比较结论、好坏评价或机制因果。没有 baseline、target band、可比对照或知识/用户证据时，数值不能被称为“较好”“偏高”或某身体/认知机制的证明；
- `TeachingTurnContract` 决定本轮允许的 observation、candidate、cue、retest intent、用户问题和 product command。Provider 可以把这些内容说自然，但不得添加第二个问题、未批准的 dose、原因或状态转移；
- 本地 teaching output validator 必须验证 Provider 输出是否仍在 contract 内。违规时使用同一 contract 的用户可读 renderer/fallback，而非继续要求 Provider 自我修复。

## 4. 产品命令与确认边界

当前 backend 已存在以下 explicit-user-fact commands，v1 将其加入 Pi Coach product-command allowlist：

- `training_plan.item.add`；
- `training_plan.execution.record`；
- `training_plan.retest.record`。

规则：

- 三者均是 write command，使用稳定 turn-local idempotency key；
- Coach 调用只创建 `needs_confirmation`，不得携带 `authorization`、`confirmation_ref`、owner、risk 或其它可信字段；
- 只有现有 UI/backend confirmation decision 可以执行；拒绝或过期后不得静默重试；
- 用户明确陈述事实仍不等于模型可直接写库；Coach 应预填并让用户确认，而不是要求用户去独立表单重复输入；
- confirmation 投影必须从本次 canonical command 的已保存参数生成具体事实摘要。item、execution 与 retest 分别说明准备加入的练习、实际完成情况、复测范围/结果和确认后的计划变化；不得显示内部 result token、对象 ref、路径、secret，也不得使用一条泛化的“执行 Coach 操作”代替事实。
- 本合同冻结确认内容与边界，不冻结逐字台词。Provider 不负责生成 confirmation message，前端继续展示 backend 投影的 `impact.message`。
- 工具失败时保留对话和草稿，明确没有完成记录，不伪造成功。

## 5. TeachingSession 与确定性教学器

v1 选择“持久教学状态 + 现有正式训练事实”的最小垂直切片：

- `TeachingSession` 以 owner / primary Coach thread 为归属，保存 lesson 的 phase、version、active run、pending confirmation、受限 lesson state 和暂停原因；它不是新的 profile，也不替代 Training Plan；
- `TeachingTurnContract` 在本地 planner 中根据 session、当前用户输入、bounded context 和已确认事实生成，并作为该 Agent run 的不可变 snapshot 保存；
- 新 lesson 的默认阶段是 `intake -> hypothesize -> teach -> practice_ready -> await_execution_confirmation -> retest_ready -> await_retest_confirmation -> revise -> follow_up`。`await_teach_back` / `teach_back_repair` 只保留给旧 contract 重放，不再由新 planner 生成；用户在 `practice_ready` 明确提问或暴露误解时临时回到 `teach` 澄清一次。任意阶段可进入 `paused`，用户主动报告不适时进入 `stopped_for_discomfort`；
- renderer 负责阶段问题、确认提示、无证据与不可比复测 fallback。validator 负责拒绝 contract 外的追问、归因、dose、状态宣称和内部术语；Provider 的角色仅限表达已批准的 observation/candidate/cue；
- Training Plan item、execution 和 retest 保持既有 persistent fact。planner 在下一 turn 前从现有 confirmation / fact store reconcile 已确认结果；拒绝、过期或不可比不静默推进，分别保持 paused / unresolved；
- message history 继续提供对话语境，但不是阶段真相来源；重试重放原 `TeachingTurnContract`，并发 run 必须通过 session version / active-run guard fail closed。
- `TeachingTurnContract.allowed_command` 只约束三种教学事实写入。Analysis、History、导航、查询和其它既有 product command 继续遵守各自权限/确认合同，但不推进或改写 TeachingSession phase；不得因为引入教学状态机而让 Coach 失去已有产品操作能力。

## 6. 验收与失败语义

自动化至少覆盖：

- 三个 explicit-user-fact commands 同时存在于 TypeScript/Python 命令集合；
- 三者按 write command 生成幂等键，并拒绝模型提供 authority、confirmation、路径、secret 或 raw payload；
- Coach-inferred 写入只能返回 `needs_confirmation`；confirm/reject/expired/duplicate 保持现有语义；
- prompt 明确要求 observation、alternatives、single-variable practice、按需澄清、check-in、matched/near-transfer 和 hypothesis revision；
- session/planner 测试证明只允许当前 phase 的一个动作与一个问题；contract 外的 Provider 输出回退到本地 renderer；
- 没有分析/知识/用户 schedule 时仍禁止伪精确剂量；
- simulated turn 能完成“解释 -> 直接 practice -> execution confirmation -> retest confirmation -> revision”，并证明普通接受不会触发理解考试、明确误解只澄清一次；
- 真实 Provider focused matrix 至少覆盖无上下文、有 Analysis、用户拒绝记录和证据不足四条路径。

失败时：

- 无可用分析：降级为 intake 或通用低风险指导；
- knowledge/query 不可用：停止机制归因，给出下一步证据需求；
- confirmation 不可用：保留待确认状态，不声称已记录；
- retest 不可比：记录为 not comparable/unavailable，不调整正式 profile metric；
- 用户主动报告不适：自然地停止当前训练建议，不主动展开症状清单或医疗说明。
