# Internal Beta E2E Remediation v1 Implementation Plan

> **Status: active for Tasks 1-4.** 点点于 2026-08-08 明确授权“全修”；各 Task 可按文件边界并行施工，但必须在 Task 4 汇总验证后才能重打内测包。

**Goal:** Repair every confirmed installed-product E2E failure in Coach data access, product-command execution, process supervision, Analysis presentation, and production navigation before producing a replacement unsigned Windows internal-test installer.

**Architecture:** Keep deterministic Analysis, bounded Coach projections, the Pi/TypeScript Coach, FastAPI runtime, and Tauri parent lifecycle as the existing single paths. Make Coach reads available before teaching conclusions, fail the turn when a required product tool fails, let Tauri supervise packaged children with bounded restart, and make the static frontend render current backend truth instead of stale success state.

**Tech Stack:** Python/FastAPI/SQLite, TypeScript/Pi runtime, React/Next static export, Tauri 2/Rust, Playwright, PowerShell/NSIS.

## Frozen Decisions

- Do not weaken grounding, privacy, owner-scope, confirmation, or evidence sufficiency gates.
- A direct user question may read the referenced Analysis and history even when evidence is insufficient for a teaching conclusion; insufficiency limits claims and prescriptions, not fact retrieval.
- A failed required product tool fails the Coach turn. Provider text after that failure is not adopted as a successful product action.
- The Pi/TypeScript Coach remains the only Coach implementation; no legacy Python answer fallback is added.
- Tauri remains the only parent supervisor. Unexpected Runtime or Sidecar exit receives at most three bounded restart attempts with short backoff; normal shutdown never restarts children.
- Do not add a database migration, updater, signing certificate, telemetry, or new Provider protocol.

## Task 1 - Coach Read And Product-Tool Truthfulness

### Allowed files

- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/product-command-tools.ts`
- directly corresponding tests under `webapp/coach-runtime/test/`
- `webapp/backend/coach_agent_runs.py`
- directly corresponding tests under `webapp/tests/`

### Tests first

1. A direct question with a referenced Analysis reaches Provider/tool execution instead of returning an intake fallback with zero Provider rounds.
2. `analysis.get` accepts the real backend Analysis projection and preserves evidence limitations without inventing target-relative facts.
3. A required product-tool error makes the turn fail and cannot be followed by a `succeeded` Agent Run.
4. Plan generation/save/activation/item-add still require the existing confirmation and immutable prepared-item contracts.

### Verification

```powershell
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = (Get-ChildItem webapp\coach-runtime\test -Filter *.test.ts | Sort-Object Name).FullName
node "--import=$loaderUrl" --test @tests
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_coach_agent_runs.py -q
```

### Stop Rule

Stop if the repair requires bypassing grounding validation, exposing raw evidence or secrets, accepting an unconfirmed write, or adding a second Coach response path.

## Task 2 - Packaged Child Process Supervision

### Allowed files

- `webapp/frontend/src-tauri/src/runtime.rs`
- `webapp/frontend/src-tauri/src/lib.rs`
- directly corresponding Rust tests under `webapp/frontend/src-tauri/src/`

### Tests first

1. Unexpected Runtime or Sidecar exit is detected and restarted with fresh child handles.
2. Restart attempts are bounded to three with short backoff and produce an explicit terminal error after exhaustion.
3. Normal application shutdown suppresses restart and terminates both process trees and listeners.
4. Development and packaged resource selection, hidden child windows, token handling, and app-data ownership remain unchanged.

### Verification

```powershell
Push-Location webapp\frontend\src-tauri
cargo +stable-x86_64-pc-windows-msvc fmt --check
cargo +stable-x86_64-pc-windows-msvc test runtime --locked
cargo +stable-x86_64-pc-windows-msvc clippy --locked --all-targets -- -D warnings
Pop-Location
```

### Stop Rule

Stop if restart ownership must move outside Tauri, a release build must fall back to source runtimes, or shutdown cannot distinguish intentional termination from a crash.

## Task 3 - Production Analysis, Status, And Plan State

### Allowed files

- `webapp/frontend/app/analysis/page.tsx`
- `webapp/frontend/app/analysis/[analysisId]/page.tsx`
- `webapp/frontend/lib/navigation.ts`
- `webapp/frontend/lib/navigation.test.ts`
- `webapp/frontend/lib/desktop.ts`
- `webapp/frontend/lib/api.ts`
- directly corresponding tests under `webapp/frontend/lib/`
- `webapp/frontend/components/task3/AppShell.tsx`
- `webapp/frontend/components/task4/HistoryClient.tsx`
- `webapp/frontend/components/task5/AnalysisWorkspace.tsx`
- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/components/task6/CoachSidebar.tsx`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/types.ts`
- directly corresponding frontend tests and Playwright specs
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/schemas.py`
- directly corresponding tests under `webapp/tests/`

