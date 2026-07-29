# Full Worktree Recovery Audit Ledger

> Status: complete. Created and reconciled 2026-07-30. This ledger is the durable scope,
> evidence register, and recovery point for the current full-project read-only
> audit. It does not authorize product, contract, code, cleanup, commit, push,
> deployment, real-provider, product-database, or real-KovaaK changes.

## 1. Objective

Establish the trustworthy current state of Aiming Cookie before workspace
cleanup. Review committed local work, tracked working-tree changes, untracked
source and evidence, current contracts, tests, and release claims. Produce an
evidence-backed disposition for each coherent batch: retain, verify, repair,
quarantine, archive, or discard after explicit approval.

## 2. Frozen Snapshot

- Repository: `C:\Users\袜子\Desktop\Aiming-cookie`
- Branch: `main`
- HEAD: `def9e3b1983008826b15aed6581f68273730ee9b`
- `origin/main`: `d821663b166eadd2982903a0ef58eaa5ed78187e`
- Local committed delta: 23 commits ahead, 0 behind
- Tracked dirty files: 117
- Tracked diff: 16,626 insertions, 1,440 deletions
- Untracked files: 110
- Staged files: 0
- Initial whitespace check: `git diff --check` passed with Windows line-ending
  conversion warnings only
- Previous assessment baseline:
  `docs/superpowers/assessments/2026-07-24-backend-full-review-ledger.md`

The audit target is the combined state of:

1. `origin/main..HEAD`;
2. `HEAD` to the tracked working tree;
3. all untracked files;
4. current runtime, test, and field claims in `docs/PROGRESS.md` and active
   specs/plans.

The only repository mutation allowed during the audit is this ledger. If any
other path changes, pause and record the new state before continuing.

Snapshot drift recorded at 2026-07-30 01:26:14 +08:00: the untracked file
`docs/superpowers/assessments/2026-07-30-coach-action-streaming-kovaak-launch-feasibility.md`
appeared after the frozen snapshot. All three Wave 1 agents deny creating it.
It is preserved but excluded from audit evidence and scope unless the user
later attributes or adopts it. The untracked count consequently became 112
including this ledger and that external file.

The same excluded file continued changing during Wave 1: it grew from 18,975
bytes to 22,402 bytes and its last-write time moved to 2026-07-30 01:31:34
+08:00. Root did not read it. The changing file remains preserved and excluded.

## 3. Truth Hierarchy

1. Current explicit user instruction
2. `docs/PRD.md` for product scope and semantics
3. `docs/ARCHITECTURE.md` for stable system, data, dependency, and security
   contracts
4. Current code, tests, and real execution for implemented behavior
5. `docs/ROADMAP.md` for sequence and release gates
6. `docs/PROGRESS.md` for the latest implementation snapshot
7. Active specs and implementation plans for local contracts and authorized
   tasks
8. Assessments, research, generated artifacts, and archive material as evidence
   only

Contract drift is a finding. Code must not silently redefine product scope,
and documentation must not deny current code behavior.

## 4. Review Rules

- Read-only specialist work. Agents must not edit files, clean the workspace,
  commit, push, branch, deploy, or update product decisions.
- No real KovaaK launch/capture, product database access, provider request,
  credential access, installer run, or external state mutation.
- Safe static checks and fixture-based tests are allowed only when they do not
  inherit live KovaaK paths or product databases. Record exact commands and
  limitations.
- Historical test counts are evidence for their historical snapshot, not proof
  for the current combined state.
- Do not report speculative targets as confirmed findings.
- Do not treat intentional process-boundary validation as duplication without
  proving accept-set or behavior drift.
- Do not propose a parallel store, registry, schema, state machine, or routing
  path before locating and evaluating the existing mechanism.

## 5. Finding Standard

Every confirmed finding must include:

- unique ID and severity (`P0` critical, `P1` high, `P2` medium, `P3` low);
- exact file and line evidence;
- affected user/system behavior;
- reproduction or complete static reasoning chain;
- current test coverage and missing test;
- smallest viable correction;
- whether it changes PRD, Architecture, a frozen contract, or an active plan;
- whether explicit user approval is required;
- root review status: candidate, corroborated, confirmed, rejected, or deferred.

Non-findings must be classified separately as:

- pending safe automation;
- pending field measurement;
- release gate;
- documentation drift;
- rejected or de-scoped candidate;
- implementation batch with unknown provenance.

## 6. Four-Wave Assignment

| Wave | Track | Scope | Agent | Status |
|---|---|---|---|---|
| 1 | W1-A | Git provenance, document hierarchy, active-plan and contract consistency | `w1_contracts_provenance` | completed; root reviewed |
| 1 | W1-B | Backend reliability, SQLite ownership, lifecycle, API/provider security and privacy | `w1_backend_security` | completed; root reviewed |
| 1 | W1-C | Analysis correctness, evidence quality, scenario activation, metrics and fail-closed behavior | `w1_analysis_correctness` | completed; root reviewed |
| 2 | W2-A | Coach runtime, TeachingSession, confirmation, Training Plan and Knowledge Registry semantics | `w2_coach_knowledge` | completed; root reviewed |
| 2 | W2-B | Frontend product workflows, UI/UX contracts, accessibility and visible failure states | `w2_frontend_ux` | completed; root reviewed |
| 2 | W2-C | Tauri/native capture, process lifecycle, performance, resource bounds and storage | `w2_native_capture` | completed; root reviewed |
| 3 | W3-A | Tests, CI, build, packaging, dependencies, release and clean-machine reproducibility | `w3_tests_release` | completed; root reviewed |
| 3 | W3-B | Aim-family and future-source extensibility without speculative abstraction | `w3_extensibility` | completed; root reviewed |
| 3 | W3-C | Duplication, complexity, module boundaries, dead paths and maintainability | `w3_module_boundaries` | completed; root reviewed |
| 4 | W4-A | Adversarial reverse review of all candidate findings and rejected alternatives | `w4_adversarial_review` | completed; root reviewed |
| 4 | W4-B | Cross-layer integration, concurrency, recovery, retention and user-visible lifecycle | `w4_cross_layer_lifecycle` | completed; root reviewed |
| 4 | W4-C | Batch provenance, validation matrix and recovery/commit slicing recommendations | `w4_recovery_slicing` | completed; root reviewed |

Only one wave runs at a time. Root review must reconcile and record a completed
wave before dispatching the next three specialists.

## 7. Batch Trust Matrix

