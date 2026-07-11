# Pi Agent Coach Runtime Spike — Evidence

> Assessment date: 2026-07-11. This file records fixture-only, reproducible evidence for the isolated Spike. It is not a production contract and does not approve source vendoring.

## 1. Environment and exact commands

- Repository workspace: `/Users/clickist/Projects/Aiming-cookie`.
- Candidate source checkout: `/tmp/aiming-cookie-pi-assessment` (resolved temporary path reported by the runner: `/private/tmp/aiming-cookie-pi-assessment`).
- Node runtime observed during Task 1: `v25.9.0`; candidate engine: `>=22.19.0`.
- No real API key, paid model, real user data, network endpoint, dependency installation, upstream fetch/pull, or candidate-source edit was used.
- Tests that start a fake proxy bind only a temporary random port on `127.0.0.1`; all test fixtures use `analysis-fixture-1` and the checked-in fixture JSON.

### Source identity and upstream baseline

```bash
test -d /tmp/aiming-cookie-pi-assessment/.git
git -C /tmp/aiming-cookie-pi-assessment remote get-url origin
git -C /tmp/aiming-cookie-pi-assessment rev-parse HEAD
git -C /tmp/aiming-cookie-pi-assessment status --short --branch
node -e 'const p=require("/tmp/aiming-cookie-pi-assessment/package.json"); console.log(p.engines?.node, p.workspaces)'
node -e 'for (const n of ["ai","agent","coding-agent","tui","orchestrator"]) { const p=require(`/tmp/aiming-cookie-pi-assessment/packages/${n}/package.json`); console.log(p.name,p.version) }'
sed -n '1,40p' /tmp/aiming-cookie-pi-assessment/LICENSE
(cd /tmp/aiming-cookie-pi-assessment && npm run test --workspace @earendil-works/pi-agent-core -- --reporter=dot)
```

Result:

- Origin: `https://github.com/earendil-works/pi.git`.
- Commit: `3ea064ea2a0f01965923ce32e1bd17466c502b23` (`fix: support Bedrock API key login`).
- Candidate checkout was clean: `## main...origin/main` with no tracked source modifications.
- Relevant packages were `@earendil-works/pi-ai@0.80.6`, `@earendil-works/pi-agent-core@0.80.6`, `@earendil-works/pi-coding-agent@0.80.6`, `@earendil-works/pi-tui@0.80.6`, and `@earendil-works/pi-orchestrator@0.80.6`.
- Root license begins `MIT License`, copyright `Copyright (c) 2025 Mario Zechner`.
- Upstream Agent Core baseline: **16 test files, 180 tests passed**.

### Spike test command

```bash
PI_SOURCE_DIR=/tmp/aiming-cookie-pi-assessment \
TSX_TSCONFIG_PATH=/tmp/aiming-cookie-pi-assessment/tsconfig.json \
node --import /tmp/aiming-cookie-pi-assessment/node_modules/tsx/dist/loader.mjs \
  --test spikes/pi-coach-runtime/test/*.test.ts
```

Final result: **21 passed, 0 failed, 0 cancelled, 0 skipped**.

### Final repository checks

```bash
git diff --check
git status --short
git diff --name-only -- webapp/backend webapp/frontend
find spikes/pi-coach-runtime -type f \( -name '*.jsonl' -o -name '*.log' \) -print
```

- `find` produced no runtime artifact output.
- `git diff --check` returned only `webapp/tests/test_queue.py:377: new blank line at EOF`. This was a known pre-existing working-tree change outside the allowed files and was not edited.
- The formal `webapp/backend` and `webapp/frontend` diff names were already present before this Spike work; no Task 1–6 file is in either directory.

## 2. Source identity

The candidate source identity, package inventory, runtime/evidence-only/excluded boundaries, and technical license notes are recorded in:

- `spikes/pi-coach-runtime/UPSTREAM.md`
- `spikes/pi-coach-runtime/assessment/source-inventory.md`
- `spikes/pi-coach-runtime/assessment/license-notes.md`

**Proven:** `packages/agent` exports the real `Agent`, tool lifecycle, JSONL session storage, `Session`, and `NodeExecutionEnv` used by the Spike. `packages/ai` supplies the real assistant message event stream used by the proxy bridge. See `packages/agent/src/index.ts`, `packages/agent/src/agent.ts`, `packages/agent/src/agent-loop.ts`, `packages/agent/src/harness/session/jsonl-storage.ts`, `packages/agent/src/harness/session/session.ts`, and `packages/ai/src/index.ts` in the pinned candidate.

