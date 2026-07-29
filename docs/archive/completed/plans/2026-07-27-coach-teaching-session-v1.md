# Coach TeachingSession v1 - Implementation Plan

> **Status: Task 1-3 已实现；点点于 2026-07-28 授权 Task 4。** TeachingSession、bounded lesson hydration、practice/execution/retest confirmation、not-comparable fallback 与可比复测的 `retain / lower / reject` 已接通。Task 4 只复用现有 Analysis candidate、Knowledge Registry、TeachingSession 与 Training Plan 合同补主动教学接线，不新建平行分析或状态系统。

**Goal:** 让 Coach 在跨 turn、重试、确认和复测之间稳定执行“解释观察 -> 教一个 cue -> 直接进入单变量练习 -> 执行确认 -> 可比复测 -> revise”闭环；只有用户明确提问或误解时才澄清一次。Provider 的违规输出降级为自然、有用且安全的本地教学步骤。

**Architecture:** SQLite 中的独立 owner/thread-scoped `TeachingSession` 是 lesson phase 的唯一真相。每个 Agent run 在启动前生成并持久化不可变 `TeachingTurnContract`；backend planner/reconciler 决定唯一下一动作，Pi runtime 的 renderer/validator 限制用户可见输出。Training Plan、execution、retest 和 confirmation 保持原有 owner-scoped store 及写入路径。

**Tech Stack:** FastAPI local backend / SQLite / pytest；pinned Pi TypeScript runtime / Node test runner；现有 generic confirmation UI。

---

## Task 1 - Add deterministic TeachingSession runtime

### Allowed files

- `docs/ARCHITECTURE.md`
- `docs/README.md`
- `docs/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md`
- `docs/superpowers/specs/README.md`
- `docs/superpowers/plans/2026-07-27-coach-guided-teaching-loop-v1.md`
- `docs/superpowers/plans/2026-07-27-coach-teaching-session-v1.md`
- `docs/superpowers/plans/README.md`
- `webapp/backend/db.py`
- `webapp/backend/teaching_session_store.py` (new)
- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_runtime.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/teaching-policy.ts` (new)
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/tests/test_teaching_session_store.py` (new)
- `webapp/tests/test_coach_agent_runs.py` (new)
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_db.py`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/coach-runtime/test/teaching-policy.test.ts` (new)

No other file is allowed. In particular, do not modify `routes.py`, `schemas.py`, frontend components/contracts, `worker.py`, queue/capture/finalizer/ingest/native code, analyzer implementations, scenario registry/manifest, evidence/profile producers, real Run media/Raw/Stats/Performance, PRD or `PROGRESS.md`.

### Tests first

1. Add store tests for owner/thread isolation, one active session per primary thread, optimistic version conflict, active-run guard, retry replay and stale/deleted evidence reconciliation.
2. Add planner tests proving exactly one phase action/question is selected, ordinary acceptance advances directly to practice, explicit misunderstanding returns to one clarification turn, user refusal pauses rather than fabricates facts, user-reported discomfort stops practice, and unconfirmed execution/retest never advances the session.
3. Add reconciliation tests for confirmed execution/retest, rejected/expired confirmation, matched immediate, delayed matched, near-transfer and not-comparable retest. Not-comparable must remain `unresolved` and never produce `retain/lower/reject`.
4. Add TypeScript policy tests that a `TeachingTurnContract` permits a source-labelled ratio-to-percent display but rejects unsupported semantic frequency, qualitative evaluation or causal expansion; reject a second/compound question, unapproved dose, internal vocabulary and completion claim. Verify deterministic fallback is stage-specific and user-readable.
5. Extend fake-stream tests so Provider output cannot select phase, call an out-of-phase write command or claim execution/retest completion; contract-valid tool events move only to the matching confirmation wait state.
6. Preserve existing Coach command, grounding, deletion, evidence reachability, owner scope, secret sentinel and stop/turn correlation tests.

### Implementation steps

1. Define the bounded Python/TypeScript `TeachingTurnContract`: session ref/version, phase, allowed observation/candidate/alternatives/cue, one changed variable, one `question_kind`, allowed command name, confirmation/refetch intent and retest comparability requirements. Do not pass raw evidence, paths, credential or arbitrary Provider text.
2. Add an idempotent SQLite migration and `teaching_session_store.py`. Persist only owner/thread-scoped lesson state, version, active run, pending confirmation, safe lesson JSON and pause/stop reason. Store the per-run contract snapshot in `coach_agent_runs`; use transaction/CAS guards so concurrent runs cannot both advance a lesson.
3. Before an agent run, reconcile existing confirmation/training facts and prepare one contract. After a valid response/tool event, atomically advance only the permitted state; retries replay the stored contract. Do not add routes, schemas or frontend state.
4. Pass the contract through `coach_service.py` / `coach_runtime.py` to `contracts.ts`. Implement `teaching-policy.ts` and integrate it in `turn.ts`: Provider may naturalize approved text only; planner/renderer owns the question, confirmation semantics, non-advancing states and safe fallback, without forcing one user-visible script.
5. Keep `coach-system.md` concise: it explains that the contract is authoritative and Provider must not invent actions, questions, completion, dose or causes. It is not the mechanism that determines phase transitions.
6. Run focused tests, then the full Coach Node suite and isolated backend Coach suite. Run the four-case real Provider matrix against isolated, read-only owner-scoped fixture data: analysis-backed lesson, missing evidence with a mouse question, user rejection, and not-comparable retest.
7. Record point-in-time test/matrix evidence in this plan only. Do not alter PRD, Architecture again unless the implementation reveals a contract conflict; do not touch the active data analysis session.