| Batch | Plan/contract owner | Code provenance | Review | Automation | Field evidence | Disposition |
|---|---|---|---|---|---|---|
| Local KovaaK account and benchmark synchronization | unknown | uncommitted | backend/module boundary reviewed | targeted backend tests pass | pending | PERF-01 and MB-01 open; cross-layer review pending |
| Coach teaching, privacy and retest loop | unknown | uncommitted | semantics/lifecycle reviewed | full Python suite has one deterministic failure; targeted Node suites pass | pending | COACH-01 and COACH-02 open |
| Analysis, evidence and scenario activation | unknown | uncommitted | correctness/extensibility reviewed | targeted Python/frontend tests pass | pending | AN-01 blocks trust; AN-03 open; EXT-02 hardening |
| Knowledge Registry v3/v4 migration | unknown | uncommitted | parity/extensibility reviewed | targeted Python/Node suites pass | pending | EXT-01 release hardening |
| Frontend and Tauri product closeout | unknown | mixed | frontend and native reviewed | frontend, Python capture, and MSVC Rust targets pass | pending | UX-01/02/03, AN-03, and CAP-01 open |
| Research harvest and generated evidence | assessment only | untracked | pending | not applicable | pending | pending |
| Product and architecture documents | upstream facts | uncommitted | pending | link checks pending | not applicable | pending |
| Release and clean-machine toolchain | Roadmap/Development | mixed | release readiness reviewed | fragmented local targets only; no CI matrix | absent | REL-01 open; REL-02 No-Go |

## 8. Findings Register

The 2026-07-24 ledger is a historical baseline and must be rechecked against
current code before reuse.

### GOV-01 (P2, confirmed) - Plan index authorizes completed or non-executable work

- Evidence: `docs/superpowers/plans/README.md:5-20` lists Active entries whose
  own headers or task bodies say complete or no executable Task, including the
  Viscose, semantic-remediation, TeachingSession, score-presentation, and
  RefleK plans. The same index says only Active plans may reach an executor at
  `:61-64`, while `AGENTS.md:82` requires a named active Task and
  `AGENTS.md:99` requires completed plans to move to archive.
- Impact: an executor can reopen completed work or mistake dirty-worktree batch
  organization for implementation authorization.
- Reproduction: follow `docs/README.md` to the Active index and compare each
  target header/task status. No runtime action is required.
- Test gap: no governance check proves that every Active entry has an
  executable Task or that every Completed entry resolves under archive.
- Minimal correction: after cleanup approval, atomically archive genuinely
  completed plans and leave only entries with a current executable Task.
- Approval: document moves/index cleanup require explicit user approval; no
  PRD or Architecture change is implied.

### GOV-02 (P3, confirmed) - Progress mixes a current snapshot with superseded status blocks

- Evidence: `docs/PROGRESS.md:3` declares the file a current snapshot and
  routes detailed history to archive. Current lines `:13`, `:39`, and `:43`
  close video/Data and four-family activation, while the retained 2026-07-26
  block at `:50-68` still calls video playback and Data release blockers and
  says Static/Dynamic/Switching lack production-active evidence.
- Impact: readers can select an obsolete blocker from the document designated
  as current state.
- Reproduction: compare the current conclusion with the dated 2026-07-26
  block. No runtime action is required.
- Test gap: no snapshot/history governance check exists.
- Minimal correction: after cleanup approval, preserve the old block under
  `docs/archive/history/` and leave one current summary/link in Progress.
- Approval: archival requires explicit user approval; product decisions do not
  change.

### SEC-01 (P3, confirmed) - Arbitrary Coach sidecar URL can receive local secrets

- Evidence: `webapp/backend/config.py:346-349` accepts an unrestricted
  `COACH_SIDECAR_URL`. `webapp/backend/coach_runtime.py:376-398` places the
  normalized runtime profile and tool bridge in the turn request, and
  `:812-819` / `:852-860` posts it directly to that base URL. Provider refresh
  credentials follow the same unrestricted base through
  `webapp/backend/provider_auth.py:178-188` and `:343-357`.
- Impact: a startup environment override can redirect Provider credentials,
  short-lived bridge bearer material, and any Desktop launch token carried by
  the bridge to an arbitrary remote HTTP endpoint. This violates the local
  sidecar boundary in `docs/PRD.md:278-283` and local-secret boundary in
  `docs/ARCHITECTURE.md:320-325`. Severity is P3 because the override is not a
  remote product input: an attacker must already control the local launch
  environment, which is also near the plaintext local credential store.
- Reproduction: set `COACH_SIDECAR_URL` to a controlled remote listener, then
  run a selected-Provider status/test/turn or OAuth refresh. Static request
  construction proves the target and payload; no live credential was used.
- Test gap: `webapp/tests/test_coach_runtime.py:89-115` proves that a credential
  reaches the default sidecar, and `:863-865` checks only the loopback default;
  no test rejects a non-loopback override.
- Minimal correction: fail closed on sidecar configuration and accept only an
  explicit loopback HTTP origin with a valid port and no userinfo, query, or
  fragment; define separately whether a path prefix is allowed. Desktop may
  need to ignore the environment override entirely.
- Approval: this changes a runtime security boundary and needs a dedicated
  authorized plan Task.

### PERF-01 (P3, confirmed) - KovaaK score reads grow across all historical snapshots

- Evidence: each successful Viscose S2 refresh materializes one fixed catalog
  snapshot in `webapp/backend/kovaak_benchmark_service.py:145-164`. The score
  projection after refresh and the score route call unbounded
  `benchmark_store.list_records` at `:179-184` and
  `webapp/backend/routes.py:820-826`. That helper first selects all owner IDs
  and then calls `get_record` once per row at
  `webapp/backend/benchmark_store.py:142-152`; even the latest-snapshot helper
  repeats the per-row query at `:155-182`.
- Impact: refresh count multiplies records read, Python allocations, and SQLite
  calls. A long-lived local profile eventually pays `1 + N` queries and
  processes obsolete snapshots whenever the score page or refresh response is
  projected.
- Reproduction: create multiple valid snapshots for one owner and count SQL
  statements or compare route latency/allocation growth. The current loops
  establish the linear behavior without touching product data.
- Test gap: no multi-snapshot capacity or query-count regression protects the
  score projection.
- Minimal correction: project scores from the latest complete snapshot only
  and fetch its rows in one query. Do not invent TTL or history deletion; the
  contract at `docs/ARCHITECTURE.md:256-258` preserves the previous successful
  snapshot on refresh failure.
- Approval: implementation changes need an authorized Task; retention policy
  must not be changed as part of the performance fix.

### AN-01 (P1, confirmed) - Switching episode projection erases upstream quality failure

