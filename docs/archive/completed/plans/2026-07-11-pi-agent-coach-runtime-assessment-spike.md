# Pi Agent Coach Runtime Assessment + Isolated Spike — Fast 可执行施工图

> **状态：待点点逐 Task 批准。本文是 assessment/Spike implementation contract，不是正式 Coach migration plan。**
>
> **上游事实源**：`docs/PRD.md`、`CLAUDE.md`、`docs/ARCHITECTURE.md`、`docs/superpowers/specs/2026-07-11-pi-agent-coach-runtime-design.md`、当前代码与拟评估 Pi 源码。
>
> **替代关系**：本文不解冻、也不替代 `docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md`。旧 plan 继续禁止执行。只有 Task 6 的 assessment 由点点和架构负责人批准后，才能另写正式 migration 替代计划。

## 0. Executor 开工口令

Fast 模型没有收到点点明确给出的 **一个 Task 编号** 时，不得开始。收到后必须先原样回显：

```text
Task: <编号 + 标题>
Depends on: <已完成 Task>
Allowed files: <逐项列出；不得写“相关文件”等模糊范围>
Tests first: <本 Task 要先写/先运行的精确检查>
Frozen decisions: 本 Task 已冻结的来源、协议、错误、retry、持久化和安全边界；不得自行重选
Stop rule: 任一源码事实不符、测试不可运行、需要扩大文件范围或出现新架构/产品/schema/migration/default 决策，立即停止并上报
```

每次只执行一个 Task。Task 通过后必须停止，不得自动继续下一 Task，不得提交或推送。

---

## 1. Goal

在不修改正式 schema、数据库、Coach API、前端路由和业务代码的前提下，用拟接管的真实 Pi 源码完成一个隔离、可删除的 Spike，验证并留下可审阅证据：

```text
Coach fixture 用户消息
→ Spike-only fake Aiming Cookie LLM proxy wire
→ Pi Agent loop
→ 只读 analysis summary tool
→ Node → Python stdio JSONL fixture adapter
→ assistant token + tool start/progress/end + stable error event
→ JSONL runtime session 落盘
→ 进程重启后恢复已完成 transcript，并识别未完成 run
```

最终产物不是产品功能，而是一份能够支持架构裁决的源码 assessment。Fast 不负责批准接管来源，也不负责设计正式 Coach 数据模型。

## 2. Success criteria

六个 Task 全部完成后必须同时满足：

1. 候选 upstream URL、commit、package version、license、package 清单均有命令证据；
2. Spike 只依赖候选源码 checkout，不复制 Pi 源码、不 vendor `node_modules`；
3. fake stream 驱动真实 `@earendil-works/pi-agent-core` 的 `Agent`，不是自写 agent loop；
4. 一个只读 tool 能产生真实 Pi `tool_execution_start/update/end`，并映射为稳定的 Spike event；
5. fake proxy adapter 覆盖文本流、usage、取消和稳定错误，且断言每次请求最多一次网络调用；
6. Node/Python 固定使用 stdio JSONL 完成 fixture summary、progress、错误和取消验证；
7. Pi JSONL session 能在重新实例化后恢复已完成消息；遗留 `running` 标记只能被识别为 interrupted，不能自动重放工具；
8. assessment 填完模块处置表、进程边界、状态所有权、事件映射、删改面、sandbox 证据、license/dependency 义务和正式接管文件候选清单；
9. 正式 `webapp/backend`、`webapp/frontend`、现有 schema/API/UI/routes 零改动；
10. 未使用真实 API key、未调用收费模型、未访问真实用户数据。

## 3. Verified current state（2026-07-11）

以下是编写本计划时已核验的事实。Fast 必须在 Task 1 重新验证，不得只抄结论：

### 3.1 Aiming Cookie

- 仓库目前没有 `spikes/` 或 `docs/superpowers/assessments/` 既有目录约定；本文明确创建它们，禁止 Fast另选目录。
- 正式 Python 合同位于 `webapp/backend/contracts.py`，已有 `analysis_result.v1`、`artifact_manifest.v1`、`error.v1`。
- 当前 Coach 仍有 session-bound 实现；Spike 阶段不得修改它。
- Node/TS 正式产品依赖目前只存在于 `webapp/frontend`；Spike 不得向 frontend 安装 Pi 依赖。

### 3.2 候选 Pi 源码基线

本计划冻结的 **assessment/Spike 候选基线** 是：

| 字段 | 冻结值 |
|---|---|
| 本机 checkout | `/tmp/aiming-cookie-pi-assessment` |
| upstream candidate | `https://github.com/earendil-works/pi.git` |
| commit | `3ea064ea2a0f01965923ce32e1bd17466c502b23` |
| branch snapshot | `main`，shallow checkout |
| commit subject | `fix: support Bedrock API key login` |
| package version | `0.80.6` |
| Node engine | `>=22.19.0` |
| license | MIT；Copyright (c) 2025 Mario Zechner |

这只是拟评估基线，不等于正式 vendor 来源已经批准。如果点点或架构负责人要求评估另一个 upstream，Fast 必须停止，不能自行 clone、切换或“选更官方的”仓库。

### 3.3 已核验的 Pi 能力

- `packages/agent/src/agent.ts`：`Agent`、订阅、取消、custom `streamFn`；
- `packages/agent/src/agent-loop.ts`：真实 agent/tool loop；
- `packages/agent/src/types.ts`：`AgentEvent`、`AgentTool.execute`、`AbortSignal`、`onUpdate`；
- `packages/agent/src/harness/session/jsonl-storage.ts`：JSONL session create/open；
- `packages/agent/src/harness/session/session.ts`：消息与 custom entry；
- `packages/agent/src/harness/env/nodejs.ts`：Node filesystem/shell execution environment；
- `packages/agent/src/proxy.ts`：Pi 自带 proxy utility 证据；
- `packages/coding-agent`：CLI/TUI、project trust、extensions、RPC、coding prompt 以及 read/bash/edit/write/find/grep/ls 等 coding tools；
- `packages/tui`、`packages/orchestrator`：不属于本 Spike runtime。

