# Aiming Cookie 文档归档

> **状态：历史资料区，不是当前事实源。** 日常工作从 `docs/README.md` 开始，不要默认扫描本目录。

## 分类

- `completed/plans/`：已经完成的实施计划，保留验收与追溯证据；
- `frozen/plans/`：当前阶段冻结、不得施工的计划；
- `retired/plans/`：技术假设或产品边界已经失效、明确不得执行的计划；
- `retired/specs/`：已被 PRD、Architecture 或新 spec 取代的旧设计；
- `legacy/`：旧产品说明、旧战略和旧研究文档；
- `reviews/`：按日期保存的历史审阅快照；
- `history/`：从活跃进度文档拆出的逐日研发流水；
- `design-references/`：旧 prompt、mockup 和设计参考，不是可执行视觉事实源。

## 使用规则

1. 归档文件的状态只代表当时，不代表现在；
2. 归档内容与活跃文档冲突时，以 `docs/README.md` 列出的事实源顺序为准；
3. 不得把归档 plan 交给 executor；
4. 需要恢复旧结论时，先核对当前代码，再在活跃 spec/plan 中重新批准；
5. 归档保留原文件名，便于 git history 和全文搜索追溯。