- Evidence: `kovaak_tracker/visual_signals.py:2525-2670` reconstructs the
  visual-quality object after episode projection. At `:2664-2670` the enabled
  family set and status are derived only from episode limitations, so upstream
  frame, crosshair, or PTS limitations that disabled Switching are lost. The
  production worker passes the projected object onward at
  `webapp/backend/visual_worker_process.py:234-263`, while
  `kovaak_tracker/target_switching_analysis.py:945-951` accepts supported or
  limited quality when `target_switching` is enabled.
- Impact: a visual result that correctly failed closed upstream can be
  re-enabled after projection and emit deterministic Switching metrics.
- Reproduction: pass a rejected or Switching-disabled upstream visual result
  through episode projection with otherwise usable episode rows; observe the
  rebuilt quality gate.
- Test gap: no test projects an upstream rejected/disabled visual result
  through the episode path.
- Minimal correction: preserve upstream limitations, status, and enabled-family
  gate; add episode limitations without widening the accepted family set. Fall
  back to outcome-only when upstream Switching is disabled.
- Approval: analysis semantics and evidence acceptance change, so this needs a
  dedicated authorized Task and regression fixture.

### Rejected AN-02 candidate - Field acceptance coverage is not a runtime threshold

- `minimum_kill_chain_coverage` appears only inside the beanTS field-review
  fixture and its test. The active registry/manifest references the accepted
  producer gate and reviewed fixture, but no production ScenarioProfile,
  Architecture, or analyzer contract defines that number as a per-Run runtime
  support threshold.
- The earlier evidence proved that a fixture acceptance criterion is not
  projected into runtime, not that an activated runtime contract is bypassed.
  The original P1 claim is therefore rejected.
- If the product owner intends this field criterion to become a per-Run
  minimum, that is a new contract decision and should be specified/tested as a
  P2 projection gap; the audit must not silently invent that meaning.

### AN-03 (P2, confirmed and corroborated by W2-B) - Partial family metrics are labeled formal

- Evidence: `webapp/frontend/lib/contracts.ts:318-332` classifies a metric as
  formal using availability plus deterministic status, while partial family
  resolution is computed separately at `:138-146`. User-visible labels in
  `webapp/frontend/components/DiagnosisView.tsx:125-133` and
  `webapp/frontend/components/DataView.tsx:253-262` call the resulting group
  formal metrics. `webapp/frontend/tests/task5-analysis.test.ts` includes a
  coverage `0.4` case but does not require partial metrics to leave the formal
  group.
- Impact: incomplete evidence can be presented with the same trust label as a
  fully supported family, overstating diagnosis certainty even when the family
  summary remains descriptive.
- Reproduction: project a deterministic metric from a partial family and
  inspect the formal-metric collection and labels.
- Test gap: the direct Task 5 test is outside the default `lib/*.test.ts` npm
  test glob, and neither suite asserts that partial-family metrics are
  excluded or visibly qualified.
- Minimal correction: define one trust rule shared by family summary and metric
  grouping, then add the partial-family case to the default frontend suite.
- Approval: W2-B independently confirmed the trust-label conflict. The
  analysis/contract owner must define the formal/limited predicate without
  inventing a new coverage threshold; implementation requires an authorized
  Task.

### UX-01 (P1, confirmed) - Coach locator reports success without a receiver

- Evidence: `webapp/frontend/components/task6/CoachPanel.tsx:143-145`
  dispatches `aiming-cookie:coach-locate` and immediately displays `已定位`.
  Repository-wide search finds no listener outside the dispatch/source test;
  `webapp/frontend/components/task5/AnalysisWorkspace.tsx:72-76` and
  `:258-292` do not handle the event or an equivalent locator contract.
- Impact: clicking a valid Coach context neither switches the analysis tab nor
  seeks, scrolls, or highlights the referenced evidence, while the UI asserts
  that navigation succeeded. This breaks the Coach/workspace linkage required
  by `docs/frontend-uiux-design.md:584-590`.
- Reproduction: open Coach beside an analysis, click locate on any context,
  and observe only the feedback text change.
- Test gap: `webapp/frontend/tests/task6-contracts.test.ts:17-35` covers safe
  context projection/deletion but no end-to-end locator receiver or success
  acknowledgement. A source test merely checks the `已定位` string exists.
- Minimal correction: define a typed workspace locator with an acknowledged
  result; only show success after the destination tab/anchor actually changes.
- Approval: this crosses frozen Coach/workspace interaction boundaries and
  needs an authorized frontend Task.

### UX-02 (P2, confirmed) - Evidence-segment failure is presented as an empty timeline

- Evidence: `webapp/frontend/components/task5/VideoView.tsx:80-84` catches the
  EvidenceSegments request failure by setting `segments` to null; `:91`
  projects that as an empty list. The rendered path at `:167-258` has no local
  failure message or retry, while only the video URL failure at `:157-163`
  receives an explicit warning and retry.
- Impact: the video can remain playable while visual evidence silently
  disappears, so the user cannot distinguish no segments from a failed
  evidence request. This violates the partial/service-failure rules at
  `docs/frontend-uiux-design.md:685-695`.
- Reproduction: return 404 or 503 from
  `/api/sessions/{id}/evidence-segments` while the video endpoint succeeds.
- Test gap: `webapp/frontend/e2e/failure-matrix.spec.ts:98-122` already injects
  the 404 but does not assert a visible segment failure or retry action.
- Minimal correction: separate video and segment request states, retain the
  player, and show a segment-specific unavailable state with an explicit retry.
- Approval: user-visible error semantics need an authorized frontend Task; no
  backend contract change is implied.

### UX-03 (P2, confirmed) - History loses the Run selected for analysis

- Evidence: `webapp/frontend/components/task4/HistoryClient.tsx:223-239` links
  to `/analyze?run=<run_ref>`, but
  `webapp/frontend/components/task3/AnalyzeClient.tsx:3-4` does not read search
  parameters and `:98-110` selects a Run only when exactly one pending Run
  exists.
- Impact: with two pending Runs, confirming the second in History opens the
  analysis page with neither selected. The user must rediscover and reselect
  the intended evidence, breaking exact Run-selection continuity.
- Reproduction: create two pending Run fixtures, choose the second in History,
  follow the analysis link, and inspect the radio state.
- Test gap: `webapp/frontend/e2e/interaction-polish.spec.ts:91-97` exercises the
  confirmation dialog but never follows the selected Run through navigation;
  no invalid/expired query fallback is covered.
