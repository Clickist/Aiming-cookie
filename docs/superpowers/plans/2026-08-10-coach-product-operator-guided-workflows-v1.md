# Coach Product Operator and Guided Workflows Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** active; no Task is authorized until 点点 explicitly names it.

**Goal:** Let the existing Pi Coach execute every eligible registered product operation from an explicit natural-language instruction and guide user-only steps through bounded, state-verified frontend intents.

**Architecture:** Reuse the current Pi Agent, product-command bridge, confirmation/idempotency/audit boundary, Coach Agent runs/events, TeachingSession, Training Plan, routes, and stores. Add a message-bound instruction grant, a derived ProductReadiness projection, a typed GuidanceIntent envelope, and one frontend GuidanceHost; do not add another Agent, generic automation framework, or workflow store.

**Tech Stack:** Python/FastAPI/SQLite backend, existing TypeScript Pi Coach runtime, Next.js/React frontend, Tauri 2 desktop adapters, pytest and Node test runner.

**Design contract:** [`../specs/2026-08-10-coach-product-operator-guided-workflows-design.md`](../specs/2026-08-10-coach-product-operator-guided-workflows-design.md)

---

## Frozen decisions

- A current, unambiguous natural-language instruction directly authorizes its registered product operation, including destructive operations; no redundant confirmation is added.
- Coach-proposed consequential operations remain `coach_inferred` and use the existing confirmation flow.
- Pi proposes a command plus an exact current-message instruction quote; only trusted backend code can bind and assign `explicit_user_request`.
- Credential/OAuth input, system/privacy permission, file selection, physical training, and subjective/reality-dependent facts remain user-only.
- Pi remains the only Agent runtime. No shell, filesystem, arbitrary network, DOM selector, simulated input, generic Tauri invoke, second product store, or second workflow engine is allowed.
- Provider profile mutation work in Task 3 may start only after the active `2026-08-09-coach-first-single-pipeline-v1.md` Task 3 is terminal and no executor is concurrently relying on its negative Provider-command tests. This plan expands that boundary later; it does not rewrite the older Task in place.

## Task 1: Message-bound authorization for existing commands

**Allowed files:**
- Modify: `webapp/backend/coach_commands.py`
- Modify: `webapp/backend/coach_agent_runs.py`
- Modify: `webapp/backend/coach_service.py`
- Modify: `webapp/coach-runtime/src/product-command-tools.ts`
- Modify: `webapp/coach-runtime/src/turn.ts`
- Test: `webapp/tests/test_coach_commands.py`
- Test: `webapp/tests/test_coach_agent_runs.py`
- Test: `webapp/tests/test_coach_runtime.py`
- Test: `webapp/coach-runtime/test/product-command-tools.test.ts`
- Test: `webapp/coach-runtime/test/turn-fake-stream.test.ts`

**Tests first:**

1. Add failing tests proving an exact instruction quote from the persisted current message can directly execute an existing write, including `analysis.delete`, with `authorization_source=explicit_user_request` and no confirmation row.
2. Add failing tests proving a missing/non-matching quote, altered command parameters, another owner/thread/message, an expired bridge, and an unregistered command cannot receive the grant; a Coach-proposed write still returns `needs_confirmation`.
3. Add the critical ambiguity test: two compatible Analysis/Run refs are reachable in the same turn, the quote does not resolve one uniquely, and direct authorization is rejected until the user disambiguates.
4. Add TeachingSession tests proving an explicitly stated execution/retest fact can reconcile from an audited direct-success event, while a Coach-inferred fact still requires the existing pending confirmation.
5. Add failing tests proving Pi payloads still cannot provide `authorization_source`, owner, confirmation ref, grant metadata, secret, URL, or path.
6. Add a failing audit assertion for safe message ref, command name, resolved target/parameter digest, grant/result status, and no raw instruction/secret.

**Implementation:**