当前判断：Spike 从 `packages/ai + packages/agent` 起步；`coding-agent` 只作为源码耦合与删改评估证据，不作为 Spike runtime。

### 3.4 已运行的上游基线检查

在候选 checkout 中：

```bash
npm run test --workspace @earendil-works/pi-agent-core -- --reporter=dot
```

结果：16 个 test files、180 个 tests 全部通过。该结果只能作为计划编写时证据；Task 1 必须重新运行。

## 4. Global frozen decisions

以下决策对 Task 1–6 全部生效，Fast 不得改变：

1. **隔离目录固定为** `spikes/pi-coach-runtime/`；最终 assessment 固定为 `docs/superpowers/assessments/2026-07-11-pi-agent-coach-runtime-assessment.md`。
2. Spike 运行时只使用候选 checkout 的 `packages/ai` 与 `packages/agent`；不得把 `coding-agent`、`tui`、`orchestrator` 接进运行链。
3. 不复制 `/tmp` 源码到仓库，不创建 symlink，不提交 `node_modules`，不在正式 frontend/backend 安装依赖。
4. Spike 测试通过 `PI_SOURCE_DIR=/tmp/aiming-cookie-pi-assessment` 显式定位源码；源码路径缺失或 identity 不符就停止。
5. TypeScript 测试使用候选 checkout 已安装的 `tsx` loader；不得为了方便改成 Jest/Vitest、不得给正式项目加 test runner。
6. Spike 事件版本固定为 `coach_runtime_event.v0`，仅供验证；不是正式产品 contract，不得写入 `webapp/*`。
7. Node/Python Spike 边界固定为 **单请求子进程 + stdin/stdout JSONL**；不得改选 HTTP、socket、gRPC、队列或长期 daemon。
8. Python adapter 只读仓库内 fixture；不得 import `webapp.backend.db`、访问 SQLite、读取真实 analysis 文件。
9. fake LLM proxy wire 固定为本地测试 server 的 NDJSON；不是正式 cloud API。不得用真实 key、真实 endpoint 或收费模型。
10. Spike retry 固定为 **0**：proxy adapter 每次调用只允许一次 `fetch`，不做 backoff；正式 retry ownership 留给后续架构计划。
11. Spike 不实现正式 timeout default。调用方只传入 `AbortSignal`；测试主动取消。正式 timeout 数值留待后续裁决。
12. stable error 复用 `error.v1` 的字段形状和已存在 category 集合，但 Spike 内独立声明，不 import 或修改正式 Python contract。
13. tool 固定为只读 `get_analysis_summary`；不得添加 write/delete/retry/billing/confirmation side effect。
14. Pi JSONL 只保存 runtime transcript 和 Spike run marker，不作为持久 Coach store，不设计 Coach table。
15. 恢复保证只包括：恢复已完成消息、识别未完成 run。**不承诺 mid-token 或 mid-tool continuation**，也不自动重放工具。
16. Task 6 只能给出 evidence-backed recommendation 与 blocker；最终 go/no-go 由点点和架构负责人批准。

## 5. Shared Spike contracts（不得由 Fast改字段）

### 5.1 `error.v1`-shaped Spike error

```ts
type SpikeErrorV1 = {
  schema_version: "error.v1";
  category:
    | "input_validation"
    | "local_cv_runtime"
    | "llm_provider"
    | "network_cloud"
    | "storage_disk"
    | "internal_unknown";
  code: string;
  message: string;
  retryable: boolean;
  trace_id: string | null;
  details: unknown | null;
};
```

固定错误映射：

| 场景 | category | code | retryable |
|---|---|---|---|
| fake proxy HTTP 非 2xx | `llm_provider` | `proxy_http_error` | `false` |
| fake proxy NDJSON 无效 | `llm_provider` | `proxy_protocol_error` | `false` |
| proxy fetch 网络失败 | `network_cloud` | `proxy_network_error` | `true` |
| AbortSignal 取消 | `network_cloud` | `proxy_aborted` | `true` |
| Python 无效请求 | `input_validation` | `analysis_request_invalid` | `false` |
| fixture analysis 不存在 | `local_cv_runtime` | `analysis_not_found` | `false` |
| Python 输出/退出异常 | `local_cv_runtime` | `analysis_adapter_failed` | `false` |
| runtime JSONL 无法读写 | `storage_disk` | `runtime_session_storage_failed` | `false` |
| 未分类 Spike 异常 | `internal_unknown` | `spike_internal_error` | `false` |

不得把堆栈、API key、绝对用户数据路径放进 `message` 或 `details`。

### 5.2 Spike UI/runtime event envelope

所有 event 必须有：

```ts
type SpikeRuntimeEvent = {
  schema_version: "coach_runtime_event.v0";
  run_id: string;
  sequence: number;       // 每个 run 从 1 开始，严格递增
  emitted_at: string;     // 注入 clock，测试固定 ISO 字符串
  type: string;
  payload: Record<string, unknown>;
};
```

本 Spike 只允许下列 `type`：

| type | payload |
|---|---|
| `run.started` | `{}` |
| `assistant.delta` | `{ text: string }`，只来自 Pi `message_update.assistantMessageEvent.type === "text_delta"` |
| `assistant.completed` | `{ stop_reason: string, usage: object | null }` |
| `tool.started` | `{ tool_call_id: string, tool_name: string, input: unknown }` |
| `tool.progress` | `{ tool_call_id: string, tool_name: string, details: unknown }` |
| `tool.completed` | `{ tool_call_id: string, tool_name: string, ok: boolean, details: unknown }` |
| `run.error` | `{ error: SpikeErrorV1 }` |
| `run.completed` | `{}` |
| `run.interrupted` | `{ previous_run_id: string }` |

Pi 的 `agent_start/end`、message/tool events 可以被 mapper 使用；不得透传整个原始 event、thinking 内容、provider payload 或 secret header。