- Minimal correction: after list load, validate the `run` query against pending
  `run_ref` values and select the match; retain the existing single-Run/default
  behavior for missing or invalid refs.
- Approval: this implements the existing product continuity contract and still
  needs an authorized frontend Task.

### CAP-01 (P2, confirmed) - A drained finalizer still incurs the full capture-exit grace

- Evidence: the capture-exit monitor skips observation whenever a finalizer
  future is pending at `webapp/backend/desktop_runtime.py:126-140`. When the
  future drains, observation starts a release task whose
  `wait_for_capture_exit_drain` initializes with no pending future at `:61-71`;
  it therefore has no memory that a finalizer was previously observed and
  waits the complete 30-second hard grace defined at `:27-28`. The native
  coordinator remains in `Finalizing` and refuses a new capture at
  `webapp/frontend/src-tauri/src/capture_coordinator.rs:855-870` until release.
- Impact: after a normal pending finalizer completes, native session release
  can still be delayed by about 30 seconds. If KovaaK reappears during that
  window, the coordinator cannot begin the next capture and creates an
  avoidable evidence gap.
- Reproduction: with a 200 ms injected grace and 10 ms poll, begin the monitor
  with `has_pending=True`, drain before the first observation, and measure from
  drain to release. Both the specialist and root observed
  `release_delay_after_drain=0.203s`.
- Test gap: `webapp/tests/test_desktop_runtime.py:431-473` proves only that
  status polling is skipped while pending and begins after drain; it does not
  assert immediate release after a known pending future drains.
- Minimal correction: preserve pending history across monitor/observe, or
  start the release drain task while pending so it can observe the transition;
  add a regression asserting no second hard-grace wait after drain.
- Approval: this changes Desktop/native lifecycle timing and needs an
  authorized Task plus a Windows field check; it does not change capture or
  retention contracts.

### TOOL-01 (P2, confirmed) - Python test/setup entry points do not select one supported runtime

- Evidence: `docs/DEVELOPMENT.md:19-27` creates the Windows venv with bare
  `py`, while the machine default is Python 3.9.7 and the repository venv used
  by Desktop/tests is Python 3.11.9. Generic tests at `:135-143` and
  `webapp/README.md:9-20` / `:31-36` use bare `pytest`, `python`, or `pip`, while
  only the Windows Gate at `docs/DEVELOPMENT.md:158-162` explicitly selects the
  venv. Current code already contains PEP 604 union syntax, including
  `webapp/backend/auth.py:23` and `tests/test_analysis_evidence.py:95`, which
  Python 3.9 cannot parse.
- Impact: following the documented Windows bootstrap in a fresh shell can
  create or invoke Python 3.9, causing collection failures or version-specific
  behavior that is then mistaken for a product defect. A bare `pytest` result
  does not verify the Desktop runtime.
- Reproduction: `py --version` returns 3.9.7 while
  `.venv\\Scripts\\python.exe --version` returns 3.11.9; earlier audit runs
  produced both a syntax collection failure and false asyncio-lock failures
  only under global 3.9.
- Test gap: no repository Python-version declaration, preflight, or CI check
  rejects an unsupported interpreter.
- Minimal correction: in a release/toolchain Task, declare the supported
  Python runtime and make setup/test commands select it explicitly via
  `python -m pytest` from the activated/absolute venv.
- Approval: this changes the development/release toolchain but not PRD or
  Architecture; it needs an authorized Task.

### TEST-01 (P2, confirmed) - Default frontend test gate omits task contracts and E2E

- Evidence: `webapp/frontend/package.json:11` defines `npm test` as only
  `lib/*.test.ts`; `:12` isolates E2E in a separate command. The current tree
  contains 2 matching lib files, 10 `frontend/tests/*.test.ts` files, and 8
  `frontend/e2e/*.spec.ts` files. Nevertheless the documented Windows frontend
  Gate at `docs/DEVELOPMENT.md:168-171` runs only the default test and build.
- Impact: the documented/default green gate omits Task 3-6 contracts, source
  checks, and all browser workflows. A regression in those surfaces can merge
  while `npm test` remains green; AN-03's direct Task 5 suite is one concrete
  omitted path.
- Reproduction: inspect the script glob and enumerate the three test
  directories; root's default command ran 9 tests while a separate four-task
  command ran 19 additional tests.
- Test gap: no aggregate unit/contract command or repository CI/release matrix
  asserts that the task and E2E suites run.
- Minimal correction: provide explicit unit/contract and E2E gates and invoke
  both from the release matrix. Keep the fast unit command distinct rather
  than hiding a build-producing Playwright run inside it.
- Approval: test-entry and CI changes need an authorized release Task; product
  contracts do not change.

### REL-01 (P2, confirmed release risk) - Clean-machine dependency and toolchain resolution is unconstrained

- Evidence: root and webapp requirements files use unpinned or lower-bound
  Python dependencies; no Python lock/constraints or version file exists.
  `docs/DEVELOPMENT.md:8-27` uses `pip install` and `npm install` despite Node
  lockfiles, the frontend package has no Node engine, the repository has no CI
  directory or toolchain-version file, and the machine's default Rust host is
  GNU while the product Gate requires explicit MSVC at `:173-180`.
- Impact: two clean Windows machines can resolve different Python/OpenCV/Node
  and Rust combinations, with no repository-controlled gate to detect a
  divergent build or test result.
- Reproduction: repository inventory finds no `.python-version`, Python lock,
  `rust-toolchain`, Node version file, or checked-in CI entry; local Python and
  Rust already demonstrate two active runtime/toolchain choices.
- Test gap: no clean-directory install matrix covers Python, frontend/Pi lock
  restoration, MSVC Rust, production frontend build, or packaging.
- Minimal correction: a dedicated release/toolchain plan must freeze the
  supported Python/Node/Rust matrix and dependency strategy, use existing Node
  lockfiles through `npm ci`, and add isolated automated gates.
- Approval: version/lock/CI policy changes require explicit release ownership;
  no product contract change is implied.

### REL-02 (P1, confirmed release blocker; not a shipped regression) - Desktop is not a distributable bundle

- Evidence: `webapp/frontend/src-tauri/tauri.conf.json:33-35` sets
  `bundle.active` false and declares no external binary/resource payload.
  `webapp/frontend/src-tauri/src/runtime.rs:35-60` launches an external Python
  module from a project root, while `:181-194` falls back to a compile-time
  source-relative root and `python` from PATH. A release executable exists, but
  no MSI/NSIS bundle directory exists. `docs/ROADMAP.md:130-142` and current
  Progress explicitly retain installer/signing/update/download/field No-Go.
