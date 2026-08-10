# KovaaK Connected Account and Coach Lookup v1

> Status: completed on 2026-07-30. 点点 later authorized the verified worktree to be committed in logical batches and pushed after all confirmed findings converged.

**Goal:** Let a learner save one local KovaaK account for manual refresh and let Coach perform a one-turn, non-persistent lookup of a profile supplied in chat.

**Architecture:** Keep the existing catalog, provider adapter, Benchmark store and de-identified Coach score projection. Add one owner-scoped local connection record for the learner's normalized Steam identity. A turn-scoped bridge binding replaces a chat-supplied profile with an opaque reference before the message reaches the selected LLM; the binding is released when the Coach turn ends.

## Frozen Decisions

- A saved connection contains one normalized 17-digit Steam ID per local owner. It represents the learner's own account, is never returned by a public API, never enters Coach context/messages/traces or the LLM request, and is deleted independently from saved score history.
- A temporary lookup accepts only an exact public Steam Profile URL or a 17-digit ID supplied in the current Coach message. Its identity is memory-only for that turn: the URL/ID is never written to Benchmark history, Coach messages, command audit, confirmation state or a Training Plan. Its raw score result is not written to Benchmark history or command audit; any normal Coach reply must label it as a temporary external lookup and must not treat it as the learner's history.
- Before a turn reaches the LLM, a recognized temporary profile is replaced by an opaque `steam_profile:N` reference. Only the in-memory loopback bridge can resolve that reference. The model may not supply a raw URL or Steam ID to a product command.
- Refreshing a saved connection writes a complete atomic snapshot through the existing Benchmark store and makes its de-identified summary available to later Coach turns. It does not run in the background.
- No Steam OAuth, vanity `/id/` resolution, leaderboard browser, account system, generic provider framework, background refresh, independent score page or score-only technique diagnosis is introduced.

## Task 1 - Saved Connection and Reusable Sync Service

**Allowed files**

- `webapp/backend/db.py`
- `webapp/backend/kovaak_connection_store.py`
- `webapp/backend/kovaak_benchmark_service.py`
- `webapp/backend/kovaak_benchmark_provider.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/tests/test_kovaak_connection.py`
- `webapp/tests/test_benchmark_store.py`
- `webapp/tests/test_db.py`

**Tests first**

1. Save, read status and delete one normalized connection per owner; responses never contain a Steam ID or URL.
2. A saved-account refresh through `POST /api/kovaak-connection/refresh` reuses the existing normalized provider response and atomic Benchmark store write; an upstream failure preserves the previous score snapshot.
3. Direct synchronization remains compatible but does not create or replace a saved connection.
4. The shared service can project a complete temporary snapshot without writing any benchmark or connection record.

**Stop rule**

Stop on OAuth, vanity resolution, background scheduling, a second score store, a generic external-account framework, or a response that reveals the saved identity.

## Task 2 - Coach Temporary Lookup and Privacy Boundary

**Allowed files**

- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_runtime.py`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_coach_runtime.py`

**Tests first**

1. A profile URL or ID in a current Coach message becomes an opaque reference before provider execution and is redacted from the stored user/assistant message.
2. `kovaak_scores.lookup` resolves only a bridge-issued opaque reference, returns a bounded identity-free score summary, persists no input and does not write score data to Benchmark history or command audit.
3. `kovaak_scores.refresh_connected` reads the saved account, atomically refreshes its owner snapshot and returns a safe summary; a missing connection is unavailable.
4. Audit records, tool events, confirmation state and Provider input contain no URL or Steam ID. The Coach may use scores for prioritization only.

**Stop rule**

Stop if a raw URL/ID can enter the LLM request, a command result/trace, confirmation data or persistent Coach message, or if a temporary lookup changes the learner's score history.

## Task 3 - Runtime Command Contract

**Allowed files**