### 5.3 Fake proxy wire `fake_llm_proxy.v0`

Request：

```json
{
  "schema_version": "fake_llm_proxy.v0",
  "run_id": "run-1",
  "model": "fixture-model",
  "messages": [],
  "tools": []
}
```

Response 为 NDJSON，每行一种：

```json
{"type":"start"}
{"type":"text_delta","delta":"文本"}
{"type":"tool_call","id":"tool-1","name":"get_analysis_summary","arguments":{"analysis_id":"analysis-fixture-1"}}
{"type":"done","stop_reason":"stop","usage":{"input":10,"output":4,"total_tokens":14}}
{"type":"error","code":"fixture_provider_error","message":"fixture provider failed"}
```

规则：

- 一次 response 必须以 `done` 或 `error` 结束；
- tool-call turn 的 `done.stop_reason` 必须为 `toolUse`；最终文本 turn 为 `stop`；
- adapter 将 wire 转成 Pi `AssistantMessageEventStream`，不得绕过 Pi loop；
- usage 只记录 fixture 数值，不计算价格、不写 billing。

### 5.4 Python stdio wire `analysis_tool_stdio.v0`

stdin 恰好一行：

```json
{"protocol":"analysis_tool_stdio.v0","request_id":"req-1","operation":"get_analysis_summary","analysis_id":"analysis-fixture-1"}
```

stdout 可多行，按顺序：

```json
{"protocol":"analysis_tool_stdio.v0","request_id":"req-1","type":"progress","stage":"loading_fixture","message":"Loading analysis fixture"}
{"protocol":"analysis_tool_stdio.v0","request_id":"req-1","type":"result","summary":{"analysis_id":"analysis-fixture-1","schema_version":"analysis_result.v1","summary_type":"flicking","diagnosis":{},"notes":[]}}
```

失败终止行：

```json
{"protocol":"analysis_tool_stdio.v0","request_id":"req-1","type":"error","error":{"schema_version":"error.v1","category":"local_cv_runtime","code":"analysis_not_found","message":"Analysis fixture not found","retryable":false,"trace_id":null,"details":null}}
```

Python 进程日志只能写 stderr；stdout 不得输出非 JSON。Node 收到一个 terminal `result/error` 后即结束；多个 terminal event、协议不匹配、非 JSON、非零退出均为 `analysis_adapter_failed`。

### 5.5 Runtime marker

写入 Pi `Session.appendCustomEntry("aiming_cookie_run.v0", data)`：

```ts
type RunMarker = {
  run_id: string;
  status: "running" | "completed" | "interrupted";
};
```

启动新 run 前，如果最后一个同类 marker 是 `running`，只追加对应 `interrupted` marker 并发出 `run.interrupted`；不得调用 `Agent.continue()`，不得重新执行任何 tool。

## 6. Shared test command

Task 2–5 统一从仓库根目录运行：

```bash
PI_SOURCE_DIR=/tmp/aiming-cookie-pi-assessment \
TSX_TSCONFIG_PATH=/tmp/aiming-cookie-pi-assessment/tsconfig.json \
node --import /tmp/aiming-cookie-pi-assessment/node_modules/tsx/dist/loader.mjs \
  --test spikes/pi-coach-runtime/test/*.test.ts
```

如 shell 展开顺序导致无法只跑单文件，则将最后路径替换为精确 test 文件。不得静默改用另一个 runner。

---

# Task 1 — Freeze candidate source provenance and module inventory

## Goal

重新验证候选源码 identity、可构建/可测试状态、license 和 package 边界，形成后续 Task 可引用的证据。此 Task 不写 Spike 代码。

## Depends on

无。

## Allowed files

仅允许新建：

- `spikes/pi-coach-runtime/UPSTREAM.md`
- `spikes/pi-coach-runtime/assessment/source-inventory.md`
- `spikes/pi-coach-runtime/assessment/license-notes.md`

不得修改本文施工图。

## Tests first / evidence first

任何写文件前，依次运行并保留输出摘要：

```bash
test -d /tmp/aiming-cookie-pi-assessment/.git
git -C /tmp/aiming-cookie-pi-assessment remote get-url origin
git -C /tmp/aiming-cookie-pi-assessment rev-parse HEAD
git -C /tmp/aiming-cookie-pi-assessment status --short --branch
node -e 'const p=require("/tmp/aiming-cookie-pi-assessment/package.json"); console.log(p.engines?.node, p.workspaces)'
node -e 'for (const n of ["ai","agent","coding-agent","tui","orchestrator"]) { const p=require(`/tmp/aiming-cookie-pi-assessment/packages/${n}/package.json`); console.log(p.name,p.version) }'
sed -n '1,40p' /tmp/aiming-cookie-pi-assessment/LICENSE
npm run test --workspace @earendil-works/pi-agent-core -- --reporter=dot
```

最后一条命令的 working directory 必须是 `/tmp/aiming-cookie-pi-assessment`。

## Frozen decisions

- identity 必须与 §3.2 完全一致；remote 的 `.git` 尾缀差异不视为差异，其他差异均停止；
- checkout 若有 tracked 源码改动，立即停止；构建生成的 ignored/untracked `dist` 不写入证据基线；
- `ai + agent` 是 Spike runtime 候选；`coding-agent` 仅 assessment；`tui + orchestrator` 排除；
- 不执行 `git pull/fetch/checkout/reset/clean`，不安装或升级依赖；
- license-notes 只记录原文事实和待法律复核项，不给法律结论。

## Implementation steps

1. `UPSTREAM.md` 写入：URL、commit、commit subject、package version、Node engine、checkout path、核验命令、核验日期；显著注明“candidate for assessment/Spike, not approved production vendor baseline”。
2. `source-inventory.md` 按以下列建表并逐行填写真实源码路径：
   - `Capability`
   - `Entry/API`
   - `Package`
   - `Spike disposition`（runtime / evidence-only / excluded）
   - `Evidence`