- Impact: the existing executable cannot be treated as a clean-machine product
  artifact: an end user is not supplied the Python runtime, backend source,
  installer, update/signing chain, or verified download asset.
- Reproduction: inspect the Tauri config/runtime resolution and bundle output;
  the executable exists while bundle/MSI/NSIS outputs and bundled runtime
  declarations do not.
- Test gap: no clean-machine install/launch/update/uninstall, signature/hash,
  static download, or multi-network field matrix exists.
- Minimal correction: keep release No-Go. A separate distribution plan must
  freeze sidecar/resource and Next/Tauri packaging, installer/update/signing,
  hashes, and clean-machine/field Gates before implementation.
- Approval: distribution architecture and release scope require PRD/
  Architecture ownership plus explicitly authorized Tasks; toggling
  `bundle.active` alone is not a fix.

### EXT-01 (P3, confirmed) - Registry validation does not resolve prescription refs against the active manifest

- Evidence: `kovaak_tracker/coach/knowledge_registry.py:399-447` validates only
  the `scenario_profile_ref` shape. The value flows through
  `kovaak_tracker/coach/agent_tools.py:522-525`,
  `webapp/backend/teaching_session_store.py:110-139`, and
  `webapp/coach-runtime/src/teaching-policy.ts:156-190`, where only prefix and
  text-safety checks are repeated.
- Impact: a packaged knowledge entry can name an unregistered, retired, or
  inactive ScenarioProfile and pass Registry/sidecar validation. This is an
  asset-validation gap, but not an execution bypass: the prepared-plan compiler
  at `webapp/backend/coach_agent_runs.py:338-410` intersects active Registry and
  manifest refs and rejects a mismatch before executable teaching state.
- Reproduction: change one in-memory v4 prescription ref to
  `scenario:movement.unreviewed@99`; both Python and TypeScript validators
  accept and return it. No file is changed.
- Test gap: Registry shape/capability/parity tests never require a prescription
  ref to resolve in the active ScenarioProfile manifest.
- Minimal correction: add the same canonical active-ref check to the packaged
  knowledge validation/release gate so invalid assets fail earlier; do not
  create a second registry.
- Approval: this strengthens asset validation without changing the existing
  execution boundary and needs an authorized Task.

### EXT-02 (P3, confirmed) - Aiming Profile lacks a second scenario metric allowlist check

- Evidence: worker dispatch enforces `allowed_metric_families` at
  `webapp/backend/worker.py:2515-2557`, but the downstream canonical projector
  at `webapp/backend/aiming_profile_store.py:197-275` accepts metrics through a
  global key/direction table without checking the scenario allowlist. The
  result is recorded at `webapp/backend/worker.py:1003-1020`. Tracking and
  Switching result scenario projections also omit `aim_family` at
  `worker.py:3017-3022` and `:3190-3195`.
- Impact: if an analyzer result is misrouted or later drifts, any recognized
  deterministic metric namespace can be persisted into the long-lived Profile
  of an exact scenario that did not authorize that metric family. Normal
  production dispatch already enforces analyzer/family/allowlist, so current
  evidence establishes missing canonical-store defense in depth rather than a
  demonstrated normal-flow corruption.
- Reproduction: an in-memory static scenario with
  `allowed_metric_families=[static_clicking]` and a deterministic
  `target_switching.transition_time_ms` metric still produces that Switching
  Profile dimension.
- Test gap: Profile tests cover partial metrics and native-static special cases
  but no reverse family/allowlist rejection.
- Minimal correction: reuse the frozen input snapshot's scenario resolution in
  the Profile projection and require analyzer/metric family consistency before
  recording; fail closed if the relation cannot be proven.
- Approval: this changes long-lived canonical Profile acceptance and needs an
  authorized Task.

### Rejected EXT-03 candidate - Video fallback may legitimately compute kinematics

- Adversarial review found no frozen contract requiring `video_fallback` to use
  the separate visual-validation phase/domain. `docs/PRD.md:133-147` defines
  this compatibility path as MP4 + Stats producing CV pan trajectory, flick
  segmentation, and deterministic diagnosis.
- The facts below describe the implementation, but they do not establish a
  semantic defect. The original P3 claim is rejected.

- Evidence: `webapp/backend/read_models.py:505-518` maps a running
  `video_fallback` without an explicit phase to `computing_kinematics`.
  `webapp/backend/worker.py:4062-4126` explicitly uses the same phase while
  executing the MP4/Stats fallback analyzer, and `:4182-4194` categorizes its
  exception as `kinematics`. The frontend renders that as `计算运动学` at
  `webapp/frontend/components/task3/TasksClient.tsx:13-18`.
- Earlier interpretation (rejected): a video fallback analysis or failure is shown as a movement-input
  computation problem, so recovery guidance and task progress identify the
  wrong source domain.
- Reproduction: `_phase_for` returns `computing_kinematics` for a running
  `video_fallback`; the worker's fallback exception takes the non-multimodal
  `kinematics` branch.
- Test gap: capability tests prove only that input-native excludes a video
  phase; they do not assert fallback phase or failure-domain semantics.
- Disposition: no correction is authorized from this audit. Changing the phase
  or failure domain would first require a new product contract decision.

### MB-01 (P2, confirmed) - Stale scores remain publicly available after account switching

- Evidence: switching the saved KovaaK account marks all currently available
  records stale at `webapp/backend/kovaak_connection_store.py:34-51`.
  `/api/kovaak-scores` nevertheless sends unfiltered `list_records` through
  `project_benchmark_summary` at `webapp/backend/routes.py:820-826`, and that
  projector does not check `availability` at
  `webapp/backend/coach_context_refs.py:55-83`. The Coach bundle uses the
  correct available-only `list_latest_snapshot` path at
  `coach_context_refs.py:616-629`.
- Impact: after the user changes the connected Steam account and before a new
  successful refresh, the public score surface can present the previous
  account's stale scores as current available data.
- Reproduction: in a temporary SQLite DB, create one complete 158-record
  snapshot, switch between two synthetic Steam IDs, and project both paths.
  All records become stale, but the public projection returns `available` with
  78 items while the Coach latest-available projection returns none. Root also
  reproduced the projection in memory.
- Test gap: the connection test asserts stale storage and empty Coach context,
  while score-route tests cover complete/partial snapshots without an account
  switch; no test calls `/api/kovaak-scores` after staling.
- Minimal correction: centralize available-only snapshot selection for public
  and Coach score projections, or make the projector reject non-available
  records. Combine this with PERF-01's single-query latest-snapshot correction
  without deleting historical rows.
