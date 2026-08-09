# Coach-First Single Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** active; design work is intentionally outside this plan and will be applied after OpenDesign handoff.

**Goal:** Make Aiming Cookie a Provider-backed Coach product with one fixed all-source analysis pipeline, while keeping capture failures explicit and preserving existing local Run records.

**Architecture:** Run-based analysis accepts only the existing `multimodal` contract, but the server requires Stats, Performance/.perf, Raw Input, managed video, and a resolved canonical window before enqueue. Provider unavailability leaves capture and pending work intact without producing a deterministic user-facing report. Coach product operations use the existing typed command/confirmation boundary; no arbitrary OS execution and no Provider add/change command are introduced.

**Tech Stack:** Python/FastAPI/SQLite backend, existing worker and queue, TypeScript Coach runtime, existing Provider profiles and product-command bridge.

---

### Task 1: Freeze the product contract and source gate

**Files:**
- Modify: `docs/PRD.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/superpowers/specs/2026-07-17-automatic-run-capture-design.md`
- Create: `webapp/backend/source_requirements.py`
- Test: `webapp/tests/test_source_requirements.py`

**Steps:**
1. Write tests for complete and incomplete source bundles, including missing Stats, Performance/.perf, Raw Input, video, and canonical window.
2. Run the focused test and verify the new module is absent or the tests fail.
3. Implement one allow-listed validator returning stable missing-source codes and a bounded public summary; do not expose paths or raw evidence.
4. Run the focused test and verify all source cases pass.
5. Update PRD, Architecture, and the active capture spec so the single all-source Coach pipeline is the only new Run analysis contract. Preserve historical records and explicitly distinguish capture-pending from analysis-ready.

### Task 2: Enforce one Run analysis mode at the backend boundary

**Files:**
- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `webapp/backend/coach_commands.py`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/queue.py`
- Modify: `webapp/backend/contracts.py`
- Modify: `webapp/backend/routes.py`
- Test: `webapp/tests/test_kovaak_runs.py`
- Test: `webapp/tests/test_coach_commands.py`
- Test: `webapp/tests/test_contracts.py`

**Steps:**
1. Add failing tests proving a new Run exposes only `multimodal` readiness and rejects missing required sources before enqueue.
2. Run the focused tests and verify the old native/video fallback behavior is still the failing expectation.
3. Reuse the source validator in readiness and `create_analysis_from_run`; reject explicit legacy modes and do not auto-select a fallback.
4. Keep historical rows readable, but make new Run-based analysis creation canonical and all-source.
5. Run the focused backend tests and verify no queue row is created for incomplete evidence.

### Task 3: Remove Provider-less Coach/report behavior without changing Provider management

**Files:**
- Modify: `webapp/backend/coach_service.py`
- Modify: `webapp/backend/coach_agent_runs.py`
- Modify: `webapp/backend/coach_runtime.py`
- Modify: `webapp/coach-runtime/src/turn.ts`
- Modify: `webapp/coach-runtime/src/load-system-prompt.ts`
- Modify: `webapp/coach-runtime/src/product-command-tools.ts`
- Test: `webapp/tests/test_coach_agent_runs.py`
- Test: `webapp/tests/test_capability_contracts.py`
- Test: `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- Test: `webapp/coach-runtime/test/product-command-tools.test.ts`

**Steps:**
1. Add failing tests for Provider-unavailable turns: no stale Coach answer, no deterministic report fallback, explicit retryable Provider state.
2. Add negative tests proving Coach cannot create, change, or delete Provider profiles; existing Settings/Onboarding APIs remain available to the user.
3. Make the production Coach path fail closed with one Provider-required error envelope and preserve pending capture/analysis state.
4. Keep typed product-command and confirmation validation intact; only add narrowly scoped read/operation commands required by the current product contract.
5. Run Python and Coach-runtime focused tests.

### Task 4: Preserve failed Runs and prepare Coach Session lifecycle APIs

**Files:**
- Modify: `webapp/backend/coach_store.py`
- Modify: `webapp/backend/coach_agent_runs.py`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/backend/db.py`
- Test: `webapp/tests/test_coach_agent_runs.py`
- Test: `webapp/tests/test_task6_backend_contracts.py`

**Steps:**
1. Write tests for first-message Session creation semantics, scenario association without content-based splitting, soft deletion, archive, rename, search, and ordering.
2. Run focused tests to verify the current primary-thread-only behavior does not satisfy them.
3. Add the smallest backend representation compatible with the existing Coach thread/message tables; keep unassociated and cross-scenario conversations in one stable container.
4. Ensure invalid source Runs remain retained with a bounded failure reason and cannot be attached as completed Analysis context.
5. Run focused backend tests and check owner isolation and secret/path redaction.

### Task 5: Regression and handoff

**Files:**
- Files modified by Tasks 1-4
- Modify: `docs/PROGRESS.md`
- Modify: `docs/superpowers/plans/README.md`

**Verification:**
```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests -q
npm.cmd --prefix webapp\frontend run test:contracts
npm.cmd --prefix webapp\frontend run type-check
git diff --check
```

The OpenDesign handoff will separately cover the Coach workspace, right session rail, video/conversation split, and removal of the old mode-selection UI. This plan must not modify those layout components before that handoff.