3. inventory 至少覆盖：Agent loop、streamFn、AgentEvent、tool registry/execute/update、abort、JSONL storage、Session、harness、proxy utility、coding tools、RPC、project trust、extension loader、TUI、orchestrator、workspace/sandbox/container evidence。
4. workspace/sandbox 行必须区分：core 内建事实、coding-agent/extension/container 示例、尚未证明项；不得写“Pi 已天然提供 Coach sandbox”。
5. `license-notes.md` 记录 root LICENSE、版权、是否发现 NOTICE、五个 package 的直接 dependencies 摘要、正式 vendor 前必须生成 third-party inventory 的待办。
6. 文档中所有判断必须带真实文件路径；仅来自 README 的内容要标 `README-only`。

## Verification

重复 Tests first 命令，并运行：

```bash
rg -n "candidate|3ea064ea2a0f01965923ce32e1bd17466c502b23|0.80.6|MIT|packages/agent|packages/coding-agent|sandbox" \
  spikes/pi-coach-runtime/UPSTREAM.md \
  spikes/pi-coach-runtime/assessment/source-inventory.md \
  spikes/pi-coach-runtime/assessment/license-notes.md
```

## Acceptance checklist

- [ ] identity 与冻结值一致，agent 上游测试通过；
- [ ] 每个关键能力有真实源码入口；
- [ ] runtime/evidence-only/excluded 边界明确；
- [ ] 没有把 sandbox 推断写成已验证事实；
- [ ] 未复制源码、未安装依赖、仅改 Allowed files；
- [ ] 已报告 `git status --short` 并停止。

## Stop conditions

- remote、commit、version、license 任一不符；
- checkout 有 tracked 源码修改；
- Node 不满足 `>=22.19.0`；
- agent baseline tests 失败；
- 需要联网、切换 upstream 或修改候选源码才能继续。

---

# Task 2 — Real Pi Agent loop + read-only fixture tool + event mapper

## Goal

用 fake in-memory `streamFn` 驱动真实 Pi `Agent`，执行一次只读 fixture tool，并把 token 与 tool lifecycle 映射为 §5.2 事件。

## Depends on

Task 1 已完成且获批。

## Allowed files

仅允许新建：

- `spikes/pi-coach-runtime/package.json`
- `spikes/pi-coach-runtime/tsconfig.json`
- `spikes/pi-coach-runtime/src/contracts.ts`
- `spikes/pi-coach-runtime/src/pi-source.ts`
- `spikes/pi-coach-runtime/src/fake-stream.ts`
- `spikes/pi-coach-runtime/src/analysis-summary-tool.ts`
- `spikes/pi-coach-runtime/src/event-mapper.ts`
- `spikes/pi-coach-runtime/test/agent-loop.test.ts`
- `spikes/pi-coach-runtime/test/event-mapper.test.ts`

不得生成 `package-lock.json`，因为本目录没有独立依赖安装。

## Tests first

先新建两个 test 文件，测试名必须精确为：

`test/agent-loop.test.ts`

- `real Pi Agent emits token and tool lifecycle for the read-only fixture tool`
- `read-only fixture tool forwards progress and returns deterministic summary`
- `tool failure maps to one stable run.error without leaking stack`

`test/event-mapper.test.ts`

- `mapper emits coach_runtime_event.v0 with monotonic sequence`
- `mapper only exposes approved event types and payload fields`
- `mapper ignores thinking and raw provider payloads`

先运行两个测试并记录因实现缺失而失败；如果失败原因是 loader/source identity，而不是缺失实现，立即停止。

## Frozen decisions

- `pi-source.ts` 只能从 `process.env.PI_SOURCE_DIR` 下的 `packages/ai/src/index.ts` 与 `packages/agent/src/index.ts` 动态 import；不得 fallback 到 npm registry；
- `PI_SOURCE_DIR` 缺失时抛出明确错误；不得默认扫描 home/tmp；
- fake stream 固定两 turn：第一 turn 产生 `get_analysis_summary` tool call，第二 turn 产生文本 `fixture coach answer`；
- tool 参数只有 `{ analysis_id: string }`，固定只接受 `analysis-fixture-1`；
- Task 2 tool 直接读取模块内常量 fixture，不读文件、不 spawn Python；Task 4 才替换执行边界；
- tool 的 `onUpdate` 恰好调用一次，details 为 `{ stage: "loading_fixture" }`；
- event mapper 使用注入 clock，测试固定 `2026-07-11T00:00:00.000Z`；
- 不映射 thinking，不实现 confirmation，不添加新 event type。

## Exact implementation

1. `package.json` 仅包含：

```json
{
  "name": "aiming-cookie-pi-coach-runtime-spike",
  "private": true,
  "type": "module"
}
```

2. `tsconfig.json` 使用 `target: ES2022`、`module/moduleResolution: NodeNext`、`strict: true`、`noEmit: true`，只 include `src/**/*.ts` 与 `test/**/*.ts`；不 extends frontend config。
3. `contracts.ts` 只声明 §5.1/§5.2 的 Spike types、`makeSpikeError` 和运行时最小字段检查；禁止引入正式 schema library。
4. `pi-source.ts`：
   - 校验 env；
   - 用 `pathToFileURL(join(root, ...)).href` 动态 import；
   - 导出 `loadPiAi()` 与 `loadPiAgent()`；
   - 不缓存跨测试可变状态。
5. `fake-stream.ts`：使用 Pi AI 的 `AssistantMessageEventStream` 或 `createAssistantMessageEventStream`，按 Pi 源码真实协议 emit `start/text_delta/toolcall_*/done`；不得返回自定义简化 Promise。
6. `analysis-summary-tool.ts`：使用 Pi `AgentTool` 形状和 TypeBox schema；结果 content 为模型可读短 JSON，details 为结构化 summary；throw 表示失败。
7. `event-mapper.ts`：
   - `createEventMapper({ runId, clock, emit })` 内部持有 sequence；
   - 输入为真实 `AgentEvent`；
   - 严格按 §5.2 映射；
   - assistant error 只生成一个 `run.error`；
   - 输出前创建新对象，不把 Pi message/event 原对象挂入 payload。
