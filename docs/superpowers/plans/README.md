# Implementation Plan 状态入口

> 本页只回答“现在是否有可交给 executor 的实施合同”。产品方向看 [`../../PRD.md`](../../PRD.md)，架构边界看 [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)，当前优先级看 [`../../ROADMAP.md`](../../ROADMAP.md)。

## Active

- [`2026-08-01-web-mock-review-cli-gates-v1.md`](2026-08-01-web-mock-review-cli-gates-v1.md): 点点已确认 Web Mock 审核 -> Web 真实联调 -> Tauri 命令式验收的施工顺序；当前只授权 Mock 审核底座，复用真实 `/api` DTO，不伪装 FastAPI、Provider、worker 或 Tauri runtime。

- [`2026-08-01-custom-provider-protocol-onboarding-v1.md`](2026-08-01-custom-provider-protocol-onboarding-v1.md): 点点已明确将 custom Provider 扩展为 OpenAI-compatible / Anthropic-compatible 两个显式协议，并固定 Onboarding 的 Provider/URL -> API key -> 模型发现 -> 手填 model-ID fallback 流程；内置 Provider 的 catalog 与认证行为保持不变。

- [`2026-07-31-opendesign-frontend-realization-v1.md`](2026-07-31-opendesign-frontend-realization-v1.md)：将已确认的 dark token、KovaaK 连接与无分类 Tab 成绩列表、Switching/Tracking/Flicking 有界 Data 呈现和 Coach 当前训练落实到正式前端；只复用现有 owner/privacy/evidence/Training Plan/confirmation 合同，最终由 Browser、真实 Tauri 与文档归档闭环。
- [`2026-07-27-launch-family-production-activation.md`](2026-07-27-launch-family-production-activation.md)：使用真实 `Run 1030 / 1032 / 1036` 逐 family 激活 Static、Dynamic 与连续 LG Switching exact hash；Static 复用 input-native analyzer，Dynamic 独立 calibration/holdout，Switching 使用 `Stats kill boundary -> event-local episode`，不建立 persistent identity。四 family 资源矩阵、全量自动化与对话循环已收敛；没有 grounded issue/active plan 的 family 仍不伪造 knowledge/plan ref。不改 PRD / Architecture，不提交私人媒体或 Raw/source payload。
- [`2026-07-20-complete-coach-analysis-context-v1.md`](2026-07-20-complete-coach-analysis-context-v1.md)：完整 Coach 的规范化 Run facts、数据后处理、专项 analyzer、有界 evidence tools、知识、画像、计划与复测实施顺序；pre-activation 治理已完成，点点已授权从 Task 1 起继续推进，每次只执行一个 Task。

在新 plan 被审阅并明确标记为 active 之前，executor 不得依据 PRD、Architecture、Roadmap、spec 或归档 plan 自行拆解实施任务。

## Unresolved

- [`2026-07-29-kovaak-score-and-analysis-presentation-closeout-v1.md`](2026-07-29-kovaak-score-and-analysis-presentation-closeout-v1.md)：Task 1-4 已完成，但 header 仍称 active for remaining OpenDesign/frontend handoff，且正文没有后续 Task。
- [`2026-07-26-tracking-cv-performance-repair.md`](2026-07-26-tracking-cv-performance-repair.md)：Task 1 已完成并 field-verified，Task 3 已按 Stop rule 零代码收口；Task 2 因缺 annotation quality 证据未激活。
- [`2026-07-13-reflek-capability-adoption.md`](2026-07-13-reflek-capability-adoption.md)：header 明确写着 active 但当前无可执行 Task；Task 1-6A 已完成，Task 6B 与任何 v1 Benchmark UI 均未获授权。
- [`2026-07-13-coach-productization-provider-management.md`](2026-07-13-coach-productization-provider-management.md)：Task 1-5 已完成，Task 6 明确后置且前置未闭合，但 header 仍称 active。

以上四项因 plan 自身状态仍有歧义，按 Task 14 Stop rule 保留文件原位并记录未决；在点点裁决其 completed、blocked 或重新激活状态前，均不得交给 executor。

## Blocked

- [`../../archive/blocked/plans/2026-07-27-coach-guided-teaching-loop-v1.md`](../../archive/blocked/plans/2026-07-27-coach-guided-teaching-loop-v1.md)：安全的 Training Plan fact-command / confirmation 切片已实现且自动化通过；真实 Provider 连续三轮仍违反单问题、无未经验证归因和可比复测合同，Task 1 已按 Stop rule 停止。ratio 的等价数学展示允许，禁止的是无来源的语义/好坏/因果扩展。后续由 completed `TeachingSession` plan 承接；不得在本计划内继续堆提示词。

## Proposed

当前没有 proposed plan。

## Frozen

- [`../../archive/frozen/plans/2026-07-12-desktop-local-first-vertical-slice.md`](../../archive/frozen/plans/2026-07-12-desktop-local-first-vertical-slice.md)：Desktop local-first vertical slice；当前为 No-Go，需先解决 `docs/ROADMAP.md` 中的前置 Gate，再重新审阅。

Frozen plan 不得执行，也不得仅通过口头指令跳过其冻结条件。

## Retired

- [`../../archive/retired/plans/2026-07-18-windows-window-capture-v1.md`](../../archive/retired/plans/2026-07-18-windows-window-capture-v1.md)：CPU-backed Media Foundation window-capture 原型；性能 Gate 未通过，已由 hardware replay buffer 与 automatic Run finalization 取代，只保留为历史基线。

## Completed

