# Implementation Plan 状态入口

> 本页只回答“现在是否有可交给 executor 的实施合同”。产品方向看 [`../../PRD.md`](../../PRD.md)，架构边界看 [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)，当前优先级看 [`../../ROADMAP.md`](../../ROADMAP.md)。

## Active

- [`2026-07-13-reflek-capability-adoption.md`](2026-07-13-reflek-capability-adoption.md)：RefleK 能力采纳、input-native 分析、Run/trace correctness、History/evidence replay 与 Coach 结构化接入；Task 1–6A 已完成，Task 6B 与任何 v1 Benchmark UI 均未获授权，必须等待点点明确指定。
- [`2026-07-13-coach-productization-provider-management.md`](2026-07-13-coach-productization-provider-management.md)：指标到训练解释链、input-native 核心指标、Pi provider/model/auth、Coach 用户级产品命令与 Provider Settings；点点已授权按建议顺序推进，每次只执行一个 Task。
- [`2026-07-13-frontend-product-reconstruction.md`](2026-07-13-frontend-product-reconstruction.md)：从产品、UI/UX 与视觉合同重建正式前端；Task 1 已完成 prototype 删除与 adapter 边界保护。Task 2–7 尚未获得具体 Task 授权，必须等待点点明确指定。

在新 plan 被审阅并明确标记为 active 之前，executor 不得依据 PRD、Architecture、Roadmap、spec 或归档 plan 自行拆解实施任务。

## Proposed

当前没有其他 proposed plan。

## Frozen

- [`../../archive/frozen/plans/2026-07-12-desktop-local-first-vertical-slice.md`](../../archive/frozen/plans/2026-07-12-desktop-local-first-vertical-slice.md)：Desktop local-first vertical slice；当前为 No-Go，需先解决 `docs/ROADMAP.md` 中的前置 Gate，再重新审阅。

Frozen plan 不得执行，也不得仅通过口头指令跳过其冻结条件。

## Completed

- [`../../archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md`](../../archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md)：Windows Steam 多库 KovaaK bounded 自动发现与 Desktop launch-token 子进程隔离；Task 1–2 已完成。
- [`../../archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md`](../../archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md)：terminal Analysis 的 SQLite v13 transient tombstone、commit-first workspace cleanup、startup reconciliation 与 API/Coach deletion invariants；Task 1–3 已完成。
- [`../../archive/completed/plans/2026-07-15-windows-developer-runtime-compatibility.md`](../../archive/completed/plans/2026-07-15-windows-developer-runtime-compatibility.md)：Windows Python↔Pi source 启动、Tauri compile-time ICO 与 Python/Coach/Pi AI/frontend adapter/Tauri MSVC 自动化 Gate；Task 1–2 已完成，正式 frontend 与真实设备 Gate 保持未闭合。
- [`../../archive/completed/plans/2026-07-14-versioned-coach-knowledge-registry.md`](../../archive/completed/plans/2026-07-14-versioned-coach-knowledge-registry.md)：canonical Coach Knowledge Registry、Flicking/Tracking/身体与设置知识迁移、Python/TS 共用检索、Pi bridge 解耦与 refs-only trace E2E；Task 1–6 已完成。
- [`../../archive/completed/plans/2026-07-12-kovaak-runs-and-raw-input.md`](../../archive/completed/plans/2026-07-12-kovaak-runs-and-raw-input.md)：KovaaKRun 自动导入与 Windows Raw Input 基础；Task 2–3 已完成，输入原生算法接入与用户路径属于后续 active plan。
- [`../../archive/completed/plans/2026-07-12-kovaak-local-ingestion.md`](../../archive/completed/plans/2026-07-12-kovaak-local-ingestion.md)：KovaaK Stats/Performance 本地发现与解析；Task 1 已完成。
近期已完成的 implementation plans 位于 [`../../archive/completed/plans/`](../../archive/completed/plans/)。它们只用于追溯已批准范围、验收方法和历史决策，不是当前施工入口。

## 使用规则

1. 只有本页列为 **active** 的 plan 才可能交给 executor；
2. 点点还必须明确指定该 plan 中的一个 Task；
3. executor 每次只执行一个 Task，并遵守其 Allowed files、Tests first、冻结决策和 Stop rule；
4. completed、frozen、retired 或其他 archive plan 均不得直接执行；
5. 需要恢复旧 plan 时，必须先核对当前代码和上游事实源，再生成或重新批准 active plan。
