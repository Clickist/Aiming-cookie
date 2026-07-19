# Active Spec 状态入口

> 本目录只保留仍在生效的局部设计合同。产品定义以 [`../../PRD.md`](../../PRD.md) 为准，稳定系统边界以 [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) 为准，桌面 IA 与交互关系以 [`../../frontend-uiux-design.md`](../../frontend-uiux-design.md) 为准。

## Active

- [`2026-07-13-kovaak-run-trace-lifecycle-design.md`](2026-07-13-kovaak-run-trace-lifecycle-design.md)：KovaaK source pairing、Raw Input buffer、trace attach、reconciliation 与稳定引用合同。
- [`2026-07-17-automatic-run-capture-design.md`](2026-07-17-automatic-run-capture-design.md)：Raw + 300 秒 KovaaK 窗口硬件编码回放缓冲、Stats/Performance 事后切 Run、normal/timescale 永久 MP4、暂停局 fail-closed、单局/多局选择、手动 fallback 与分类存储管理合同。
- [`2026-07-13-analysis-evidence-coach-context-design.md`](2026-07-13-analysis-evidence-coach-context-design.md)：三种 input mode、AnalysisResult v2、evidence provenance 与 Coach allow-list context。
- [`2026-07-13-coach-product-commands-explanations-provider-design.md`](2026-07-13-coach-product-commands-explanations-provider-design.md)：Coach 用户级产品命令、指标到训练解释链、Provider/model/auth Settings 与 secret 边界。
- [`2026-07-13-frontend-product-reconstruction-design.md`](2026-07-13-frontend-product-reconstruction-design.md)：正式前端路由、页面职责、状态矩阵、Desktop/Web 差异、Coach shell、Preview/Benchmark/ownership 边界与 prototype 重建合同。
- [`2026-07-14-versioned-coach-knowledge-registry-design.md`](2026-07-14-versioned-coach-knowledge-registry-design.md)：Coach canonical Knowledge Registry、版本/source/claim/limitation、Flicking/Tracking/身体候选知识、确定性检索与历史引用合同。
- [`2026-07-16-analysis-deletion-reconciliation-design.md`](2026-07-16-analysis-deletion-reconciliation-design.md)：terminal Analysis 的 SQLite logical delete、Coach 保留、transient cleanup tombstone 与 managed workspace reconciliation。

此前关于 IA、常驻 Coach 与 Pi Coach runtime 的耐久结论，已经分别吸收到 PRD、Architecture 和 frontend UI/UX 合同。旧文件位于 [`../../archive/retired/specs/`](../../archive/retired/specs/)，仅用于历史追溯，不得作为当前实施授权。

## Proposed

当前没有其他 proposed spec。

## 新 spec 的边界

新 spec 只应冻结一个尚未由上游文档定义清楚的局部合同，例如：

- 单一交互流或状态机；
- 一个跨层接口的输入、输出和失败语义；
- 一个迁移、恢复或删除流程的原子性要求；
- 一个明确受限的 UI/UX 局部决策。

新 spec 不得：

1. 复制 PRD、Architecture 或 UI/UX 文档的大段正文；
2. 修改产品目标、阶段范围或系统边界而不先更新对应上游事实源；
3. 混入施工顺序、完成状态或测试流水；
4. 在没有 active implementation plan 的情况下直接授权 executor 编码。