7. Extend the existing Pi product-command tool schema with one bounded `instruction_quote` field; describe it as an exact substring used only when the current message explicitly requests that command.
8. Pass the current user message to `issue_tool_bridge` as turn-scoped in-memory material; do not add it to the public bridge envelope, normal logs, or command audit.
9. Before granting, resolve each command's required ref kind from the current selected/context ref or exactly one compatible reachable ref. Parse scalar user facts through the command's existing typed validator. Zero/multiple refs or unstated scalar values remain `coach_inferred`/ambiguous.
10. Add an internal `instruction_grant.v1` record to `_ToolBridge`. Bind the resolved target and normalized parameter digest, owner/thread/message, expiry, and bridge before assigning `explicit_user_request`.
11. Extend TeachingSession reconciliation to accept only audited `explicit_user_request` success for a fact explicitly present in the current message; keep the `coach_inferred -> needs_confirmation` path unchanged.
12. Keep confirmation consumption, journal reservation, idempotency replay/conflict, audit, and result redaction unchanged.
13. Run:
   - `.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_commands.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_coach_runtime.py -q`
   - `npm.cmd --prefix webapp/coach-runtime test -- product-command-tools.test.ts turn-fake-stream.test.ts`

**Stop rule:** Stop if direct authorization requires trusting a model-supplied authorization source or arbitrary model-selected target, persisting raw user text in audit/grant storage, widening bridge payloads beyond the exact quote, or weakening owner/ref/idempotency/TeachingSession validation.

## Task 2: ProductReadiness and GuidanceIntent contracts

**Allowed files:**
- Create: `webapp/backend/coach_guidance.py`
- Create: `webapp/shared/guidance-targets.v1.json`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/backend/coach_commands.py`
- Modify: `webapp/backend/coach_runtime.py`
- Modify: `webapp/frontend/lib/types.ts`
- Modify: `webapp/frontend/lib/api.ts`
- Test: `webapp/tests/test_routes_coach.py`
- Test: `webapp/tests/test_coach_commands.py`
- Test: `webapp/tests/test_coach_runtime.py`
- Test: `webapp/frontend/lib/api.test.ts`

**Tests first:**

1. Add table-driven failing tests for every `product_readiness.v1` domain state from existing Provider, capture, KovaaK connection, pending Run, Analysis/task, Training Plan, Storage, and onboarding sources.
2. Prove each domain distinguishes `known empty` from `unavailable`, returns bounded compatible opaque refs/count/truncation for multi-object domains, and never converts a read failure to ready.
3. Prove the projection contains no secret, path, Steam identity, raw payload, display-text-derived ref, or new persistent state.
4. Add schema/runtime tests for all seven `guidance_intent.v1` kinds and one shared `guidance_target_registry.v1`; reject selectors, arbitrary URLs/routes, scripts, paths, credential/file/permission prefill targets, unknown readiness keys, and mutation payloads outside existing command results.
5. Add a failing command test for `product.readiness.get` using the same projection as `GET /api/product-readiness`.

**Implementation:**

6. Implement one pure projection in `coach_guidance.py` that reads existing adapters/stores and returns domain availability, bounded refs/counts and safe reason codes; do not cache or persist it.
7. Make `webapp/shared/guidance-targets.v1.json` the canonical semantic target/safe-prefill registry. Backend/runtime validates it; the frontend maps only those IDs.
8. Add `GET /api/product-readiness` for the frontend bootstrap/recovery guide and register `product.readiness.get` as a read-only Coach command.
9. Add Pydantic/TypeScript GuidanceIntent and readiness types, allow the bounded `guidance` Agent-run event type, and add a single frontend API client.
10. Run:
   - `.venv\Scripts\python.exe -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_coach_commands.py webapp/tests/test_coach_runtime.py -q`
   - `npm.cmd --prefix webapp/frontend test -- lib/api.test.ts`

**Stop rule:** Stop if the projection needs a new database table, copies canonical business state, exposes raw/provider identity data, or invents readiness from missing/failed sources.

## Task 3: Close eligible product-command coverage

**Precondition:** The active `2026-08-09-coach-first-single-pipeline-v1.md` Task 3 must be terminal. Its negative Provider-command tests must then be intentionally revised under this Task's design contract; do not run both Tasks concurrently.

**Allowed files:**
- Modify: `webapp/backend/coach_commands.py`
- Modify: `webapp/backend/provider_commands.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `webapp/backend/calibration_profile_store.py`
- Modify: `webapp/backend/kovaak_connection_store.py`
- Modify: `webapp/backend/kovaak_benchmark_service.py`
- Modify: `webapp/backend/provider_store.py`
- Modify: `webapp/backend/coach_store.py`
- Modify: `webapp/backend/coach_context_refs.py`
- Modify: `webapp/backend/coach_agent_runs.py`
- Modify: `webapp/backend/coach_runtime.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/coach-runtime/src/product-command-tools.ts`
- Test: `webapp/tests/test_coach_commands.py`
- Test: `webapp/tests/test_routes.py`
- Test: `webapp/tests/test_task6_backend_contracts.py`
- Test: `webapp/tests/test_kovaak_runs.py`
- Test: `webapp/tests/test_kovaak_connection.py`
- Test: `webapp/tests/test_provider_store.py`
- Test: `webapp/tests/test_provider_routes.py`
- Test: `webapp/tests/test_provider_auth.py`
- Test: `webapp/tests/test_coach_store.py`
- Test: `webapp/tests/test_coach_agent_runs.py`
- Test: `webapp/coach-runtime/test/product-command-tools.test.ts`