**Inferred:** these source entries are suitable starting points for a later full-source product takeover because they passed the isolated fixture chain.

**Unverified:** production Desktop/internal-preview deployment, production workspace isolation, cloud authentication, billing, rate limits, secrets, and durable Coach data migration.

## 3. Test matrix

| Test name | Capability tested against real Pi source | Result |
|---|---|---|
| `real Pi tool forwards Python progress into tool.progress` | Real `Agent` tool lifecycle carries `onUpdate` to `tool_execution_update`, then the mapper emits `tool.progress`. | Pass |
| `read-only fixture tool forwards progress and returns deterministic summary` | Read-only `get_analysis_summary` tool returns the fixture-only summary. | Pass |
| `tool failure maps to one stable run.error without leaking stack` | Tool failure produces a single stable Spike error; stack/absolute-path leakage is excluded. | Pass |
| `mapper emits coach_runtime_event.v0 with monotonic sequence` | Pi events map to the Spike event envelope with increasing sequence. | Pass |
| `mapper only exposes approved event types and payload fields` | Mapper allowlist rejects unapproved product payload. | Pass |
| `mapper ignores thinking and raw provider payloads` | Raw provider/thinking data is not emitted. | Pass |
| `real Pi Agent completes proxy tool turn and final text turn` | Real Agent performs proxy tool-use turn followed by final text turn. | Pass |
| `proxy adapter maps NDJSON text and usage into a Pi assistant stream` | Fake NDJSON text/usage becomes a real Pi assistant stream. | Pass |
| `proxy adapter maps one tool call into Pi toolUse without argument loss` | One fake proxy tool call reaches Pi unchanged. | Pass |
| `proxy adapter aborts the single fetch and emits proxy_aborted` | Abort signal terminates the single fixture fetch and yields a terminal Pi error stream event. | Pass |
| `proxy adapter performs zero retries after HTTP failure` | One failed HTTP request causes zero retry attempts. | Pass |
| `proxy adapter rejects malformed or unterminated NDJSON with proxy_protocol_error` | Invalid fixture stream yields stable protocol failure. | Pass |
| `Python adapter returns one progress event and deterministic analysis_result.v1 summary` | Python stdio adapter emits JSONL progress/result and fixed fixture summary. | Pass |
| `Python adapter returns analysis_not_found for an unknown fixture id` | Valid negative fixture result remains structured. | Pass |
| `Node client maps malformed stdout and nonzero exit to analysis_adapter_failed` | Adapter process failures map to stable `error.v1`. | Pass |
| `Node client terminates the child when AbortSignal is aborted` | Node sends `SIGTERM` and waits for child close. | Pass |
| `recovery marks a stale running marker interrupted without replaying the tool` | JSONL reopen appends `interrupted`; no tool execution or continuation occurs. | Pass |
| `a completed marker does not emit run.interrupted on reopen` | Completed prior marker is not classified as interrupted. | Pass |
| `end-to-end Spike emits approved events and recovers the completed transcript` | Fake proxy + Python fixture + real Agent + JSONL session completes and reopens transcript. | Pass |
| `Pi JSONL session reopens completed user assistant and tool-result transcript` | Pi `Session.buildContext()` returns completed user/assistant/toolResult/assistant sequence after reopen. | Pass |
| `storage failure maps to runtime_session_storage_failed` | Deterministic `ENOTDIR` storage failure maps to a stable error shape. | Pass |

## 4. Event samples (sanitized, fixture only)

The E2E run produced this ordered event-type sequence:

```text
run.started
assistant.completed       # stop_reason=toolUse; fixture usage only
tool.started              # get_analysis_summary, analysis-fixture-1
tool.progress             # { stage: "loading_fixture" }
tool.completed            # deterministic fixture summary only
assistant.delta           # "session coach answer"
assistant.completed       # stop_reason=stop; fixture usage only
run.completed
```

Representative safe envelope:

```json
{
  "schema_version": "coach_runtime_event.v0",
  "run_id": "run-e2e-1",
  "sequence": 4,
  "emitted_at": "2026-07-11T00:00:00.000Z",
  "type": "tool.progress",
  "payload": {
    "tool_call_id": "session-tool-1",
    "tool_name": "get_analysis_summary",
    "details": { "stage": "loading_fixture" }
  }
}
```

Representative stable process failure shape:

