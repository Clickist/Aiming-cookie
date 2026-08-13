# Coach Problem-Hypothesis Diagnosis Design

> **Status: active.** 点点于 2026-08-06 确认本合同，并授权完成对应功能。

## 1. Purpose

Analysis 继续负责可追溯的测量、规则化观察和证据定位；Coach 负责把单个或多个观察组织成一个用户能理解的功能性主问题，并通过追问、低风险实验和复测逐步排除候选原因。Coach 不得停在指标复述，也不得把未测身体状态、认知原因或设备适配写成事实。

本合同补齐既有 `Knowledge Registry -> Coach context -> TeachingSession -> Training Plan -> retest` 链路中缺失的问题编译和软启动，不建立第二套 Registry、会话、计划、消息路由或诊断存储。

## 2. Responsibility Boundary

| 层 | 负责 | 不负责 |
|---|---|---|
| Analysis | 测量、质量状态、规则化观察、issue、metric、evidence 和限制 | 最终归因、用户问诊、设备推荐、训练关系 |
| Problem compiler | 在同一 family 内组合支持同一功能问题的信号，选择一个主问题，保留反例和缺失证据 | 跨 family 总分、身体/reading/设备因果判定、写入用户事实 |
| Coach | 用自然语言解释主问题、说明不确定性、最多给两个候选原因并问一个区分问题 | 暴露内部 schema/分数、同时推进多个问题、伪造用户回答 |
| TeachingSession | 保存当前主问题的教学阶段、用户确认后的候选、练习和复测状态 | 从 Provider 文本反推状态、替代 Profile 或 Training Plan |
| Knowledge Registry | 提供版本化机制、替代解释、反证、cue、实验、复测和来源 | 自动证明某个用户的原因 |

## 3. Functional Problem Groups

编译器只在可比、同 family 的 Analysis context 内聚合；显式选择的 issue 仍优先。首版功能问题组为：

- 到点与收尾控制；
- 动态点击的确认时机；
- 获取目标偏慢；
- 速度匹配与 movement reading；
- 变向响应与重新捕获；
- 持续跟随的修正负担；
- Switching 的转移与到达。

问题组不是新指标或新诊断规则。每个组只列出已存在的 issue signal / observation / metric 映射；没有 grounded issue 时不因原始数值、用户自由文本或模型猜测生成问题。跨多个 Analysis 时优先重复出现且有支持证据的同组问题；没有重复证据时按既有显式选择、可教学场景和 issue priority 排序。不同 family 不合成一个“瞄准总分”。

## 4. Compiled Problem Contract

每次只编译一个主问题，输出至少包括：

- `problem_id` 与面向用户的 `problem_label`；
- `evidence_strength`: `limited | supported | repeated`；
- 1-4 条 `supporting_evidence`，每条保留 context/analysis/issue/metric 来源；
- 0-2 条 `counterevidence`；没有可用反例时必须明确 `counterevidence_status=not_observed`，不能暗示已经检查；
- 一个 `primary_hypothesis`、最多两个 `alternative_hypotheses`；
- 一个 `discriminator_question` 或一个可逆、单变量的 `discriminator_experiment`；
- 既有 Registry、cue、dose、matched/near-transfer retest 引用（仅在当前知识和场景合同支持时）。

`limited` 表示只有单一或描述性迹象；`supported` 表示同一 Analysis 内有多个相互支持的 grounded 观察，或一个观察同时有合格反例检查；`repeated` 表示至少两个可比 Analysis 支持同一功能问题且未被反例推翻。证据强度不等于临床置信度，也不证明原因。

## 5. Coach Conversation Contract

Coach 的第一轮顺序固定为：

1. 用一句话说当前最值得先处理的功能问题；
2. 用 1-2 句说明哪些观察共同支持它，并说明证据强度；
3. 明确“这还不能确定原因”；
4. 提出一个主要可能性和最多两个替代解释；
5. 只问一个能区分候选的问题。

用户回答后，Coach 才能在既有 TeachingSession 中推进候选。无法唯一映射用户回答时继续问一个更窄的问题，不把近义词或否定回答误当确认。用户要求解释时先直接解释；用户明确继续或接受实验后，才进入 cue、单变量练习、执行确认和复测。复测不支持时必须降低或推翻原假设。

## 6. Body, Reading, And Peripheral Boundaries

- “握得太紧”只能在用户自述或可逆实验后作为候选；轨迹不能测得握力、肌肉激活、姿势或疼痛。
- “反应慢”必须区分目标变化后的响应、视觉/系统延迟、获取目标和重新捕获，不把一个延迟指标写成一般反应能力。
- `reading` 指持续提取方向、速度、加减速和变向线索；只有对应目标相对数据与质量 gate 可用时才作为候选，不等于反应时。
- “鼠标不合适”只能作为后置的设备适配假设。先询问舒适度、手型/握法自述、当前设备特征、症状是否跨任务出现，以及是否存在系统延迟；先给免费、可逆、单变量的排查，不主动推荐购买或具体型号。
- 疼痛、麻木、无力或持续不适立即停止实验；Coach 不提供医疗诊断。

## 7. Analysis Soft Start

当 Provider 状态为 `ready`，用户首次打开一个已完成 Analysis，且该 Analysis 能产生 grounded problem 时：

- 前端展开现有 Coach；
- 后端原子附加该 Analysis context，并创建 `initiator=system`、`trigger_ref=analysis:<id>` 的现有 agent run；
- 同一 owner + trigger 只成功创建一次，刷新、重试和多窗口并发均不得重复发言；
- system run 不写入 user message，不伪造用户说过任何内容，只写 Coach assistant message 与既有 run/event；
- 首轮只呈现问题、证据、不确定性和一个区分问题，不推进 TeachingSession phase；
- 用户回答或明确继续后，普通 user-initiated run 才能推进 TeachingSession。

Provider 不可用、Analysis 未完成、无 grounded problem、context 不可用或已有 active Teaching run 时 fail closed；Analysis 和 History 继续可用。

## 8. Data And Privacy

问题编译器只消费现有 bounded Coach context、Registry entry 与 owner-scoped Profile snapshot。它不接收 Raw trace、MP4、原始 CSV/protobuf、路径、credential 或跨 owner 数据。compiled problem 是 turn-local 派生合同；长期状态继续由 TeachingSession、Profile 与 Training Plan 承担，不新增平行问题数据库。

## 9. Verification

- 单元测试覆盖每个功能问题组、跨 family 不合并、显式 issue 优先、证据强度、反例和无问题 fail closed；
- Python/TypeScript 对同一 TeachingTurn 字段严格校验，Provider 不能扩大证据、原因、问题数或问题数；
- Registry Python/Node parity、历史版本可读与新版本 source/claim/experiment 约束通过；
- 并发软启动只产生一个 run、零 user message、一个 assistant message，并保持 session phase；
- 前端测试证明完成 Analysis 会触发一次软启动，普通打开/刷新不重复，Provider 不 ready 时不触发；
- 真实 Provider/Tauri、真实 KovaaK 与硬件表现仍是独立 field/release Gate，不由自动化测试伪装关闭。
