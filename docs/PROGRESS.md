# Flicking 模块进度

> 最后更新：2026-07-11（晚间对账）

## 当前交付罗盘（2026-07-11）

### 发布裁决

- **长期目标**：`docs/PRD.md` 定义的完整 Desktop hybrid 产品，范围不删减。
- **2026-07-13 至 2026-07-19**：只发布受控环境中的 **flicking-only 内部技术预览**，不称为完整产品 v1。
- **当前状态**：完整产品 v1 `No-Go`；内部技术预览完成 `docs/ROADMAP.md` §5 全部 P0 Gate 后才 `Go`。

### 当前能力基线

**已核验**：

- 全仓单一入口：218 passed，1 skipped（文档值；工作区有大量未提交改动，以提交后重跑为准）；
- 分开核验：core 116 passed；Web backend 102 passed，1 skipped；
- 仓库真实 MP4 + Stats CSV E2E：上传 → enqueue → worker → CV → deterministic report → done 已通过；
- frontend type-check 和 production build 已通过；
- **Pi assessment/Spike 已完成**：`spikes/pi-coach-runtime/` 21 tests；assessment **CONDITIONAL GO** 已裁决（`docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`）。

**当前阻塞**：

1. 当前 Coach chat 仍归属 analysis session，删除 session 仍级联删除消息 — **线 A plan 已激活，正在施工**；
2. 上传仍整文件读内存，且没有显式 per-session workspace、无自动 TTL、主动删除、quota/orphan 等完整文件生命周期；
3. `X-User-Id` 不是可信身份边界；
4. 无 supervisor、health/readiness、structured logs 和 browser E2E release gate；
5. Desktop 只有研究，无 shell、sidecar、IPC、installer、签名或更新工程；
6. **工作区 ~85 项未提交改动**（已完成 P0 代码 + 文档搬家 + Spike），需分批落盘以免基线漂移。

### 当前执行顺序

1. **P0 线 A（优先）**：常驻 Coach **数据归属**（SQLite + 删除语义 + `/coach`；runtime 暂留 Python `chat_with_coach`）— plan：`2026-07-11-persistent-coach-data-ownership.md`；
2. **P0 线 B（并行可写 plan，不堵线 A）**：Pi `third_party` 接管 + sidecar；
3. workspace/streaming/file lifecycle → 可信访问与运行基线 → release gate；
4. **P1 / P2**：见 Roadmap；P0 结束前冻结横向产品扩张。

### 2026-07-11 范围裁决与后续交接

- **最小 History P0**：列表、状态/摘要、详情回看、仅删除 done/failed；趋势、对比、筛选和 export/import 后移到 P1 — **代码侧已具备（待提交）**。
- **Coach 拆分**：产品 P0 = 数据归属（线 A，已开工）；Pi runtime 接管 = 线 B（CONDITIONAL GO，另写 plan）。旧 `docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md` 仍不得执行。
- **下一小刀**：**线 A Task 1**（schema v2 + store + 测试）。

### 执行模型分级

- **可交给较弱模型（已有精确 plan 时）**：pytest collection、health/readiness、小范围回归测试、已冻结规则后的默认路由或 UI 空状态。
- **可施工但必须由强模型先冻结合同**：session workspace、streaming upload、artifact 删除、无自动 TTL/orphan/quota、structured logging、browser E2E。
- **仅强模型裁决/规划**：Pi 源码纳入与产品化删改边界、Coach schema/migration/旧数据兼容、可信 owner 边界、quota/低磁盘阈值、Web/Desktop 路线。

Fast/executor 只能执行已批准 plan 的一个明确 Task。开工前必须回显 Task、Allowed files、Tests first、Frozen decisions 和 Stop rule；代码与 plan 不一致、需要扩大范围、测试不能运行或出现新 schema/default/migration 决策时立即停止。完成后报告 changed files、验证命令、未运行检查、偏差和 `git status`，不得自动继续下一 Task。

### 文档职责

- `docs/PRD.md`：产品目标、完整范围和阶段事实源；
- `docs/ARCHITECTURE.md`：系统边界、数据合同和演进顺序；
- `docs/ROADMAP.md`：发布定义、P0/P1/P2、2–4 周路线和 Gate；
- 本文件：当前状态和下方历史研发记录；
- `docs/design-system.md`：前端视觉治理。

## 历史记录

2026-06-27 至 2026-07-10 的逐日研发流水已移至 `docs/archive/history/PROGRESS-2026-06-27-to-2026-07-10.md`。该归档只用于追溯，不参与当前决策。