```json
{
  "schema_version": "error.v1",
  "category": "local_cv_runtime",
  "code": "analysis_adapter_failed",
  "message": "Analysis adapter failed",
  "retryable": false,
  "trace_id": null,
  "details": null
}
```

No sample includes provider raw events, thinking, secrets, stack traces, absolute artifact paths, or real analysis content.

## 5. Recovery evidence

- `spikes/pi-coach-runtime/src/runtime-session.ts` calls the candidate `JsonlSessionStorage.create/open` and `Session` rather than implementing JSONL.
- The session header metadata is exactly `{ purpose: "pi-coach-runtime-spike" }`.
- The transcript subscriber writes only Pi `message_end` messages whose roles are `user`, `assistant`, or `toolResult`.
- Before a run, `run-spike.ts` appends `aiming_cookie_run.v0` with `{ run_id, status: "running" }`; after normal `agent_end` without assistant error it appends `completed`.
- On reopen, only the last `running` marker gets an appended same-run `interrupted` marker. The recovery test verifies the tool execution count remains zero.
- The reopened completed context is exactly role-ordered as `user`, `assistant`, `toolResult`, `assistant` for the fixture run.

**Limit:** this proves completed transcript reopen and stale-run classification only. It does not prove or claim mid-token resume, mid-tool continuation, tool replay, reconnect, cross-process locking, or Coach canonical-store recovery.

## 6. Known failures and limitations

1. The Spike uses a fake local NDJSON wire protocol (`fake_llm_proxy.v0`), not a cloud LLM contract. It deliberately has no auth, billing, retry/backoff, timeout, rate limit, observability, or provider configuration.
2. The Python adapter reads a checked-in fixture and uses no production DB, domain API, real analysis, queue, or artifact file access.
3. The runtime session JSONL is a Pi runtime transcript/marker only; it is not a canonical Coach conversation, billing ledger, analysis owner record, or deletion authority.
4. Confirmation/approval events, user result intent, client reconnect/backpressure, cancellation UX, and mid-run continuation have not been proven by this Spike.
5. `packages/coding-agent` workspace tools, arbitrary extension loading, project trust, RPC, CLI/TUI, and any sandbox/container example were not enabled or safety-validated as Coach runtime capabilities.
6. MIT and dependency facts are a technical inventory only; legal review remains required.

## 7. Changed files

All implementation evidence is isolated under `spikes/pi-coach-runtime/`:

- Task 1: `UPSTREAM.md`; `assessment/source-inventory.md`; `assessment/license-notes.md`.
- Task 2: `package.json`; `tsconfig.json`; `src/contracts.ts`; `src/pi-source.ts`; `src/fake-stream.ts`; `src/analysis-summary-tool.ts`; `src/event-mapper.ts`; `test/agent-loop.test.ts`; `test/event-mapper.test.ts`.
- Task 3: `src/proxy-stream.ts`; `test/proxy-stream.test.ts`; `test/proxy-agent-integration.test.ts`.
- Task 4: `fixtures/analysis-result.v1.json`; `python/analysis_adapter.py`; `src/python-analysis-client.ts`; `test/python-analysis-client.test.ts`; permitted updates to `src/analysis-summary-tool.ts`, `test/agent-loop.test.ts`, and `test/proxy-agent-integration.test.ts`.
- Task 5: `src/runtime-session.ts`; `src/run-spike.ts`; `test/runtime-session.test.ts`; `test/recovery.test.ts`.
- Task 6: this file and `docs/superpowers/assessments/2026-07-11-pi-agent-coach-runtime-assessment.md`.

No `webapp/backend`, `webapp/frontend`, schema, API, UI, route, PRD, Architecture, Roadmap, migration plan, `output/frame_errors.csv`, or `output/metrics.json` file was modified by this assessment/Spike scope.

## 8. Checks not run

- Real cloud LLM endpoint/authentication/billing/rate-limit/observability tests: intentionally not run; would require product decisions and/or credentials.
- Real user analysis, database, artifact, queue, filesystem-owner, delete-semantics, or migration tests: intentionally not run; outside isolated Spike scope.
- Desktop shell, internal preview deployment, container, workspace sandbox, project trust, extension loading, coding CLI/TUI, RPC, and host-level permission tests: not run; not evidence from the core Agent Spike.
- Formal legal license/compliance review and complete transitive dependency/SBOM review: not run; required before production source distribution/vendoring.
- Whole-worktree `git diff --check` is not fully clean because of the known pre-existing blank line at `webapp/tests/test_queue.py:377`; it was not changed.