- Approval: this changes public score freshness semantics within the existing
  contract and needs an authorized Task.

### COACH-01 (P2, confirmed) - Deleted Analysis evidence can continue driving a TeachingSession

- Evidence: terminal Analysis deletion marks its active Coach contexts deleted
  in `webapp/backend/queue.py:845-860` and
  `webapp/backend/coach_context_refs.py:641-650`. The next bundle loads only
  active contexts at `coach_context_refs.py:556-573`, so deleting the only one
  yields `contexts=[]`. `_hydrate_teaching_state` at
  `webapp/backend/coach_agent_runs.py:477-496` clears mismatched source refs only
  when `active_refs` is non-empty; the empty set therefore preserves the old
  observation, candidate, cue, and retest intent. `create_run` persists and
  contracts that state at `:1589-1623`.
- Impact: a deleted Analysis can remain the factual basis for a subsequent
  teaching contract and advance confirmation/training phases, even though the
  evidence ref is no longer available. This conflicts with
  `docs/ARCHITECTURE.md:223-229`: history may remain, but unsafe teaching must
  return to intake/unresolved rather than guess from unavailable evidence.
- Reproduction: hydrate a lesson from one analysis context, then hydrate the
  same state with an empty context bundle. Root observed the deleted source ref,
  observation, and `immediate_matched` retest remain; adding one unrelated
  active context triggers the clear branch.
- Test gap: `webapp/tests/test_coach_agent_runs.py:1082-1092` currently asserts
  the stale fields remain when contexts are empty. No integration test covers
  delete Analysis -> next TeachingSession run -> unavailable evidence.
- Minimal correction: distinguish an intentionally context-free lesson from a
  source ref that is deleted/unavailable; clear or pause only state grounded in
  the invalid ref, and add both deletion and unrelated-context controls.
- Approval: this changes Coach lifecycle state transitions within the frozen
  deletion contract and needs an authorized contract/implementation Task.

### COACH-02 (P3, confirmed) - Coach failure reply loses the null sentinel during privacy redaction

- Evidence: `webapp/backend/coach_engine.py:310-330` returns `reply=None` when
  the selected Pi runtime fails and fallback is disabled. The dirty change in
  `webapp/backend/coach_service.py:286-288` now passes that value through
  `redact_temporary_steam_profiles`, whose non-string branch returns `""` at
  `webapp/backend/coach_commands.py:158-163`. The API contract still declares
  the reply nullable at `webapp/backend/schemas.py:520-523`, and the committed
  regression test expects the null sentinel at
  `webapp/tests/test_routes_coach.py:982-1017`.
- Impact: an actual runtime failure is projected as an empty successful-text
  value instead of the established no-reply sentinel. Strict API consumers can
  no longer distinguish "no reply was generated" from a generated empty reply,
  and the current aggregate Python gate is red. The visible frontend currently
  treats both values as falsy, so this is P3 rather than a blocked user flow.
- Reproduction: under the repository Python 3.11 venv, both the isolated node
  and the complete `test_routes_coach.py` file fail with `assert '' is None`;
  the latter result is `1 failed, 28 passed`. The full isolated Python suite
  reports the same sole failure with `1 failed, 1522 passed, 5 skipped`.
- Test coverage: the existing committed route regression test catches the
  defect; no new test is required to prove it. Privacy-redaction tests should
  retain explicit `None` and string controls when the implementation is fixed.
- Minimal correction: preserve `None` before invoking the string redactor and
  redact only a real reply string. Do not broaden the API schema or rewrite the
  existing test to accept an empty string.
- Approval: this is a narrow implementation correction inside the existing
  nullable API contract; it needs an authorized Coach command/sidecar Task but
  no PRD or Architecture decision.

### Rejected W2-A candidate - Module-level asyncio locks fail under the product runtime

- The candidate was produced only by bare global `pytest` on Python 3.9. The
  two reported node IDs both pass under the repository Python 3.11 venv, and
  their full seven-file extended order passes 149/149 under that same runtime.
- Current Desktop/FastAPI product execution uses one application event loop;
  no current contract or code path establishes cross-loop access to these
  module-level locks. A hypothetical multi-loop/worker design is not evidence
  of a current product defect.
- The global `pytest`/Python 3.9 mismatch remains useful input for W3-A's
  clean-machine and test-entry review, especially because other repository
  tests already use Python syntax unsupported by 3.9.

## 9. Validation Log

