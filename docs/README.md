# Aiming Cookie 文档入口

> **用途：唯一日常文档导航。** 新会话先读本页，不扫描 `docs/archive/`。

## 事实源顺序

发生冲突时，严格按以下顺序裁决：

1. [`PRD.md`](PRD.md)：产品目标、阶段边界和用户语义；
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)：已定型的系统边界、依赖方向和稳定合同；
3. 当前代码、测试和真实运行结果：判断已经实现什么；
4. [`ROADMAP.md`](ROADMAP.md)：当前施工顺序、优先级和 Go/No-Go Gate；
5. 活跃 spec / implementation plan：对上游决策的局部展开。

下游文档不得反向覆盖上游。发现冲突时，先修正上游或明确废止下游，不允许并存两套答案。

## 日常必读

| 文档 | 回答的问题 |
|---|---|
| [`PRD.md`](PRD.md) | 我们要做什么、当前阶段做什么、不做什么？ |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 系统怎么分层、哪些边界已经定型？ |
| [`ROADMAP.md`](ROADMAP.md) | 接下来按什么顺序施工、什么条件才能发布？ |
| [`PROGRESS.md`](PROGRESS.md) | 当前已经完成什么、还卡在哪里？ |
| [`design-system.md`](design-system.md) | 前端视觉值和组件治理以什么为准？ |

## 活跃参考

- [`deployment-guide.md`](deployment-guide.md)：当前香港 VPS 路线的部署研究与 checklist；真正部署前仍须核实价格、政策和供应商状态。
- [`aim-kinematics-research.md`](aim-kinematics-research.md)：flicking 运动学研究底座。
- [`coach-theory-foundation.md`](coach-theory-foundation.md)：教练、反馈和技能习得理论底座。
- [`coach-prescription-manual.md`](coach-prescription-manual.md)：诊断信号到处方的规则参考。
- [`coach-community-frontier.md`](coach-community-frontier.md)：时间敏感的社区材料，只用于解释和处方理由，不作为诊断规则事实源。
- [`superpowers/specs/README.md`](superpowers/specs/README.md)：当前有效的局部设计说明。
- [`superpowers/plans/README.md`](superpowers/plans/README.md)：当前可执行施工计划及其状态。

## 归档边界

[`archive/`](archive/) 保存已完成计划、冻结/退役方案、旧研究、历史 review 和逐日研发记录。归档内容：

- 可以用于追溯遗留问题；
- 不能作为当前产品、架构或施工依据；
- 不应被新 agent 默认加载；
- 若其中的结论需要恢复，必须先重新核对 PRD、Architecture 和当前代码，再写回活跃文档。
