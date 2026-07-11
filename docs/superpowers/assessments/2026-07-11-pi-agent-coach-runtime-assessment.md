# Pi Agent Coach Runtime Assessment — 2026-07-11

> **Assessment status:** evidence-backed architecture review only. It does not approve vendoring, a schema/API/UI migration, or execution of the retired Coach migration plan.
>
> **Evidence set:** `spikes/pi-coach-runtime/EVIDENCE.md`, the isolated Spike at `spikes/pi-coach-runtime/`, candidate source commit `3ea064ea2a0f01965923ce32e1bd17466c502b23`, and the source inventory/license notes adjacent to the Spike.

## A. Executive decision

### Recommendation: CONDITIONAL GO

The frozen direction—**take over the complete Pi source tree as the default baseline and maximize reuse of the mature `Agent` runtime; do not reimplement the agent loop**—has sufficient isolated evidence to proceed to a *new, separately approved formal takeover implementation plan*. It is **not** a production GO and is not approval to vendor source now.

### 已证明

- Pinned Pi `packages/agent` and `packages/ai` can be driven directly from source through the real `Agent` loop, injected `streamFn`, tool registry, tool progress, abort path, assistant event stream, JSONL session storage, and `Session`; upstream Agent Core baseline is 180/180 passing.
- A read-only domain-shaped tool can execute a fixture-only Python stdio adapter, surface progress through Pi events, and return stable summary/error envelopes without rewriting the agent loop.
- A local fixture proxy can drive a tool-use turn and a final assistant turn; stream text, usage, one tool call, malformed-stream failure, HTTP zero-retry, and abort behavior were tested.
- Pi JSONL can reopen completed user/assistant/tool-result transcript entries and classify a final stale `running` run marker as `interrupted` without tool replay.
- The Spike event mapper can safely allowlist token, assistant completion, tool start/progress/end, run lifecycle, and one stable error path while excluding raw provider events and thinking.

### 推断（需在正式计划中复核）

- A full-source takeover rooted at one vendored Pi source snapshot can preserve `packages/ai` + `packages/agent` as the runtime core while treating coding-agent surfaces as removable/productized rather than as a second agent loop.
- A thin Aiming Cookie proxy bridge plus approved domain-tool IPC is likely the minimal integration seam; that inference is based on the fixture chain, not a production cloud protocol.

### 未证明 / blockers

1. Production canonical Coach data ownership, schema compatibility migration, delete semantics, owner authorization, retention, and cross-device sync are unproven and require explicit product/architecture decisions.
2. Production LLM auth, secret handling, billing/usage authority, retry ownership, timeout, rate limiting, observability, provider selection, and cloud error contracts are unproven.
3. Confirmation/approval flow, result intent, reconnect/backpressure, mid-token resume, mid-tool continuation, and user-visible cancellation semantics are unproven.
4. A safe Desktop/internal-preview workspace, sandbox/container, extension, project-trust, host filesystem, and subprocess policy is unproven. A coding-agent sandbox example is not core Agent proof.
5. Legal review plus a direct-and-transitive third-party dependency/notice inventory remains required before source distribution or product vendoring.

### 点点 / 架构负责人必须批准的下一步

Approve or reject a new formal takeover plan only after it freezes the source layout/provenance policy, module removal list, process and trust ownership, persistent Coach state/delete contracts, LLM proxy responsibilities, product event contract, and explicit preview gates. Until then, the final stop gate in section N applies.

## B. Source baseline and takeover scope

| Item | Evidence-backed value |
|---|---|
| Upstream | `https://github.com/earendil-works/pi.git` |
| Frozen candidate | `3ea064ea2a0f01965923ce32e1bd17466c502b23` (`fix: support Bedrock API key login`) |
| Assessed packages | `@earendil-works/pi-ai@0.80.6`, `@earendil-works/pi-agent-core@0.80.6`, `@earendil-works/pi-coding-agent@0.80.6`, `@earendil-works/pi-tui@0.80.6`, `@earendil-works/pi-orchestrator@0.80.6` |
| Node engine / observed runtime | `>=22.19.0` / `v25.9.0` |
| License fact | Root MIT text, `Copyright (c) 2025 Mario Zechner`; technical inventory only, pending legal review |
| Baseline test | `npm run test --workspace @earendil-works/pi-agent-core -- --reporter=dot` → 180/180 passed |

### One recommended formal source directory

If approved, import the **complete frozen Pi source tree** under exactly one repository root: `third_party/pi/`. Preserve the complete pinned source baseline and provenance material there; do not substitute an external npm dependency, do not copy `node_modules`, and do not make continuing upstream compatibility a requirement. This keeps the PRD/Architecture direction of full source takeover while allowing the formal plan to compile/use only approved runtime packages.