### Verification commands

```powershell
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
node "--import=$loaderUrl" --test webapp\coach-runtime\test\teaching-policy.test.ts webapp\coach-runtime\test\turn-fake-stream.test.ts webapp\coach-runtime\test\system-prompt-and-tools.test.ts
```

```powershell
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("aiming_cookie_teaching_session_" + [guid]::NewGuid().ToString("N"))
$env:DATABASE_URL = "sqlite+aiosqlite:///" + (Join-Path $testRoot "test.db").Replace("\", "/")
$env:DATA_ROOT = $testRoot
$env:KOVAAK_INSTALL_DIR = Join-Path $testRoot "missing-kovaak"
.\.venv\Scripts\python.exe -m pytest -q webapp\tests\test_teaching_session_store.py webapp\tests\test_coach_agent_runs.py webapp\tests\test_coach_runtime.py
```

### Frozen decisions

- `TeachingSession` is the teaching-process truth; messages are context, and Training Plan/execution/retest are formal facts.
- There is one active primary-thread lesson per owner. Version/active-run guards fail closed on concurrency; retry reuses the stored turn contract.
- The planner selects one action and at most one question before Provider invocation. Default teaching advances directly to practice; clarification is conditional and never becomes a repeat-back gate. Renderer/validator own user-visible questions, safe fallback and whether the lesson may advance; Provider may vary natural wording but cannot advance phase.
- A mathematically equivalent ratio display is allowed when it preserves source semantics. No validator rejects it solely for using percent; unsupported frequency, qualitative judgement or causal interpretation remains prohibited.
- Retest comparability is a local contract over scenario, settings, metric/version and required key conditions. Missing comparability yields `unresolved`, never a revision decision.
- Existing generic confirmation UI and command paths remain unchanged. No new API, schema, route, frontend page/card/form, Provider protocol, raw-evidence access or peripheral catalogue is introduced.

### Stop rule

- A required state transition cannot be derived from existing trusted confirmation or confirmed fact stores without changing routes, schemas or frontend contracts.
- The data model requires a second canonical Training Plan/profile store, raw evidence persistence, or a product-scope change.
- Existing owner scope, idempotency, secret redaction, tool reachability, deletion, stop/turn correlation or data-analysis tests regress.
- Any required edit falls outside Allowed files, including data analysis pipeline files or `PROGRESS.md`.

On Stop, preserve the latest valid session state, record the exact violated contract in this plan, and request a separate design decision. Do not silently revert to prompt-derived teaching state.

## 2026-07-27 Task 1 closeout

### Implemented

- SQLite v20 persists one owner/primary-thread `TeachingSession`, immutable per-run contracts, active-run CAS, retry replay, pending confirmation and bounded lesson state. A stale CAS cannot persist an assistant reply.
- The local planner hydrates only canonical Coach context: selected issue `plain_language_meaning`, explicitly labelled candidate root causes, one prescription cue and deterministic ratio values with a user-facing definition. Missing or detached evidence fails closed; profile labels, metric keys, expected results and unsafe/raw fields do not become lesson claims.
- Intake asks one question. A user-initiated mouse-change question receives “现在没必要换鼠标” plus one discriminator; device advice is not inserted into unrelated turns.
- Coach 讲清 cue 后直接进入练习，不要求用户复述。“好 / 明白了 / 开始吧”继续练；只有用户明确提问或误解时才澄清一次。旧 `await_teach_back / teach_back_repair` 状态仅为已存 contract 的重放兼容，并在下一轮迁移到新流程。明确拒绝会暂停；只有用户主动报告不适时才停止当前练习；确认跳过的执行不会推进到复测。
- Confirmed `training_plan.item.add` binds the exact owner-scoped `active_item_ref`; execution/retest writes must target that item. Confirmed execution advances to a bounded retest intent, and not-comparable retest remains unresolved without a revision decision.
- The TypeScript planner/validator owns the action, exact question, required observation/candidate/alternatives/cue, active item, confirmation semantics and retest boundary. Provider omissions, extra causes/questions/dose/completion, wrong item writes and unsupported ratio semantics use the local stage fallback; user-visible protocol phrasing and unsolicited symptom checklists are rejected. Pause, user-reported discomfort and not-comparable retest accept varied natural Provider wording but emit an internal hold note, so the run releases without clearing the pause reason or advancing the lesson. The fallback wording is used only when Provider output fails validation.

### Point-in-time verification

- Active-plan Python focus including DB migration: `99 passed`.
- Wider isolated Coach/backend regression, excluding `test_coach_context.py` because the concurrent Dynamic session may modify it: `301 passed`.
- Complete Coach Node suite: `139 passed, 0 failed`.
- Deterministic/fake-stream coverage includes Analysis-backed hydration, missing evidence plus a user-initiated mouse question, direct practice after teaching, one conditional clarification, legacy teach-back migration, user refusal/reported discomfort, stale CAS, exact item binding, three retest intents, varied natural wording with a non-advancing hold, safe fallback and not-comparable revise.
- `git diff --check`: passed; only the repository's existing LF/CRLF conversion warnings were emitted. No commit or push was made.

### Not run

