# Aiming Cookie 文档入口

本页是活跃文档的导航，不是产品或实现事实源。先判断问题属于哪一类，再读取对应主责任文档；不要默认扫描整个 `docs/` 或 `archive/`。

## 主责任事实源

| 要回答的问题 | 主责任事实源 | 不负责什么 |
|---|---|---|
| 产品为什么存在、为谁、产品形态、阶段范围与非目标 | [`PRD.md`](PRD.md) | 当前实现状态、代码细节、施工流水 |
| 系统边界、数据归属、依赖方向、稳定合同与安全边界 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 产品取舍、当前完成度、日期排期 |
| 现在代码实际具备什么 | 当前代码、测试与真实运行结果 | 产品“应该是什么” |
| 接下来做什么、按什么顺序、何时可放行 | [`ROADMAP.md`](ROADMAP.md) | 已完成工作的详细流水 |
| 最近完成、当前阻塞、验证结果与交接 | [`PROGRESS.md`](PROGRESS.md) | 长期产品或架构合同 |
| 桌面产品骨架、信息架构与交互关系 | [`frontend-uiux-design.md`](frontend-uiux-design.md) | 视觉 token、代码实现、收费规则 |
| 视觉方向与语义设计语言 | [`../DESIGN-cursor.md`](../DESIGN-cursor.md) | 页面 IA、组件代码、当前实现状态 |
| token、主题和组件如何实现与评审 | [`design-system.md`](design-system.md) + 当前前端实现 | 产品范围与视觉方向 |
| 安装、启动、测试和代码入口 | [`DEVELOPMENT.md`](DEVELOPMENT.md) | 产品决策、进度与发布承诺 |

核心原则：**上游文档定义“应该是什么”；代码、测试和运行结果定义“现在实际上是什么”。** 两者不一致时记录实现差距，不让任何一方静默覆盖另一方。

## 按任务阅读

- 产品问题：`PRD.md`；涉及交付先后时再读 `ROADMAP.md`、`PROGRESS.md`。
- 架构或数据合同：`PRD.md` 相关段落 + `ARCHITECTURE.md`，然后核对代码和测试。
- 前端产品设计：`PRD.md` + `frontend-uiux-design.md`；涉及视觉再读 `DESIGN-cursor.md` 与 `design-system.md`。
- OpenDesign 桌面前端设计交接：从 [`opendesign-desktop-handoff.md`](opendesign-desktop-handoff.md) 进入；它只路由既有事实源、冻结设计自由度与交付 Gate，不构成 implementation Task 授权。Landing 明确延后到正式桌面截图和演示素材可用后。
- 开发与验证：`DEVELOPMENT.md`，再进入相关代码目录。
- 当前施工：只使用 [`superpowers/plans/README.md`](superpowers/plans/README.md) 列为 active 的 plan/Task。
- full-worktree 合同修复已完成；范围、逐 Task 验收与最终矩阵见 [`archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md`](archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md)。真实现场与发行 Gate 不因自动化通过而闭合。
- 局部设计：只使用 [`superpowers/specs/README.md`](superpowers/specs/README.md) 列为 active 的 spec。
- 自动 Run 采集与分析前选择：使用 active [`superpowers/specs/2026-07-17-automatic-run-capture-design.md`](superpowers/specs/2026-07-17-automatic-run-capture-design.md)；它只冻结产品/系统合同，不构成实施授权。
- 自动 Run finalization 的内部实施已完成；范围与验证证据见 completed [`archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md`](archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md)。AMD/Intel 物理硬件仍由 Roadmap 维护为外部发布 Gate。
- 完整 Coach 的 aim-family 范围、L0-L3 context/tool results、EvidenceSegment 与 outcome-only 降级：使用 active [`superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md`](superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md)；它是设计合同，不构成实施授权。
- 完整 Coach 的当前施工顺序与 Gate：使用 active [`superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md`](superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md)，按指定 Task 的 Allowed files、Tests first 与 Stop rule 执行。
- Coach 主动带练的 intake、候选假设、teach-back、单变量练习、执行确认与复测改口：使用 active [`superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md`](superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md)。提示词切片的旧 [`archive/blocked/plans/2026-07-27-coach-guided-teaching-loop-v1.md`](archive/blocked/plans/2026-07-27-coach-guided-teaching-loop-v1.md) 已按 Stop rule blocked；TeachingSession 的已完成实施证据见 [`archive/completed/plans/2026-07-27-coach-teaching-session-v1.md`](archive/completed/plans/2026-07-27-coach-teaching-session-v1.md)，后续施工须由新的 active plan 承接。
- Viscose S2 Easier/Medium 的有限成绩同步、去身份 Coach 摘要和 Easier→复测→Medium 课程衔接：设计合同使用 active [`superpowers/specs/2026-07-29-viscose-s2-sync-coach-progression-design.md`](superpowers/specs/2026-07-29-viscose-s2-sync-coach-progression-design.md)；已完成实施证据见 [`archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md`](archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md)。
- 本地已连接 KovaaK 账号与 Coach turn-scoped 临时 Profile 查询：长期合同使用 active [`superpowers/specs/2026-07-30-kovaak-connected-account-and-coach-lookup-design.md`](superpowers/specs/2026-07-30-kovaak-connected-account-and-coach-lookup-design.md)，实施证据见 [`archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md`](archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md)。
- Analysis 删除/恢复：长期合同使用 active [`superpowers/specs/2026-07-16-analysis-deletion-reconciliation-design.md`](superpowers/specs/2026-07-16-analysis-deletion-reconciliation-design.md)；实施证据见 completed [`archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md`](archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md)。
- Windows Desktop 前端前置 Gate：Steam 多库 KovaaK bounded discovery 与 launch-token descendant isolation 的实施证据见 completed [`archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md`](archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md)。

