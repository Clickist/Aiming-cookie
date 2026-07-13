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
- 开发与验证：`DEVELOPMENT.md`，再进入相关代码目录。
- 当前施工：只使用 [`superpowers/plans/README.md`](superpowers/plans/README.md) 列为 active 的 plan/Task。
- 局部设计：只使用 [`superpowers/specs/README.md`](superpowers/specs/README.md) 列为 active 的 spec。

## 专题资料

以下文档提供理论、处方、部署或评估证据，但不能反向决定产品范围、架构合同或施工状态：

- [`aim-kinematics-research.md`](aim-kinematics-research.md)：运动学研究；
- [`coach-theory-foundation.md`](coach-theory-foundation.md)：教练理论基础；
- [`coach-community-frontier.md`](coach-community-frontier.md)：社区实践观察；
- [`coach-prescription-manual.md`](coach-prescription-manual.md)：处方规则说明；
- [`deployment-guide.md`](deployment-guide.md)：2026-07-10 部署候选调研快照；使用前必须重新核实并通过新的 active plan；
- [`superpowers/assessments/`](superpowers/assessments/)：评估证据与历史决策输入。
  - [`superpowers/assessments/2026-07-13-reflek-capability-adoption.md`](superpowers/assessments/2026-07-13-reflek-capability-adoption.md)：RefleK 全产品能力、数据链、分析与 Coach 采纳评估。

## 归档边界

[`archive/`](archive/) 保存 completed、frozen、retired、review 和 history 材料。归档只用于追溯；不得把归档 spec/plan 当作当前合同或直接交给 executor。

根目录草稿、mockup、Stitch、HTML 和 `.firecrawl/` 采集结果默认都是参考材料。若要升级为事实源，必须先把结论写入相应活跃主责任文档。