- The real Provider four-case matrix was not run. The repository has no committed matrix harness, the current shell has no isolated Provider profile, and using the existing credential/database state would cross the concurrent-session boundary. Automated deterministic/fake-stream coverage is not reported as a real Provider result.
- `test_coach_context.py` and the Dynamic/worker/scenario/visual-signal suites were not run or modified in this Task because they are owned by the concurrent data-analysis session.

### Exact Stop-rule blocker

`training_plan_retests.result` is currently unrestricted text. No current trusted fact or frozen mapping defines how a comparable result becomes `retain`, `lower` or `reject`. TeachingSession can therefore preserve `not_comparable -> unresolved`, but it cannot deterministically produce the required comparable-retest revision without inventing a result vocabulary or product default.

The required follow-up decision is one of:

1. add a versioned bounded retest outcome/decision field to the existing Training Plan fact contract; or
2. define a versioned deterministic mapping from an existing canonical Analysis comparison outcome to `retain / lower / reject`.

Until that decision is frozen, a comparable retest with no canonical decision remains `revision_decision=null`; Provider text cannot supply the missing decision, and Task 1 remains blocked rather than claiming the teaching loop is complete.

## Task 2 - Connect confirmed retest outcome to lesson revision

> **Status: active and authorized by 点点 on 2026-07-28.** 本 Task 只关闭 Task 1 的 comparable-retest 断点；不新增事实表、比较器、计划命令或 Profile store。

### Allowed files

- `docs/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md`
- `docs/superpowers/plans/2026-07-27-coach-teaching-session-v1.md`
- `webapp/backend/coach_agent_runs.py`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

No other file is allowed. In particular, do not modify `db.py`, `schemas.py`, `routes.py`, frontend, Training Plan stores, Analysis compare implementation, analyzers, scenario files, `worker.py`, `PROGRESS.md`, real Run/media or the concurrent data-analysis session's files.

### Tests first

1. Add backend tests proving confirmation idempotency resolves the exact execution/retest fact rather than the latest row for an item; missing, failed, foreign-owner or wrong-item facts fail closed.
2. Add backend tests for the versioned result mapping: improved -> retain, unchanged/mixed -> lower, worsened -> reject; unavailable, not-comparable and legacy text remain without a decision. A user request to retry an unresolved retest returns the lesson to `retest_ready`.
3. Add TypeScript tests that TeachingSession retest writes require the matching item, retest kind, comparability/result pairing and one of the four versioned values.
4. Add policy/fake-stream tests for all three decisions, negated or conflicting decision language, comparable-null hold behavior and distinct immediate/delayed/near-transfer scope.

### Implementation steps

1. Reuse `coach_command_idempotency.result_json` to resolve the exact succeeded fact ref for confirmed item, execution and retest writes. Query the fact by that ref plus owner and active item.
2. Keep historical `training_plan_retests.result` untouched. For new TeachingSession retest writes, allow only `coach_retest_outcome.v1:*`; map it deterministically in backend reconciliation. Unknown text never receives a retrospective meaning.
3. Keep `unavailable` as unresolved and `not_comparable` as not comparable. Both remain in revise without a decision until the user asks to retry, then return to `retest_ready`.
4. Harden the TypeScript tool boundary and decision-language validator. Renderer fallback may vary by retest intent, but Provider remains free to use natural wording within the contract.
5. Run focused Python/Node tests, then the complete Coach Node suite and isolated backend Coach suite permitted by the concurrent-session boundary.

### Frozen decisions

- Versioned result tokens are internal persisted facts; user-facing confirmation and Coach narration use natural language.
- A confirmed user fact may supply the bounded outcome. A future deterministic Analysis comparison may produce the same token, but this Task does not invent a meaningful-change threshold or alter `history_trends.compare_analysis_results()`.
- `retain / lower / reject` revise the current teaching hypothesis. `training_plan.adjust` is not used because it only creates a plan version and does not modify the active item.
- Old free-text result rows remain readable and unresolved by this mapper.

### Stop rule

- Completing the flow requires changing a Training Plan item payload/status, adding a new plan command, schema, route, DB column or frontend contract.
- A confirmation cannot be tied to one exact succeeded owner/item-scoped fact.
- Any data-analysis, worker, scenario, `PROGRESS.md`, real Run or database boundary must be crossed.
- Existing confirmation, owner scope, idempotency, stop/turn correlation or Coach runtime tests regress.

## 2026-07-28 Task 2 closeout

### Implemented

- Confirmation reconciliation now resolves the exact succeeded `plan-item:`, `plan-execution:` or `retest:` fact through the confirmation's idempotency result, then verifies owner and active item. A later row for the same item cannot replace the fact the user confirmed.
- New TeachingSession retests accept only the internal versioned `coach_retest_outcome.v1:*` vocabulary at the TypeScript tool boundary. Historical free text remains readable; backend reconciliation does not reinterpret it.
- Comparable outcomes map deterministically to `retain / lower / reject`; `unavailable`, not-comparable and unknown legacy result text keep the lesson without a decision. Brief affirmative consent can retry the retest; negated retry language pauses instead of advancing.
- Renderer and validator preserve immediate, delayed and near-transfer scope. Negated/conflicting decision language, unapproved retest dose, wrong retest kind and incompatible comparability/result pairs fail closed before a write or use the bounded local fallback.

### Verification

- Tests-first red state: backend `8 failed, 15 passed`; focused Node `5 failed, 73 passed`.
- Final isolated TeachingSession/backend set: `67 passed`.
- Final complete Coach Node suite: `146 passed, 0 failed`.
- `git diff --check`: passed before closeout; only existing LF/CRLF conversion warnings were emitted.

