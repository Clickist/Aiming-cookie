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
| 什么改动在什么时刻必须过什么测试；发版前全旅程验收清单 | [`testing-tiers.md`](testing-tiers.md) | 测试命令细节、Go/No-Go 结论、验证流水 |
| 外部遥测（KovaaK's 内存读取管线）导入合同与启用方式 | [`EXTERNAL_TELEMETRY_IMPORT.md`](EXTERNAL_TELEMETRY_IMPORT.md) | KovaaKRun 主链路行为、分析侧指标定义 |

核心原则：**上游文档定义”应该是什么”；代码、测试和运行结果定义”现在实际上是什么”。** 两者不一致时记录实现差距，不让任何一方静默覆盖另一方。

> 2026-08-13 架构重写（SQLite → JSON 文件、Coach 直连 sidecar）已完成。稳定系统合同以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准；当时的授权与计划已归档为 [`archive/completed/plans/2026-08-13-architecture-rewrite.md`](archive/completed/plans/2026-08-13-architecture-rewrite.md)。

## 按任务阅读

- 产品问题：`PRD.md`；涉及交付先后时再读 `ROADMAP.md`、`PROGRESS.md`。
- 架构或数据合同：`PRD.md` 相关段落 + `ARCHITECTURE.md`，然后核对代码和测试。
- 前端产品设计：`PRD.md` + `frontend-uiux-design.md`；涉及视觉再读 `DESIGN-cursor.md` 与 `design-system.md`。
- 已退役的 OpenDesign 桌面交接记录见 [`archive/retired/opendesign-desktop-handoff.md`](archive/retired/opendesign-desktop-handoff.md)；它只保留历史设计决策，不构成当前产品或实现依据。
- 开发与验证：`DEVELOPMENT.md`，再进入相关代码目录。
- full-worktree 合同修复已完成；范围、逐 Task 验收与最终矩阵见 [`archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md`](archive/completed/plans/2026-07-30-full-worktree-contract-remediation-v1.md)。真实现场与发行 Gate 不因自动化通过而闭合。
- 2026-08-12 Coach 架构交接草稿已归档到 [`archive/history/2026-08-12-coach-architecture-handoff.md`](archive/history/2026-08-12-coach-architecture-handoff.md)；当前 E2E 启动流程以 `DEVELOPMENT.md` 为准，当前验证状态以 `PROGRESS.md` 为准。
- Coach Skills 架构：三类 skill（引导/执行/教学）、Route A 状态管理决策、系统提示词更新方向与 `teaching_session.update` 命令设计见 [`skills-design.md`](skills-design.md)。
- 自动 Run finalization 的内部实施已完成；范围与验证证据见 completed [`archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md`](archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md)。AMD/Intel 物理硬件仍由 Roadmap 维护为外部发布 Gate。
- Coach 主动带练 TeachingSession 的实施证据见 [`archive/completed/plans/2026-07-27-coach-teaching-session-v1.md`](archive/completed/plans/2026-07-27-coach-teaching-session-v1.md)，后续施工按当前任务推进。
- Viscose S2 的已完成实施证据见 [`archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md`](archive/completed/plans/2026-07-29-viscose-s2-sync-coach-progression-v1.md)。
- 本地已连接 KovaaK 账号与 Coach 查询的实施证据见 [`archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md`](archive/completed/plans/2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md)。
- Analysis 删除/恢复的实施证据见 completed [`archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md`](archive/completed/plans/2026-07-16-analysis-deletion-reconciliation.md)。
- 历史设计合同与实施计划已归档至 [`archive/superpowers/`](archive/superpowers/)，仅供追溯参考，不构成当前开发约束。
- 2026-08-16 的四份研究/评审产物已归档至 [`archive/history/`](archive/history/)：知识库一致性审核 [`knowledge-consistency-audit-2026-08-16.md`](archive/history/knowledge-consistency-audit-2026-08-16.md)、静态 CV GitHub 调研 [`static-cv-github-survey-2026-08-16.md`](archive/history/static-cv-github-survey-2026-08-16.md)、静态 CV 管线提案 [`static-cv-pipeline-proposal-2026-08-16.md`](archive/history/static-cv-pipeline-proposal-2026-08-16.md)、x76 wiki 知识评审 [`x76-wiki-knowledge-review-2026-08-16.md`](archive/history/x76-wiki-knowledge-review-2026-08-16.md)。两份以 registry v7 为基线（当时仅存在于未合并的 feat 分支）；它们只供追溯与引用，不构成当前产品或实现合同。x76 wiki 抓取快照未入仓库。
- Windows Desktop 前端前置 Gate：Steam 多库 KovaaK bounded discovery 与 launch-token descendant isolation 的实施证据见 completed [`archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md`](archive/completed/plans/2026-07-16-windows-desktop-prefrontend-gates.md)。

## 专题资料

以下文档提供理论、处方、部署或评估证据，但不能反向决定产品范围、架构合同或施工状态：

