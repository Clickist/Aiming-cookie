# KovaaK Score and Analysis Presentation Closeout v1

> Status: active for the remaining OpenDesign/frontend handoff. Tasks 1-4 implementation and verification completed on 2026-07-29. The one-shot identity rule below is superseded only by active `2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md`. No commit or push is authorized.

**Goal:** Finish the non-visual-design contracts for direct KovaaK score sync, an identity-free score read model, source-taxonomy-aware Coach context, and readable Switching semantics inside the existing Analysis workspace.

**Architecture:** Reuse the current KovaaK provider, versioned benchmark catalog, atomic Benchmark store, Coach benchmark projection, Analysis presentation contract and shared Data view. User-facing names remain neutral (`KovaaK scores` / `training scores`); internal source refs remain available only for provenance. No second store, provider framework, score page, Analysis workspace, diagnosis path or threshold is introduced.

## Frozen decisions

- Accept a strict 17-digit Steam ID or an HTTPS `steamcommunity.com/profiles/<17 digits>/` URL. Do not resolve vanity `/id/<name>` URLs and do not add Steam OAuth or another provider.
- Public score responses and Coach context never expose Steam ID, profile URL, external identity refs, provider payloads, consent records or source URLs. The 2026-07-30 active plan may save one local connected identity and perform an in-memory Coach lookup, but does not relax this response/context boundary.
- Catalog category/subcategory labels describe course emphasis only. They never diagnose anatomy, reading, tension, grip, hardware or technique.
- Switching analysis, evidence chains, video anchors and Coach knowledge connection are existing capabilities owned by their current sessions. This plan only fills missing user-facing labels/formatting in the shared Analysis presentation; no new route, tab, component family, evidence contract, analyzer, advice or Registry entry is allowed.
- AMD/Intel are unverified and unsupported for v1 rather than release blockers. OAuth is deferred. Existing worker restart recovery and Coach continuation/stop/failure recovery are complete implementation facts.

## Task 1 - Direct score sync and safe frontend read model

### Allowed files

- `webapp/backend/kovaak_benchmark_provider.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/api.ts`
- corresponding backend/frontend tests

### Tests first

1. Normalize a raw 17-digit ID and an exact Steam Community profile URL to the same ID; reject whitespace, non-HTTPS/non-Steam URLs, extra path/query/fragment, vanity names and malformed IDs.
2. The sync route accepts either input form and still persists no external identity.
3. A new KovaaK score response reuses the latest complete snapshot and returns observed time, stage completion/rank, scenario name, catalog category/subcategory, score, scenario rank and completion without identity/provider payload fields.
4. Empty or invalid stored state returns an explicit unavailable result and never reconstructs a partial snapshot.
5. Frontend types and API helpers match the stable response and sync contracts without rendering UI.

### Stop rule

Stop on any migration, identity persistence, vanity lookup, OAuth, background sync, leaderboard or second score projection implementation.

## Task 2 - Source taxonomy in the existing Coach score projection

### Allowed files

- `webapp/backend/coach_context_refs.py`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- corresponding backend and Node tests

### Tests first

1. Each projected scenario carries the catalog category and subcategory with existing score/rank facts.
2. Python and Node coercion reject unknown or malformed taxonomy and continue stripping identity/provider fields.
3. Prompt/tool policy calls these source course labels, permits prioritization/grouping, and forbids treating them as player traits or diagnoses.

### Stop rule

Stop if taxonomy requires a new Registry, diagnosis, Training Plan generator, or inference from score/category alone.

## Task 3 - Switching semantic closeout in the shared Analysis workspace

### Allowed files

- `webapp/frontend/components/task5/DataView.tsx`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/tests/task5-analysis.test.ts`
- `webapp/frontend/tests/task5-source.test.ts`
- focused existing Analysis Data E2E only if required

### Tests first

1. Map the four existing production Switching metrics and five event kinds to natural Chinese labels.
2. Render path efficiency as a percentage.
3. Replace only known Switching internal issue/metric keys with natural text; preserve unknown safe strings and evidence refs.
4. Preserve the existing generic fallback, bounded projection and video seek behavior.

### Stop rule

Stop if the change needs a new layout, route, tab, processed-row contract, chart, advice, Registry or design decision.

## Task 4 - Truth-source reconciliation and integrated verification

### Allowed files

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- `docs/frontend-uiux-design.md`
- `docs/opendesign-desktop-handoff.md`
- `docs/README.md`
- `docs/superpowers/plans/README.md`
- this plan

### Verification

1. Record current support decisions without rewriting historical field evidence.
2. Remove stale current claims that only Tracking is active or that completed recovery capabilities are missing.
3. Keep user-facing naming neutral while preserving internal provenance.
4. Run focused backend, Coach Node and frontend tests, compile checks and `git diff --check`.
5. Use the provided Steam profile URL only against a dedicated temporary SQLite database; verify the public response contains no identity, then recycle the temporary directory.

### Stop rule

Stop before cleanup, commit, push, archive, real product database access, or changes to another session's Analysis algorithms/Registry thresholds.