### Not run and remaining boundary

- A wider Python batch including unrelated Task6 suites exceeded the 180-second command timeout without a reported assertion failure; it is not counted as passed. The focused affected backend set passed separately.
- No real Provider matrix was run because that would use credential/database state outside the isolated concurrent-session boundary.
- The current generic confirmation dialog still does not explain the prepared execution/retest facts in user-facing language. Fixing that requires a separately authorized Task covering confirmation projection/UI tests.
- This Task does not derive a meaningful-change threshold from Analysis deltas and does not mutate Training Plan items. `training_plan.adjust` remains unsuitable for that job because it only creates a plan version.

## Task 3 - Make confirmations, Analysis retests and plan revisions concrete

> **Status: active and authorized by 点点 on 2026-07-28.** This Task closes the three remaining guided-loop gaps without changing the concurrent Analysis pipeline or inventing uncalibrated metric thresholds.

### Allowed files

- `docs/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md`
- `docs/superpowers/plans/2026-07-27-coach-teaching-session-v1.md`
- `webapp/backend/coach_confirmations.py`
- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/training_plan_store.py`
- `webapp/backend/coach_retest_decision.py` (new)
- `webapp/tests/test_coach_confirmations.py` (new)
- `webapp/tests/test_coach_retest_decision.py` (new)
- `webapp/tests/test_coach_retest_command.py` (new)
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_training_plan_store.py`

No other file is allowed. In particular, do not modify `history_trends.py`, `test_history_trends.py`, `coach_context.py`, `test_coach_context.py`, frontend files/contracts, `db.py`, `schemas.py`, `routes.py`, analyzers, scenario registry/manifest/rules, `worker.py`, `PROGRESS.md`, real Run/media or the concurrent data-analysis session's files.

### Tests first

1. Add confirmation projection tests for item, execution and retest commands. Assert the message names the prepared facts and plan effect in natural Chinese, does not expose internal outcome tokens/refs, and falls back safely for malformed, unsupported or oversized parameters.
2. Add deterministic retest-decision tests proving existing Analysis comparability is authoritative; equal values become `unchanged`, a non-zero delta without a registered metric-change policy becomes `mixed_or_inconclusive`, and incompatible/missing/foreign-owner Analysis facts do not become a result.
3. Add command tests proving a Coach-inferred retest with exactly `[baseline, current]` Analysis refs is normalized before idempotency and confirmation. Confirmed execution must replay the exact normalized parameters; single-Analysis user facts retain the existing confirmed-outcome path.
4. Add reconciliation tests proving confirmed completed/partial execution activates the exact item while skipped/discomfort does not; `retain` keeps it active, `lower` returns it to planned, and `reject` cancels it. Missing metric-change policy, unresolved/not-comparable/failed/foreign facts do not change item status. Reconciliation retries are idempotent.
5. Preserve owner scope, safe projection, confirmation audit, idempotency, active-run/CAS, stop/turn correlation and complete Coach Node behavior.
6. Preserve existing non-teaching product commands. Analysis, History, navigation and query events must not be rejected as out-of-phase teaching writes and must not advance the lesson phase.

### Implementation steps

1. Build a bounded confirmation-message projector over the canonical `command_name + parameters_json` already stored in `coach_command_confirmations`. Persist only the existing impact code/message fields; do not add schema, route or frontend state.
2. Add a small deterministic retest decision adapter that consumes the existing `history_trends.compare_analysis_results()` result. It separates comparability from meaningful change, emits the existing versioned retest outcome vocabulary, and fails closed when a metric/version/condition-specific change policy is unavailable.
3. For Coach-inferred retest writes with exactly two ordered Analysis refs, normalize comparability/result/limitations before the idempotency digest and confirmation are created. Confirmation replay uses those exact saved parameters. Do not reinterpret historical retest rows.
4. Reuse `set_plan_item_status()` after exact confirmed facts reconcile: completed/partial execution -> `active`; skipped/discomfort -> unchanged; retain -> `active`; lower -> `planned`; reject -> `cancelled`. A missing metric-change policy leaves the lesson unresolved and the item unchanged. The status reason is a versioned internal teaching decision. A later grounded successor continues through the existing item-add confirmation rather than being fabricated here.
5. Run focused red/green tests, the isolated affected backend Coach suite, the complete Coach Node suite, `git diff --check`, and a final dirty-worktree boundary audit.

### Frozen decisions

- The existing Analysis comparator remains the sole comparability authority. This Task wraps it; it does not duplicate or edit it.
- `delta != 0` and `percent_change` are not meaningful-change thresholds. Until a versioned policy backed by repeated-measure calibration exists for the exact metric/version/conditions, the deterministic outcome is inconclusive.
- For two Analysis refs the order is `[baseline, current]`. The backend owns the normalized result; Provider text cannot override it. A single Analysis ref remains a user-confirmed fact path.
- Existing item status is enough for the minimum real plan effect: active, returned to planned, or cancelled. Skipped/discomfort and uncalibrated Analysis differences do not change it. `training_plan.adjust` is not used, and no priority field or second plan store is introduced.
- The retest confirmation explicitly describes the resulting plan effect, so one confirmation authorizes recording the retest and applying its deterministic item-status consequence. Rejected, expired or unresolved confirmations do neither.
- Confirmation content is deterministic but wording is not a fixed visible script. No unsolicited peripheral advice is added.

### Stop rule

