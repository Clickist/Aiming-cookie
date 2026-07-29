# Coach Field Regression Repair - Implementation Plan

> **Status: Task 1 implementation complete (2026-07-27); awaiting workspace batching.** 点点已授权 Task 1 全部修复；本计划不提交、不推送，不修改 PRD / Architecture。

**Goal:** 修复真实 Provider 复测中暴露的无依据指标、停止后旧回复串线、Analysis 删除缺少结构化确认、Evidence 查询参数不可用和无上下文伪精确处方。

**Architecture:** 在 Pi sidecar 输出边界做确定性 grounding 与 turn correlation 校验，首稿不合格时最多进行一次只重写文本的 grounding repair，第二稿仍不合格则 fail-closed；提示词只作为第二道约束。通过现有 owner-scoped product command journal 增加 `analysis.delete`，复用既有 pending confirmation、幂等 reservation 与 `queue.delete_session()`；Evidence 查询继续 fail-closed，只补充模型可执行的精确参数工作流，不放宽 reachable-ref。

**Tech Stack:** TypeScript / pinned Pi / Node test runner；FastAPI / SQLite / pytest。

---

## Task 1 - Repair grounded Coach turns and consequential commands

### Allowed files

- `docs/superpowers/plans/2026-07-27-coach-field-regression-repair.md`
- `docs/superpowers/plans/README.md`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/product-command-tools.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/test/product-command-tools.test.ts`
- `webapp/coach-runtime/test/sidecar-server.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_runtime.py`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_task6_backend_contracts.py`

### Tests first

1. A comparison reply that cites a metric unavailable in one attached Analysis is rejected as `grounding_violation`; the invalid assistant reply is not persisted.
2. A no-context reply that invents an exact quantitative dose is rejected unless the value appears in the user request or a current-turn bounded tool result.
3. A stopped turn may return only text generated after the current prompt; prior assistant history cannot become `partial_reply`. Sidecar responses carry the request `run_id`, and Python rejects a mismatched response.
4. `analysis.delete` is in the TypeScript and Python write allowlists, accepts only `analysis_ref`, returns `needs_confirmation` for Coach-inferred use, deletes only after the existing confirmation decision, remains owner-scoped and idempotent, and never deletes Run-owned evidence.
5. The product tool description documents the exact Evidence chain: start from `analysis:N`; call `analysis.evidence.list` first; call `analysis.evidence.signal_window` only with a returned `segment_ref` and returned `available_channels`. Guessed artifact/segment refs remain rejected.
6. Limitations remain non-causal: missing or unsupported signals cannot be presented as measured player deficits. Prompt guidance and grounding checks must agree.
7. 首稿 grounding 失败后最多重写一次；重写只复用当前 turn 已有上下文和 tool results，不要求重复副作用。合格重写成功返回，第二次仍失败则返回 retryable `grounding_violation` 且不持久化任一错误草稿。

### Verification

1. Run focused Node tests for turn, sidecar, product commands and prompt.
2. Run focused Python tests for runtime correlation, product command confirmation/deletion, agent-run persistence and Evidence reachability.
3. Run the complete Coach Node suite and Coach/backend Python contract suites with `KOVAAK_INSTALL_DIR` set to a nonexistent path.
4. Reuse the existing isolated Task 12 field DB and configured Provider for 0/1/N context, stop/retry, Evidence and delete-confirmation tests. Do not recapture KovaaK data and do not expose credentials or local paths.

### Frozen decisions

- Grounding is enforced at a deterministic boundary; prompt wording alone is not accepted as the fix.
- Coach still receives only bounded L1-L3 projections and registered tool results. Raw trace, paths, original CSV/protobuf, video frames, secrets and arbitrary payloads remain unavailable.
- `analysis.delete` always requires the existing explicit confirmation flow. A prose request or prose confirmation is never sufficient.
- Evidence descendants become reachable only after a successful bounded query returns them; the model cannot guess refs.
- No new dependency, migration, frontend page, product default or release claim is introduced.

### Stop rule

- A fix requires changing PRD / Architecture, weakening owner/ref validation, exposing full tool payloads in persisted traces, adding a dependency or migration, or changing Analysis deletion semantics.
- The real Provider still produces an unsupported claim after the deterministic guard should have rejected it, or confirmation can execute without a canonical pending record.

## Closeout checkpoint (2026-07-27)

- Coach egress now removes headings, emphasis, inline code, numbered-list prefixes and bullet prefixes without consuming the bounded grounding repair.
- Runtime failure DTOs retain their machine codes and retryability while exposing stable Chinese user messages instead of internal exception text.
- Point-in-time validation: focused turn tests `49/49`, complete Coach Node suite `113/113`, Coach/Python contracts `225/225`, and full pytest `1311 passed, 5 skipped`.
- The isolated real Provider matrix passed 0/1/N context, Evidence, delete-confirmation rejection and stop. Analysis 3 remained `done`; replies contained no Markdown list prefixes or internal error messages; no credential pattern, traceback or SQLite error appeared in the captured logs.
- Per 点点's decision, Chinese-form prescription quantities and model-rendered unit conversions were not expanded in this final repair slice. The deterministic fail-closed behavior remains unchanged.
