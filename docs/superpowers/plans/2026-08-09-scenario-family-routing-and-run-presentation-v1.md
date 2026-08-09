# Scenario Family Routing And Run Presentation v1 Implementation Plan

> **Status: active for Tasks 1-4.** 点点已明确授权修复新场景不可分析、优先自动识别，并移除用户侧的 `Run N` / `analysis:N` 文案。完成前不得重打内测包。

**Goal:** A new KovaaK scenario is automatically routed to a safely limited family analysis when local evidence supports a family, while every user-facing record and Coach context uses a scenario-and-time label rather than an internal identifier.

**Architecture:** Freeze a versioned scenario resolution that records family-recognition provenance separately from an exact reviewed profile. A local `.sce` definition is the primary automatic classifier input; Performance Challenge facts and curated aliases may corroborate but display-name matching alone never dispatches. The worker calls a baseline analyzer made from current Raw/Stats/Performance facts; exact visual calibration remains the gate for target-relative metrics and prescriptions. Presentation labels are derived from existing projected scenario/training/completion timestamps; refs remain stable opaque transport keys.

**Tech Stack:** Python/FastAPI/SQLite, existing local KovaaK parser and scenario registry, React/Next static frontend, Pi/TypeScript Coach runtime.

## Frozen Decisions

- Never activate or borrow an exact visual profile for another hash.
- A `.sce` classifier reads only bounded structural fields needed for family classification; it never serializes its path or full source text into public DTOs, Analysis results, or Coach context.
- A name match is a candidate signal only and may not independently select a family analyser.
- Baseline analysis preserves every current evidence/quality limitation and must not emit target-relative geometry, hit association, target speed, or prescription claims without the existing exact gate.
- `run:*` and `analysis:*` remain canonical internal references and API inputs. They are not user-facing labels.
- Do not add a migration, cloud lookup, telemetry, or a manual picker as the normal path.

## Task 1 - Versioned Automatic Family Resolution

### Allowed files

- `kovaak_tracker/scenario_profiles.py`
- `webapp/backend/contracts.py`
- `webapp/backend/kovaak_run_store.py`
- `tests/test_scenario_profiles.py`
- `webapp/tests/test_contracts.py`
- `webapp/tests/test_kovaak_runs.py`
- bounded test fixtures under `tests/fixtures/scenarios/`
- `docs/PRD.md`
- `docs/ARCHITECTURE.md`

### Tests first

1. A copied local definition for `1wall5targets_pasu` resolves as confirmed `dynamic_clicking` without an exact profile ref.
2. The same display name without trusted structural evidence stays a non-dispatching candidate.
3. Malformed, oversized or untrusted definition content yields an unknown resolution without a crash or path disclosure.
4. An automatic-family resolution exposes only baseline analyzers/metric families and keeps exact visual claim limits unavailable.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scenario_profiles.py webapp\tests\test_contracts.py webapp\tests\test_kovaak_runs.py -q
```

### Stop Rule

Stop if the only available classification evidence is a display-name heuristic, a network lookup, or an unbounded local source file.

## Task 2 - Family Baseline Dispatch

### Allowed files

- `webapp/backend/worker.py`
- directly corresponding tests under `webapp/tests/`
- `kovaak_tracker/dynamic_clicking_analysis.py`
- `kovaak_tracker/tracking_analysis.py`
- `kovaak_tracker/target_switching_analysis.py`
- directly corresponding tests under `tests/`

### Tests first

1. An auto-classified dynamic scenario with Raw/Performance/MP4 invokes the dynamic baseline path rather than `scenario_outcome_only.v1`.
2. Without an exact visual calibration, the result is a `dynamic_clicking.v1` baseline with only supported non-target-relative facts; all geometry/outcome-dependent metrics remain unavailable with explicit limitations.
3. Exact reviewed profiles retain their current family-specific paths and metric coverage.
4. Unknown scenarios and invalid source snapshots remain fail-closed without invoking a family analyser.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_worker.py tests\test_dynamic_clicking_analysis.py tests\test_target_switching_analysis.py -q
```

### Stop Rule

Stop if baseline mode requires inventing target identity, visual geometry, an outcome association, a diagnosis threshold, or a training prescription.

## Task 3 - Safe Run And Analysis Presentation Labels

### Allowed files

- `webapp/backend/queue.py`
- `webapp/backend/read_models.py`
- `webapp/backend/schemas.py`
- `webapp/backend/coach_context_refs.py`
- directly corresponding tests under `webapp/tests/`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/components/task3/TasksClient.tsx`
- `webapp/frontend/components/task4/HistoryClient.tsx`
- `webapp/frontend/components/task4/RunInspector.tsx`
- `webapp/frontend/components/task5/AnalysisWorkspace.tsx`
- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/components/task6/CoachSidebar.tsx`
- directly corresponding frontend tests

### Tests first

1. A Run and Analysis list item has a label containing scenario, training time and analysis time when known, while retaining the ref separately for navigation/API calls.
2. Task cards, History, Analysis workspace, attached Coach context and Coach-visible metadata render labels and never literal `Run N` / `analysis:N` as user copy.
3. An unavailable training or analysis time uses a neutral fallback without exposing the ref.
4. Labels reject paths, raw evidence data, secrets and owner identity.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_kovaak_runs.py webapp\tests\test_coach_agent_runs.py -q
npm.cmd --prefix webapp\frontend run test:contracts
npm.cmd --prefix webapp\frontend run type-check
```

### Stop Rule

Stop if replacing labels requires changing ref ownership, URL/API contracts, or persisting a second display-only data store.

## Task 4 - Regression And Field Replay

### Allowed files

- files modified by Tasks 1-3
- this plan
- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`

### Tests first

1. The copied `1wall5targets_pasu` fixture resolves automatically and generates a limited dynamic result without reusing `pasu small reload` calibration.
2. Existing static, dynamic, tracking and switching fixtures preserve their exact-profile behavior.
3. A read-only copy of the installed DB shows presentation labels for the affected Runs/Analyses and no newly created session mutates the live DB.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix webapp\frontend run test:contracts
npm.cmd --prefix webapp\frontend run type-check
npm.cmd --prefix webapp\frontend run build
git diff --check
```

### Stop Rule

Do not report an existing live Analysis as retroactively reclassified. Historical results are immutable; verify the new route in an isolated copied DB or a new explicit Analysis only.
