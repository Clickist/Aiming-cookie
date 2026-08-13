# Aiming Cookie 文档归档

> **状态：历史资料区，不是当前事实源。** 日常工作从 [`../README.md`](../README.md) 开始，不要默认扫描本目录。

## 分类

- [`completed/plans/`](completed/plans/)：已经完成的实施计划，保留批准范围、验收方式与追溯证据；
- [`frozen/plans/`](frozen/plans/)：前置条件未满足或当前阶段明确 No-Go 的计划；
- [`retired/plans/`](retired/plans/)：技术假设或产品边界已经失效、明确不得执行的计划；
- [`retired/specs/`](retired/specs/)：已被 PRD、Architecture、UI/UX 文档或新 spec 吸收/取代的旧设计；
- [`retired/`](retired/) 下的其它文档：已退役的设计交接或说明材料，仅作历史参考，不构成当前入口或执行授权；
- [`legacy/`](legacy/)：旧产品说明、旧战略和旧研究文档；
- [`reviews/`](reviews/)：按日期保存的历史审阅快照；
- [`history/`](history/)：从活跃进度文档拆出的逐日研发流水；
- [`design-references/`](design-references/)：旧 prompt、mockup 和设计参考，不是可执行视觉事实源。
- [`history/2026-08-12-coach-architecture-handoff.md`](history/2026-08-12-coach-architecture-handoff.md)：Coach 直连 Node sidecar 的架构交接与 E2E 背景，仅供追溯；当前启动流程和状态分别以 [`../DEVELOPMENT.md`](../DEVELOPMENT.md) 与 [`../PROGRESS.md`](../PROGRESS.md) 为准。

`docs/superpowers/assessments/` 中的 assessment 是调研证据和某一时点的判断，不是产品、架构或当前实现 authority；耐久结论必须吸收到对应活跃事实源后才生效。

## 使用规则

1. 归档文件的状态只代表当时，不代表现在；
2. 归档内容与活跃文档冲突时，以 [`../README.md`](../README.md) 列出的职责边界和事实源为准；
3. 不得把 completed、frozen、retired 或其他归档 plan 交给 executor；
4. 需要恢复旧结论时，先核对当前代码，再在对应活跃事实源、spec 或 plan 中重新批准；
5. 归档尽量保留原文件名，便于 git history 和全文搜索追溯；
6. 逐日进度快照使用 `history/PROGRESS-YYYY-MM-DD-<topic>.md` 命名；历史链接应指向实际归档位置，不保留失效的 active 路径；
7. 活跃文档可以链接归档作为证据，但不得把归档内容复制回来形成第二套事实源。
