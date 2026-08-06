# Coach Goal And Feedback Learning Loop v1 Implementation Plan

> **Status: active for Task 1.** 点点于 2026-08-06 明确授权创建并连续执行本计划。

**Goal:** 在不改变 Analysis 数据和显化的前提下，让现有 Coach 依据用户目标组织问题、区分证据来源、记录训练感受，并通过现有确认路径调整 Training Plan；同时吸收 Viscose 社区经验。

## Frozen Decisions

- 复用现有 TeachingSession、Training Plan、execution/retest facts、confirmation、Registry 和鼠标数据。
- 不新增 store、状态机、鼠标 catalog 或 Analysis 字段。
- 用户目标与反馈只能来自用户明确表达或确认。
- 社区身体与外设内容只能作为经验、候选和可逆实验；不推荐具体型号。
- matched、near-transfer、main-game transfer 分开，不承诺迁移到所有 FPS。

## Task 1 - Goal, Feedback, Plan Adjustment, And Registry v6

### Allowed files

- `docs/README.md`, `docs/PROGRESS.md`, `docs/superpowers/specs/README.md`, `docs/superpowers/plans/README.md` and this plan/spec
- `knowledge/coach/registry.v6.json`
- `kovaak_tracker/coach/knowledge_registry.py`
- `webapp/backend/teaching_session_store.py`, `coach_problem_compiler.py`, `coach_agent_runs.py`, `coach_commands.py`, `training_plan_store.py`
- `webapp/coach-runtime/src/contracts.ts`, `knowledge-registry.ts`, `teaching-policy.ts`, `turn.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- corresponding Registry, TeachingSession, compiler, agent run, plan, command and Analysis compatibility tests under `tests/coach`, `webapp/tests` and `webapp/coach-runtime/test`

### Tests first

1. 旧 TeachingSession/TeachingTurn/execution/Registry v5 继续读取；新字段受长度、枚举、refs 和 owner/CAS 约束。
2. learner context 为空时 compiler 排序不变；明确目标只作为相关性 tie-break，不生成或改写 Analysis issue。
3. typed evidence 保留来源类型与 refs；Provider 不得升级类型或伪造用户事实。
4. 自由文本 feedback、structured learner response、skipped/discomfort 和不可比复测路径均可复现。
5. 调整必须复用 `training_plan.adjust`、confirmation、版本历史、evidence refs 和 verification targets。
6. Registry v6 只增加社区解释/实验经验，v1-v5 可读，Python/Node parity 通过且没有型号推荐。
7. Analysis Workspace、Analysis Data/family、History 和 `current_training.v1` 固定 DTO 不变。

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak-goal-feedback"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_teaching_session_store.py webapp/tests/test_coach_problem_compiler.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_training_plan_store.py webapp/tests/test_coach_commands.py tests/coach/test_knowledge_registry.py webapp/tests/test_capability_contracts.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$coachTests = (Get-ChildItem webapp\coach-runtime\test -Filter *.test.ts | Sort-Object Name).FullName
node "--import=$loaderUrl" --test --test-name-pattern="knowledge|teaching|goal|feedback|adjust" @coachTests
git diff --check
```

### Stop Rule

若实现需要新增 session/feedback/plan/mouse store，改变 Analysis/History/current-training DTO，从 Provider 文本推断用户目标或身体状态，绕过产品确认修改计划，或把 Viscose 社区经验提升为无条件产品因果规则，立即停止并回到合同审查。