- [`aim-kinematics-research.md`](aim-kinematics-research.md)：运动学研究；
- [`coach-theory-foundation.md`](coach-theory-foundation.md)：教练理论基础；
- [`coach-community-frontier.md`](coach-community-frontier.md)：社区实践观察；
- [`coach-prescription-manual.md`](coach-prescription-manual.md)：处方规则说明；
- [`deployment-guide.md`](deployment-guide.md)：2026-07-10 部署候选调研快照；使用前必须重新核实并通过相关测试与发布 Gate；
- [`landing-brief.md`](landing-brief.md)：落地页纯内容 brief（2026-08-19 草稿）；视觉交由 Open Design 自由发挥；
- [`landing-truth-update.md`](landing-truth-update.md)：落地页真值化口径更新清单（2026-08-31 待执行：约 10 处 A 级失实点位、hero 视频去留决策、话术升级方向；发版 runbook 的一部分）；
- [`marketing/`](marketing/)：B站宣传视频脚本三件套——口播定稿 v3（活跃使用：基准=点点 08-31 念稿，含 D 方案 15 秒钩子与五处闪现清单；**段二管线句等遥测升级定论后改词**）、Gemini 原稿、砍字对比稿（后两者仅供追溯）；HyperFrames 动效制作的已拍板决策、实机校准事实、官方模板参考与下一 session 开工顺序见 [`marketing/hyperframes-bilibili-hook-handoff-2026-09-03.md`](marketing/hyperframes-bilibili-hook-handoff-2026-09-03.md)；交给 OpenDesign 直接接手制作的交接提示词（含本轮视频机制踩坑、素材路径、实现路径二选一）见 [`marketing/opendesign-hyperframes-handoff.md`](marketing/opendesign-hyperframes-handoff.md)；各站 Google SEO 官方实践 checklist 与三站体检快照（2026-09-07 实测口径）见 [`marketing/seo-checklist.md`](marketing/seo-checklist.md)。拍摄素材双维度清单（主体按口播段落定位语境、文末按机位拍摄批次归堆，含 Mock 已覆盖项与 v0.1.14 录制口径）见 [`marketing/bilibili-video-shotlist.md`](marketing/bilibili-video-shotlist.md)。
- [`video-pane-revamp-brief.md`](video-pane-revamp-brief.md)：视频面板复盘体验升级提案（2026-08-27 草稿：P0 逐帧/三档变速/@time 暂停、P1 事件上轴、P2 信号片段 AB 循环与色带规格）；含四个待拍板决策点，未批准施工；
- [`handoff-frontend-parity-2026-08-27.md`](archive/history/handoff-frontend-parity-2026-08-27.md)：前端追平工程交接总纲（**已归档至 archive/history**：其「六批未提交改动」已全部提交并随 v0.1.13/v0.1.14 发布，只供追溯，不再反映当前工作区）；
- [`frontend-parity-research-digests.md`](frontend-parity-research-digests.md)：十二路调研合订摘要（对话流/Provider接入/侧栏/视频面/训练数据/任务通知/键盘面板/版式诊断/视觉工艺/富文本排印/输入框编排/安装包品牌化）；只供引用，不构成合同；
- [`quote-feature-research.md`](quote-feature-research.md)：Coach 引用回复（划选引用到输入框）实现调研（2026-08-27 草稿，供拍板，非实施合同；功能本体此前已上线，v0.1.12 为其浮层偏移修复）；
- [`pro-benchmark-smoke-plan.md`](pro-benchmark-smoke-plan.md)：高手基准 CV 冒烟验证方案（2026-08-27 落库，状态待执行：立项「高手基准离线提取工具」前用真实高手录像量化免校准 CV 管线精度边界）；
- [`../sdk/knowledge-pack/SPEC.md`](../sdk/knowledge-pack/SPEC.md)：第三方知识包 SDK 的交付入口（SPEC：包格式、导入校验、档切换语义；含模板与校验器 CLI 说明）；知识包的档切换、整库替换与词汇表冻结等稳定合同见 [`ARCHITECTURE.md`](ARCHITECTURE.md) §4.5，产品语义见 [`PRD.md`](PRD.md) 决策日志 2026-09-20 条；
- [`../affiliate-worker/README.md`](../affiliate-worker/README.md)：透明联盟转链 Worker（Cloudflare Workers，部署于 `affiliate.gearclickist.com`，淘宝联盟/拼多多转链，密钥只存 Worker Secrets）的 API、部署与冒烟入口；被 Coach `purchase_links.lookup` 经 `affiliate-native.ts` 消费；
- `superpowers/assessments/`：历史评估证据与决策输入，仅供追溯。

## 归档边界

[`archive/`](archive/) 保存 completed、blocked、frozen、retired、review 和 history 材料。归档只用于追溯；不得未经核对把归档 spec/plan 当作当前合同。`archive/superpowers/` 保存了已归档的设计合同（specs）、实施计划（plans）和评估材料（assessments）。

根目录草稿、mockup、Stitch、HTML 和 `.firecrawl/` 采集结果默认都是参考材料。若要升级为事实源，必须先把结论写入相应活跃主责任文档。
