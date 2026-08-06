# Coach Problem-Hypothesis Diagnosis v1 Implementation Plan

> **Status: active for Tasks 1-5.** 点点于 2026-08-06 明确授权自行连续推进这一功能直到完成，并要求完成后提供模拟对话。

> **Implementation closeout (2026-08-06):** Tasks 1-5 的代码与自动化验证已完成。真实 Provider 的 Static/Dynamic/Tracking/Switching 合成 TeachingTurn 均在当前端点返回 `HTTP 502`，subprocess fallback 对 Static 复现相同结果；因此本 plan 保持 active，仅等待真实 Provider 连续问诊 field Gate，不把自动化通过伪装成该 Gate 已关闭。

**Goal:** 复用现有 Coach 闭环，把一个或多个 grounded 数据观察编译成一个可解释、可追问、可实验和可修订的用户问题，并在首次完成 Analysis 后软启动 Coach。

**Architecture:** 新增一个无持久化的确定性 problem compiler；扩展既有 TeachingTurn 的表达能力；升级现有 Registry；soft start 继续使用 context、agent run、message 和 TeachingSession。Analysis/Data UI 不展示原因假设。

**Tech stack:** Python/FastAPI/SQLite、TypeScript/Node Coach runtime、Next.js/React、JSON Knowledge Registry。

## Frozen Decisions

- 不新增第二套 Registry、Session、Plan、消息、画像或诊断 store。
- 一次只处理一个主问题；不同 family 不合成总分。
- 身体、reading、反应与设备只能是可修订候选，不是从轨迹直接推出的事实。
- soft start 不写 user message，同一 Analysis 幂等一次，用户回答前不推进 TeachingSession。
- 外设排查先免费、可逆、单变量，不主动推荐购买或具体型号。

## Task 1 - Active Contracts And Problem Compiler

### Allowed files

- `docs/README.md`
- `docs/superpowers/specs/README.md`
- `docs/superpowers/specs/2026-08-06-coach-problem-hypothesis-diagnosis-design.md`
- `docs/superpowers/plans/README.md`
- this plan
- `webapp/backend/coach_problem_compiler.py`
- `webapp/tests/test_coach_problem_compiler.py`

### Tests first

1. 每个已支持 family 的多个 issue 可归到正确功能问题；跨 family 不合并。
2. 显式 issue 仍优先；否则重复支持、可教学场景和 priority 的排序确定且稳定。
3. 输出一个主问题、bounded evidence、反例状态、最多三个候选和一个区分问题/实验。
4. 无 grounded issue、质量不足或只有未注册自由文本时返回 `None`。

## Task 2 - TeachingTurn Integration

### Allowed files

- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/teaching_session_store.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_teaching_session_store.py`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`

### Tests first

1. TeachingTurn 严格包含问题、证据强度、支持证据、反例状态、候选原因和一个区分问题。
2. Provider 不能新增证据、升级证据强度、把候选写成原因或一次问多个问题。
3. 现有 phase、command、confirmation、prepared item、fallback 和 retest 行为保持不变。

## Task 3 - Knowledge Registry v5 Peripheral Differential

### Allowed files

- `knowledge/coach/registry.v5.json`
- `kovaak_tracker/coach/knowledge_registry.py`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/backend/coach_runtime.py`
- `tests/coach/test_knowledge_registry.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- `webapp/tests/test_coach_runtime.py`

### Tests first

1. v1-v4 历史引用继续可读，默认 Registry 变为 v5，Python/Node parity 通过。
2. 设备适配条目要求用户报告、系统延迟排查、单变量实验和 matched retest。
3. 条目明确禁止从轨迹推出握法、舒适度、最佳鼠标、购买需要或具体型号。

## Task 4 - Idempotent Analysis Soft Start

### Allowed files

- `webapp/backend/db.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/tests/test_db.py`
- `webapp/tests/test_routes_chat.py`
- `webapp/tests/test_coach_agent_runs.py`

### Tests first

1. v23 migration 为 run 增加显式 `initiator` 和 `trigger_ref`，并以 owner + trigger 对 system run 幂等。
2. soft start 原子附加 owned done Analysis；并发请求只返回同一个 run。
3. system run 写零条 user message、至多一条 assistant message，不发工具、不推进 TeachingSession。
4. Provider 不可用由调用方 gate；Analysis 不可用、无问题或 busy 状态 fail closed。

## Task 5 - Frontend Trigger And End-To-End Verification

### Allowed files

- `webapp/frontend/components/task3/AppShell.tsx`
- `webapp/frontend/components/task6/CoachSidebar.tsx`
- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/api.test.ts`
- `webapp/frontend/tests/task6-source.test.ts`
- `webapp/frontend/e2e/browser-smoke.spec.ts`
- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`
- this plan

### Tests first

1. `ready + done Analysis` 展开 Coach 并调用一次 soft start；刷新由后端幂等，不依赖 localStorage 作为事实源。
2. 非 ready、非 done、普通页面和无效 Analysis id 不调用；失败只保留可恢复 Coach，不影响 Analysis。
3. CoachPanel 能接住 soft-start run 并轮询/刷新同一消息流，不自动发送用户文案。

## Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_problem_compiler.py tests/coach/test_knowledge_registry.py webapp/tests/test_teaching_session_store.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_routes_chat.py webapp/tests/test_db.py webapp/tests/test_coach_runtime.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$coachTests = (Get-ChildItem webapp\coach-runtime\test -Filter *.test.ts | Sort-Object Name).FullName
node "--import=$loaderUrl" --test --test-name-pattern="knowledge|teaching|problem|hypothesis" @coachTests
cd webapp\frontend
npm.cmd test
npm.cmd run type-check
npm.cmd run build
cd ..\..
git diff --check
```

真实 Provider 至少跑一条 Static/Dynamic/Tracking/Switching 可用样本，检查主动首轮、追问、否定候选、单变量实验和复测改口。真实 Tauri/KovaaK/hardware 与 distribution 保持独立 Gate。

## Stop Rule

若必须从原始 trace、模型自由文本或跨 family 总分生成问题；若 soft start 需要伪造 user message；若 Provider 可写入原因事实或绕过确认；若设备方向无法保持可逆且非购买优先；或实现需要第二套 store/registry/session/plan，立即停止并回到本合同审查。