- A safe confirmation message would require exposing raw evidence, paths, secrets or unbounded Provider text.
- Analysis comparison requires a universal percentage, an invented target band or edits to the concurrent Analysis/scenario/worker files.
- A successor item cannot be grounded in an existing complete prescription; leave the next item uncreated rather than inventing it.
- Plan status reconciliation cannot remain owner-scoped, exact-fact-bound and idempotent without a schema/route/frontend change.
- Existing confirmation, idempotency, owner scope, TeachingSession CAS, stop/turn correlation or data-analysis tests regress.

## 2026-07-28 Task 3 closeout

### Implemented

- Pending item, execution and retest confirmations now derive a bounded natural-Chinese fact summary from the canonical stored `command_name + parameters_json`. The existing dialog shows the exact practice condition/cue/guardrail, completed dose/status/feedback, or retest scope/result/plan effect without exposing internal refs, versioned outcome tokens, paths, URLs or secrets. Malformed, unsafe, oversized and unsupported payloads retain the generic safe fallback.
- A new Coach-side retest adapter reuses `history_trends.compare_analysis_results()` as the sole comparability authority for ordered `[baseline, current]` Analysis refs. Not comparable stays unresolved; equal values become `unchanged`; a non-zero delta without a calibrated metric-change policy becomes `mixed_or_inconclusive + metric_change_policy_missing` and does not change the plan. Missing, unfinished or foreign-owner Analysis facts fail before confirmation.
- Two-Analysis retest parameters are normalized before idempotency and confirmation. Confirmation execution replays the exact saved normalized parameters and does not re-read mutable Analysis state after the user has seen the dialog. Single-Analysis user-confirmed facts retain the existing path.
- Exact confirmed completed/partial execution activates the item; skipped or user-reported discomfort leaves it unactivated. `retain / lower / reject` apply `active / planned / cancelled` through the existing owner-scoped item status store with versioned reasons. A missing metric-change policy, not-comparable/unavailable result, failed idempotency fact or foreign item changes no status; retry reconciliation is idempotent.
- TeachingSession now filters only the three teaching fact writes when enforcing phase. Existing Analysis, History, navigation, query and other product commands can run without advancing the lesson, closing a regression exposed by the confirmation API end-to-end test.

### Verification

- Task 3 combined focused backend set: `62 passed` before the final non-teaching command regression repair; the final wider isolated Coach/backend set containing all focused files: `220 passed`.
- Confirmation API subset, including canonical once-only execution: `5 passed`; Coach routes: `29 passed`.
- Complete Coach Node suite: `146 passed, 0 failed`.
- Python compileall, new-file trailing-whitespace checks and whole-worktree `git diff --check`: passed. The latter emitted only existing LF/CRLF conversion warnings.

### Not run and remaining evidence gate

- No real Provider matrix or real user database/Run/media test was run; those would cross the isolated credential/data boundary and the concurrent Analysis session.
- Automatic meaningful non-zero improvement/worsening remains deliberately disabled until repeated-measure calibration supplies a versioned policy for the exact metric/version/conditions. Each future policy needs measurement noise (for example SEM/MDC or typical error), a worthwhile-change/target-band rule, and speed-accuracy or outcome guardrails where relevant. The current fail-closed result is a working product behavior, not a fabricated universal threshold.
- No frontend change was needed because the existing dialog already renders backend `impact.message`. No commit or push was made.

## Task 4 - Connect grounded candidates to active teaching

> **Status: completed at the safe existing-contract boundary on 2026-07-28.** This Task replaces the fixed intake/default-first-cause behavior with the smallest grounded connection across the existing Analysis, Knowledge Registry and TeachingSession surfaces. Switching remained excluded while the concurrent session was still exposing that family. Automatic Training Plan item creation remains closed because the current TeachingTurn does not carry the complete grounded item contract.

### Allowed files

