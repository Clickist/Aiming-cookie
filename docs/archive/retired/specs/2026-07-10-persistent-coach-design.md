# Persistent Coach — 产品迁移边界

> **状态：产品边界有效；原技术实施细节已冻结，等待 Pi adoption assessment 后重写 implementation plan。**
>
> **上游**：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/superpowers/specs/2026-07-11-pi-agent-coach-runtime-design.md`。
>
> 本文件不再授权直接修改 SQLite schema、Python `chat_with_coach`、Coach API 或前端页面。旧 `docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md` 中的 Tasks 已不具备执行资格。

## 1. 终局产品模型

Coach 从「某一次 analysis session 的附属 chat」重做为用户拥有的连续关系：

```text
user
  └─ primary Coach relationship
       ├─ conversation / recoverable interaction state
       └─ references: 0..N completed analyses
```

必须满足：

1. 用户可以在没有分析引用时向 Coach 提问；
2. 用户可以从 Report 附加一个已完成分析，并在同一条长期关系中深挖；
3. Coach 可引用 0～N 次完成分析；分析是可选上下文，不拥有对话；
4. 删除 `done/failed` 分析只删除分析、输入和 artifacts；Coach 对话/关系保留，相关引用显示为已删除或不可用；
5. `queued/running` 分析不可删除；
6. Coach、分析引用和任何领域操作都受同一 owner 语义约束；
7. `/coach` 是终局入口；`/sessions/{id}/coach` 与 `/sessions/{id}/chat` 只能作为迁移兼容层。

## 2. 已冻结的迁移不变量

- 每个用户以一条主 Coach 关系作为最小产品心智模型；本阶段不提供多 thread、文件夹、归档或 thread 删除 UI；
- 新能力不得再依赖 `chat_messages.session_id` 或将 session 视为消息父级；
- 从 Report 进入 Coach 时，完成分析被幂等附加到同一主关系，而不是创建 per-session chat；
- 已删除分析不得再进入后续 Agent 上下文，也不得以引用快照保留原视频、CSV、完整 deterministic result 或内部路径；
- deterministic diagnosis 仍是事实源；没有可用分析时 Coach 必须承认没有当前指标上下文；
- 旧 chat 数据是否迁移、如何排序、Pi session 是否保存、何时移除旧 route，都必须在选定 Pi 路径后的替代 plan 中明确，不能临时猜测。

## 3. 交互边界

- `/coach` 呈现连续 Coach 关系、当前可用/已删除分析引用以及 Agent 产生的用户可理解交互；它不应复刻旧的「视频 + timeline + chat」固定双栏；
- Report 的「跟教练深聊」进入 `/coach?analysis=<id>` 或等价的产品级 attach 行为；
- report、video、timeline 仍是独立能力，Coach 通过工具结果或产品意图引导用户查看；
- 所有改变状态的产品动作（例如删除、付费、敏感偏好）必须有明确用户确认；具体事件协议以选定 Pi 路径的替代 plan 为准。

## 4. 显式撤销的旧实施假设

以下内容曾出现在旧 design/plan 中，但在 Pi 最大复用路径确定前均不是事实：

- SQLite `PRAGMA user_version` 必然从 1 升到 2，以及固定的三张 Coach 表；
- 必然迁移所有旧 `chat_messages`，或以某个 `legacy_session_id` 字段保存它们；
- Python `chat_with_coach` 必然是新的 Coach runtime 入口；
- 固定 REST endpoint、固定 message response shape、固定「最近 3 个 refs」上下文规则；
- 固定 Agent event 表、run/tool-invocation 表或 UI event wire format。

这些不是被删除的产品需求，而是需要由 Pi adoption assessment 和最小 Spike 用真实复用证据重新选择的实现方式。

## 5. 下一步

先完成 `2026-07-11-pi-agent-coach-runtime-design.md` 的 assessment 和 Spike。通过后，新 implementation plan 必须覆盖：

1. 选定的 Pi 复用层级与版本；
2. Coach 关系、历史兼容和分析删除的持久化方案；
3. Aiming Cookie domain tools 与 Python analysis runtime 的 adapter；
4. 云端 LLM proxy、事件桥和 `/coach` UI；
5. 旧 session-bound chat 的可回退迁移/移除步骤及测试。
