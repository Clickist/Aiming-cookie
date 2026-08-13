# Coach Streaming, Latency, And DeepSeek Web Search v1 Implementation Plan

> **Status: active for Task 1 only.** 点点于 2026-08-05 明确批准先修流式输出
> 和测速。Task 2 与 Task 3 仍未授权，不得施工。

**Goal:** 让普通 Coach 回答在安全文本生成后尽快可见，能用无敏感信息的分段
时序区分本地与 Provider 耗时，并为显式触发的 DeepSeek 在线检索建立可审查边界。

**Current evidence:** 真实 Coach 回合总耗时为 6-34 秒，现有 Provider 连接测试约
1.3-1.8 秒；但当前没有 Provider TTFT、模型轮次、工具轮次或 repair 耗时，不能把
完整回合耗时归因于 Provider。Pi 已产生文本增量，sidecar 和 Python 仍等待整轮 JSON，
`partial_text` 也只在终态写入。DeepSeek `/responses` 与强制 `web_search` 已真实返回
成功；后者本次 TTFT 约 18 秒、总耗时约 22 秒，且未返回可依赖的结构化 citations。

## Frozen decisions

- 第一阶段使用 sidecar -> Python 的 NDJSON 响应流；前端继续复用现有 400/700 ms
  Run polling 与 `partial_text` 展示，不新增 SSE endpoint、DB migration 或第二套事件库。
- 每个文字 revision 都是经过 secret redaction、egress 与 grounding 校验的完整替换快照，
  不是 raw token delta。thinking、工具参数、原始工具结果和 Provider 搜索事件不得投影。
- `partial_text` 更新与对应 run-local event 必须在同一 SQLite transaction 提交；terminal
  状态只有一个 winner，terminal 后拒绝新的 partial、tool 与 status event。
- 收到首个流 frame 后发生中断时保留最后安全文本并明确失败，不静默重跑 Provider；
  只有首 frame 前的连接失败可保留既有 subprocess fallback。
- TeachingSession 的原始 JSON envelope 不得流给 UI。Task 1 只发布完整校验后的 Teaching
  文本；Teaching 的真正渐进文本必须由 Task 2 先改变本地合同并单独验证。
- 时序使用 monotonic duration，只记录阶段名、毫秒值、Provider round 数、tool round 数和
  repair 标志；不记录 prompt、回复、URL、ref、credential、token、路径或原始 payload。
- Web Search 默认关闭，只在用户本轮明确要求联网、最新信息或来源时启用；搜索结果只作为
  `online_reference`，不能触发或升级正式 diagnosis、Knowledge Registry、Training Plan、
  外设推荐或本地测量事实。
- 当前 Coach egress 禁止 URL，而本次 DeepSeek 实测未提供可靠结构化 citations。Task 3
  在来源身份与安全投影合同获批前不得把模型生成的 URL 变成可点击链接。

## Task 1 - Safe ordinary-turn revisions and latency trace

### Allowed files

- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`
- this plan
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/sidecar-server.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/coach-runtime/test/sidecar-server.test.ts`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_task6_backend_contracts.py`

### Tests first

1. A fake ordinary turn emits two safe cumulative text revisions before its final response; secret,
   internal, ungrounded, old-round, thinking and tool payload content never reaches the callback.
2. `/v1/turn` returns bounded NDJSON partial frames followed by exactly one final frame; malformed,
   oversized or truncated frames fail closed. A frame after terminal is rejected.
3. Python consumes frames incrementally and invokes `on_partial` before final. A connection failure
   before the first frame may fallback; EOF or transport failure after any frame must not rerun.
4. A single atomic writer updates `partial_text` and inserts the matching replace-revision event with
   monotonic sequence. Stop/completion/late-partial races preserve one terminal winner.
5. Timing tests distinguish local preparation, sidecar transport, each Provider round, first safe text,
   tool time, repair time and persistence without storing request/response content or secrets.
6. TeachingSession never exposes incremental JSON and only publishes a revision after the existing
   draft and grounding validators accept the complete user-visible text.

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
cd webapp\coach-runtime
npm.cmd test -- --test-name-pattern="stream|partial|latency|teaching"
cd ..\..
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_runtime.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_task6_backend_contracts.py -q
git diff --check
```