## 专题资料

以下文档提供理论、处方、部署或评估证据，但不能反向决定产品范围、架构合同或施工状态：

- [`aim-kinematics-research.md`](aim-kinematics-research.md)：运动学研究；
- [`coach-theory-foundation.md`](coach-theory-foundation.md)：教练理论基础；
- [`coach-community-frontier.md`](coach-community-frontier.md)：社区实践观察；
- [`coach-prescription-manual.md`](coach-prescription-manual.md)：处方规则说明；
- [`deployment-guide.md`](deployment-guide.md)：2026-07-10 部署候选调研快照；使用前必须重新核实并通过新的 active plan；
- [`superpowers/assessments/`](superpowers/assessments/)：评估证据与历史决策输入。
  - [`superpowers/assessments/2026-07-13-reflek-capability-adoption.md`](superpowers/assessments/2026-07-13-reflek-capability-adoption.md)：RefleK 全产品能力、数据链、分析与 Coach 采纳评估。
  - [`superpowers/assessments/2026-07-22-real-run-analysis-capability-audit.md`](superpowers/assessments/2026-07-22-real-run-analysis-capability-audit.md)：现存 normal/timescale/restart/pause 实机四件套、已产品化采集规则、Task 6-9 可用与禁止用途，以及 visual input 合同纠偏。
  - [`superpowers/assessments/2026-07-22-task10-cross-family-coach-knowledge-research.md`](superpowers/assessments/2026-07-22-task10-cross-family-coach-knowledge-research.md)：Task 10R 的真人教练/社区 cue、运动控制证据边界、跨 family observation-to-coaching 矩阵与 Registry v2 输入。
  - [`superpowers/assessments/2026-07-28-coach-scenario-prescription-research.md`](superpowers/assessments/2026-07-28-coach-scenario-prescription-research.md)：真人教练、Viscose、Voltaic 与运动学习证据如何约束问题到场景、剂量、matched/near-transfer 与实战迁移；同时定义当前三个 exact 场景的可执行边界。
  - [`superpowers/assessments/2026-07-30-coach-action-streaming-kovaak-launch-feasibility.md`](superpowers/assessments/2026-07-30-coach-action-streaming-kovaak-launch-feasibility.md)：Coach 可点击 Product Action、真流式工具事件与 KovaaK 启动/聚焦/指定场景切换的现状、拟议合同、降级和实机 Gate。
  - [`superpowers/assessments/2026-08-01-peripheral-recommendation-capability-map.md`](superpowers/assessments/2026-08-01-peripheral-recommendation-capability-map.md)：外设推荐当前可见事实、用户补充事实、可逆验证边界与逐轮知识访谈格式。
  - [`superpowers/assessments/2026-07-24-backend-full-review-ledger.md`](superpowers/assessments/2026-07-24-backend-full-review-ledger.md)：当前 dirty worktree 的 12 路后端、原生采集、性能、数据、Coach、安全、扩展性、文档与 UI/UX 全量审计；包含 confirmed findings、待实测边界、驳回项和修复顺序。

## 归档边界

[`archive/`](archive/) 保存 completed、frozen、retired、review 和 history 材料。归档只用于追溯；不得把归档 spec/plan 当作当前合同或直接交给 executor。

根目录草稿、mockup、Stitch、HTML 和 `.firecrawl/` 采集结果默认都是参考材料。若要升级为事实源，必须先把结论写入相应活跃主责任文档。
