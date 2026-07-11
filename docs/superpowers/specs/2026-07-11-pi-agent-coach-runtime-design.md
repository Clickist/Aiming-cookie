# Pi Source Takeover Assessment — Coach Runtime 产品化边界

> **状态：内部预览 P0 前置研究；不是 implementation contract。**
>
> **上游**：`docs/PRD.md`、`docs/ARCHITECTURE.md`。本文件不覆盖已确定的 Coach 产品语义，也不重新讨论是否接管 Pi 源码；它只确定完整源码接管后的基线、纳入范围、产品化删改和系统边界。
>
> **执行限制**：在本 assessment 形成结论并通过隔离 Spike 前，任何 executor 不得据此 vendor/import Pi 源码，不得修改 schema、Coach API、前端路由或业务代码。正式实施仍须由强模型编写新的 implementation plan，并由点点逐个批准 Task。

## 1. 已固定的产品与源码策略

以下不再是 assessment 的可选项：

1. Coach 是用户级常驻 Agent 操作层，不是某个 analysis session 的附属聊天框；
2. 一条主 Coach 关系可以引用 0～N 次已完成分析，也允许没有分析的对话；
3. analysis 是领域工具可读取的上下文；删除分析不删除 Coach 对话或长期关系，引用只变为已删除/不可用；
4. `/coach` 是终局入口，旧 session-bound route 只允许作为兼容迁移入口；
5. deterministic diagnosis 是事实源；领域操作必须保留 owner/state/delete/billing 等业务语义；
6. **完整 Pi 源码是 Coach runtime 的默认起点**，由 Aiming Cookie 接管、直接修改和长期维护；
7. Aiming Cookie 不承诺持续兼容、合并或跟随 Pi 上游升级；选定的来源与 commit/tag/version 只作为可审计的源码基线；
8. “完整源码接管”指以完整 runtime 能力为评估起点，不代表原样暴露 coding agent、TUI、shell/file tools 或 project trust 等产品语义；这些能力必须按 Coach 需要保留、改造、禁用或删除；
9. Pi/runtime 的 workspace、权限和 sandbox/container 能力优先从真实源码中复用和产品化，不在业务层无证据重写同类底层系统；
10. 持久 Coach 数据、analysis 所有权、删除语义、计费和产品权限始终归 Aiming Cookie 领域层，不得交给 Pi runtime session 代替。

## 2. Assessment 要裁决什么

本 assessment **不比较 core / RPC / fork 三条平行采用路径，也不决定是否 fork**。源码接管已经确定；现在必须基于真实源码回答以下问题：

1. 接管哪个上游仓库、commit/tag/version，纳入哪些 package；
2. 源码放入 Aiming Cookie 的哪个目录，如何记录来源、补丁和本地维护责任；
3. LICENSE、NOTICE、版权声明及第三方依赖分别要求什么；
4. 哪些 runtime 模块原样保留，哪些需要产品化适配，哪些必须禁用或删除；
5. Node/TypeScript Coach runtime、Python analysis runtime、Web/Desktop shell 之间采用什么最小进程边界；
6. Aiming Cookie LLM cloud proxy、领域工具、UI event bridge 和持久 Coach store 分别接入哪里；
7. workspace、权限、sandbox/container 在 Desktop 与内部预览环境中如何部署；
8. 哪些 coding-agent 假设会污染 Coach 产品语义，以及清除这些假设所需的最小改造面。

完成 assessment 时必须记录源码入口文件、依赖关系和证据，不得仅凭 README、包名或既有印象下结论。

## 3. 模块处置证据表

源码评估必须填充下表。处置结论只能是“保留”“改造”“禁用”“删除”之一；无法确定时标为阻塞，不得由 executor 猜测。

| Pi 模块或能力 | 真实入口/API | 初始处置倾向 | 必须核验 | Aiming Cookie 边界或改造点 |
|---|---|---|---|---|
| Agent loop 与模型流 |  | 保留并改造 | stream 接口、取消、错误、重试和模型配置 | 接入 Aiming Cookie LLM cloud proxy，不让 runtime 持有产品 billing 决策 |
| Tool registry、schema 与执行 |  | 保留并改造 | 注册方式、进度、确认、错误映射 | 只暴露批准的稳定领域工具 |
| Hook / event stream |  | 保留并产品化 | token、tool start/progress/end、confirmation、result/error | 映射为 versioned UI/runtime events |
| Runtime session、compaction 与 recovery |  | 保留或改造 | 进程重启、长上下文、状态落盘方式 | 与持久 Coach store 明确分工，不成为产品数据事实源 |
| Extension / custom tool 能力 |  | 保留或改造 | 扩展加载、隔离、权限和生命周期 | 仅作为领域工具接入机制，不开放任意插件语义 |
| RPC / process boundary |  | assessment 裁决 | 嵌入、sidecar、IPC、崩溃隔离和打包代价 | 固定 Node/Python/Web/Desktop 的最小连接边界 |
| Workspace / sandbox / container |  | 保留、简化或改造 | 平台支持、权限模型、文件生命周期和部署依赖 | 用于 Coach runtime 隔离，不复制到产品业务层 |
| Shell / file / coding tools |  | 默认禁用或删除 | 是否存在 Coach 必需的受限能力 | 未经独立产品与安全裁决不得启用 |
| TUI / project trust / coding prompts |  | 默认删除或替换 | 与 runtime 核心的耦合程度 | 清除 coding-agent UI、信任和提示词语义 |
| Provider / credential handling |  | 改造 | key、provider config、telemetry、日志泄露风险 | 统一通过 Aiming Cookie proxy 和秘密管理 |
| Aiming Cookie LLM cloud proxy adapter |  | 新增薄接入层 | 流协议、usage、错误、取消和超时 | 不复制模型客户端和计费状态机 |
| Python CV/runtime adapter |  | 新增最小边界 | 调用协议、进度、结果 contract 和失败恢复 | 只连接稳定 analysis/domain contracts |
| Source provenance 与依赖义务 |  | 必须记录 | upstream URL、commit/tag/version、LICENSE、NOTICE、第三方依赖 | 仓库内形成可审计基线，不承诺持续上游同步 |

