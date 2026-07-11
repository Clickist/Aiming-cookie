# Persistent Coach Migration — 已冻结的旧施工计划

> **状态：不得执行。常驻 Coach 迁移目标已于 2026-07-11 提升为内部预览 P0，但必须由 Pi assessment/Spike 后的新 implementation plan 替代本文件。**
>
> 2026-07-11 后，Coach 已被确认以完整 Pi 源码接管和产品化改造为默认 runtime 方案。原计划在未核验 Pi 源码的前提下，提前冻结了 SQLite v2、Python `chat_with_coach` adapter、固定 REST API 和固定前端聊天实现，因此不再是有效 implementation plan。
>
> **有效上游**：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/superpowers/specs/2026-07-10-persistent-coach-design.md`、`docs/superpowers/specs/2026-07-11-pi-agent-coach-runtime-design.md`。

## 1. 仍然有效的目标

- Coach 由 user-owned 主关系承载，而不是由 analysis session 承载；
- Coach 可以引用 0～N 条完成分析，也支持没有分析的对话；
- `/coach` 是终局入口；旧 session Coach/chat 仅兼容迁移；
- 删除 done/failed 分析不删除 Coach 对话/关系，queued/running 不可删除；
- deterministic diagnosis、owner/state/delete/billing 等领域语义不能交给 LLM 或由 Pi 的通用运行时替代。

## 2. 已失效、不得执行的假设

本文件原 Task 1–6 的下列假设全部失效：

1. 固定升级 SQLite `user_version` 1 → 2；
2. 固定 `coach_threads` / `coach_messages` / `coach_thread_analysis_refs` 表结构；
3. 固定将旧 `chat_messages` 迁移到新表的算法；
4. 固定将 Python `chat_with_coach` 改造成长期 Coach runtime；
5. 固定 `/api/coach/primary...` REST response 和前端静态 chat 页面；
6. 固定在 Pi 未验证前自行设计 run、tool event、context window 和 session persistence。

它们可以在后续被重新采用，但必须由源码评估与 Spike 证明，而不是从本文件复制。

## 3. 替代计划的前置序列（非执行 Task）

1. **Pi 源码接管 assessment（只读）**：固定纳入的基线版本/package，核验许可证与第三方依赖，标注保留、改造、删除的模块；
2. **最小 Spike（隔离）**：用接管后的真实 Pi 源码、一个只读 analysis tool 与 Aiming Cookie LLM proxy 验证流、工具进度、恢复和 Python adapter 边界；
3. **架构裁决**：固定源码目录/进程边界、coding-agent 能力删改清单和产品数据分工；不再以跟随 Pi 上游升级为约束；
4. **替代 implementation plan**：再拆持久 Coach、工具、事件桥、兼容迁移、`/coach` UI 的单 Task 施工合同；
5. **实施**：仅在点点明确指定替代 plan 的单个 Task 后开始。

## 4. 保留原因

本文件保留在仓库中作为“哪些技术决定曾被提前冻结”的可追溯记录，避免后续误把它当作新 PRD 的实现事实。它不应被 Fast/executor、自动化或后续文档引用为可执行计划。