### Required provenance / patch / maintenance policy inputs

The future plan must freeze: source import digest/commit, upstream URL, local patch ledger, permitted local patch locations, upstream update policy (including explicit non-following option), license/NOTICE placement, lockfile and transitive inventory generation, CI verification against the vendored source, and removal review after each Pi update. These are required inputs, not decisions made by this assessment.

## C. Module disposition table

`保留`/`改造`/`禁用`/`删除` are the only affirmative dispositions. `阻塞` means evidence is insufficient and no safe disposition has been selected.

| Module/capability | Real source entry | Disposition | Evidence | Required product change | Blocker |
|---|---|---|---|---|---|
| Agent loop | `packages/agent/src/agent.ts`; `packages/agent/src/agent-loop.ts` | 保留 | Real Agent completed fixture tool + final text turn; 180 core tests. | Invoke through product sidecar and approved tools; do not reimplement loop. | Product lifecycle/concurrency policy not frozen. |
| Tool registry / execute / updates | `packages/agent/src/types.ts`; `agent.ts`; `agent-loop.ts:390-787` | 保留 | Real `initialState.tools`, execute and `onUpdate` used by Spike. | Register only versioned Aiming Cookie domain tools. | Tool authorization/confirmation policy unproven. |
| Assistant event stream | `packages/ai/src/index.ts`; `packages/ai/src/types.ts`; `packages/agent/src/types.ts` | 改造 | Real assistant stream and Agent events mapped in 21 passing tests. | Freeze future product event version, reconnect/backpressure/error semantics. | Confirmation/result-intent/reconnect gaps. |
| Session / compaction / recovery | `packages/agent/src/harness/session/jsonl-storage.ts`; `session.ts`; `harness/compaction/` | 改造 | JSONL reopen + stale marker classification proven; compaction only source-inventoried. | Separate runtime transcript from canonical Coach store; define compaction ownership. | Canonical schema, delete/retention, locking and mid-run recovery unproven. |
| Harness | `packages/agent/src/harness/agent-harness.ts` | 改造 | Source inventory only; not exercised in Spike. | Evaluate only if later sidecar needs harness behavior. | Need formal lifecycle/system-prompt decision. |
| Extensions | `packages/coding-agent/src/core/extensions/loader.ts:521-698` | 禁用 | Source inventory identifies arbitrary local/global loading; no Coach need proven. | Do not load extensions by default. | Any approved extensibility model must be specified first. |
| RPC/process boundary | `packages/coding-agent/src/rpc-entry.ts`; `modes/rpc/rpc-types.ts`; `packages/orchestrator/src/*` | 删除 | Not required by Spike; generic coding/process surfaces were not tested. | Use a later approved Node/Python product IPC contract instead. | IPC auth/framing/owner policy not frozen. |
| Workspace / sandbox / container | `packages/coding-agent/examples/extensions/sandbox/index.ts`; no core Agent container entry found | 阻塞 | Example-only sandbox evidence; no Coach deployment validation. | None until Desktop/internal preview policy is designed and tested. | Safe sandbox/container/workspace model is unproven. |
| Shell/file/coding tools | `packages/coding-agent/src/core/tools/{read,bash,edit,write,find,grep,ls}.ts` | 删除 | Explicitly coding-oriented and unused by isolated Coach Spike. | Omit entirely from Coach runtime. | None for default removal. |
| TUI / CLI | `packages/coding-agent/src/main.ts`; `packages/coding-agent/src/modes/*`; `packages/tui/src/*` | 删除 | Not used by Spike or Desktop product path. | Omit runtime entrypoints. | None for default removal. |
| Project trust | `packages/coding-agent/src/core/project-trust.ts`; `trust-manager.ts`; `cli/project-trust.ts` | 禁用 | Coding-project trust, not Coach ownership proof. | Do not map it to account/session authorization. | Product owner authorization still needs design. |
| Coding system prompt | `packages/coding-agent/src/core/system-prompt.ts` | 删除 | Coding prompt is not Coach behavior and was not used. | Replace later with approved Coach prompt policy, if needed. | Coach prompt/content safety policy not frozen. |
| Provider/auth configuration | `packages/ai` provider clients; coding-agent configuration surfaces | 改造 | Real `streamFn` injection proved; production provider/auth was deliberately not used. | Route through Aiming Cookie cloud proxy/secret boundary. | Auth, provider, telemetry, retry, billing policies unproven. |
| Aiming Cookie LLM proxy adapter | Spike `src/proxy-stream.ts`; Pi `StreamFn` in `packages/agent/src/types.ts` | 改造 | NDJSON/usage/abort/error/zero-retry fixture tests pass. | Freeze real cloud contract without duplicating model agent loop. | Fake wire is not production protocol; timeout/rate-limit/observability absent. |
| Python analysis adapter | Spike `python/analysis_adapter.py`; `src/python-analysis-client.ts`; Pi tool APIs | 改造 | Fixture stdio request/progress/result/error/abort passed. | Bind only approved analysis/domain IPC and ownership checks. | Real analysis access, filesystem/artifact and cancellation semantics unproven. |
| Source provenance / dependency obligations | Root `LICENSE`; candidate manifests; Spike inventory/license notes | 改造 | MIT fact and direct dependency inventory recorded. | Vendor license/NOTICE, source provenance, SBOM, patch/update records. | Legal review and transitive inventory required. |

