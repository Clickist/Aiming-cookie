# Aiming Cookie Repository Agent Contract

本文件是本仓库的项目级 Agent Contract。`AGENTS.md` 与 `CLAUDE.md` 必须保持**字节级一致**；它们只是不同工具的入口，不是两套规则，也不承载易变化的产品状态。

## 1. 协作与安全

- 每次解释性回复开头称呼用户：**点点**。
- 开工前简要写明假设、计划和可验证的成功标准；存在歧义时不得静默选择。
- 优先选择最小、简单、可验证、可回退的方案；不增加未要求的功能、抽象或配置。
- 只修改当前任务直接需要的内容，不顺手重构、格式化或清理无关代码。
- 尊重工作区已有未提交改动；不得 reset、checkout、覆盖或改写用户的工作。
- 删除文件不得使用 `rm -rf`；使用系统 `trash`，或在明确的文档归档任务中使用可追踪的 `mv`。
- 审阅和诊断默认只读。除非点点明确要求实施，不修改业务代码或产品决策。
- 未经明确要求，不提交、不推送、不开始下一个任务。

## 2. 项目事实源与职责边界

先从 [`docs/README.md`](docs/README.md) 进入文档体系。不同问题由不同主责任源回答：

| 问题 | 主责任事实源 |
|---|---|
| 产品目标、用户语义、产品形态、阶段范围、非目标 | [`docs/PRD.md`](docs/PRD.md) |
| 系统边界、数据归属、依赖方向、稳定合同、安全边界 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 当前实际实现、接口和可运行能力 | 当前代码、测试和真实运行结果 |
| 当前优先级、施工顺序、发布 Gate | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| 最近完成、阻塞、验证结果和交接状态 | [`docs/PROGRESS.md`](docs/PROGRESS.md) |
| 桌面产品骨架、信息架构和交互关系 | [`docs/frontend-uiux-design.md`](docs/frontend-uiux-design.md) |
| 视觉方向与语义设计语言 | [`DESIGN-cursor.md`](DESIGN-cursor.md) |
| 前端 token、主题和组件的实现治理 | [`docs/design-system.md`](docs/design-system.md) 与当前前端实现 |
| 安装、启动、测试和代码入口 | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| 单个局部设计合同 | 相关设计文档、当前代码和测试 |
| 施工顺序与当前状态 | [`docs/ROADMAP.md`](docs/ROADMAP.md)、[`docs/PROGRESS.md`](docs/PROGRESS.md) 与当前任务说明 |
| 历史证据 | [`docs/archive/README.md`](docs/archive/README.md)；只供追溯，不是当前依据 |

`AGENTS.md` / `CLAUDE.md` 只定义协作和文档治理规则，不得复制产品状态、发布日期、代码地图、算法说明、测试数字或当前施工清单。

## 3. 冲突处理

- 系统、开发者和点点当前明确指令优先于仓库文档。
- 在产品问题上，PRD 是最高项目事实源；若点点的新要求与 PRD 冲突，明确指出并询问是否更新 PRD，不能只在下游文档或代码中打补丁。
- Architecture 只能展开 PRD，不得改写产品范围；Roadmap、Progress、spec 和 plan 不得反向覆盖 PRD 或 Architecture。
- 上游文档定义“应该是什么”；代码、测试和运行结果定义“现在实际上是什么”。二者不一致时，记录为实现差距，不让代码静默重定义产品，也不让文档否认代码事实。
- 同一层级冲突时，优先采用范围更具体、状态仍 active 且更新时间更近的文档；仍无法判断则停止并向点点说明。
- 研究、assessment、mockup、Stitch、HTML、根目录草稿和归档材料只提供证据或参考，不自动成为产品或实现合同。

## 4. 按任务读取，避免全量加载

- 产品范围：读 PRD；涉及交付顺序再读 Roadmap 和 Progress。
- 架构或数据合同：读 PRD 的相关部分与 Architecture，再核对代码和测试。
- 前端产品设计：读 PRD、`frontend-uiux-design.md`；涉及视觉再读 `DESIGN-cursor.md` 和 `design-system.md`。
- 实施任务：先确认当前目标、影响范围和测试；相关 spec/plan 只在需要时作为参考，不构成开工门槛。
- 当前状态或 review：以相关 diff、关键调用链、测试和 Progress 为范围，不自动扩展成全仓审计。
- 理论或处方：只读取与问题相关的 research / theory 文档；它们不能决定产品优先级或实施状态。

## 5. 实施与验证

开始实施前，把任务转成可验证目标，例如：

```text
1. [改动] → verify: [检查]
2. [改动] → verify: [检查]
```

实施时：

- 测试优先或至少先定义失败复现和验收条件；
- 每一处改动都应能追溯到当前请求；
- 只清理本次改动造成的未使用代码，不处理既有无关债务；
- 发现需要扩大文件范围、改变冻结合同或无法运行必要验证时，停止并上报；
- 长时间、会污染工作区或需要额外权限的验证，先说明原因。

完成后必须报告：

- 实际 changed files 与变更范围；
- 已运行的验证和结果；
- 未运行或无法运行的检查；
- 与计划的偏差和剩余风险；
- 最终 `git status`，并区分本次改动与原有改动。

## 6. 轻量开发流程

- 不要求安装或使用 superpowers 系列 skill，也不要求 active plan/Task 才能开工；
- 普通任务按当前请求、相关 PRD/Architecture、代码和测试推进，先说明假设、计划和成功标准；
- 保持改动小而可验证，完成后报告 changed files、验证结果、未运行检查、剩余风险和 `git status`。

## 7. 文档治理规则

- 每类事实只设一个主责任文档；其他文档只做摘要和链接，不复制长期正文。
- PRD 维护产品决策；Architecture 维护稳定系统合同；Roadmap 维护未来顺序；Progress 维护当前快照。不要在四份文档中重复同一状态清单。
- 代码入口和命令写入 `docs/DEVELOPMENT.md`，不写入 Agent Contract。
- 易变化的测试数字、当前 commit、日期化交接和详细 review 放入 Progress；过期后移入 archive history。
- active spec 只描述尚需长期引用的局部合同；实施完成且结论已回写上游后移入 `docs/archive/retired/specs/`。
- `docs/superpowers/plans/` 与 `docs/superpowers/specs/` 作为可选设计参考和历史记录维护，不构成施工授权或流程门槛。
- 归档文件尽量保留原正文；若当前结论需要恢复，先核对 PRD、Architecture 与代码，再写回活跃文档。
- 新增、移动或退役文档时，同步更新 `docs/README.md`、相应索引和活跃文档链接。
- 若修改本文件，必须将完全相同的内容同步到 `AGENTS.md` 和 `CLAUDE.md`，并用 `cmp -s AGENTS.md CLAUDE.md` 验证。