8. agent-loop test 必须实例化真实 `new Agent({ streamFn, initialState: { tools: [...] } })`、subscribe mapper、调用 `prompt(...)`；不得直接调用 tool 来伪造集成通过。
9. 单独 tool unit test 可以直接调用 `execute` 验证一次 progress。

## Verification

```bash
PI_SOURCE_DIR=/tmp/aiming-cookie-pi-assessment \
TSX_TSCONFIG_PATH=/tmp/aiming-cookie-pi-assessment/tsconfig.json \
node --import /tmp/aiming-cookie-pi-assessment/node_modules/tsx/dist/loader.mjs \
  --test \
  spikes/pi-coach-runtime/test/agent-loop.test.ts \
  spikes/pi-coach-runtime/test/event-mapper.test.ts
```

再运行 Task 1 的 upstream agent baseline test，确认未污染候选源码。

## Acceptance checklist

- [ ] 测试先红后绿；
- [ ] 真实 Pi Agent 完成 tool-use 两 turn；
- [ ] token/tool start/progress/end 全部来自真实 Pi events；
- [ ] sequence 严格递增，payload 无 thinking/provider/raw event；
- [ ] stable error 不含 stack/绝对路径；
- [ ] 无 `node_modules`、lockfile、源码复制；
- [ ] 仅改 Allowed files，报告 status 后停止。

## Stop conditions

- Pi event/type API 与本文不符；
- 必须修改候选 Pi 源码才能完成；
- 必须引入 coding-agent 或新 npm dependency；
- TypeBox import 无法从候选 checkout 解析；
- 需要新增 event/error/default 决策。

---

# Task 3 — Fake LLM cloud proxy adapter contract

## Goal

用本地 fake HTTP server 验证 §5.3 NDJSON wire 可转换为 Pi stream，并覆盖 text、tool call、usage、取消、协议错误和零重试。

## Depends on

Task 2 已完成且获批。

## Allowed files

仅允许新建：

- `spikes/pi-coach-runtime/src/proxy-stream.ts`
- `spikes/pi-coach-runtime/test/proxy-stream.test.ts`
- `spikes/pi-coach-runtime/test/proxy-agent-integration.test.ts`

Task 2 文件不得顺手重构；如真实集成要求修改，停止并申请扩大范围。

## Tests first

测试名必须精确为：

`test/proxy-stream.test.ts`

- `proxy adapter maps NDJSON text and usage into a Pi assistant stream`
- `proxy adapter maps one tool call into Pi toolUse without argument loss`
- `proxy adapter aborts the single fetch and emits proxy_aborted`
- `proxy adapter performs zero retries after HTTP failure`
- `proxy adapter rejects malformed or unterminated NDJSON with proxy_protocol_error`

`test/proxy-agent-integration.test.ts`

- `real Pi Agent completes proxy tool turn and final text turn`

先写测试，用 Node 内建 `http.createServer()` 监听 `127.0.0.1` 的随机端口；不得监听公网地址。先运行并记录实现缺失失败。

## Frozen decisions

- adapter public API 固定为：

```ts
createProxyStreamFn(options: {
  endpoint: string;
  runId: string;
  fetchImpl?: typeof fetch;
}): StreamFn
```

- 使用 Node 内建 `fetch`，不加 HTTP client dependency；
- `Authorization`、API key、billing/user identity 不属于本 Spike request；
- 每次 Pi provider call 对应一次 POST；失败不 retry；
- 取消只由 Pi 传入的 `options.signal` 驱动；
- adapter 必须遵循 Pi `StreamFn` contract：普通 provider/runtime 失败编码为终止 `error` event 和 error AssistantMessage，不以 rejected promise 结束；
- fake server 计数请求次数，HTTP failure 测试必须断言为 1；
- 不写 timeout 数值、不实现 backoff、不读取环境 secret。

## Exact implementation

1. 将 Pi context 转为 §5.3 request，只发送 model id、messages、tools；测试断言没有 secret/header dump。
2. POST header 只允许 `content-type: application/json` 和 `accept: application/x-ndjson`。
3. 按 chunk 增量切行，允许最后一行有或没有换行；空行忽略。
4. 将 wire event 映射成 Pi `AssistantMessageEvent`：
   - `start` → `start`；
   - `text_delta` → 必要的 `text_start/text_delta/text_end` 生命周期；
   - `tool_call` → 必要的 `toolcall_start/toolcall_delta/toolcall_end`；
   - `done` → final AssistantMessage，保留 fixture usage；
   - `error`/HTTP/parse/abort → final error AssistantMessage。
5. 生成 AssistantMessage 时 provider 固定 `aiming-cookie-proxy-fixture`、model 取请求 model、cost 全为 0；不得推算价格。
6. `proxy-agent-integration` 必须复用 Task 2 的 tool 与 mapper，真实 `Agent.prompt()` 完成两次 proxy request。

## Verification

运行 `proxy-stream.test.ts`、`proxy-agent-integration.test.ts`，随后运行 §6 全量命令。

## Acceptance checklist

- [ ] text/tool/usage wire 均进入真实 Pi stream；
- [ ] abort 终止 fetch；
- [ ] HTTP failure 请求计数恰好 1；
- [ ] malformed/unterminated stream 稳定失败；
- [ ] 无真实 endpoint/key/token 消耗；
- [ ] 仅改 Allowed files，报告 status 后停止。

## Stop conditions

- Pi `StreamFn` contract 与计划不符；
- 需要定义正式 cloud proxy auth、billing、retry 或 timeout；
- 测试必须联网或使用真实模型；
- 需要修改 Task 2/正式产品文件。

---

# Task 4 — Node → Python read-only analysis adapter boundary

## Goal

把 Task 2 的模块内 fixture tool 改为调用固定 stdio JSONL Python adapter，验证 progress/result/error/cancel；仍不访问正式 DB/API。

## Depends on

Task 3 已完成且获批。

## Allowed files

允许新建：