- `webapp/coach-runtime/src/product-command-tools.ts`
- `webapp/coach-runtime/test/product-command-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

**Tests first**

1. The fixed command list contains only `kovaak_scores.lookup` and `kovaak_scores.refresh_connected` in addition to existing commands.
2. Lookup accepts exactly a `profile_ref` shaped as `steam_profile:N`; a raw URL, Steam ID, foreign key or nested payload is rejected before the loopback request.
3. Refresh accepts no parameters. Safe command events retain only the result/audit references and never contain the profile reference or score payload.

**Stop rule**

Stop if the TypeScript bridge widens URL/credential acceptance for any other command or exposes its bearer/desktop secret.

## Task 4 - Contract Reconciliation and Verification

**Allowed files**

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- `docs/frontend-uiux-design.md`
- `docs/archive/retired/opendesign-desktop-handoff.md`（历史引用）
- `docs/README.md`
- `docs/superpowers/plans/README.md`
- `docs/superpowers/specs/README.md`
- `docs/superpowers/specs/2026-07-30-kovaak-connected-account-and-coach-lookup-design.md`
- this plan

**Verification**

1. Update the product and architecture boundary from one-shot-only identity use to a learner-owned local connection plus an ephemeral Coach lookup; keep all public and LLM-facing output identity-free.
2. Run focused connection/provider/store/route, Coach backend and Coach runtime Node tests, then the full affected Python and Node suites, `compileall`, and `git diff --check`.
3. Use the supplied public profile only with a dedicated temporary SQLite data directory. Verify saved refresh reaches Coach context and temporary lookup writes no connection, benchmark, message or audit identity. Recycle the directory afterward.

**Stop rule**

Stop before frontend design/implementation, real product database access, a real Provider request containing a Steam identity, commit, push or cleanup of unrelated worktree changes.

## Task 5 - Latest Available Score Snapshot Repair (PERF-01 / MB-01)

**Allowed files**

- `webapp/backend/benchmark_store.py`
- `webapp/backend/kovaak_benchmark_service.py`
- `webapp/backend/routes.py`
- `webapp/tests/test_benchmark_store.py`
- `webapp/tests/test_kovaak_connection.py`
- this plan, for Task closeout only

**Tests first**

1. Two complete Viscose S2 snapshots must return only the newest current snapshot through a bounded store read, without one `get_record()` query per historical row.
2. An incomplete newer available snapshot must continue to fall back to the older complete available snapshot for `/api/kovaak-scores`.
3. Switching a saved connection must mark the previous snapshot stale and make `/api/kovaak-scores` unavailable until a replacement complete available snapshot is refreshed.
4. A replacement complete available snapshot must become the only public current-score projection.

**Frozen decisions**

- Current scores are the latest complete, `available` Viscose S2 snapshot. Historical and stale Benchmark rows remain intact.
- Generic `/api/benchmarks` remains an owner-scoped historical record read; this Task does not bound or redefine it.
- Account replacement keeps the existing stale-marking and lock semantics. A failed refresh preserves prior data only for the same active connection.
- No schema/index migration, TTL, cleanup job, second score store, API schema change, identity exposure, provider request in tests, or generic provider/account framework is introduced.

**Stop rule**

Stop for a product decision if retaining the latest-complete fallback needs a changed score API contract, if focused query evidence requires a schema/index migration, or if the repair needs to change retention, account locking, or the public/Coach identity boundary.

## Verification Record

- Tasks 1-3 are implemented without adding OAuth, vanity lookup, background refresh, a second score store, a leaderboard or a product account.
- A saved-account refresh and a temporary score projection were verified against the supplied public profile using a dedicated temporary SQLite directory. The saved refresh wrote one complete 158-record snapshot and produced a Coach context summary; the temporary projection left that record count unchanged and neither output contained the external identity. The temporary directory was sent to the Recycle Bin.
- Focused and affected Python suites passed with `304 passed`; the Coach runtime command suites passed with `80 passed`. `compileall` and scoped `git diff --check` passed.
- Frontend Settings/onboarding and the minimal score view remain outside this plan and await the separate OpenDesign-led frontend task.
- Task 4 reconciled PRD, Architecture, Roadmap, UI/UX and development contracts without exposing an identity or expanding the product into OAuth, leaderboard or a second score system.
- Task 5 replaced historical per-record reads with one bounded latest-complete/available query. Switching the connected account leaves historical rows intact but makes stale scores unavailable to the public route and Coach until a replacement snapshot exists. Focused tests passed `13`; Coach/routes cross-check passed `33`.