## D. Coding surface removal matrix

| Surface | Default formal disposition | Evidence / reason |
|---|---|---|
| `read` | 删除 | `packages/coding-agent/src/core/tools/read.ts` is coding workspace capability; no Coach requirement. |
| `bash` | 删除 | `packages/coding-agent/src/core/tools/bash.ts` would create arbitrary command execution; no Spike evidence or approved product policy. |
| `edit` | 删除 | Coding source-edit capability; not a Coach tool. |
| `write` | 删除 | Coding filesystem write capability; not a Coach tool. |
| `find` | 删除 | Coding workspace traversal; not a Coach tool. |
| `grep` | 删除 | Coding workspace search; not a Coach tool. |
| `ls` | 删除 | Coding workspace listing; not a Coach tool. |
| Extension arbitrary loading | 禁用 | Loader source is coding-agent-specific; no allowlist, signing, or Coach extension policy is proven. |
| Project trust | 禁用 | Coding project trust must not be treated as account/analysis authorization. |
| Coding system prompt | 删除 | Coding-agent prompt conflicts with Coach product role. |
| CLI/TUI | 删除 | `packages/coding-agent` modes and `packages/tui` are not the target Web/Desktop UI integration. |
| RPC | 删除 | Coding RPC/orchestrator is unneeded evidence-only surface; later product IPC must be explicit. |
| Telemetry/update/provider attribution | 改造 | No production observability/update/provider policy was exercised; preserve only what formal plan explicitly owns. |

## E. Process and trust boundaries

```text
Web/Desktop shell
  ↕ versioned product events (future contract; not frozen by Spike)
Node/TS Coach sidecar (Pi runtime)
  ↕ approved domain-tool IPC
Python analysis runtime
  ↕ owner-checked Aiming Cookie domain access (future; not proven by Spike)
Aiming Cookie cloud LLM proxy
```

| Boundary responsibility | Required owner | Spike evidence / status |
|---|---|---|
| Cloud auth and secrets | Aiming Cookie cloud proxy | Not used or proven; fake proxy has no auth. |
| Billing/usage authority | Aiming Cookie cloud proxy / product ledger | Fixture usage maps into assistant data; billing ledger is unproven. |
| Owner check | Aiming Cookie canonical domain layer | Not accessed by Spike; must not be delegated to Pi JSONL or coding project trust. |
| Delete semantics | Aiming Cookie canonical Coach/analysis domain | Not designed or tested; runtime marker/transcript is not authority. |
| Retry and timeout | Future frozen product proxy/tool contract | Spike deliberately uses zero retries and no timeout/backoff. |
| Secret handling | Product secret manager/cloud proxy | Not used or proven. |
| Sandbox | Future Desktop/internal-preview runtime policy | Blocked; coding example is not core proof. |
| Tool authorization | Approved domain-tool layer | Read-only fixture tool only; confirmation/mutation policy unproven. |

## F. State ownership table

| State | Canonical owner | Runtime cache / copy | Delete behavior | Spike evidence | Formal decision still required |
|---|---|---|---|---|---|
| Coach canonical conversation | Future Aiming Cookie persistent store | Pi JSONL transcript may be transient/export-like runtime state only | Must be product-defined; deleting analysis must not silently delete Coach history | Not proven; explicit boundary only | Schema, migration, retention, delete semantics, sync |
| Pi runtime transcript | Pi `JsonlSessionStorage` / `Session` | JSONL file | Runtime lifecycle only; not canonical conversation deletion | Reopen completed messages proven | File location, retention, encryption, locking |
| Run interruption marker | Pi JSONL custom entry `aiming_cookie_run.v0` | Same runtime session | Classify stale `running`; no automatic continuation | `running` → `interrupted` no replay proven | User-facing recovery and cleanup |
| Analysis result | Aiming Cookie analysis domain | Fixture summary passed through tool | Domain-defined | Fixture only | Owner authorization, freshness, invalidation |
| Artifact files | Aiming Cookie artifact/domain layer | None in Spike | Domain-defined | Not accessed | Path policy, owner checks, deletion, sandbox |
| Usage/billing | Aiming Cookie cloud/product ledger | Fixture usage fields only | Product-defined | Usage parsing proven; billing not proven | Accounting, retries, refunds, observability |
| Workspace files | No Coach workspace default | None | Not applicable by default | Coding tools excluded | Any future workspace feature and safe sandbox policy |