**Tests first:**

1. Add a reviewed coverage table test that classifies each current user-visible frontend mutation as `registered_command`, `user_only`, or `legacy_not_user_reachable`; fail when a new mutation has no classification.
2. Add failing owner/idempotency/result-redaction tests for the missing eligible domains: Storage/incomplete capture, Run evidence removal, calibration, KovaaK connection lifecycle, Provider non-secret profile lifecycle/status/test/default, Coach session/context/run lifecycle, and product/capture/task status.
3. Add negative tests proving credentials, OAuth/device-code input, Steam identity, file paths, consent/system permission, arbitrary endpoint/network, and subjective facts without an exact user statement are not accepted by Coach commands.

**Frozen command policy:**

| Command family | Risk | Direct grant | Idempotency identity | Desktop capability | Safe result projection |
|---|---|---|---|---|---|
| `storage.incomplete.list`, `storage.incomplete.remove` | `query` / `destructive_local_delete` | remove only for one explicitly resolved `storage_item_ref` | command + owner + item ref | required; trusted backend supplies it | bounded item refs/count/bytes or removed/released bytes; no path |
| `run.evidence.remove` | `destructive_local_delete` | only for one resolved `run_ref` plus typed video-or-raw kind | command + owner + run ref + evidence kind | required; trusted backend supplies it | run ref, evidence kind, removed/remaining availability; no path |
| `calibration.get`, `calibration.save`, `calibration.delete` | `query` / `reversible_write` / `destructive_local_delete` | save only for typed values explicitly stated by the user; delete only for singleton `calibration:current` | command + owner + `calibration:current` + normalized scalar digest | not required beyond the owner-scoped Coach bridge | singleton ref, availability, bounded `cm_per_360`/FOV values |
| `kovaak.connection.get`, `kovaak.connection.refresh`, `kovaak.connection.disconnect` | `query` / `external_side_effect` / `destructive_local_delete` | refresh/disconnect only for singleton `kovaak_connection:current`; creating/replacing identity remains user-only | command + owner + `kovaak_connection:current` | not required beyond the owner-scoped Coach bridge | singleton ref, connected/refresh state, optional snapshot ref; never Steam identity |
| `provider.profile.list`, `get`, `status`, `create`, `update`, `set_default`, `test`, `delete` | `query`, `reversible_write`, `external_side_effect`, or `destructive_local_delete` per operation | only catalog-backed non-secret metadata and one resolved profile ref; custom endpoint, credential, and auth operations remain user-only | command + owner + profile ref or create key + normalized safe metadata digest | not required beyond the owner-scoped Coach bridge | existing public profile/status DTO reduced to profile/catalog/model refs, default flag and bounded status; no credential or arbitrary endpoint |
| `coach.session.create`, `rename`, `archive`, `delete`; `coach.context.attach`, `detach` | `reversible_write` or `destructive_local_delete` | yes for one resolved session/context ref and explicit bounded title/context | command + owner + session/context ref + normalized safe digest | not required beyond the owner-scoped Coach bridge | stable refs and lifecycle state; only existing redacted public title/context fields |
| `coach.run.stop`, `retry` | `run_control` | yes for one resolved run ref | command + owner + run ref + terminal generation/action | not required beyond the owner-scoped Coach bridge | run ref, bounded status/error code and result ref |

Read-only product, capture, and task status should reuse `product.readiness.get` or an existing bounded read command. Do not add a second status model merely to increase the command count.

**Implementation:**