- `docs/superpowers/plans/README.md`
- `docs/superpowers/plans/2026-07-27-coach-teaching-session-v1.md`
- `webapp/backend/coach_context.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

No other file is allowed. In particular, do not modify Knowledge Registry assets or schemas, `teaching_session_store.py`, `training_plan_store.py`, `coach_commands.py`, `coach_runtime.py`, DB/routes/frontend contracts, analyzers, scenario registry/manifest/rules, `worker.py`, Switching files, `PROGRESS.md`, real Run/media or the concurrent data-analysis session's files.

### Tests first

1. Prove the context projection reuses an exact existing v2 Knowledge Registry match for an analyzer-produced issue and projects its cue, dose guardrail, verification and candidate explanation through the existing issue/prescription shape. Missing, ambiguous, retired or unsafe knowledge remains absent.
2. Prove Static, Dynamic and Tracking lessons ask one issue-specific discriminator instead of the fixed deceleration question. A clear answer naming one current candidate changes the persisted primary candidate; unmatched or ambiguous prose does not.
3. Prove confirmed execution `user_feedback` can promote only an explicitly named current alternative. It must not infer a new bodily, perceptual or hardware cause, change a cue without a grounded prescription, or treat immediate ease as learned skill.
4. Prove Registry-backed dose guardrails populate the existing `approved_dose` contract field. A teaching `training_plan.item.add` write must use the exact approved cue and dose guardrail; otherwise it is rejected before the product bridge.
5. Preserve historical v1 TeachingSession/run-contract replay, owner scope, CAS, confirmation/idempotency, stop/turn correlation, non-teaching product commands and complete Coach runtime behavior.

### Implementation steps

1. At Coach context projection time, call the existing canonical Python Knowledge Registry query/resolve helpers after an analyzer issue exists. Require one exact active signal match; adapt only the already-defined v2 definition/cue/dose/retest/stop sections into the existing bounded issue/prescription projection. Do not modify Analysis results or create a second knowledge loader.
2. Extend the local lesson adapter to consume the complete projected prescription fields it already receives, including the Registry-backed dose guardrail. Build the discriminator from the current observation/candidates; do not hard-code a Static/Dynamic/Tracking question table.
3. Reorder only current persisted candidates when the user explicitly identifies one by label or unambiguous position. Reuse the same bounded reducer for confirmed execution feedback. Leave candidate, cue and phase unchanged when the answer does not uniquely match.
4. Reuse `approved_dose`, `cue` and the existing turn-local tool restriction to reject mismatched item payloads before the product command bridge. Missing complete prescription data keeps the lesson in a non-writing state rather than letting Provider invent it.
5. Run focused red/green Python and Node tests, the affected Coach regression sets, isolated field-context fixtures, `git diff --check`, then an opt-in real Provider matrix against a credential-free isolated copy of public Analysis/context facts.

### Frozen decisions

- Knowledge Registry v2 remains the only cross-family mechanism/cue/dose/retest source. Task 4 does not add `teaching_variants`, a second registry, a family switch table or a new evidence query engine.
- Analysis facts select whether a knowledge entry is reachable; Registry knowledge does not manufacture an analyzer finding. A single-run descriptive metric without a calibrated baseline cannot be upgraded into a problem or cause.
- Candidate reordering is evidence bookkeeping, not causal confirmation. A user answer may identify their subjective experience; matched and delayed retests still decide whether the experiment is retained, lowered or rejected.
- The discriminator is one natural question and may present two current choices. No teach-back is required unless the user explicitly asks or shows a clear misunderstanding.
- External-device advice appears only after the user raises it or a grounded experiment requires it. No product recommendation catalogue is added.
- Task 4 may bind only fields already present in the current issue/prescription, TeachingTurn and Training Plan item contracts. It does not invent missing diagnosis, metric, scenario or retest references.

### Stop rule

- A required candidate-to-prescription link cannot be obtained from the existing analyzer issue plus canonical Knowledge Registry without positional guessing or text-keyword joins.
- A complete Training Plan item would require fabricated refs, a new store/schema/route, or an unapproved exact dose.
- Safe implementation requires edits to the concurrent Analysis, Switching, scenario, worker, real Run/media or `PROGRESS.md` files.
- Provider text or tool output would become authoritative for TeachingSession state instead of the local planner and confirmed facts.
- Existing owner scope, safe projection, confirmation/idempotency, TeachingSession CAS, stop/turn correlation or data-analysis tests regress.

## 2026-07-28 Task 4 closeout

### Implemented

- Coach context now calls the existing canonical Knowledge Registry v2 query after an analyzer issue exists. One exact active signal/metric match atomically replaces the issue prescription with the Registry cue, dose guardrail, matched retest and stop rule; existing Analysis candidates remain attached to the issue, and unmatched or ambiguous knowledge remains absent. No second registry, family switch table, query engine, store or schema was added.
- Static, Dynamic and Tracking fixtures prove the same Registry path. TeachingSession uses the current observation and candidates to ask one issue-specific discriminator, normalizes the selected direction into primary/alternative wording, and rejects ambiguous or negated feedback such as “不是张力介入” instead of promoting it.
- `dosage` now survives the existing Python and TypeScript bounded projections as `approved_dose`. Practice text must preserve the approved cue and dose.
- The complete Training Plan item requires diagnosis, knowledge, scenario, metric, matched/near-transfer retest and review refs that are not present in TeachingTurn v1. Consequently every Coach `training_plan.item.add` attempt is rejected before the product bridge, including a payload with correct cue/dose but fabricated well-formed refs. Existing execution and retest writes for an already grounded active item remain unchanged.
- When an Analysis has no grounded issue, intake now says that the Analysis has not identified a clear problem and asks which mistake or movement segment the learner wants to address, instead of presenting a generic category picker.

### Verification

- Tests-first red state: Python `2 failed, 72 passed`; focused Node `94 passed, 1 failed`. The failures were stale normalized-label and fallback-object expectations.
- Final focused Task 4 set: Python `78 passed`; Node `95 passed, 0 failed`.
- Final isolated Coach/backend set, including TeachingSession and Training Plan stores: Python `269 passed`.
- Complete Coach Node suite: `149 passed, 0 failed`.
- Python compileall and scoped `git diff --check`: passed; only existing LF/CRLF conversion warnings were emitted.
- Read-only field projection used `analysis:11` Static, `analysis:16/17` Dynamic and `analysis:3` Tracking. Static Registry-matched issues carried an approved dose; current Dynamic and Tracking results contained no diagnosis issue, so they correctly stayed in intake instead of manufacturing a problem.
- The saved DeepSeek profile was loaded from the field database through SQLite read-only mode and used only in memory. The real async product runtime completed Static, Dynamic and Tracking calls with no tool events and no database writes. Static policy fell back to the exact approved Registry practice; Dynamic/Tracking no-issue turns fell back to the planner-owned intake question.

### Remaining product gates

- Registry v2 prose is English. The real Static turn therefore produced a safe but mixed-language fallback. This needs a localized extension of the existing Registry and its Python/TypeScript validators; a hard-coded translation table or unconstrained Provider translation would create an untraceable second knowledge source.
- Restoring automatic `training_plan.item.add` requires a separately authorized contract revision that carries a locally approved complete item payload and checks every field before the bridge. The current store can already persist the item; no second plan store is needed.
- The initial field attempt hit the already-running sidecar's HTTP 502. The independent async subprocess path, which decodes UTF-8 and matches the product's async Coach entry, completed successfully. The legacy synchronous Windows subprocess path still has a GBK stdout decoding defect outside Task 4 scope.
- No Switching files, scenario assets, analyzers, worker, `PROGRESS.md`, real Run/media or real database rows were modified. No commit or push was made.

## Task 5 - Compile grounded scenario prescriptions into existing Training Plan items

> **Status: completed on 2026-07-28.** This Task follows the deep research in `docs/superpowers/assessments/2026-07-28-coach-scenario-prescription-research.md`. It restores `training_plan.item.add` only for a complete locally prepared item; it does not broaden analyzer or scenario activation.

### Allowed files

- `docs/README.md`
- `docs/superpowers/assessments/2026-07-28-coach-scenario-prescription-research.md`
- `docs/superpowers/plans/README.md`
- `docs/superpowers/plans/2026-07-27-coach-teaching-session-v1.md`
- `knowledge/coach/schema.v2.json`
- `knowledge/coach/registry.v2.json`
- `knowledge/coach/registry.v3.json`
- `kovaak_tracker/coach/knowledge_registry.py`
- `tests/coach/test_agent.py`
- `tests/coach/test_diagnosis.py`
- `tests/coach/test_knowledge_registry.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_context.py`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/teaching_session_store.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_tool_runtime.py`
- `webapp/tests/test_teaching_session_store.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

No other file is allowed. In particular, do not modify Scenario Registry/Manifest, analyzers, `worker.py`, Switching files, Training Plan/confirmation/command stores, DB/routes/schema, frontend, `PROGRESS.md`, real Run/media or the field database.

### Tests first

1. Prove Knowledge Registry v2 can carry either one bounded `scenario_prescription` or `not_applicable`; Python/TypeScript/schema validation rejects unknown refs, invalid shapes and prescriptions without complete cue/dose/matched/near-transfer support.
2. Prove Static, Dynamic and Predictable Tracking exact issue + deterministic metric + active prescribed scenario + owner active plan compile all existing Training Plan item fields. Reactive/control Tracking, unsupported profiles, missing/ambiguous metrics, no active plan and missing Registry sections compile nothing.
3. Prove a `practice_ready` turn without a prepared item has no write command and does not advance or call the bridge. It may still explain the grounded practice naturally.
4. Prove Node allows `training_plan.item.add` only when `plan_ref` and every item field exactly equal the immutable prepared command. Any Provider mutation is rejected before the bridge.
5. Prove the exact prepared command reuses the existing needs-confirmation, idempotency, item persistence and TeachingSession `active_item_ref` reconciliation path.

### Implementation steps

1. Preserve the historical Registry v2 asset and publish Registry v3 with a source-bounded `scenario_prescription` field. Only the current reviewed Static, Dynamic and Predictable Tracking entries receive bindings; all others are `not_applicable`.
2. Reuse the existing exact Knowledge resolution, Scenario Registry and Launch Manifest loaders. Compile an item only when the Analysis scenario equals the prescribed active profile and exactly one issue metric has a deterministic projected value.
3. Read the current owner-scoped active plan at turn creation. Derive diagnosis/retest refs deterministically from the attached Analysis issue and Knowledge entry; keep all prose and expected direction from the same entry.
4. Add `prepared_plan_ref` and `prepared_item` to the immutable TeachingTurn contract. When either is absent, `practice_ready` exposes no item-write command and remains in place.
5. Replace the unconditional Node item-write block with exact structured equality against the prepared command. Provider remains a renderer and cannot choose or rewrite any item field.
6. Run focused red/green tests, Python/TypeScript Registry parity, affected Coach suites, complete Node Coach runtime, isolated field projections and `git diff --check`.

### Frozen decisions

- Existing Knowledge Registry is the sole issue/cue/dose/retest source; existing Scenario Registry is the sole exact scenario identity/analyzer source; existing Training Plan store is the sole plan/item store.
- Voltaic S5, Viscose and other external scenarios remain research candidates until exact local hash/version/analyzer review. This Task does not activate them.
- The current exact Analysis scenario is the matched practice scenario. A different external scenario is not substituted by name.
- No universal minutes, runs, accuracy target, rank threshold or review-day constant is invented. `review_date` stores the bounded Registry review condition.
- No Analysis issue means no item. One descriptive uncalibrated metric does not create an issue or causal diagnosis.
- No active plan means no automatic draft/activation and no item write. Coach may explain the exercise without claiming it was saved.
- Reactive/control Tracking and Switching remain closed unless a matching exact active profile and prescription binding exist.
- No unsolicited device recommendation is added.

### Stop rule

- The current dirty Scenario Registry/Manifest is not active or changes underneath the Task's exact references.
- A complete item requires a Provider-authored field, a hard-coded family router, a second registry/store, or an unverified external scenario identity.
- Safe work requires edits outside Allowed files or writes to real user/Run/media/Provider data.
- A metric cannot be tied to one deterministic projected Analysis fact.
- Existing owner scope, confirmation/idempotency, TeachingSession CAS, runtime safety, Registry parity or concurrent data-analysis tests regress.

### Task 5 closeout

- Published Knowledge Registry v3 while preserving historical v2. Four knowledge entries bind to three reviewed exact scenario profiles: Static terminal control, Dynamic acquisition/reading and Predictable Tracking speed matching. Every other entry is explicitly `not_applicable` for scenario prescription.
- A reviewed Analysis issue now compiles the existing 11-field Training Plan item only when its deterministic metric, exact active scenario and owner active plan all match. `analysis:11` resolves the reviewed `reverse_ratio` issue instead of the higher-ranked unsupported `decel_frac` observation.
- `TeachingTurn` carries the locally prepared plan/item. Node exposes `training_plan.item.add` only for that exact structure and rejects any Provider field change before the bridge. Missing plan, issue or approved scenario leaves the write unavailable.
- Python TeachingTurn safety now matches forbidden underscore-delimited field segments. It permits `scenario_profile_ref` without weakening rejection of `file_path`, `raw_trace`, `item_payload` or `api_key`.
- Read-only field smoke loaded real `analysis:11` and the saved DeepSeek `deepseek-v4-flash` profile. The real field database has no active Training Plan, so the product correctly compiled no item in its current state. A separate in-memory plan ref exercised the full prepared turn through the real async Provider runtime: it succeeded with all 11 fields, no tool bridge and no tool events. No real database row was written.
- Verification: focused safety regression `8 passed`; affected Python Coach set `235 passed`; expanded Python Coach/Provider/TeachingSession/Training Plan set `534 passed`; complete Node Coach runtime `154 passed`. Python compile, Registry parity and scoped diff checks are recorded after the final closeout edit.
- No Scenario Registry/Manifest, analyzer, `worker.py`, Switching file, frontend, `PROGRESS.md`, real Run/media or field database row was modified. No commit or push was made.

## Task 6 - Close deterministic fallback and no-lesson loops

> **Status: completed and field-verified on database copies on 2026-07-29.** This Task closes the two real Provider usability loops found by the integrated family Gate. It reuses the existing TeachingSession contract, local fallback, validator and agent-run lifecycle; it does not add another conversation state system.

### Allowed files

- `webapp/backend/coach_agent_runs.py`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/teaching-policy.ts` only if the existing fallback result cannot express the required distinction
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- this plan and `docs/superpowers/plans/README.md`

