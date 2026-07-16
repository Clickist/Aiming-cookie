# Analysis Deletion and Workspace Reconciliation Implementation Plan

> **状态：completed（2026-07-16）。** Task 1–3 已完成；正式 frontend、真实 Windows/KovaaK/Raw Input 设备 Gate 不在本计划完成声明内。
> **Executor:** 按 Task 的 Allowed files、Tests first、冻结决策与 Stop rule 执行；不得提交、推送或进入正式 frontend。

**Goal:** 让 terminal Analysis 的 SQLite logical delete 与 managed workspace cleanup 在 Windows 文件锁、进程崩溃和 SQLite rollback 后都能确定收敛。

**Architecture:** SQLite v13 增加单用途 transient tombstone。删除请求先在一个事务内保留 Coach、写 tombstone 并删除 session；commit 后再清 workspace。Desktop ready 前重试 pending/failed cleanup，成功即删除 tombstone。

**Tech Stack:** Python 3.11、FastAPI、aiosqlite/SQLite WAL、pathlib/shutil、pytest/pytest-asyncio。

**Design contract:** [`../../../superpowers/specs/2026-07-16-analysis-deletion-reconciliation-design.md`](../../../superpowers/specs/2026-07-16-analysis-deletion-reconciliation-design.md)

---

## Task 1 — SQLite v13 transient deletion tombstone

**状态：completed（2026-07-16）。** Exact DDL、fresh/v12 transactional migration、完整
`init_schema()` rollback、v13 idempotent self-heal 与非法组合 CHECK 已验收；结果见
[`../../../PROGRESS.md`](../../../PROGRESS.md)。

### Allowed files

- `webapp/backend/db.py`
- `webapp/tests/test_db.py`

### Tests first

1. fresh schema 通过 v13 migration helper 创建 `analysis_deletion_tombstones`，字段、default 与 CHECK 约束精确符合 spec；
2. v12→v13 upgrade 创建同一结构并设置 `PRAGMA user_version=13`；
3. 第二次 `init_schema()` 幂等；
4. migration helper 在 caller transaction rollback 后不留下 table；
5. 完整 v12→v13 `init_schema()` 在 v13 table 创建后注入失败：`user_version` 仍为 12 且 table 不存在；
6. invalid state、negative attempts、空 owner、pending/error 与 failed/attempt 组合不一致均被 CHECK 拒绝；
7. table 不包含 path/workspace path 列、不 foreign-key 到 sessions，也不创建 state index。

### Implementation

- 将 `TARGET_USER_VERSION` 从 12 升至 13；
- 新增含 spec exact DDL 的 `_V13_ANALYSIS_DELETION_TOMBSTONES` 与 transactional migration helper；
- 不把 v13 DDL 放入事务前执行的 canonical `SCHEMA`；
- 在 fresh/idempotent/v12 upgrade 路径调用同一 helper，且 table creation 与 `PRAGMA user_version=13` 位于同一个外层 transaction；
- 只存 stable session id、owner、state、attempt/error code 与 timestamps。

### Verify

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_db.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp\backend\db.py webapp\tests\test_db.py
git diff --check -- webapp/backend/db.py webapp/tests/test_db.py
```

Expected: all focused tests pass；无 frontend/queue/workspace 改动。

### Stop rule

- 需要修改 `sessions.status` 或删除现有列；
- 需要 completed tombstone retention、通用 operation journal 或任意 path 列；
- migration 无法在 SQLite transaction 内真实 rollback。

## Task 2 — Commit-first logical delete and idempotent cleanup

**状态：completed（2026-07-16）。** Phase A commit-first delete、post-commit managed workspace cleanup、
pending/failed tombstone reconciliation、failure/cancellation rollback 与稳定 API outcome 已验收；结果见
[`../../../PROGRESS.md`](../../../PROGRESS.md)。

### Allowed files

- `webapp/backend/queue.py`
- `webapp/backend/workspace.py` only if the existing managed cleanup helper cannot express absent-as-success
- `webapp/backend/coach_store.py` only for same-connection transaction helpers
- `webapp/tests/test_queue.py`
- `webapp/tests/test_workspace.py`
- `webapp/tests/test_desktop_local.py` only for existing terminal delete ordering/failure expectations

### Tests first

1. Phase A 在 legacy migration、ref update、tombstone insert、chat delete、session delete 或 commit 前失败：DB rollback，workspace 未触碰；
2. Phase A commit 后 cleanup 前模拟 crash：session absent、Coach message 保留、ref deleted、tombstone pending、workspace 保留；
3. cleanup 抛 OSError 或只删除部分内容：返回 `deleted=true` + `cleanup_failed=[workspace]`，tombstone failed 且不泄露异常/path；
4. workspace 不存在视为成功；
5. cleanup 成功后 tombstone 删除；cleanup 后、tombstone delete 前 crash 可由 reconcile 收敛；
6. `reconcile_analysis_deletions()` 对 pending/failed 幂等，单项失败不阻止其他项；
7. wrong owner/non-terminal 不创建 tombstone、不触碰 workspace；
8. legacy message exactly-once，ref exactly-once deleted；
9. Run row、Run-owned trace、用户 Stats/Performance/source MP4 fixture 保持不变。
10. 更新既有 desktop-local 删除测试：cleanup OSError 发生在 Phase A commit 后，session absent、ref deleted、tombstone failed、partial workspace 可重试；不得继续断言 workspace 在 DB delete 前清理。

### Implementation

- 重排 `queue.delete_session`：Phase A 只做 SQLite；commit 后才调用 workspace helper；
- Phase A 写 transient tombstone，并在同一事务完成 legacy Coach migration、ref invalidation 和 session/chat delete；
- cleanup 成功删除 tombstone；失败只写稳定 error code/attempt count；
- 新增 owner-safe、path-free `reconcile_analysis_deletions()` aggregate result；
- rollback 不得被二次 rollback 错误掩盖。

### Verify

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_queue.py webapp\tests\test_workspace.py webapp\tests\test_desktop_local.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp\backend\queue.py webapp\backend\workspace.py webapp\backend\coach_store.py
git diff --check -- webapp/backend/queue.py webapp/backend/workspace.py webapp/backend/coach_store.py webapp/tests/test_queue.py webapp/tests/test_workspace.py webapp/tests/test_desktop_local.py
```

