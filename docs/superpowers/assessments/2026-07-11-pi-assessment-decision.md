# Pi Assessment 裁决 — 2026-07-11

> **裁决人**：点点（产品）+ 本会话架构对账  
> **上游证据**：`docs/superpowers/assessments/2026-07-11-pi-agent-coach-runtime-assessment.md`  
> **Spike 证据**：`spikes/pi-coach-runtime/`（21 tests passed）

## 1. 裁决

**CONDITIONAL GO — 接受。**

- 方向确认：Coach runtime 默认以完整 Pi 源码接管为基线；不重写 agent loop。
- 这 **不是** 现在立刻 vendor `third_party/pi/` 的授权。
- 这 **不是** 生产 GO。
- Spike 协议（`coach_runtime_event.v0` / `fake_llm_proxy.v0` / fixture stdio）**不得** 直接升为产品 v1。

## 2. 为打破规划死锁而冻结的拆分

Assessment §N 要求“一份正式 takeover plan 解决全部 blocker”才许改 schema。这会把 **产品 P0（删除分析不抹对话）** 绑死在 **runtime 工程（vendor Pi + sidecar）** 上，造成永久空转。

**本次明确拆成两条可并行施工线：**

| 线 | 目标 | 是否阻塞内部预览 P0 删除语义 | 状态 |
|---|---|---|---|
| **A. 常驻 Coach 数据归属** | SQLite canonical 关系/消息/分析引用；删除语义；`/coach` 入口；旧 chat 迁移 | **是，P0 阻塞** | completed（[`2026-07-11-persistent-coach-data-ownership.md`](../../archive/completed/plans/2026-07-11-persistent-coach-data-ownership.md)） |
| **B. Pi runtime 接管** | `third_party/pi/` import、删改 coding 面、sidecar、事件桥、proxy | 否（预览可先用现有 Python `chat_with_coach`） | **薄切片已完工**（[`2026-07-12-pi-coach-runtime-integration.md`](../../archive/completed/plans/2026-07-12-pi-coach-runtime-integration.md) Task 1–5：vendored Pi、`webapp/coach-runtime`、subprocess 桥、`/coach` 默认 `COACH_RUNTIME=pi` + Python fallback；daemon / 云账单 / Desktop 沙箱仍待后续 plan） |

Architecture 已写明：canonical Coach 对话归属 Aiming Cookie 领域层；Pi JSONL 只是 runtime transcript。因此 **线 A 不依赖 vendor Pi**。

## 3. 线 A 已冻结决策（数据层）

1. Canonical store = 本地 SQLite（现有 webapp DB），不是 Pi JSONL。
2. 预览阶段 Coach **对话 runtime** 仍用 Python `chat_with_coach`；不在线 A 引入 Node sidecar。
3. 每用户一条 primary Coach thread（不提供多 thread UI）。
4. 分析是 0..N 引用；删除 `done/failed` 分析只删 session + 输入/artifacts，消息保留，引用标 `deleted`。
5. `queued/running` 不可删。
6. 旧 `chat_messages` 一次性迁移到 primary thread；`chat_messages` 表保留到兼容窗口结束，新写入不再依赖它作为权威源。
7. `/coach` 为终局入口；`/sessions/{id}/coach` 与 `/sessions/{id}/chat` 仅兼容。

## 4. 线 B 必须在独立 plan 中冻结（不得混进线 A Task）

见 assessment §M：source layout、removal manifest、sidecar lifecycle、product event contract、cloud proxy、domain-tool IPC、sandbox、SBOM/legal、preview gate。

## 5. 立即行动

1. 关闭 assessment Spike 为 **completed（裁决已记录）**。
2. 线 A implementation plan 已完成并归档：[`../../archive/completed/plans/2026-07-11-persistent-coach-data-ownership.md`](../../archive/completed/plans/2026-07-11-persistent-coach-data-ownership.md)。
3. 工作区对账后按 commit batch 落盘基线，再按 Task 施工。