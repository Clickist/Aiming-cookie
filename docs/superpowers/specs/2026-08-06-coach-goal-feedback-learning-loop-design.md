# Coach Goal And Feedback Learning Loop Design

> **Status: active.** 点点于 2026-08-06 确认本合同并授权实施。

## 1. Purpose

Coach 必须先理解用户这次想改善什么，再决定现有 Analysis 里的哪个问题最值得处理。训练后的用户感受、执行情况和复测结果必须回到同一条 TeachingSession 与 Training Plan 链路，形成可追溯的调整依据。

本合同只补充现有 `Problem compiler -> TeachingSession -> Training Plan -> execution -> retest`。不新增第二套 session、feedback、plan、Registry 或鼠标目录，也不修改 Analysis 的计算、DTO、历史结果或页面显化。

## 2. Learner Context

`TeachingSession` 保存可选的 `learner_context`：

- `player_problem`：用户用自己的话描述的问题；
- `desired_outcome`：用户希望看到的可观察结果；
- `practice_intent`：`warm_up | practice | benchmark | main_game_transfer | unspecified`；
- `constraints`：时间、身体不适、设备或其它用户明确说明的限制。

这些字段只能来自用户明确表达或用户确认，不能从 Analysis、轨迹、Registry 或 Provider 自行推断。旧 session 缺少字段时归一化为空上下文，继续可读可写。

用户目标只改变 Coach 的问题优先级、解释和复测选择，不改变 Analysis issue、metric、evidence 或历史数据。同一 Analysis 在不同目标下可以有不同 Coach 优先级；没有目标时保持现有确定性排序。

## 3. Typed Evidence

Coach 内部证据区分 `measured`、`self_reported`、`observed`、`inferred` 与 `external`。每条证据保留面向用户的短文本和 bounded refs。类型只约束 Coach 如何表述，不升级来源强度。Registry 经验不得冒充对该用户的测量；Analysis projection 不消费这些新增字段。

## 4. Learner Response

一次执行后的结构化 `learner_response` 复用现有 execution `user_feedback` 事实，不建新表。它至少能表达 cue 是否容易理解、动作是否更容易控制、是否更僵/更难做/不舒服、是否疲劳或疼痛、是否愿意继续，以及用户补充说明。

用户可见问题使用自然语言，例如：“照这个练时，动作是更容易控制，还是手更僵、更难做或不舒服？”不得显示内部字段名或“cue 会不会让你更紧”之类表达。

旧自由文本 `user_feedback` 继续是合法输入。结构化信息只能来自用户明确反馈；疼痛、麻木、无力或持续不适进入现有停止路径。

## 5. Living Plan Adjustment

计划调整必须复用现有 `training_plan.adjust`、版本历史、owner scope、confirmation、evidence refs 和 verification targets。不得直接覆盖已保存 plan，也不得根据单次低分或模糊感受自动换计划。

只有存在 active/saved plan、用户明确接受调整方向、反馈或可比复测给出具体理由、新版本包含证据与验证目标、且产品确认通过时，Coach 才能调整。matched retest、near-transfer 和 main-game transfer 分开记录；aim trainer 改善不得写成“已经迁移到所有 FPS”。

## 6. Viscose Community Knowledge

Registry 新版本保留作者、来源、日期、适用范围和反例，吸收以下社区经验：

- 效率框架：必要动作量、动作连续性、现实可控速度三者平衡；不是绝对评分，linear clicking 等情境存在例外；
- tension budget：爆发后释放、手臂/手腕/指尖分担负荷；这是社区教学模型，只能用于解释、用户自述候选和可逆实验；
- 练习意图：热身、长期练习和 benchmark/test 不是一回事；避免无目的 autopilot，围绕已观察弱点选任务并复测；
- 难度：简单任务用于减少干扰和打磨动作，困难任务用于压力测试；短期掉分不自动等于练法失败。

“欠冲优于过冲”等经验保留其适用条件，不提升为所有任务的产品规则。外设只保留舒适度、摩擦、延迟、输入一致性等特征经验；具体型号不进入 Registry。现有鼠标数据与后置外设入口继续复用，Coach 未收到用户主动外设意图时不得推荐购买。

## 7. Compatibility And Presentation

- `analysis_result.v2`、Analysis Workspace、Analysis Data/family DTO、History 与 `current_training.v1` 字段保持不变；
- 旧 TeachingSession、旧 TeachingTurn、旧 execution row 和 Registry v1-v5 继续可读；
- 用户主诉、社区经验和外设候选只出现在 Coach，不显示为 Analysis 的确定性结论；
- 缺少旧目标或主游戏证据时显示证据缺口，不回填或重解释历史 Analysis；
- Provider 不能新增用户目标、身体事实、证据或计划调整理由。

## 8. Verification

- Python/TypeScript 对新旧 TeachingSession 与 TeachingTurn 兼容；
- 没有 learner context 时 problem compiler 排序与当前实现一致；有明确目标时只改变 Coach 优先级或复测说明；
- 旧自由文本反馈、skipped、不适停止、不可比复测路径保持；
- `training_plan.adjust` 仍需确认并只生成不可变新版本；
- Registry v6 Python/Node parity、v5 历史读取、来源与 claim ceiling 通过；
- Analysis/History/Analysis Data/current training 固定 DTO 回归不变。