No route, schema, migration, store, Registry, Training Plan, analyzer, worker, frontend, PRD, Architecture, real Provider credential or field database file is allowed.

### Tests first

1. A grounded, non-writing Static intake whose Provider draft is rejected may use the exact planner-owned deterministic fallback and advance exactly once. The same question must not repeat on the next turn.
2. `teaching_hold`, any tool/confirmation/write phase, a fallback with tool events, missing grounded observation/candidate, retry replay and stale CAS never advance through this exception.
3. An Analysis with no grounded issue returns an honest, finite no-lesson result. It must not ask for free text that the planner cannot consume, manufacture a candidate or prescription, or claim the Analysis is normal.
4. Existing stop, retry, owner, active-run, confirmation, prepared-item equality and deletion behavior remains unchanged.

### Minimal implementation

1. Keep Provider output non-authoritative. Distinguish the already validated local deterministic fallback from a non-advancing hold; only the former may drive the existing `_state_after_success` transition when the immutable contract is grounded, non-writing and has no tool event.
2. Keep `teaching_hold` and every side-effect phase non-advancing. Do not relax the Provider validator or turn prompt wording into state.
3. Replace the no-grounded-issue discriminator with a bounded no-lesson response that offers only actions already supported by the product, such as explaining the available result or attaching another Analysis. Do not synthesize a candidate from arbitrary user prose.
4. Run focused Python and Node tests, the complete Coach Node suite, the isolated Coach backend set, then repeat the four-family Provider matrix on database copies.