Expected: all focused tests pass；Windows cleanup failure 可恢复；无 Run/source/trace 删除。

### Stop rule

- 需要扫描或删除没有 tombstone 的 workspace；
- 需要改变 Coach deleted ref、legacy message retention 或 Analysis terminal-only 规则；
- 需要 Run/source/trace retention 决策；
- 需要在 Phase A commit 前触碰文件系统。

## Task 3 — Startup reconciliation and API outcome

**状态：completed（2026-07-16）。** FastAPI lifespan reconciliation、安全 aggregate log、workspace failure non-blocking、unexpected DB failure fail-closed 与 API/Coach invariants 已验收；结果见 [`../../../PROGRESS.md`](../../../PROGRESS.md)。

### Allowed files

- `webapp/backend/app.py`
- `webapp/backend/routes.py` / `schemas.py` only if existing delete envelope cannot express cleanup pending
- `webapp/tests/test_app.py` (new) or focused existing lifespan tests
- `webapp/tests/test_routes.py`
- `webapp/tests/test_routes_coach.py` only for deleted/unavailable invariants

### Tests first

1. app lifespan 在 `init_schema()` 后、API ready 前调用 reconciliation；
2. pending cleanup 成功不留下 workspace/tombstone；
3. cleanup failure 不阻止启动，但 tombstone 保留且 aggregate failed 可观察；
4. API Phase A success + cleanup failure 仍返回 `deleted=true` 与 `cleanup_failed=[workspace]`；
5. 删除后 Analysis detail/video/timeline unavailable，Coach messages/context 保留且 ref 为 deleted；
6. response/log/tombstone 不含绝对路径、底层 OSError 或 traceback。

### Implementation

- 在 FastAPI lifespan 的 schema init 后运行一次 reconciliation；
- 只记录 aggregate counts/stable code，不记录路径或 exception text；
- 保持现有 delete response envelope，除非 focused test 证明无法表达冻结语义；
- 不在 `desktop_runtime.py` 重复执行同一 reconciliation。

### Verify

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_app.py webapp\tests\test_routes.py webapp\tests\test_routes_coach.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q kovaak_tracker webapp\backend webapp\tests tests
git diff --check
```

Expected: focused 与完整 Python suite pass；startup failure policy 与 API outcome 符合 spec。

### Stop rule

- 需要 frontend 文件或正式 UI；
- 需要让可恢复 cleanup failure 阻止 Desktop/API 启动；
- 需要公开 tombstone/path/internal exception；
- 需要同时实现 uploading/importing orphan recovery。

## Global verification gate

- Windows pytest 必须单进程串行，避免 SQLite `WinError 32`；
- v12→v13、fresh schema、rollback、locked-file/partial cleanup、restart reconciliation 全部有自动化覆盖；
- `AGENTS.md` / `CLAUDE.md` byte parity；
- 最终报告 changed files、focused/full tests、未运行检查、偏差、剩余风险与 `git status --short`；
- 未经点点指示不提交、不推送；Task 1–3 完成前不开始正式 frontend。