- `spikes/pi-coach-runtime/fixtures/analysis-result.v1.json`
- `spikes/pi-coach-runtime/python/analysis_adapter.py`
- `spikes/pi-coach-runtime/src/python-analysis-client.ts`
- `spikes/pi-coach-runtime/test/python-analysis-client.test.ts`

允许修改：

- `spikes/pi-coach-runtime/src/analysis-summary-tool.ts`
- `spikes/pi-coach-runtime/test/agent-loop.test.ts`
- `spikes/pi-coach-runtime/test/proxy-agent-integration.test.ts`

不得修改其他文件。

## Tests first

先新增测试名：

- `Python adapter returns one progress event and deterministic analysis_result.v1 summary`
- `Python adapter returns analysis_not_found for an unknown fixture id`
- `Node client maps malformed stdout and nonzero exit to analysis_adapter_failed`
- `Node client terminates the child when AbortSignal is aborted`
- `real Pi tool forwards Python progress into tool.progress`

先让现有两个 agent integration tests 改为注入 Python client，再运行并记录因 client 缺失而失败。

## Frozen decisions

- Python executable 默认 `python3`，测试可注入 executable/path；不得创建 venv、pip install；
- 每次 tool call spawn 一个进程，stdin 一行后关闭；
- cwd 固定为 `spikes/pi-coach-runtime`；adapter 只允许读取相对固定路径 `fixtures/analysis-result.v1.json`；
- fixture 必须符合现有 `analysis_result.v1` 外形，但不得 import 正式 Python contract；
- summary 只取：`schema_version`、`summary_type`、`deterministic.diagnosis`、`notes`；不得把 figures/timeline/artifact paths 发给模型；
- `analysis_id` 只接受 `analysis-fixture-1`；
- abort 时 Node 发送 `SIGTERM` 并等待 child close；本 Task 不定义 kill timeout/SIGKILL fallback；若 child 无法退出，停止上报；
- stderr 仅供测试诊断，不进入产品 event/error details。

## Exact fixture

`analysis-result.v1.json` 至少包含：

```json
{
  "schema_version": "analysis_result.v1",
  "analysis_version": "flicking_fair_summary.v1",
  "summary_type": "flicking",
  "created_at": "2026-07-11T00:00:00Z",
  "completed_at": "2026-07-11T00:01:00Z",
  "input": {"cm_per_360": 34.5, "fov": 103.0},
  "deterministic": {
    "diagnosis": {"summary": {"fixture_signal": "stable"}},
    "figures": {},
    "timeline": []
  },
  "narration": {"status": "not_requested", "text": null, "provider": null, "model": null, "usage": null},
  "artifact_manifest": {"schema_version": "artifact_manifest.v1", "inputs": [], "outputs": []},
  "notes": ["fixture-only"],
  "normalization_issues": []
}
```

## Exact implementation

1. Python 用标准库 `json/sys/pathlib`；顶层捕获预期协议错误并输出一个 terminal error，禁止 traceback 到 stdout。
2. Python 先完整校验 request protocol/operation/request_id/analysis_id，再输出 progress；无效请求不应先发 progress。
3. Node client public API：

```ts
getAnalysisSummary(request: {
  requestId: string;
  analysisId: string;
  signal?: AbortSignal;
  onProgress?: (progress: { stage: string; message: string }) => void;
}): Promise<AnalysisSummary>
```

4. Node 使用 `spawn` 参数数组，不拼 shell 字符串；逐行解析 stdout；验证 protocol 与 request_id。
5. tool factory 改为显式注入 client，不在模块 import 时 spawn；tool `onUpdate` 使用 Python progress；terminal result 写入 tool details/content。
6. agent integration 测试必须继续实例化真实 Pi Agent，并断言 `tool.progress.payload.details.stage === "loading_fixture"`。

## Verification

```bash
printf '%s\n' '{"protocol":"analysis_tool_stdio.v0","request_id":"manual-1","operation":"get_analysis_summary","analysis_id":"analysis-fixture-1"}' | \
  python3 spikes/pi-coach-runtime/python/analysis_adapter.py
```

手工输出必须恰好为合法 JSONL progress + result。随后运行 §6 全量测试。

## Acceptance checklist

- [ ] success/progress/not-found/malformed/nonzero/abort 均有测试；
- [ ] tool progress 来自 Python adapter；
- [ ] summary 不含 figures/timeline/artifact path；
- [ ] 未访问 DB、API、真实 analysis；
- [ ] 未安装 Python 依赖；
- [ ] 仅改 Allowed files，报告 status 后停止。

## Stop conditions

- 需要 HTTP/socket/daemon 才能完成；
- 需要 import 或修改 `webapp/backend`；
- 子进程取消无法可靠结束；
- 需要定义正式权限、owner、delete 或数据保留语义；
- 需要扩大文件范围。

---

# Task 5 — Runtime JSONL recovery and ownership boundary

## Goal

用 Pi 自带 JSONL session 保存 Spike runtime transcript/run marker，验证重开恢复和 interrupted 分类，不实现自动 continuation。

## Depends on

Task 4 已完成且获批。

## Allowed files

仅允许新建：

- `spikes/pi-coach-runtime/src/runtime-session.ts`
- `spikes/pi-coach-runtime/src/run-spike.ts`
- `spikes/pi-coach-runtime/test/runtime-session.test.ts`
- `spikes/pi-coach-runtime/test/recovery.test.ts`

不得提交测试产生的 `.jsonl`；测试只能写 OS temp dir 并在自身 finally 中用 `fs.rm(path, { recursive: true })` 清理临时目录。项目文件删除仍不得使用 `rm -rf`。

## Tests first

测试名必须精确为：

- `Pi JSONL session reopens completed user assistant and tool-result transcript`
- `recovery marks a stale running marker interrupted without replaying the tool`
- `a completed marker does not emit run.interrupted on reopen`
- `storage failure maps to runtime_session_storage_failed`
- `end-to-end Spike emits approved events and recovers the completed transcript`