After automation, run at least three no-tool and three analysis-attached turns against the selected
DeepSeek profile. Record POST-to-first-safe-text, POST-to-terminal, Provider rounds, repair use and UI
first-visible time. Do not put the API key or request/response bodies in the artifact.

### Stop rule

Stop if a revision can be observed before all existing redaction/egress/grounding checks pass; if
Teaching JSON, thinking or tool payload reaches the UI; if a partial can overwrite terminal state; if
any post-first-frame failure retries the whole turn; or if implementation requires a DB migration,
frontend protocol change or files outside Allowed files.

## Task 2 - TeachingSession progressive text contract

### Allowed files

- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`
- this plan
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`
- `webapp/backend/teaching_session_store.py`
- `webapp/backend/coach_agent_runs.py`
- `webapp/tests/test_teaching_session_store.py`
- `webapp/tests/test_coach_agent_runs.py`

### Tests first

1. The provider-facing Teaching contract separates progressively displayable text from local state
   transition data; no partial can advance phase, create a fact, invoke a command or satisfy confirmation.
2. Every visible cumulative Teaching revision independently passes a conservative teaching-text validator
   for evidence, question count, dose, stop rule, matched retest, cause and unsupported numbers.
3. A final contract failure replaces the preview with a safe local response, holds state, and records no
   execution/retest fact. Already displayed text must never have contained the rejected claim.
4. Direct explanation/correction interruptions remain direct answers and do not fall back to a scripted
   question while preserving the existing TeachingSession state machine.

### Verification

```powershell
cd webapp\coach-runtime
npm.cmd test -- --test-name-pattern="teaching|stream|partial"
cd ..\..
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_teaching_session_store.py webapp/tests/test_coach_agent_runs.py -q
git diff --check
```

Then run a real DeepSeek teaching conversation containing explanation, correction, practice direction,
dose, stop condition and retest questions. Verify that useful text becomes visible before terminal and
that no rejected draft is ever shown.

### Stop rule

Stop if the only implementation is incremental parsing of the existing raw JSON string; if a partial can
advance state or trigger a product command; if a later validation failure means the user already saw an
unsafe claim; or if the change weakens current grounding, confirmation or fact boundaries.

## Task 3 - Explicit DeepSeek Responses Web Search

### Allowed files

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`
- this plan
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/deepseek-responses-research.ts`
- `webapp/coach-runtime/src/provider-models.ts`
- `webapp/coach-runtime/src/sidecar-server.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/test/deepseek-responses-research.test.ts`
- `webapp/coach-runtime/test/provider-models.test.ts`
- `webapp/coach-runtime/test/sidecar-server.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

### Tests first

1. A project-side research wrapper can make an isolated DeepSeek `deepseek-v4-flash` Responses request
   without modifying pinned Pi, normal Coach turns, or unrelated built-in/custom Provider profiles.
   The wrapper receives only the current explicit research question and reuses the selected credential
   without exposing it; it receives no Analysis, TeachingSession, history, product tool or bridge data.
2. A deterministic local intent gate enables `web_search` only for an explicit current-turn request for
   online/latest/source information; model preference, attached Analysis or prompt text cannot enable it.
3. Search lifecycle and source data are bounded and safely projected. Search query, raw result payload,
   arbitrary URL and page content never enter Coach run events, local facts or product commands.
4. Search-derived prose is visibly labeled `online_reference`, cannot support formal diagnosis or training
   prescription, and fails closed when source identity cannot be verified from structured provider output.
5. Search timeout/incomplete/unsupported behavior returns an explicit recoverable state without retrying a
   side-effecting turn or disabling local Analysis/History.

### Verification

```powershell
cd webapp\coach-runtime
npm.cmd test -- --test-name-pattern="responses|web search|deepseek|egress"
cd ..\..
git diff --check
```

Run one ordinary and one explicitly search-triggered real DeepSeek turn. Record TTFT, total duration,
search call count and source projection; do not persist API keys, queries, raw pages or response bodies.

### Stop rule

Stop if DeepSeek still returns no trustworthy structured source identity; if the implementation must trust
or render model-generated URL text; if search can run without explicit user intent; if online content can
alter diagnosis/Registry/Training Plan/product commands; or if using the feature requires modifying pinned
Pi or changing normal Provider profile semantics.