4. Extract only the smallest shared domain functions needed so UI routes and Coach commands call the same owner-scoped mutation. Do not call FastAPI routes internally, duplicate store mutations, or introduce a generic command service.
5. Extend the command registry and Pi tool schema exactly to the frozen families above. Add the smallest risk enum expansion required by the table; authorization still comes only from Task 1's trusted grant/confirmation path.
6. Split Provider profile metadata from credential/auth and arbitrary endpoint input: Coach may operate catalog-backed non-secret metadata and existing opaque profile refs, while credential, custom endpoint, and external auth remain trusted UI flows.
7. Enforce the table's stable refs, desktop capability, safe result projection, idempotency identity, audit, and negative boundaries for every command before exposing it to Pi.
8. Run:
   - `.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_commands.py webapp/tests/test_routes.py webapp/tests/test_task6_backend_contracts.py webapp/tests/test_kovaak_runs.py webapp/tests/test_kovaak_connection.py webapp/tests/test_provider_store.py webapp/tests/test_provider_routes.py webapp/tests/test_provider_auth.py webapp/tests/test_coach_store.py webapp/tests/test_coach_agent_runs.py -q`
   - `npm.cmd --prefix webapp/coach-runtime test -- product-command-tools.test.ts`

**Stop rule:** Stop on any operation that lacks a shared domain handler, requires a secret/path/raw payload, bypasses a Tauri/system permission boundary, or would need a generic HTTP/OS action. Classify it as user-only and return to the design owner instead of building a bypass.

## Task 4: Deterministic guided-workflow compiler

**Allowed files:**
- Modify: `webapp/backend/coach_guidance.py`
- Modify: `webapp/backend/coach_agent_runs.py`
- Modify: `webapp/backend/coach_service.py`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/routes.py`
- Test: `webapp/tests/test_coach_agent_runs.py`
- Test: `webapp/tests/test_routes_coach.py`

**Tests first:**

1. Add failing state-table tests for the initial goals: first usable training, analyze latest Run, today's practice, explicit execution/retest record, evidence/progress inspection, and Provider/capture/KovaaK/Analysis/storage recovery.
2. Prove each state produces exactly one allowed next intent, asks one disambiguating question for multiple targets, and never infers a user-only fact.
3. Prove command success is followed by canonical state verification; lost/unknown outcomes inspect state before any retry.
4. Prove Provider-not-ready bootstrap produces deterministic UI guidance without creating a Provider-less Coach answer or second conversation.
5. Add route tests for `POST /api/coach/guidance/ack`: reject another owner/run, stale or repeated intent, non-current intent, unknown outcome, readiness/product refs, command parameters, and any frontend claim of product success.
6. Prove all four UI outcomes append only a bounded event; `completed` still re-reads canonical readiness and cannot complete the goal unless the backend completion condition is true.
7. Prove the Pi turn keeps its existing terminal Agent-run status, guidance events can continue after terminal completion, the latest unacknowledged event is the only current intent, and acknowledgement does not reopen or retry the model run.

**Implementation:**

8. Compile the next GuidanceIntent from goal + ProductReadiness + existing stable refs. Pi selects/expresses the goal; the compiler owns allowed transitions and completion checks.
9. Append intents as bounded `guidance` events on the existing Agent run and reuse existing confirmation refs. Keep the Pi turn terminal; do not add a workflow table, parallel action log, or `awaiting_input` Agent-run status.
10. Implement `POST /api/coach/guidance/ack` with the frozen `guidance_ack_request.v1`/`guidance_ack_response.v1` contract. Accept only run ref, current intent id and UI outcome; re-read ProductReadiness server-side, append the safe acknowledgement event, and return exactly one next intent or terminal state.
11. Resume from the latest unacknowledged guidance event plus canonical state after navigation/reload; if that is insufficient, stop and obtain approval before adding persistence.
12. Run `.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_agent_runs.py webapp/tests/test_routes_coach.py -q`.

**Stop rule:** Stop if a workflow requires Provider-authored state transitions, assistant prose as completion evidence, a second planner/Agent, or a duplicate Training Plan/TeachingSession lifecycle.

## Task 5: Frontend GuidanceHost

**Allowed files:**
- Create: `webapp/frontend/components/task7/GuidanceHost.tsx`
- Modify: `webapp/frontend/components/task3/AppShell.tsx`
- Modify: `webapp/frontend/components/task7/CoachWorkspace.tsx`
- Modify: `webapp/frontend/components/task6/SettingsWorkspace.tsx`
- Modify: `webapp/frontend/lib/navigation.ts`
- Modify: `webapp/frontend/lib/types.ts`
- Modify: `webapp/frontend/lib/api.ts`
- Test: `webapp/frontend/lib/navigation.test.ts`
- Test: `webapp/frontend/lib/api.test.ts`
- Test: `webapp/frontend/tests/task7-coach-workspace.test.tsx`
- Test: `webapp/frontend/tests/task7-guidance-host.test.tsx`

**Tests first:**

1. Add failing tests for allow-listed navigation, reveal, accessible focus, safe prefill, user-action waiting, completion, cancellation, failure, and state re-verification.
2. Prove Coach session/draft survives cross-route guidance and only one next action is visible.
3. Reject unknown routes/sections/focus targets, DOM selectors, arbitrary URLs, secret fields/values, paths, scripts, and arbitrary Tauri invokes.
4. Prove Provider credential/OAuth, capture consent/system permission, file selection, Steam identity, real training, and subjective feedback stop at the trusted existing control and cannot be auto-completed by a guidance event.
5. Prove every navigation/user-action outcome is reported through `POST /api/coach/guidance/ack` with only schema version, run ref, intent id and UI outcome; the Host renders the backend-returned next intent and never marks the product goal complete from local UI state.

**Implementation:**

6. Mount one GuidanceHost in the Coach-first AppShell and map semantic targets to existing routes/components. Keep mappings local and allow-listed.
7. Reuse Settings/onboarding controls and existing API/Tauri adapters. Safe prefill is limited to named non-secret slots; do not duplicate form state or API clients.
8. Add one typed API client for guidance acknowledgement. After an allow-listed UI action, submit only the UI outcome, then render the backend-returned next intent or terminal state; do not submit ProductReadiness or infer completion locally.
9. Replace the current page-local CustomEvent/sessionStorage compatibility path only where the new host covers the same behavior; preserve unrelated in-progress Coach-first UI work.
10. Run:
   - `npm.cmd --prefix webapp/frontend test -- lib/api.test.ts lib/navigation.test.ts tests/task7-coach-workspace.test.tsx tests/task7-guidance-host.test.tsx`
   - `npm.cmd --prefix webapp/frontend run type-check`

**Stop rule:** Stop if a target cannot be mapped semantically to an existing product control, requires selector search/simulated input, or conflicts with the single Coach-first workspace and Session rail.

## Task 6: End-to-end operator journeys and regression gates

**Allowed files:**
- Modify: `webapp/tests/test_routes_coach.py`
- Modify: `webapp/tests/test_coach_runtime.py`
- Modify: `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- Modify: `webapp/frontend/e2e/browser-smoke.spec.ts`
- Modify: `webapp/frontend/e2e/desktop-matrix.spec.ts`
- Modify: `webapp/frontend/tests/task7-guidance-host.test.tsx`
- Modify: `docs/PROGRESS.md`