先写测试并记录实现缺失失败。

## Frozen decisions

- 使用候选源码的 `NodeExecutionEnv`、`JsonlSessionStorage`、`Session`；不得另写 JSONL storage；
- session header metadata 只写 `{ purpose: "pi-coach-runtime-spike" }`；
- runtime session id/run id 由调用方注入，测试固定；不在此定义产品 ID；
- 订阅 Pi `message_end`，按顺序 append user/assistant/toolResult；同一事件只写一次；
- run 开始前写 `running`，正常 `agent_end` 且无 error 后写 `completed`；
- 进程崩溃留下 `running` 是预期证据；reopen 时只标 `interrupted`；
- 不恢复 mid-token、mid-tool，不自动 prompt/continue/replay；
- `.jsonl` 是 Pi runtime state，不是 Coach canonical conversation/store；
- `run-spike.ts` 是 fixture CLI，不监听端口、不接 webapp。

## Exact implementation

1. `runtime-session.ts` 暴露：

```ts
openOrCreateRuntimeSession(options: {
  env: unknown;
  filePath: string;
  sessionId: string;
}): Promise<RuntimeSessionHandle>

recoverInterruptedRun(handle: RuntimeSessionHandle): Promise<{ previousRunId: string } | null>
```

2. 若 file 存在用 `JsonlSessionStorage.open`，否则 `create`；不得吞 storage error。
3. 查找最后一个 `customType === "aiming_cookie_run.v0"` marker；只有最后状态为 `running` 才追加同 run id 的 `interrupted`。
4. 提供 subscriber 将 Pi `message_end` 依次落盘；测试断言重开后的 `Session.buildContext().messages` 包含完成 transcript。
5. interrupted 测试用计数 spy 包裹 tool execute，reopen 后断言计数仍为 0。
6. `run-spike.ts` 组装 Task 3 proxy adapter、Task 4 tool、Task 2 mapper、runtime session；所有 endpoint/session path/run id 由显式参数传入，不读 secret。
7. end-to-end test 启动本地 fake proxy + Python fixture，完成 run，销毁对象，再重建 session 并验证 transcript。

## Verification

运行 §6 全量测试，然后确认仓库没有 Spike runtime 产物：

```bash
find spikes/pi-coach-runtime -type f \( -name '*.jsonl' -o -name '*.log' \) -print
```

预期无输出。

## Acceptance checklist

- [ ] 已完成 transcript 可重开；
- [ ] stale running 被标 interrupted；
- [ ] recovery 不执行 tool、不自动 continue；
- [ ] storage error 稳定映射；
- [ ] end-to-end 链路使用 fake proxy + Python fixture + Pi Agent + Pi JSONL；
- [ ] 无 runtime 产物提交；
- [ ] 仅改 Allowed files，报告 status 后停止。

## Stop conditions

- Pi Session API 与计划不符；
- 恢复已完成 transcript 需要修改 Pi 源码；
- 只能通过自动重放 tool 才能“恢复”；
- 需要设计 Coach DB/schema/migration/retention；
- 需要扩大文件范围。

---

# Task 6 — Evidence-backed assessment and go/no-go recommendation

## Goal

只基于 Task 1–5 的源码与测试证据形成架构评审材料。不得继续实现、vendor 或修改产品。

## Depends on

Task 1–5 全部完成且各自获批。

## Allowed files

允许新建：

- `spikes/pi-coach-runtime/EVIDENCE.md`
- `docs/superpowers/assessments/2026-07-11-pi-agent-coach-runtime-assessment.md`

允许修改：

- `spikes/pi-coach-runtime/assessment/source-inventory.md`
- `spikes/pi-coach-runtime/assessment/license-notes.md`

不得修改代码、测试、spec、PRD、Architecture、旧/new plan。

## Tests first / evidence first

写文档前依次运行：

1. Task 1 全部 identity 与 upstream baseline checks；
2. §6 Spike 全量测试；
3. `git diff --check`；
4. `git status --short`；
5. 检查正式目录未被本计划 Task 修改：

```bash
git diff --name-only -- webapp/backend webapp/frontend
```

该命令可能显示点点原有改动；必须对照 Task 前 status，不能把原有改动归因于 Spike，也不能覆盖它们。

## Frozen decisions

- assessment 只允许四种模块处置：`保留`、`改造`、`禁用`、`删除`；证据不足写 `阻塞`，不得猜；
- 推荐可为 `GO`、`CONDITIONAL GO`、`NO-GO` 三者之一，但不等于批准；
- license 结论必须标技术盘点或待法律复核；
- “完整源码接管是默认起点”仍然有效；Spike 失败不得自动改成外部 dependency + adapter；
- 不把 `coach_runtime_event.v0`、`fake_llm_proxy.v0`、`analysis_tool_stdio.v0` 自动提升为产品 v1；
- 不写正式 schema/API/UI/migration Task；只列后续 plan 的输入合同和 blocker。

## Required `EVIDENCE.md` structure

1. Environment and exact commands；
2. Source identity；
3. Test matrix（test name → capability → result）；
4. Event samples（脱敏、fixture only）；
5. Recovery evidence；
6. Known failures/limitations；
7. Changed files；
8. Checks not run。

不得写“all good”而不列命令与数量。

## Required assessment structure

### A. Executive decision

- recommendation；
- 已证明；
- 未证明；
- blockers；
- 点点/架构负责人必须批准的下一步。

### B. Source baseline and takeover scope

- URL/commit/version/license；
- package include/exclude；
- 正式源码建议目录（只能给一个 evidence-backed recommendation，不创建目录）；
- provenance/patch/maintenance policy 输入。

### C. Module disposition table

至少逐行覆盖 spec §3：Agent loop、tool registry、event stream、session/compaction/recovery、extensions、RPC/process boundary、workspace/sandbox/container、shell/file/coding tools、TUI/CLI/project trust、system prompt、auth/provider config、LLM proxy adapter、Python adapter、source provenance。

列固定为：