## G. Event mapping and product gaps

### Proven source-to-Spike mapping

| Pi source event | Spike event | Evidence |
|---|---|---|
| `agent_start` | `run.started` | `src/event-mapper.ts`; mapper tests |
| assistant `message_update` with `text_delta` | `assistant.delta` | proxy text test and E2E |
| assistant `message_end` | `assistant.completed` with stop reason/usage | proxy Agent integration and E2E |
| `tool_execution_start` | `tool.started` | real tool and E2E |
| `tool_execution_update` | `tool.progress` | Python progress test |
| `tool_execution_end` | `tool.completed`; tool-error path emits stable `run.error` | mapper/error tests |
| `agent_end` | `run.completed` | E2E |
| stale JSONL `running` marker | `run.interrupted` on next run | recovery test |

### Product gaps / blockers

- **Confirmation:** no source event was exercised or mapped; do not claim existing confirmation capability.
- **Result intent:** no product-level result/next-action intent contract was exercised.
- **Reconnect/backpressure:** no client transport contract or replay cursor was tested.
- **Provider terminal errors:** the proxy turns failure into a terminal Pi stream error, but a complete product event/error policy for all provider cases is not proven.
- **Mid-run resume:** not supported by this Spike; no mid-token/mid-tool continuation or tool replay is promised.
- `coach_runtime_event.v0`, `fake_llm_proxy.v0`, and `analysis_tool_stdio.v0` remain Spike-only and must not be silently promoted to product v1.

## H. LLM proxy findings

**Proven in fixture scope:** one Pi provider call makes one HTTP POST to the explicit injected endpoint; only `content-type: application/json` and `accept: application/x-ndjson` are set; NDJSON text/tool-call/done usage maps into Pi; malformed/unterminated NDJSON, HTTP failure, fetch failure, and abort become terminal Pi stream errors; abort terminates the fetch; zero retries are performed.

**Not proven:** real proxy authentication, request identity, provider choice, cloud billing/usage authority, timeout, retry/backoff, rate limits, circuit breaking, audit/telemetry, observability, logging redaction, and production error versioning.

The fake wire exists only to test the `StreamFn` seam. It is **not** a formal Aiming Cookie cloud LLM API contract and cannot define billing or retry ownership.

## I. Recovery findings

Completed Pi transcript recovery is proven through `JsonlSessionStorage.open` plus `Session.buildContext()`. A stale final `aiming_cookie_run.v0` marker with `status: "running"` is classified by appending a same-run `interrupted` marker. The test verifies no tool executes and no `Agent.continue()`/prompt/replay occurs.

The recovery limit is deliberate: no mid-token, mid-tool, or side-effect continuation is offered. Pi runtime session data must remain distinct from the future canonical Coach store, which owns user-visible message history, deletion, retention, and account/analysis relationships.

## J. Workspace and sandbox findings

The verified runtime core is `packages/ai` + `packages/agent`; it does not prove a Coach sandbox. `packages/coding-agent/examples/extensions/sandbox/index.ts` is an extension example, while coding tools/trust/extensions live under `packages/coding-agent`. Source inventory found no core container runtime entry under `packages/agent/src`.

Therefore:

- Core Agent capability: real loop, events, tools, abort, session, JSONL—**proven**.
- Coding-agent/extensions/container examples: evidence-only and not a default Coach capability—**not deployment-proven**.
- Desktop shell and internal preview: no local host permission, container, sandbox, or deployment validation was run—**blocked**.

README claims or examples are not substitutes for source-backed and deployment-backed safety evidence.

## K. License and dependency obligations

The root candidate license is MIT with Mario Zechner copyright. No root `NOTICE` was found during the technical inventory. `spikes/pi-coach-runtime/assessment/license-notes.md` records direct dependencies for the five assessed packages.

This is a **technical inventory, pending legal review**, not a legal conclusion. Before source distribution or vendoring, the formal takeover work must generate a complete direct/transitive dependency inventory (including lockfile-resolved artifacts), identify licenses/notices/source-distribution obligations, define attribution placement, and obtain legal review.

