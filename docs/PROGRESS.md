# Flicking 模块进度

> 最后更新：2026-07-12（Coach 结构加固 plan Task 1–4 收口）

## 当前交付罗盘（2026-07-11）

### 发布裁决

- **长期目标**：`docs/PRD.md` 定义的完整 Desktop hybrid 产品，范围不删减。
- **2026-07-13 至 2026-07-19**：只发布受控环境中的 **flicking-only 内部技术预览**，不称为完整产品 v1。
- **当前状态**：完整产品 v1 `No-Go`；内部技术预览完成 `docs/ROADMAP.md` §5 全部 P0 Gate 后才 `Go`。

### 当前能力基线

**已核验**：

- 全仓单一入口：**247 passed，1 skipped**（2026-07-12 结构加固 Task 4 回归）；
- 分开核验：core **116 passed**；`webapp/tests` **131 passed，1 skipped**（含 coach hardening 与 Pi 路由分支）；
- 仓库真实 MP4 + Stats CSV E2E：上传 → enqueue → worker → CV → deterministic report → done 已通过；
- frontend `tsc --noEmit` 与 production build 已通过（Task 5，2026-07-12；Task 4 回归再次 `tsc --noEmit` 通过）；
- **Pi assessment/Spike 已完成**：`spikes/pi-coach-runtime/` 21 tests；assessment **CONDITIONAL GO** 已裁决（`docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`）。
- **线 A 常驻 Coach 数据归属**：schema v2、`coach_store`、删除不级联抹消息、`/coach` API+页面、相关 pytest — **已完工**（Task 1–5）。
- **线 B Pi Coach runtime 薄切片（Task 1–4 代码 + Task 5 回归）**：
  - `third_party/pi/` vendored（冻结 commit + `PROVENANCE.md`）；
  - `webapp/coach-runtime/` 单轮 Pi turn（产品 system prompt、无 coding tools、Node 测试）；
  - `webapp/backend/coach_runtime.py` subprocess 桥 + `coach_runtime_turn.v0`；
  - `POST /api/coach/primary/messages`（及 session chat 兼容）默认 **`COACH_RUNTIME=pi`**；失败时 **`COACH_RUNTIME_FALLBACK_PYTHON=1`** 回退 `chat_with_coach`；
  - **未做**：完整云代理账单、长期 Pi daemon、Desktop 沙箱、browser E2E gate。
- **Coach 结构加固（`2026-07-12-coach-structure-hardening.md` Task 1–3 + Task 4 回归）**：
  - schema **v3**：`legacy_chat_message_id`、单一 `migrate_session_legacy_messages`、幂等迁移；
  - `delete_session`：**单事务**（lock → migrate → mark refs → delete rows；文件删除仍在 commit 后）；
  - **`coach_engine.py` + `coach_service.py`**：Pi/Python 引擎与一轮编排迁出 routes；`COACH_RUNTIME` + fallback 行为与现测一致；
  - **`routes.py` 约 572 行**（coach 大段 if pi/python 已搬出；目标 <600 已达成）。

**当前阻塞**（预览仍 No-Go）：

1. 线 A / 线 B / 结构加固等工作区改动 **可能仍待 commit**；需落盘后重跑 pytest 固化基线；
2. 上传仍整文件读内存，且没有显式 per-session workspace、无自动 TTL、主动删除、quota/orphan 等完整文件生命周期；
3. `X-User-Id` 不是可信身份边界；
4. 无 supervisor、health/readiness、structured logs 和 browser E2E release gate；
5. Desktop 只有研究，无 shell、sidecar、IPC、installer、签名或更新工程；
6. 有 LLM key 时 `/coach` 真机一轮 **未在本 Task 强制验收**（无 key 不阻塞；有 key 建议手工记一条到本文件）。

### 当前执行顺序

1. **P0 线 A**：常驻 Coach 数据归属 — **已完成**。
2. **P0 线 B 薄切片**：`2026-07-12-pi-coach-runtime-integration.md` Task 1–5 — **已完成**。
3. **P0 结构加固**：`2026-07-12-coach-structure-hardening.md` Task 1–4 — **已完成**（文档收口于本日）。
4. **下一队列**（各需独立 plan）：vendor/runtime 裁剪、Pi sidecar、session workspace、artifact lifecycle、health + E2E gate — 见 `docs/superpowers/plans/README.md`。
5. workspace/streaming/file lifecycle → 可信访问与运行基线 → release gate；
6. **P1 / P2**：见 Roadmap。

### 2026-07-12 交接

- **下一小刀**：结构加固已收口；按 plans README「尚待独立 plan」择一开工（优先顺序由点点定：vendor 裁剪 / Pi sidecar / workspace / lifecycle / health+E2E）；**不要**在未授权时扩 scope（daemon 加厚、云账单、Desktop 沙箱）。
- 旧 persistent-coach-migration plan 仍不得重复执行。

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