- [`../../archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md`](../../archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md)：四波全工作区审计确认的 AN/UX/Coach/Capture/Security/Extensibility/Tooling/Governance finding 已按最小 Task 修复并完成根会话 aggregate 验证；release 与真实 field Gate 保持 No-Go。
- [`../../archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md`](../../archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md)：本地已连接 KovaaK 账号、turn-scoped 临时 Profile 查询、去身份成绩摘要和 latest available snapshot 修复已完成；不包含 OAuth、排行榜、后台刷新或第二套成绩系统。
- [`../../archive/completed/plans/2026-07-29-analysis-coach-knowledge-boundary-remediation-v1.md`](../../archive/completed/plans/2026-07-29-analysis-coach-knowledge-boundary-remediation-v1.md)：Task 1-5 已完成。Analysis Provider narration 已退役，Registry v4 / stable refs / neutral UI 已闭合；无 calibration 的 meaningful-change 继续 fail closed，Profile 不再把任意非零差异写成趋势。metric-ref canonical version 仍是明确 blocker，须由新的合同迁移 Task 承接。
- [`../../archive/completed/plans/2026-07-29-target-switching-coach-connection-v1.md`](../../archive/completed/plans/2026-07-29-target-switching-coach-connection-v1.md)：Target Switching 已复用现有 History 比较、Knowledge Registry 与 11 字段 Training Plan 编译链完成 Coach 接线；Python `199 passed`、完整 Coach runtime `156 passed`。
- [`../../archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md`](../../archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md)：Task 1-3 已完成并通过官方当前匿名端点的真实产品 API 验证；复用现有 Benchmark store、Coach context、TeachingSession、Training Plan 和 confirmation，已接 Viscose S2 Easier/Medium 原子成绩同步与确认改善后的 Medium 名称级建议。
- [`../../archive/completed/plans/2026-07-29-real-coach-semantic-remediation-v1.md`](../../archive/completed/plans/2026-07-29-real-coach-semantic-remediation-v1.md)：Task 1 已完成并用 Static/Dynamic/Tracking/Switching field 数据只读验证；修复普通问答 TeachingTurn 误接管、限制语被当作原因、候选否定/同义表达误推进、Provider 自然改写频繁回退和多 Analysis 稳定选择。复用现有 Provider、TeachingSession、Registry 与 Training Plan；已保存 Provider 的连接测试和真实无工具 Coach 回合均通过。
- [`../../archive/completed/plans/2026-07-27-coach-teaching-session-v1.md`](../../archive/completed/plans/2026-07-27-coach-teaching-session-v1.md)：Task 1-6 已完成；Task 6 已在四份 field DB 副本上关闭 deterministic fallback 与 no-grounded-issue 对话死循环，并保持现有 TeachingSession/validator/Training Plan 单一事实源。
- [`../../archive/completed/plans/2026-07-27-coach-field-regression-repair.md`](../../archive/completed/plans/2026-07-27-coach-field-regression-repair.md)：真实 Provider 复测暴露的 Coach grounding、stop/turn 串线、Analysis 删除确认与 Evidence 查询可用性已按确定性 fail-closed 边界修复并通过 field matrix。
- [`../../archive/completed/plans/2026-07-27-task12-field-blocker-remediation.md`](../../archive/completed/plans/2026-07-27-task12-field-blocker-remediation.md)：Task 1–5 已完成并闭合真实 Tracking Run 暴露的 Analysis Data、video-fallback、Tasks 读模型与产品节奏 capture lifecycle；CV 恢复 exact parity 和约 148 秒中位数，但 `<=130s` 目标未达到，Task 12 release Gate 仍是外部 No-Go。
- [`../../archive/completed/plans/2026-07-24-full-review-remediation.md`](../../archive/completed/plans/2026-07-24-full-review-remediation.md)：backend full review 的当前正确性修复与小范围合同漂移已由三路 terra-high 分波收口；frontend、packaging、release、长期规模与缺少 caller context 的项目保留为明确 Gate。
- [`../../archive/completed/plans/2026-07-13-frontend-product-reconstruction.md`](../../archive/completed/plans/2026-07-13-frontend-product-reconstruction.md)：正式前端 Task 1–7 已逐项授权并完成；涵盖 prototype 清理、tokens/theme/primitives、产品路由、History、Analysis、Coach、Settings、Browser/Desktop E2E、截图与 accessibility。真实 KovaaK、跨 GPU、Provider/OAuth、worker restart 与发布工程继续由 Roadmap Gate 管理。
- [`../../archive/completed/plans/2026-07-20-windows-capture-compatibility-repair-v1.md`](../../archive/completed/plans/2026-07-20-windows-capture-compatibility-repair-v1.md)：lifecycle 与 Win32/runtime failure repair 已通过；高 polling batching 与更细硬件错误暴露按 Stop rule 收口为 assessment-only，AMD/Intel 仍是 Roadmap 外部 Gate。
- [`../../archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md`](../../archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md)：Capture Coordinator、Run Finalizer、Run-owned evidence、pending readiness、recovery、Storage 与 NVIDIA product-path field matrix 已完成；AMD/Intel 仍是 Roadmap 外部 Gate。
- [`../../archive/completed/plans/2026-07-18-hardware-replay-buffer-v1.md`](../../archive/completed/plans/2026-07-18-hardware-replay-buffer-v1.md)：GPU-resident hardware encode、300 秒 replay ring、normal/timescale/Restart 与 pause fail-closed 路径已完成；跨 vendor 物理验证不由本计划伪装闭合。
- [`../../archive/completed/plans/2026-07-18-time-alignment-v2.md`](../../archive/completed/plans/2026-07-18-time-alignment-v2.md)：TimeAlignment v2、Stats/Performance 组合锚点、半开切窗与实机前置合同已完成；新的 source correctness delta 由 Complete Coach Task 1 承接。
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
