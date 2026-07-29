# Coach Guided Teaching Loop v1 - Implementation Plan

> **Status: Task 1 stopped at the real-Provider gate (2026-07-27).** 安全的 Training Plan fact-command / confirmation 切片已经实现并通过自动化；真实 Provider 仍不能稳定遵守单问题、无未经验证归因和可比复测教学合同，因此完整带练闭环未验收。ratio 的数学等价展示允许；失败的是无来源的语义、好坏或因果扩展。不得把本 Task 标为 completed，也不得在本计划内继续堆提示词或扩大到 `turn.ts`、schema、migration、frontend 或数据分析管线。后续实施只使用 [`2026-07-27-coach-teaching-session-v1.md`](../../completed/plans/2026-07-27-coach-teaching-session-v1.md)。

**Goal:** 让 Coach 使用现有 Training Plan facts 和 confirmation infrastructure，主动带用户完成“候选解释 -> teach-back -> 单变量练习 -> 执行确认 -> 复测 -> 调整”循环。

**Architecture:** 不新增数据库或前端页面。Pi runtime 暴露 backend 已实现的三个 explicit-user-fact commands，并将它们纳入现有 write/idempotency/confirmation 边界；system prompt 冻结单步教学协议，自动化和真实 Provider matrix 验证模型不会跳步、伪造完成或把即时表现冒充长期学习。

**Tech Stack:** TypeScript / pinned Pi / Node test runner；FastAPI product-command bridge / pytest；现有 React Coach confirmation UI。

---

## Task 1 - Connect the guided teaching loop

### Allowed files

- `docs/superpowers/plans/2026-07-27-coach-guided-teaching-loop-v1.md`
- `docs/superpowers/plans/README.md`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/coach-runtime/src/product-command-tools.ts`
- `webapp/coach-runtime/test/product-command-tools.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/backend/coach_commands.py`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_runtime.py`

No other file is allowed. In particular, this Task does not modify DB migrations, stores, routes, schemas, frontend components, PRD, Architecture, scenario/analyzer assets, evidence/profile producers or native capture code.

### Tests first

1. Add a TypeScript contract test asserting that `training_plan.item.add`, `training_plan.execution.record` and `training_plan.retest.record` are registered product commands and members of the write-command behavior.
2. Add tool tests proving all three commands receive stable turn-local idempotency keys, reject model-provided authority/confirmation/owner/path/secret/raw payload fields, and return only the safe audit projection.
3. Extend Python command tests to prove a Coach-inferred call for each command returns `needs_confirmation`, does not create a plan fact before confirmation, executes exactly once after the trusted confirmation decision, and remains owner-scoped.
4. Add prompt contract assertions for: one discriminating question at a time; one primary hypothesis plus at most two alternatives; one cue; teach-back before practice; single-variable practice; explicit execution feedback; immediate matched versus delayed/near-transfer distinction; and retain/lower/reject revision.
   Coach uses natural spoken Chinese rather than audit-report phrasing. It discusses peripherals only when the user raised them or current evidence makes a reversible equipment experiment relevant; unrelated lesson recap must not enumerate an unnecessary hardware conclusion.
5. Add a fake-stream transcript test that reaches the execution and retest confirmation tools without allowing the assistant to claim the user completed practice or improved before a confirmed fact/result exists.
6. Preserve existing grounding tests: no-context fake precision, limitations-as-cause, ungrounded metrics, stale-turn partial reply, deletion confirmation and Evidence reachability must remain unchanged.

### Implementation steps

1. Run the new focused tests and confirm they fail only because the three commands and teaching protocol are absent from the Pi runtime surface.
2. Add the three exact command names to `PRODUCT_COMMAND_NAMES` and `WRITE_COMMANDS`; do not widen parameter validation or add a generic command escape hatch.
3. Extend the product-tool description only enough to state that explicit user facts are prepared by Coach but authorized by trusted confirmation; keep all current Evidence and deletion instructions intact.
4. Extend `coach-system.md` with the guided loop in concise Chinese: observe, discriminate, teach, teach-back, practice, check-in, matched/delayed/near-transfer retest, revise. Keep current plain-text, grounding, limitation, safety and no-fake-dose rules.
5. Re-run focused Node and Python tests, then the complete Coach Node suite and bounded Coach/backend Python suites.
6. Run an isolated real Provider matrix using existing redistributable/owner-scoped test data only. Verify: one Analysis-backed teaching turn, one user rejection, one missing-evidence path and one retest-not-comparable path. Do not recapture KovaaK or expose credentials, local paths or raw sources.
7. Record only point-in-time verification evidence in this plan closeout; do not update PRD/Architecture or claim the full human-coach gap is complete.

### Verification commands

```powershell
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
node "--import=$loaderUrl" --test webapp\coach-runtime\test\product-command-tools.test.ts webapp\coach-runtime\test\system-prompt-and-tools.test.ts webapp\coach-runtime\test\turn-fake-stream.test.ts
```

```powershell
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("aiming_cookie_teaching_" + [guid]::NewGuid().ToString("N"))
$env:DATABASE_URL = "sqlite+aiosqlite:///" + (Join-Path $testRoot "test.db").Replace("\", "/")
$env:DATA_ROOT = $testRoot
$env:KOVAAK_INSTALL_DIR = Join-Path $testRoot "missing-kovaak"
.\.venv\Scripts\python.exe -m pytest -q webapp\tests\test_coach_commands.py webapp\tests\test_coach_runtime.py
```