## L. Proposed formal takeover file/package list

### 建议纳入且尽量保持不动

- Full pinned source tree under proposed `third_party/pi/`, with provenance files and root license retained.
- `third_party/pi/packages/ai/src/index.ts` and the assistant-stream/types implementation it exports.
- `third_party/pi/packages/agent/src/agent.ts`, `agent-loop.ts`, `types.ts`, `index.ts`.
- `third_party/pi/packages/agent/src/harness/session/jsonl-storage.ts`, `harness/session/session.ts`, and `harness/env/nodejs.ts`, subject to product runtime path/retention policy.

### 建议纳入并产品化改造

- `third_party/pi/packages/agent/src/harness/agent-harness.ts` and `harness/compaction/*` only after a formal lifecycle/context policy review.
- Pi build/package manifests and selected `packages/ai` provider-adjacent configuration only as needed to support the product-owned `StreamFn` proxy seam.
- A new product-owned LLM proxy adapter, domain-tool registry, Node/Python IPC boundary, event bridge, and persistent Coach integration—these are new product layers, not a replacement agent loop.
- Provenance/license/dependency material: `LICENSE`, any required notices, source manifest, patch ledger, dependency/SBOM output.

### 仅证据参考、不纳入 runtime

- `third_party/pi/packages/coding-agent/src/core/agent-session-runtime.ts` and `core/agent-session.ts` until a future plan demonstrates a Coach need.
- `third_party/pi/packages/coding-agent/examples/extensions/sandbox/*` as example-only reference, not a production sandbox.
- `third_party/pi/packages/orchestrator/src/*` as process/RPC reference only.

### 禁用 / 删除

- `third_party/pi/packages/coding-agent/src/core/tools/{read,bash,edit,write,find,grep,ls}.ts`.
- `third_party/pi/packages/coding-agent/src/core/extensions/loader.ts` arbitrary loading path.
- `third_party/pi/packages/coding-agent/src/core/{project-trust.ts,trust-manager.ts}` and `cli/project-trust.ts` from the Coach path.
- `third_party/pi/packages/coding-agent/src/core/system-prompt.ts` from the Coach path.
- `third_party/pi/packages/coding-agent/src/{main.ts,modes/*,rpc-entry.ts}`; `third_party/pi/packages/tui/src/*`; `third_party/pi/packages/orchestrator/src/*` from the Coach runtime product path.

### 尚阻塞

- Any source selection or modification claiming a safe product workspace/container/sandbox.
- Any canonical Coach persistence, migration, delete/retention, authentication/owner check, billing, timeout/retry, confirmation, or cloud proxy policy.
- Any legal distribution conclusion before SBOM/notices and legal review.

## M. Inputs to the future replacement migration plan

The replacement plan must freeze contracts and task boundaries before implementation; it must not inherit Spike defaults silently:

1. Full-source import layout at `third_party/pi/`, provenance digest, license/NOTICE/SBOM process, patch ledger, and update policy.
2. Exact compiled/runtime package graph and an approved removal/disable manifest for all coding, trust, extension, CLI/TUI, RPC, and workspace surfaces.
3. Node Coach sidecar lifecycle, process supervision, crash handling, storage location/locking, and supported recovery boundary.
4. Versioned product event contract: payload allowlist, sequence/cursor/reconnect/backpressure, confirmation, cancellation, terminal errors, and result intent.
5. Product-owned LLM proxy contract: auth, secret handling, identity, provider attribution, usage/billing authority, timeout, retry, rate limiting, logs, observability, and redaction.
6. Approved domain-tool IPC: tool allowlist, owner authorization, confirmation/mutation semantics, Python cancellation, result validation, and stable product errors.
7. Canonical Coach conversation and analysis relationship: migration from session-bound chat, delete semantics, retention, export/sync, privacy, and regression tests.
8. Desktop/internal-preview sandbox/workspace policy validated separately from coding-agent examples.
9. Legal/compliance completion criteria and CI checks for source provenance and dependency inventory.
10. Explicit preview gate, rollback boundary, and test plan covering deletion, authorization, recovery, offline/error handling, and UI/API compatibility.

## N. Final stop gate

**不得** vendor source, modify formal schema/migration/API/UI/routes, unfreeze the retired plan, or start formal Coach migration until 点点 and the architecture owner approve a new formal implementation plan that resolves the listed blockers. The Spike stays isolated; no `coach_runtime_event.v0`, fake proxy protocol, or fixture stdio protocol is automatically a product v1 contract.