| Date | Scope | Command/evidence | Result | Limitations |
|---|---|---|---|---|
| 2026-07-30 | Initial Git snapshot | `git status`, `git rev-parse`, `git rev-list`, `git diff --stat`, untracked inventory | Snapshot recorded in section 2 | No functional tests run |
| 2026-07-30 | Whitespace | `git diff --check` | Passed | LF/CRLF conversion warnings only |
| 2026-07-30 | Wave 1 dispatch | Three `gpt-5.6-terra high` read-only specialists | W1-A, W1-B, and W1-C in progress | Findings require root corroboration |
| 2026-07-30 | Local verification runtime | Python 3.11.9; Node 24.14.0; npm 11.9.0; Rust/Cargo 1.97.0; default GNU toolchain | Python, frontend, Pi loader, and existing `.next` dependencies are present | Rust product validation must explicitly use the MSVC toolchain |
| 2026-07-30 | Agent contract parity | SHA-256 for `AGENTS.md` and `CLAUDE.md` | Both `12156098...6BB5BA1` | Byte parity confirmed |
| 2026-07-30 | Snapshot drift | New untracked Coach action-streaming/KovaaK launch feasibility assessment | Creation time 01:26:14 +08:00; source unknown; all Wave 1 agents deny authorship | Preserved and excluded from audit evidence |
| 2026-07-30 | Continuing snapshot drift | The same excluded assessment changed during Wave 1 | 18,975 -> 22,402 bytes; last write 01:31:34 +08:00 | Root did not read it; it remains outside audit evidence |
| 2026-07-30 | W1-A contracts/provenance | Git range, current docs/indexes, active plan headers, code-visible account boundary | Five committed provenance batches and three dirty date batches mapped; GOV-01 P2 and GOV-02 P3 confirmed | No functional tests; post-snapshot assessment excluded |
| 2026-07-30 | W1-B backend/security | Backend config/request paths, SQLite stores/routes, ownership and lifecycle guards | SEC-01 initially P2 and PERF-01 P3; 166 backend tests and 42 Coach runtime tests passed under repository Python 3.11 venv | W4 later downgraded SEC-01 to P3; no live credentials, Provider, or product DB used |
| 2026-07-30 | W1-C analysis/correctness | Analyzer gates, exact scenario profiles, evidence projection, frontend trust grouping | AN-01 P1, AN-02 initial candidate P1, and AN-03 P2 | W4 rejected AN-02 after contract review; agent's Python runs used global 3.9 and root repeated the relevant scope under the venv |
| 2026-07-30 | Root Wave 1 Python verification | `.venv\\Scripts\\python.exe -m pytest` with KovaaK discovery disabled | Analysis group: 246 passed, 2 skipped; time/CSV/ingest/contracts plus performance parser: 83 passed | Targeted suites only; temporary/test stores used; no field capture |
| 2026-07-30 | Root Wave 1 frontend verification | default frontend test command and direct Task 5 Node test | 9 passed and 7 passed | Direct Task 5 suite is not included in default npm test glob |
| 2026-07-30 | Root W1-B verification | `.venv\\Scripts\\python.exe -m pytest` across DB, queue, Provider, benchmark, connection, and Coach runtime suites | 208 passed | Targeted backend suites only; no Provider, product DB, or real KovaaK access |
| 2026-07-30 | Wave 1 reconciliation | Root line review plus targeted Python/frontend verification | W1-A/B/C completed; pre-adversarial register had 2 P1, 3 P2, and 2 P3 | W4 later changed SEC-01 and rejected AN-02; no finding was fixed |
| 2026-07-30 | Wave 2 dispatch | Three `gpt-5.6-terra high` read-only specialists | W2-A Coach/knowledge, W2-B frontend UX, and W2-C native capture in progress | Findings require root corroboration; excluded changing assessment remains out of scope |
| 2026-07-30 | W2-B frontend product path | Coach locator, analysis video/evidence state, History-to-analysis selection, trust labels | UX-01 P1, UX-02 P2, UX-03 P2 confirmed; AN-03 independently corroborated | No Playwright E2E run; static paths and existing fixture contracts reviewed |
| 2026-07-30 | Root W2-B verification | frontend `type-check` plus direct Task 3/4/5/6 contract tests | Type-check passed; 19 tests passed | Existing tests do not exercise the three confirmed failure paths |
| 2026-07-30 | W2-A Coach/knowledge | Coach bridge, confirmation/retest, TeachingSession, temporary Steam refs, Registry v3/v4 parity | No new confirmed defect; 201 Python tests and 138 Node tests passed in the specialist scope | Specialist used global Python 3.9; its two lock failures required venv recheck |
| 2026-07-30 | Root W2-A verification | Repository Python 3.11 venv on reported lock failures and full extended order | Reported nodes: 2 passed; extended set: 149 passed; target Coach/knowledge set: 201 passed; Node parity/runtime: 138 passed | Python 3.9 lock candidate rejected; clean-machine version pinning deferred to W3-A |
| 2026-07-30 | W2-C native capture | Desktop release monitor, capture finalizer/client, coordinator lifecycle | CAP-01 P2 confirmed; 64 passed, 1 skipped in Python scope | Specialist's first MSVC command used the wrong GNU host toolchain and was not accepted |
| 2026-07-30 | Root W2-C verification | 200 ms isolated lifecycle reproduction; Python capture suites; `cargo +stable-x86_64-pc-windows-msvc test --lib capture_coordinator` | Delay reproduced at 0.203 s; 64 passed, 1 skipped; MSVC Rust 13 passed | No real KovaaK/capture or Windows field measurement |
| 2026-07-30 | Wave 2 reconciliation | Root line review, runtime correction, and targeted verification | W2-A/B/C completed; cumulative register now has 3 P1, 6 P2, and 2 P3 confirmed findings | Field evidence remains pending; no finding was fixed |
| 2026-07-30 | Wave 3 dispatch | Three `gpt-5.6-terra high` read-only specialists | Tests/release, extensibility, and module-boundary tracks in progress | Findings require root corroboration; no install/build/deploy authority |
| 2026-07-30 | W3-A tests/release | Runtime entry points, test globs, locks/version files, CI, Tauri bundle/runtime and release gates | TOOL-01 P2, TEST-01 P2, REL-01 P2, and REL-02 P1 release blocker confirmed | Read-only inventory only; no dependency install, build, bundle, sign, or deploy |
| 2026-07-30 | W3-B extensibility | Scenario prescription refs, Profile metric family acceptance, task phase/failure semantics | EXT-01/02 initially P2 and EXT-03 initially P3 | W4 downgraded EXT-01/02 to P3 and rejected EXT-03 |
| 2026-07-30 | Root W3-B verification | Python/TypeScript in-memory mutations plus scenario/knowledge/Profile tests | Invalid scenario ref accepted by both runtimes; cross-family metric persisted; fallback maps to kinematics; 90 targeted tests passed | No packaged asset or product DB modified |
| 2026-07-30 | W3-C module boundaries | Registry/Coach parity and benchmark ownership/projection paths | MB-01 P2 confirmed; Registry/Coach scopes passed 14 Node and 191 Python tests; benchmark scope passed 18 | No generic size/duplication findings reported without behavior evidence |
| 2026-07-30 | Root W3-C verification | In-memory stale projection plus benchmark/connection/Coach-summary suites | Stale snapshot projected as available with 78 items; 18 targeted tests passed | Agent independently reproduced with a temporary SQLite DB; no product data used |
| 2026-07-30 | Wave 3 reconciliation | Root line review, in-memory/temporary-DB mutations, and targeted verification | W3-A/B/C completed; pre-adversarial register had 4 P1, 12 P2, and 3 P3 | W4 later removed/downgraded findings; REL-02 remains a declared No-Go, not a shipped regression |
| 2026-07-30 | Wave 4 dispatch | Three `gpt-5.6-terra high` read-only specialists | Adversarial verdicts, cross-layer lifecycle, and recovery slicing in progress | No cleanup or implementation; all three must return before final reconciliation |
| 2026-07-30 | W4-A adversarial review | All 19 pre-adversarial IDs plus targeted Python/frontend tests | 14 upheld, SEC-01/EXT-01/EXT-02 downgraded, AN-02/EXT-03 rejected; 58 passed, 1 skipped plus 9 default and 26 direct frontend tests | No new independent issue; root corroborated the contract/production guards |
| 2026-07-30 | W4-B cross-layer lifecycle | Analysis deletion -> Coach context -> TeachingSession hydration/contract | COACH-01 P2 confirmed; root in-memory reproduction retained deleted ref/observation/retest; existing stale-retention test passed | No Provider or product DB; integration regression still missing |
| 2026-07-30 | W4-C recovery slicing | Git path inventory, diff metadata, plan/code/test ownership | 117 tracked modifications and 112 untracked files stable; recovery batches and hunk-contaminated files mapped | Path/metadata stability only, not a whole-tree content hash; excluded assessment unread |
| 2026-07-30 | Wave 4 reconciliation | Adversarial verdicts, root contract review, lifecycle reproduction, recovery map | Pre-full-suite register: 3 P1, 10 P2, and 5 P3 confirmed findings; 2 candidates rejected | REL-02 is a release No-Go; no cleanup or implementation performed |
| 2026-07-30 | Root full Python verification | Repository Python 3.11.9 venv with isolated DB/data/KovaaK paths | `1 failed, 1522 passed, 5 skipped`; only `test_coach_runtime_pi_failure_no_fallback` failed (`None` became `""`) | No Provider, product DB, or real KovaaK access; no failure was fixed |
| 2026-07-30 | Root Coach failure isolation | The failing node alone, then complete `webapp/tests/test_routes_coach.py` | Node: `1 failed`; file: `1 failed, 28 passed`; identical assertion proves deterministic regression rather than suite-order pollution | Final register: 3 P1, 10 P2, and 6 P3 (19 total); 3 candidates rejected, including the earlier Python 3.9 runtime mismatch |