| Module/capability | Real source entry | Disposition | Evidence | Required product change | Blocker |
|---|---|---|---|---|---|

### D. Coding surface removal matrix

逐项列 `read/bash/edit/write/find/grep/ls`、extension arbitrary loading、project trust、coding system prompt、CLI/TUI、RPC、telemetry/update/provider attribution；说明默认关闭、删除或改造依据。

### E. Process and trust boundaries

必须画清：

```text
Web/Desktop shell
  ↕ versioned product events (future contract)
Node/TS Coach sidecar (Pi runtime)
  ↕ approved domain-tool IPC
Python analysis runtime
  ↕ owner-checked Aiming Cookie domain access (future; not proven by Spike)
Aiming Cookie cloud LLM proxy
```

分别写明谁拥有 auth、billing、owner check、delete semantics、retry、timeout、secret、sandbox。

### F. State ownership table

至少区分：Coach canonical conversation、Pi runtime transcript、run interruption marker、analysis result、artifact files、usage/billing、workspace files。每行写 canonical owner、runtime cache、delete behavior、Spike evidence、正式待决项。

### G. Event mapping and product gaps

- Pi source event → Spike event；
- token/tool progress/error 已证明；
- confirmation、result intent、reconnect/backpressure、mid-run resume 未证明时明确标 blocker/gap；
- 不得把没有 source event 的 confirmation 写成“已有能力”。

### H. LLM proxy findings

- stream/usage/cancel/error/zero-retry 证据；
- 正式 auth、billing、retry ownership、timeout、rate limit、observability 待决项；
- 明确 fake wire 不是正式 cloud contract。

### I. Recovery findings

- completed transcript reopen；
- stale running classification；
- 不支持/未证明 mid-token/mid-tool continuation；
- Pi runtime session 与 Coach canonical store 的分工。

### J. Workspace/sandbox findings

必须引用真实源码。明确哪些能力在 core，哪些只在 coding-agent/extensions/container 示例，Desktop/internal preview 各自还缺什么。不得以 README 宣传替代部署验证。

### K. License and dependency obligations

- MIT text/copyright；
- NOTICE 是否存在；
- direct/transitive third-party inventory 状态；
- source distribution/attribution 待办；
- 明确“待法律复核”。

### L. Proposed formal takeover file/package list

分别列：

- 建议纳入且尽量保持不动；
- 建议纳入并产品化改造；
- 仅证据参考、不纳入 runtime；
- 禁用/删除；
- 尚阻塞。

必须精确到 package 和关键文件，不得只写“Pi core”。

### M. Inputs to the future replacement migration plan

只列后续 plan 必须冻结的合同与 Task 边界，不写可直接执行的正式 schema/API/UI migration steps。

### N. Final stop gate

明确：未得到点点/架构负责人批准前，不得 vendor source、改 schema/migration/API/UI、解冻旧 plan 或开始正式 Coach migration。

## Verification

```bash
rg -n "recommendation|已证明|未证明|阻塞|Agent loop|coding|sandbox|confirmation|mid-token|license|retry|billing|canonical|不得" \
  spikes/pi-coach-runtime/EVIDENCE.md \
  docs/superpowers/assessments/2026-07-11-pi-agent-coach-runtime-assessment.md

git diff --check
git status --short
```

## Acceptance checklist

- [ ] 所有 required sections 存在；
- [ ] 每个关键判断有源码路径或测试证据；
- [ ] 已证明/推断/未验证清楚区分；
- [ ] confirmation、sandbox、mid-run recovery 未夸大；
- [ ] license 不冒充法律意见；
- [ ] 没有正式产品改动或新 migration plan；
- [ ] 仅改 Allowed files，报告 status 后停止等待裁决。

## Stop conditions

- Task 1–5 任一未完成或验证失败；
- 证据互相矛盾；
- 必须通过真实 key/真实用户数据才能下结论；
- 需要新增产品/schema/migration/security/default 决策；
- 需要修改 Allowed files 之外文件。

---

## 7. Required completion report for every Task

Fast 完成每个 Task 后必须使用以下格式，不能只说“完成了”：

```md
## Task completed
- Task: <编号 + 标题>

## Changed files
- <逐项路径；标 new/modified>

## Tests first
- <先运行命令>
- Initial result: <预期失败/基线结果及原因>

## Verification
- `<精确命令>` → <pass/fail + test 数量或关键输出>

## Acceptance checklist
- [x]/[ ] <逐项>

## Deviations / risks
- None
# 或逐项列出；任何合同偏差不能自行合理化

## Checks not run
- <逐项；无则 None>

## Workspace
- 本 Task 自己的改动：<paths>
- 点点原有改动：<只概括，不修改>
- `git status --short`: <粘贴或准确摘要>

## Stop
- 已停止；未执行下一个 Task；未 commit/push。
```

## 8. Global stop conditions

任一发生即停止并上报：

- 未收到明确 Task 编号；
- 依赖 Task 未批准；
- 候选 Pi identity 变化；
- 需要联网、真实 key、收费模型或真实用户数据；
- 需要修改 Pi 候选源码、正式 backend/frontend、schema、migration、API、UI 或 routes；
- 需要安装/升级正式依赖或 vendor `node_modules`；
- 需要自行决定 upstream、retry、timeout、security、owner、delete、billing、retention 或产品默认值；
- 测试无法按本文命令运行；
- 需要扩大 Allowed files；
- 工作区原有改动与本 Task 文件冲突；
- 发现本文与 PRD/CLAUDE/Architecture/最新 spec 冲突。

## 9. Rollback

- Task 1–5 产物全部隔离在 `spikes/pi-coach-runtime/`；如 assessment 被否决，由点点另行批准后使用系统 `trash` 删除该目录，Fast 不得自行删除。
- Task 6 assessment 是证据文档；否决时保留或归档由点点决定。
- 本计划从未修改正式 schema/API/UI，因此 rollback 不应涉及 database migration、route 或产品代码。
- 不得使用 `rm -rf`、`git reset`、`git checkout` 或覆盖点点原有改动。