After focused checks, run the complete Coach Node suite and the existing Coach/backend Python contract suite using the same isolated environment. Expected result: all tests pass; no source, credential, path, raw payload or unconfirmed user fact enters tool traces, messages or stores.

### Frozen decisions

- One current action per teaching turn; a long report is not the default lesson interface.
- One primary hypothesis and at most two alternatives; the model cannot turn a community concept into a measured cause.
- Teach-back happens before a prescribed practice block unless the user explicitly requested explanation/report only.
- Completion, subjective feedback, discomfort and retest outcome are user/reality facts. Coach may prefill them, but only trusted confirmation can write them.
- Immediate improvement is acquisition evidence, not retention or main-game transfer.
- The current generic confirmation UI is reused. This Task does not add a page, form, card type, migration, dependency or new product default.
- Prompt is the teaching policy; deterministic runtime boundaries remain responsible for grounding, ownership, idempotency and side effects.

### Stop rule

- Generic confirmation cannot safely execute one of the three explicit-user-fact commands without frontend/schema/store changes.
- The existing backend command and Pi parameter shapes differ or require a migration.
- Fake-stream or real Provider testing shows the loop cannot avoid fabricating completion/retest facts without a deterministic teaching state machine.
- The implementation requires changing PRD, Architecture, Provider onboarding, analysis/capture contracts or any file outside Allowed files.
- Any existing grounding, deletion, stop/turn correlation, Evidence reachability, owner-scope or secret-sentinel regression appears.

On Stop, report the exact failing contract and prepare a separate design for persistent `TeachingSession`; do not widen Task 1.

## Stop-rule closeout - 2026-07-27

### Implemented and retained

- Pi product tools now expose `training_plan.item.add`, `training_plan.execution.record` and `training_plan.retest.record` through the existing write-command allowlist.
- Model-provided authority, confirmation, owner, path, URL, credential, secret and raw payload fields fail closed. Tool traces retain only the bounded audit projection and stable turn-local idempotency key.
- `coach_inferred` user facts enter the existing trusted `needs_confirmation` flow. No fact is written before confirmation; a valid same-owner confirmation executes once; wrong-owner, expired and repeated confirmations do not create an additional write. `system_safe` cannot bypass this boundary.
- The prompt now states the intended guided loop, teach-back, single-variable practice, immediate/delayed/near-transfer distinctions, external-device relevance rule and no-fake-completion rule. These remain advisory model policy, not deterministic teaching state.

### Verification

- Complete Coach Node suite: `117 passed, 0 failed`.
- Isolated `test_coach_commands.py + test_coach_runtime.py`: `113 passed` using a temporary SQLite database, temporary `DATA_ROOT` and nonexistent `KOVAAK_INSTALL_DIR`.
- Earlier bounded Coach/backend Python suite: `412 passed` before the final prompt-only changes; backend code did not change afterward.
- `git diff --check`: no whitespace errors; existing LF/CRLF conversion warnings only.
- No commit or push. No real Run, capture source, analyzer/scenario registry, evidence/profile producer or data-analysis-pipeline file was read for mutation or changed by this Task.

### Real Provider matrix

The matrix used an isolated loopback sidecar and read-only owner-scoped Provider profile. It did not connect to the other active session's API, worker, database or sidecar and did not expose the credential, URL, path or raw source in output.

- Analysis-backed teaching: failed after three prompt iterations. 将有来源 ratio 显示为百分比本身不是失败；Provider 的错误是把它进一步解释成无来源的 occurrence frequency，并在单局证据上提出 reading 与 hand tension 的因果分拆，而非只问一个区分问题。
- Missing evidence plus mouse question: partially passed. It eventually said “现在没必要换鼠标” and did not claim a measured cause, but still asked two body questions in one turn; an earlier attempt also invented an unsourced repetition dose and causal explanation.
- User rejects recording/retest: passed. It accepted the refusal, did not fabricate a record or result, and returned to a single teach-back action.
- Retest not comparable: failed. It correctly refused the causal conclusion, but exposed internal comparison vocabulary, gave contradictory “one condition / all baseline conditions” instructions and appended a compound question.

### Exact blocker and required follow-up design

Prompt-only policy cannot reliably enforce the teaching protocol with the selected real Provider. The missing deterministic contracts are:

1. A persistent `TeachingSession` state that records the current observation, primary and alternative hypotheses, one active cue, teach-back status, single changed variable, execution-confirmation state, matched/delayed/near-transfer retest intent and revision decision.
2. A deterministic turn planner that selects exactly one allowed next action from that state before the Provider writes user-facing language.
3. A teaching-output validator that permits semantic-preserving ratio mathematical displays, but rejects unsourced semantic/qualitative/causal expansion, unsupported doses, multiple or compound questions, internal vocabulary, unconfirmed physical attribution, and invalid comparable-retest instructions.
4. A bounded repair/fallback for each teaching state so a Provider violation returns a useful coaching step instead of a generic answer or failed turn.

该设计已冻结在 active spec，执行入口为 [`2026-07-27-coach-teaching-session-v1.md`](../../completed/plans/2026-07-27-coach-teaching-session-v1.md)。本计划保留为 blocked closeout；不得在此计划内继续施工。