### Stop rule

- The fix requires a new phase/schema/store/route/tool, Provider-authored state or an ungrounded candidate.
- A fallback could advance a confirmation/write/hold state or replay twice.
- Existing CAS, retry, stop, owner, confirmation, prepared-item or privacy behavior regresses.
- Safe implementation requires touching Analysis, worker, Registry, Training Plan store, frontend or real user data.

### Task 6 closeout

- `teaching_fallback` may advance only a grounded, non-writing `intake` with bounded non-empty observation and candidate, no confirmation and no teaching tool event. It reuses the existing `_state_after_success` transition once; later fallback, `teaching_hold`, write/confirmation/tool phases, missing observation/candidate, retry replay and stale CAS do not advance.
- An `intake` without a grounded candidate now returns the existing local `pause` action as an honest finite no-lesson response before Provider loading. It asks no free-text discriminator, creates no candidate or prescription, calls no tool and leaves ordinary zero-context Coach turns Provider-backed.
- Tests first exposed both original loops. Final focused Python was `44 passed`; the complete Coach Node suite was `156 passed`; `webapp/tests/test_task6_backend_contracts.py` was `18 passed`; the full isolated Python suite was `1440 passed, 5 skipped`. Compileall and whole-worktree `git diff --check` passed.
- Four fresh field-database copies reused the saved ready DeepSeek profile without modifying the original database. Static `Analysis 22` moved from `intake` to `hypothesize / version 2` on the first real turn, then produced a different non-question reply without advancing again; neither turn emitted a tool or confirmation. Dynamic `23`, Tracking `24` and Switching `25` each returned the finite no-lesson result, remained `intake / version 0` and emitted no tool or confirmation. Temporary copies and subprocesses were removed; no credential, path or private context was printed.