## 4. 隔离 Spike（正式源码纳入前唯一允许的实现性验证）

Spike 不接入正式产品数据迁移，不升级 SQLite schema，不替换现有 chat API，也不把 Pi 源码大规模落入正式业务目录。它应在隔离目录或独立分支性工作区中，以拟接管的真实源码基线验证以下链路：

```text
Coach 用户消息
→ Aiming Cookie LLM cloud proxy adapter
→ Pi agent loop
→ 一个只读 analysis summary tool（隔离 fixture）
→ assistant token + tool start/progress/end + stable error event
→ runtime 进程重启后恢复该次运行所需状态
```

Spike 必须回答：

1. 选定的上游来源、commit/tag/version 和 package 纳入清单是什么；
2. LLM proxy 的流、usage、取消、错误与重试接法是否成立；
3. 领域 tool 如何注册、返回进度、请求确认并产生稳定产品错误；
4. Pi runtime session/recovery 与 Aiming Cookie 持久 Coach 关系如何分工；
5. Node/TypeScript Coach runtime 与 Python analysis runtime 的最小连接边界是什么；
6. UI event 映射是否足以支持文本、工具活动、确认、结果意图和恢复状态；
7. 哪些 coding/TUI/project-trust/shell/file 模块可直接移除或禁用，哪些与核心耦合而必须改造；
8. workspace/sandbox 能力在目标 Desktop/runtime 部署中能否复用；
9. 正式接管必须修改的 Pi 文件/模块清单，以及预计可保持不动的核心清单；
10. 许可证、NOTICE、源码来源和第三方依赖义务能否在仓库内完整履行。

Spike 失败不自动恢复为“外部依赖 + adapter”方案。若真实源码证明接管成本或许可证条件不可接受，必须停止并回到点点与架构负责人重新裁决，executor 不得自行改变方向。

## 5. Assessment 交付物与架构裁决

通过 assessment/Spike 后，必须先形成可审阅的架构裁决，至少包含：

- 上游来源、commit/tag/version、纳入 package 与校验方式；
- 仓库内源码目录、来源记录、LICENSE/NOTICE 和第三方依赖记录；
- 每个主要模块的保留/改造/禁用/删除结论及证据；
- coding-agent 能力删改清单与默认关闭项；
- Node/Python/Web/Desktop 进程边界和 IPC/contract 责任；
- Pi runtime state、持久 Coach store、analysis 数据与文件生命周期的所有权表；
- LLM proxy、领域工具、UI event bridge 的接入点；
- Spike 代码与正式产品代码的处置方式；
- 已知风险、未决项和明确 Stop conditions。

在该裁决批准前，不得开始 schema、迁移、API 或 `/coach` UI 实施。

## 6. 新 implementation plan 的强制结构

只有架构裁决通过后，强模型才可以创建新的 Coach migration implementation plan。建议按以下职责拆成独立 Task，最终以代码事实为准：

1. **Import/vendor source baseline**：纳入批准的 Pi 源码基线、来源记录、LICENSE/NOTICE 和最小构建验证；
2. **Remove/disable coding surface**：按批准清单删除、禁用或替换 coding tools、TUI、project trust 与 coding prompts；
3. **LLM proxy adapter**：接入 stream、usage、取消和稳定错误，不夹带产品数据迁移；
4. **Domain tools and event bridge**：接入只读/受控领域工具及 versioned UI events；
5. **Persistent Coach schema and compatibility migration**：迁移旧 session-bound chat，冻结数据所有权与删除语义；
6. **Coach API and `/coach` UI**：在已批准 contracts 上实施产品入口和兼容路由；
7. **Delete-analysis regression and preview gate**：验证删除分析、输入和产物不会删除 Coach 对话或长期关系，并完成内部预览 Gate。

每个 Task 必须单独冻结：

- Allowed files；
- Tests first 与精确验证命令；
- schema/default/migration/security/delete/retry 等不可自行改变的决策；
- acceptance checklist；
- Stop conditions。

弱模型每次只能执行点点明确批准的一个 Task。若源码事实与 plan 不一致、需要扩大文件范围、测试无法运行或出现新的架构/产品/schema/migration 决策，必须立即停止并上报，不能自行修订 plan 或继续下一 Task。

旧 `docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md` 继续冻结，不得执行；新的 plan 通过后应明确将其标记为被替代。