### Tests first

1. Static production navigation accepts `/analysis?id=N` and `/analysis/?id=N` without `useSearchParams()` build or CSR bailout.
2. Runtime loss clears stale success indicators and gives an explicit unavailable state without hanging requests.
3. A managed-child restart invalidates stale loopback connection data and retries only after obtaining the new Tauri connection contract.
4. Run inspector exposes finalization, alignment, and evidence coverage from the real DTO.
5. Analysis limitations do not contradict issue wording or claim unavailable target-relative facts.
6. Training Plan writes invalidate/refetch the visible plan without a route reload.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_kovaak_runs.py -q
npm.cmd --prefix webapp\frontend run test:contracts
npm.cmd --prefix webapp\frontend run type-check
npm.cmd --prefix webapp\frontend run build
npm.cmd --prefix webapp\frontend run test:e2e
```

### Stop Rule

Stop if the fix needs a new data store, a database migration, fabricated evidence, or a Next server in the packaged product.

## Task 4 - Aggregate Regression, Repackage, And Installed E2E

### Allowed files

- files modified by Tasks 1-3
- existing installer/runtime scripts and their direct tests under `scripts/`
- `docs/PROGRESS.md`
- this plan and `docs/superpowers/plans/README.md`

### Tests first

1. Full Python, Coach runtime, frontend contracts/type/build/production E2E, and Rust gates pass from the shared worktree.
2. The unsigned installer rebuilds from current sources and its SHA-256 is recorded.
3. A separate installed copy can open an existing Analysis, answer a referenced-Analysis Coach question, create and surface a confirmed Training Plan, survive one Runtime crash and one Sidecar crash, persist state across app restart, and shut down without child processes or listeners.
4. Failure states remain visible and fail closed; no automated result may be reported as a real Provider, KovaaK, or hardware result unless that path was actually exercised.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = (Get-ChildItem webapp\coach-runtime\test -Filter *.test.ts | Sort-Object Name).FullName
node "--import=$loaderUrl" --test @tests
npm.cmd --prefix webapp\frontend run test:contracts
npm.cmd --prefix webapp\frontend run type-check
npm.cmd --prefix webapp\frontend run build
npm.cmd --prefix webapp\frontend run test:e2e
Push-Location webapp\frontend\src-tauri
cargo +stable-x86_64-pc-windows-msvc fmt --check
cargo +stable-x86_64-pc-windows-msvc check --locked
cargo +stable-x86_64-pc-windows-msvc test --locked
cargo +stable-x86_64-pc-windows-msvc clippy --locked --all-targets -- -D warnings
Pop-Location
powershell.exe -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1
powershell.exe -ExecutionPolicy Bypass -File scripts\test-windows-installer.ps1
git diff --check
```

### Stop Rule

Do not label the replacement installer ready if any installed-product critical path depends on the repository, a hidden stale process, an unverified Provider response, or a stale UI state. Do not sign, commit, push, or delete user data without separate authorization.
