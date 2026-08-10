# Viscose S2 Sync and Coach Progression v1 Implementation Plan

> Status: Tasks 1-3 complete and live-verified on 2026-07-29. The file remains in the active worktree batch until its heavily dirty shared checkout is organized; no commit or push is authorized.

**Goal:** Import Viscose S2 Easier/Medium highest scores and connect them to the existing Coach teaching loop without parallel stores or state machines.

**Architecture:** A single course catalog is shared by a narrow KovaaK provider adapter, the existing Benchmark store and Coach's safe projection. Teaching progression extends the existing TeachingSession contract with one bounded next recommendation; Training Plan, Registry, comparison and confirmation remain authoritative.

## Task 1 - Product contract and course catalog

### Allowed files

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/frontend-uiux-design.md`
- `docs/archive/retired/opendesign-desktop-handoff.md`（历史引用）
- `docs/README.md`
- `docs/superpowers/specs/README.md`
- `docs/superpowers/plans/README.md`
- this plan and its active spec
- `knowledge/benchmarks/viscose-s2.v1.json`
- `webapp/backend/benchmark_catalog.py`
- catalog tests

### Tests first

Catalog loader rejects wrong version, duplicate IDs/names, non-39 difficulties, broken pairs and invalid exact refs. The production asset contains exactly 39 Easier/Medium pairs and only reviewed local refs.

### Stop rule

Stop if an external display name must become an exact Analysis identity or if a second Scenario Registry is required.

## Task 2 - Atomic KovaaK sync and Coach-safe score summary

### Allowed files

- `webapp/backend/kovaak_benchmark_provider.py`
- `webapp/backend/benchmark_store.py`
- `webapp/backend/coach_context_refs.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- corresponding backend and Node tests

### Tests first

1. Validate Steam ID, consent, two complete 39-scenario payloads, score conversion, ranks and one timestamp.
2. Timeout, HTTP failure, missing/duplicate/unknown scenario or invalid score/rank writes nothing and preserves the old snapshot.
3. Owner A data is invisible to owner B; sync response and Coach summary never contain Steam ID, URL, leaderboard identity or raw payload.
4. Coach summary exposes scores/completion only and its contract says score cannot diagnose technique.

### Stop rule

Stop on any required database migration, credential flow, background sync, leaderboard UI or generic provider abstraction.

## Task 3 - Existing TeachingSession progression

### Allowed files

- `webapp/backend/teaching_session_store.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_runtime.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/teaching-policy.ts`
- corresponding backend and Node tests
- `docs/PROGRESS.md`
- this plan and plan index

### Tests first

1. Only a confirmed Easier `improved` retest creates the paired Medium recommendation.
2. Unchanged, worsened, inconclusive, not comparable, missing pair or missing policy creates no recommendation.
3. Recommendation states stress test/new baseline, not transfer; no exact Medium ref means no prepared item or write command.
4. Existing retry, confirmation, one-active-item, owner and Provider fallback tests remain green.

### Stop rule

Stop if progression requires a second plan/session store, Provider-authored transition, invented meaningful-change threshold or name-only Analysis activation.

## Final verification

Run focused catalog/provider/store/routes/context/TeachingSession tests, full affected Coach backend tests, full Coach Node tests, Python compile, real read-only Easier/Medium fetch, `git diff --check` and final dirty-worktree attribution.

## 2026-07-29 integration closeout

- Catalog, atomic store write, sync route, de-identified Coach summary and confirmed Easier-improved to Medium recommendation are implemented through the existing Benchmark store, Coach context, TeachingSession, Training Plan and confirmation flow.
- Payload completeness and learner completion remain separate: the provider validates all 39 rows per difficulty, while zero highest scores count as not yet played.
- A Medium recommendation is a stress test/new baseline only. Without an exact Medium ScenarioProfile it cannot prepare or write a Training Plan item.
- Affected Python regression: `433 passed`. Complete Coach Node regression: `167 passed`. Final focused benchmark Python after privacy-fixture cleanup: `26 passed`; focused Node summary/tool: `16 passed`. Python compileall and `git diff --check` passed.
- KovaaK's current Benchmark Tracker bundle uses the anonymous `player-progress-rank-benchmark` endpoint; the prior `/pending/benchmark` path was obsolete and returned HTTP 401. A real product-route sync against an isolated SQLite database returned HTTP 200, wrote 158 records atomically, projected Easier `18/39` and Medium `0/39`, and persisted no external identity ref. No token was hard-coded and EVXL HTML was not adopted as an undeclared provider.
- Final affected Python regression was rerun in four isolated groups: `26 + 232 + 119 + 56 = 433 passed`. UI implementation remains outside this backend-first plan.
