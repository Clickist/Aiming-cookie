---
name: teaching
description: 用户想要系统性训练、跟课练习或继续上次的教学（"带我练""继续上课""按课走"）时加载。规定教学闭环的状态维护、阶段推进纪律和禁止事项。
---

带课教学闭环（guided teaching）

用一个持久的教学状态带用户走完整闭环：问诊 → 假设 → 讲解 → 复述检查 → 单变量练习 → 执行 → 复测 → 修订。状态只有一份文件，每步推进都通过 teaching_session.update 落盘。

1 状态文件

教学状态存在 teaching/session.json，每个用户只有这一份。开始带课前先 read 它：

- phase — 当前阶段。取值：intake（问诊）、hypothesize（提出假设）、teach（讲解）、await_teach_back（等用户复述）、teach_back_repair（纠正复述）、practice_ready（给出单变量练习）、await_execution_confirmation（等练习完成）、retest_ready（给出复测）、await_retest_confirmation（等复测完成）、revise（按复测结果修订）、follow_up（后续跟进）、paused（暂停）、stopped_for_discomfort（用户不适，停止）。
- lesson — 当前课的受限字段：observation（观察到的现象）、hypothesis（候选解释）、cue（唯一注意点）、single_variable（这组只改的变量）、practice_refs（引用，如 analysis:N、场景名）。
- completed_lessons — 已完成课程历史。
- paused_reason — 暂停原因（可空）。

文件不存在就是还没开过课；第一次 update 必须以 phase 为 intake 创建。

2 推进：teaching_session.update

每一步推进都用 run_product_command 调 teaching_session.update，参数只有 updates: { phase?, lesson?, paused_reason? }。写入口会校验阶段转移和字段白名单，被拒绝的更新不会落盘：

- 主线按序推进：intake → hypothesize → teach → await_teach_back → practice_ready → await_execution_confirmation → retest_ready → await_retest_confirmation → revise → follow_up。teach 后用户直接说"明白了、开始吧"时可以跳过复述进 practice_ready。
- 只允许相邻或回退一步：发现没讲清可以回 teach；复述有误进 teach_back_repair，纠正后回 await_teach_back；练习、复测等待中可以退回 practice_ready 或 retest_ready 重来。
- 不许跳阶段。例如 intake 直接进 practice_ready 会被拒绝，返回 invalid_teaching_transition。
- 暂停：{ phase: "paused", paused_reason: "原因" }。恢复：{ phase: "回到的阶段", paused_reason: null }。
- 用户报告身体不适时用 { phase: "stopped_for_discomfort" }，先让他休息；之后从 intake 重新开始。
- 从 revise 或 follow_up 回到 intake 会把当前课归档进 completed_lessons 并清空 lesson，开始新课。lesson 是按字段合并，不是整体替换。

3 每个阶段做什么

- intake：一次只问一个最能区分候选解释的问题。把观察到的现象写进 lesson.observation。
- hypothesize：基于分析和问诊提出候选解释，写进 lesson.hypothesis。措辞保持"可能、待验证"——它还不是事实。
- teach：讲一个 cue 和一个心智模型，写进 lesson.cue，并确定这组只改的 single_variable。
- await_teach_back / teach_back_repair：让用户用自己的话复述；复述有误先纠正，再继续。
- practice_ready：给出单变量练习，把引用写进 practice_refs。练习、执行、复测的正式记录仍走 training_plan.* 命令，教学状态不替代训练事实。
- await_execution_confirmation / await_retest_confirmation：等用户自己报告完成，不要替他确认。
- retest_ready：按同条件（或近迁移一个条件）安排复测。复测只回答"这条 cue 有没有帮助这一轮"，不等于已经学会。
- revise：按复测结果修订方向：支持就继续用，不明显支持就往后放。之后 follow_up 跟进，或回 intake 开新课。

4 纪律（禁止事项）

- 不替用户宣称已完成：没有用户的明确陈述，不把练习、执行或复测写成已完成，不宣称"你已经掌握"。延迟复测才检查保留。
- 不把候选机制写成测量事实：hypothesis 永远是待验证的解释，只有分析和复测结果能支持或推翻它。训练器里的改善不等于主游戏提升。
- 一次只问一个问题；一组练习只改一个变量；没有可靠来源时不编造次数或阈值。
- 不绕过 teaching_session.update 改状态：不要直接用 write 写 teaching/session.json。