**Tests first:**

1. Add E2E coverage for the eight acceptance journeys in the design contract, including explicit destructive execution, inferred-action confirmation, ambiguity, user-only waits, Provider recovery, and lost-result recovery.
2. Verify no secret/path/Steam identity/raw payload appears in Provider requests, tool events, confirmations, audits, messages, or browser storage.
3. Run focused Python, Coach-runtime, frontend unit/type-check/build, Browser E2E, and real Tauri smoke. Use a nonexistent `KOVAAK_INSTALL_DIR` for automated safety; real KovaaK and physical permission/training steps remain separate field Gates.
4. Update `docs/PROGRESS.md` with exact results, unrun field checks, and Go/No-Go blockers only after the aggregate verification is complete.

**Verification commands:**

- `.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_commands.py webapp/tests/test_coach_runtime.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_routes_coach.py webapp/tests/test_routes.py webapp/tests/test_provider_auth.py -q`
- `npm.cmd --prefix webapp/coach-runtime test`
- `npm.cmd --prefix webapp/frontend test`
- `npm.cmd --prefix webapp/frontend run type-check`
- `npm.cmd --prefix webapp/frontend run build`
- Browser/Desktop commands from `docs/DEVELOPMENT.md` for the current environment.
- `git diff --check`

**Stop rule:** Automated checks do not close real Provider/OAuth, Windows permission, Tauri WebView, real KovaaK, physical training, or hardware capture Gates. Report each separately and do not claim universal operation coverage while any eligible frontend mutation remains unclassified.