## 10. Recovery Slicing

`origin/main..HEAD` is an already committed 23-commit baseline. Do not mix it
into dirty-worktree recovery commits. The dirty tree should be recovered in the
following review/repair batches; this is a recommendation, not authorization to
stage, move, clean, or commit anything.

| Order | Batch | Boundary and disposition | Required verification |
|---|---|---|---|
| 1 | Scenario/evidence/Switching | `knowledge/scenarios/**`, scenario fixtures, `analysis_evidence.py`, `outcome_association.py`, `target_switching_analysis.py`, `visual_signals.py`, `worker.py`, `visual_worker_process.py`, focused tests. Repair AN-01 before trusting Switching. EXT-02 is P3 store hardening; AN-02 requires no work. | Scenario, evidence, outcome, Switching, visual, worker and Profile venv suites; later authorized field replay |
| 2 | Knowledge Registry v3/v4 | Registry/schema/migrations, Python knowledge/advice/diagnosis, Pi knowledge tools and parity tests. EXT-01 is release hardening, not an execution blocker because prepared plans already recheck the active manifest. | Python Registry/advice/diagnosis and Node Registry/parity/analysis-context suites |
| 3 | Coach command and sidecar safety | Coach commands, confirmations, context, runtime/service, prompt/contracts/product tools. Keep TeachingSession and benchmark hunks out. Repair COACH-02 at the nullable-reply/privacy boundary; SEC-01 is a P3 launch-environment hardening item. | Python command/context/runtime/confirmation including the no-fallback route regression; Node product-command/system-prompt/turn suites |
| 4 | TeachingSession/retest lifecycle | `teaching_session_store.py`, relevant DB/agent-run/engine/history/retest hunks, teaching policy and tests. Repair COACH-01 before review because deleted evidence can still advance a lesson. | TeachingSession, agent-run, tool-runtime, confirmation and retest Python tests; Node teaching-policy/turn tests; deletion integration regression |
| 5 | Viscose catalog and scores | Benchmark catalog/provider/service/store plus score-only route/schema/context/tool hunks. Fix PERF-01 and MB-01 together at the latest-available selection/query boundary without deleting history. | Catalog/provider/store/routes/context/summary Python tests; focused Node score-summary tests; temporary-DB account-switch regression |
| 6 | Analysis workspace and Coach locator | Task 5 Data/video/diagnosis, Task 6 locator, frontend API/contracts/types and matching E2E. Repair AN-03, UX-01, and UX-02. | type-check; Task 5/6 direct tests; locator acknowledgement and segment-failure Playwright cases; screenshots/accessibility |
| 7 | Capture and analysis entry continuity | Desktop runtime/finalizer/ingest/native client/queue and Tauri capture/runtime, then Task 3/History entry hunks. Repair CAP-01 before the capture contract, then UX-03 against that contract. | Python capture suite; explicit MSVC Rust suite; History-to-Analyze E2E; authorized Windows field capture last |
| 8 | Toolchain and release | Python/Node/Rust version and dependency policy, aggregate tests/CI, Tauri distribution. TOOL-01, TEST-01, REL-01, and REL-02 must remain separate from feature commits. Release stays No-Go. | clean-machine matrix, full Python/Node/Pi/MSVC/frontend/E2E, installer/sign/update/hash/download/field Gates |
| 9 | Docs and plan indexes | Current upstream docs, plan/spec indexes and untracked plans/specs, excluding assessments. Repair GOV-01/02 only after accepted code state is known. | Link/index governance and fact-source reconciliation; functional tests are not a substitute |
| 10 | Evidence-only material | `.firecrawl/**`, `artifacts/**`, research assessments and this ledger. Preserve outside product commits; 62 research/artifact files are about 1.93 MiB. | Metadata/inventory only |
| 11 | Excluded unknown file | Preserve but do not read, classify, stage, move, or cite `2026-07-30-coach-action-streaming-kovaak-launch-feasibility.md`. | None; user attribution required |

Dependencies:

`Scenario/evidence -> Registry -> Coach commands -> TeachingSession -> Viscose scores -> Connected-account projection`

Task 5/6 waits for corrected analysis/Registry semantics. Task 3/History waits
for the capture contract. Native, toolchain, and evidence-preservation work can
be reviewed independently, but release cannot proceed before all declared
Gates.

The following files contain multiple logical batches and are unsafe to stage
wholesale: `coach_agent_runs.py`, `coach_commands.py`, `coach_runtime.py`,
`db.py`, `routes.py`, `schemas.py`, `worker.py`, `turn.ts`,
`product-command-tools.ts`, `coach-system.md`, frontend `types.ts` and
`contracts.ts`, plus shared test/E2E files. Use reviewed hunk-level boundaries
only after prerequisite batches are accepted.

## 11. Recovery Instructions

After compaction or session restart:

1. Read this ledger first.
2. Re-run `git status --short --branch` and compare the snapshot. Ignore this
   ledger's expected modification; stop on other unexplained changes.
3. Inspect live agent status. Do not duplicate a running or completed track.
4. Resume the first non-completed wave only.
5. Recheck every returned finding in the current root context before marking it
   confirmed.
6. Do not start cleanup or implementation until all four waves and the final
   synthesis are complete and the user authorizes the next phase.
