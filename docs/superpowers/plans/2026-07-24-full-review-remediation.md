# Full Review Remediation - Implementation Plan

> **状态：complete，本地分批 commit 已收口，尚未 push。** 点点于 2026-07-24 批准 backend full review 的推荐决策，并明确授权按三路 `gpt-5.6-terra high` 并行修复、主审验收、最终分批 commit；需要新产品面、发布系统或新 caller context 的项目保留为 Gate。
> **For executor:** 每个 agent 每次只执行一个明确 Task；只修改 Allowed files；tests first；不得启动 KovaaK、读取真实产品 DB、修改 PRD、提交或推送。
> **2026-07-24 size audit：** 不再把全部 27 个 finding 等同于立即增加代码。只实施当前正确性缺陷、已冻结的 Knowledge 边界和能用 benchmark/golden 证明的删除型优化；frontend、packaging、release、长期规模与低价值 P3 保留为 Gate。
> **Assessment:** [`../assessments/2026-07-24-backend-full-review-ledger.md`](../assessments/2026-07-24-backend-full-review-ledger.md)

**Goal:** 收敛 assessment 的 27 个 confirmed findings，并把无法在仓库内自动闭合的真实硬件、签名、法律和未来来源工作保留为明确 No-Go Gate。

**Architecture:** 先修测试隔离和 SQLite transaction ownership，恢复可信验证基础；再修 capture/recovery/evidence quality；随后处理长期存储、Coach/公开合同、性能和 frontend adapter；最后补 CI/工具链与事实源文档。保留现有 KovaaK source adapter、跨进程独立验证和 family-specific analyzer，不借审计修复引入通用插件或大规模重构。

**Tech Stack:** Python/FastAPI/aiosqlite、Rust/Tauri/Windows Raw Input/WGC、TypeScript/Pi Coach runtime、Next.js adapters、SQLite、pytest/Node test/Cargo MSVC/GitHub Actions。

---

## 1. 冻结决策

1. Capture：已知 finalizer drain 后立即 release；无进展时 30 秒 hard grace，超时后晚到来源只能 partial/unavailable。
2. Raw：按 metric/family fail closed，不因局部缺失整局失败；任何缺失都不能声明完整 native evidence。
3. QoS：游戏、Raw 与下一局优先；KovaaK 存活时重型 finalizer 并发上限 1，退出后才可提高吞吐。
4. Evidence：原始 Run/Raw 不降采样；正式 metric 使用完整数据或数学等价 streaming aggregate；只有 UI projection 可有界降采样并记录 provenance。
5. Knowledge：默认 strict family lock；cross-family 只允许显式 transfer mode，并保留适用性标注。
6. Preview：unsigned owner 仅限 loopback dev/test；非 loopback 必须 trusted proxy，否则 fail closed。
7. Recovery data：计入 Storage、用户显式删除、无自动 TTL 或静默清理。
8. CI：hosted Linux + Windows MSVC；无真实 Provider secret/产品 DB；硬件矩阵继续人工 Gate。
9. Distribution：未来 installer 必须自包含 runtime；本计划不在正式 frontend 缺失时伪造可发布安装包。

## 2. 并行与恢复规则

- 每波最多 3 个 agent；同波 Allowed files 不重叠。若实际需要扩大到另一 Task 的文件，立即停止并交回主审串行处理。
- agent 先新增失败回归，再做最小实现；不得删除或覆盖审计前的 dirty 改动。
- 主审在每波后阅读完整 diff、运行 focused tests、更新本计划状态，再启动下一波。
- Web 全量 pytest 只有 Task 1 通过进程隔离回归后才能运行；所有测试显式把 `KOVAAK_INSTALL_DIR` 指向不存在路径并使用 temp DB/root。
- subagent 不 commit。所有 commits 由主审在全量收敛后按逻辑批次创建。

### 2.1 增量控制

- 开审前已有 104 个 dirty 条目；当前 Git 总增量不能归因于本计划，任何 agent 必须按自己实际 patch 单独报告生产/测试净增。
- 测试行数不是免责理由。只保留能在修复前失败、保护跨进程 wire shape，或锁定恢复/并发安全语义的回归。
- 性能 Task 必须 production net-neutral 或净减少；没有 golden/benchmark 证据不得以“优化”为名新增 abstraction。
- frontend、sidecar packaging、CI/release、长期 Storage/History 只记录 Gate，等待相应产品面或发布计划开工，不在本轮提前实现。

## 3. 执行矩阵

| Wave | Task | Finding | 状态 |
|---:|---:|---|---|
| 1 | 1 | P0-02 test DB isolation | verified: sentinel + DB/queue 99 passed |
| 1 | 2 | P0-03 SQLite transaction ownership | verified: DB/queue/provider/plan 115 passed |
| 1 | 3 | P1-01 capture process-exit release | verified: Python 46 passed/1 skipped; Rust 13 passed |
| 2 | 4 | P1-02 Raw completeness receipt/Gate | verified: Python 414 passed/1 skipped shared Wave suite; MSVC Raw 24 passed/1 ignored |
| 2 | 5 | P1-05/P1-06 PTS + partial diagnosis quality | verified in shared Wave suite |
| 2 | 6 | P1-04 uploading recovery + P2-01 finalizer QoS | verified in shared Wave suite |
| 3 | 7 | P2-02/P2-03 storage, orphan, pagination/index | deferred: frontend/long-term scale Gate |
| 3 | 8 | P2-06/P2-09/P3-03/P3-05 Knowledge parity/family | verified narrow: validator/secret/cwd fixed; strict family remains caller-context Gate |
| 3 | 9 | P2-13/P3-04 post-analysis resource cost | verified narrow: duplicate derive removed; MP4 hash remains benchmark Gate |
| 3 | 10 | P2-08/P2-10/P3-01 public/snapshot projection | verified narrow: snapshot/error/segment fixed; nested public result DTO deferred |
| 4 | 11 | P2-14/P3-06 sidecar + preview boundary | deferred: frontend/packaging Gate; feature remains No-Go |
| 4 | 12 | P2-16 frontend DTO/adapters | deferred: frontend reconstruction Gate |
| 5 | 13 | P2-12/P2-15 toolchain, lock, CI | deferred: dedicated release plan |
| 5 | 14 | P3-02 evidence revision cleanup | deferred: low-value P3; existing workspace deletion/accounting remains the guard |
| 5 | 15 | DOC-01/DOC-02/assessment/indices | verified: dirty-state and remediation closeout recorded |

## Task 1 - Isolate Web pytest database

**Allowed files:** `webapp/tests/conftest.py`, `webapp/tests/test_test_database_isolation.py`, `docs/DEVELOPMENT.md`.

**Tests first:** add a subprocess test that preloads a sentinel external `DATABASE_URL`, imports the Web test fixture, and proves the sentinel path/content is untouched while the child uses a unique absolute temp SQLite path.

**Implement:** overwrite `DATABASE_URL` before any backend import; derive cleanup from that exact path; refuse non-temp/non-test paths. Do not add environment flexibility beyond tests.

**Verify:** isolated subprocess regression, a small Web DB suite, then `pytest --collect-only webapp/tests` with explicit temp root.

**Stop rule:** any import before the forced DB assignment, or any test touching repo-root/product DB.

## Task 2 - Enforce SQLite transaction ownership

**Allowed files:** `webapp/backend/db.py`, `webapp/tests/test_db.py`, `webapp/tests/test_queue.py`, plus the smallest directly required transaction-helper call sites after main-agent approval.

**Tests first:** reproduce `BEGIN IMMEDIATE` tombstone + concurrent real heartbeat/commit and assert the second task cannot commit the first task; add rollback/cancellation and normal concurrent read/write cases.

**Implement:** one process-wide transaction owner/gate around the existing shared connection. Standalone writes and commits must participate; nested same-task access must not deadlock. Do not rewrite every store or introduce a new database library.

**Verify:** DB/queue/deletion/provider/plan focused suites and the original independent reconciliation reproduction.

**Stop rule:** SQL string heuristics, task-local connections that leak, or a change requiring broad store rewrites without review.

## Task 3 - Release capture after KovaaK exits

**Allowed files:** `webapp/frontend/src-tauri/src/capture_coordinator.rs`, `webapp/backend/kovaak_capture_finalizer.py`, `webapp/backend/desktop_runtime.py`, their focused tests.

**Tests first:** process exit enters Finalizing; known finalizers drain then release exactly once; no-source path releases at 30 seconds; restart without app exit creates a new session; per-Run finalization while KovaaK is alive does not release.

**Implement:** explicit process-exit drain/release handshake with capture-session identity and hard grace. Preserve multi-Run pre-roll and runtime shutdown semantics.

**Verify:** Python finalizer/runtime tests and MSVC Rust focused tests/check.

**Stop rule:** release after each Run, sleep-based race tests, or changing the 300-second replay contract.

## Task 4 - Carry Raw completeness into Run quality

**Allowed files:** `webapp/frontend/src-tauri/src/raw_input.rs`, `webapp/frontend/src-tauri/src/capture_coordinator.rs`, `webapp/backend/native_capture_client.py`, `webapp/backend/kovaak_capture_finalizer.py`, `webapp/backend/kovaak_run_store.py`, focused tests/fixtures.

**Tests first:** queue drop, ring expiry and missing window coverage appear in versioned receipt; zero loss remains attached; nonzero loss becomes limited/unavailable per dependent metric and never complete.

**Implement:** session/window-scoped counters and strict receipt parsing; preserve drop-first producer behavior and ACRI compatibility.

**Verify:** Rust Raw tests, native client/finalizer/Run store tests, 1/4/8 kHz synthetic capacity cases.

**Stop rule:** block Raw producer, change ACRI bytes, or reject Stats-only independent facts.

## Task 5 - Propagate visual/native quality to profile and Coach

**Allowed files:** `kovaak_tracker/visual_signals.py`, `kovaak_tracker/native_flicking_analysis.py`, `kovaak_tracker/coach/diagnosis.py`, `webapp/backend/worker.py`, `webapp/backend/aiming_profile_store.py`, focused tests.

**Tests first:** missing/non-monotonic/out-of-window PTS reduces metric completeness/profile contribution; partial native alignment cannot produce `fluid_precise`/`fluid_tracker` confidence 1.0; independent valid metrics survive as limited.

**Implement:** one quality projection from artifact limitations/coverage into metrics, diagnosis and profile Gate. Do not add new diagnosis labels unless existing `unclassified/unavailable` cannot express the result.

**Verify:** visual/analyzer/worker/profile/diagnosis suites.

**Stop rule:** one bad frame invalidates unrelated evidence, or Coach recomputes metrics.

## Task 6 - Recover stale uploads and bound finalizer QoS

**Allowed files:** `webapp/backend/app.py`, `webapp/backend/routes.py`, `webapp/backend/queue.py`, `webapp/backend/desktop_runtime.py`, `webapp/backend/kovaak_capture_finalizer.py`, focused tests.

**Tests first:** crash after uploading row/workspace creation recovers on startup and unblocks owner; temp upload publishes atomically; active capture permits at most one heavy finalizer; exit can drain without starving shutdown.

**Implement:** managed-temp upload + startup stale reconciliation; bounded finalizer scheduler with capture-aware concurrency. Reuse existing tombstone/workspace safety helpers.

**Verify:** routes/queue/runtime/finalizer/reconciliation tests.

**Stop rule:** delete terminal Analysis, scan arbitrary directories, or silently discard recoverable Run evidence.

## Task 7 - Make long-term Storage/History bounded and complete

**Allowed files:** `webapp/backend/queue.py`, `webapp/backend/kovaak_run_store.py`, `webapp/backend/workspace.py`, `webapp/backend/routes.py`, `webapp/backend/schemas.py`, DB migration/tests.

**Tests first:** cursor pagination stability; `(user_id,kovaak_run_id)` query plan/index; orphan/recovery bytes included as their own category; no synchronous recursive walk on API event loop.

**Implement:** bounded cursor APIs and off-thread/cached accounting using existing DTO evolution rules; recovery data remains manually removable without TTL.

**Verify:** history/storage/Run/routes/migration suites and large synthetic listing.

**Stop rule:** break current readers without compatibility, auto-delete orphan data, or add a background service when an off-thread bounded call suffices.

## Task 8 - Align Knowledge validators and strict family retrieval

**Allowed files:** `kovaak_tracker/coach/knowledge_registry.py`, `webapp/coach-runtime/src/knowledge-registry.ts`, `webapp/coach-runtime/src/knowledge-tools.ts`, `webapp/coach-runtime/src/product-command-tools.ts`, parity tests/fixtures.

**Tests first:** shared malformed duplicate-section corpus has Python/TS accept/reject parity; default query excludes other families; explicit transfer mode returns only eligible general/transfer entries with scope; TS rejects password/secret before bridge fetch; parity works from package and repo cwd.

**Implement:** matching uniqueness validation and server-derived family filter. Preserve independent validation in both runtimes.

**Verify:** Python registry tests and all Coach Node tests.

**Stop rule:** merge runtimes, trust model-supplied family, or rewrite valid canonical assets without necessity.

## Task 9 - Remove proven post-analysis waste

**Allowed files:** `kovaak_tracker/native_flicking_analysis.py`, `kovaak_tracker/visual_signals.py`, `webapp/backend/evidence_store.py`, `webapp/backend/worker.py`, performance/focused tests.

**Tests first:** metric/artifact golden equality; 100k Raw and 60/300-second visual budgets; source-change TOCTOU tests retain pre-CV and pre-commit guards.

**Implement:** remove duplicate trajectory derivation; use streaming/numeric projection only where output is exactly equivalent; remove only adjacent redundant MP4 hash proven unnecessary.

**Verify:** golden fixtures plus wall/RSS/artifact-size benchmark recorded in assessment.

**Stop rule:** alter metric values, downsample canonical evidence, or optimize solely from file length.

## Task 10 - Close public result and snapshot contracts

**Allowed files:** `webapp/backend/contracts.py`, `webapp/backend/schemas.py`, `webapp/backend/queue.py`, `webapp/backend/routes.py`, `webapp/backend/coach_commands.py`, contract/queue/route tests.

**Tests first:** v2 unknown private/raw/secret fields cannot enter Session API; error details are projected; claim/get/retry all reject non-object snapshots consistently; evidence segment projection uses a public helper.

**Implement:** explicit public projection/allowlist and shared dict-only snapshot decoder. Preserve internal persistence needed by worker, stable refs and legacy supported readers.

**Verify:** contracts/queue/routes/Coach command suites.

**Stop rule:** silently coerce unsupported schema versions or remove defense-in-depth validators.

## Task 11 - Bind Coach sidecar and preview trust

**Allowed files:** `webapp/frontend/src-tauri/src/runtime.rs`, `webapp/backend/config.py`, `webapp/backend/coach_runtime.py`, `webapp/backend/auth.py`, `webapp/backend/desktop_runtime.py`, `webapp/coach-runtime/start-sidecar.ts`, `webapp/coach-runtime/src/sidecar-server.ts`, focused tests/config.

**Tests first:** random-port child sidecar capability required on secret-bearing routes; wrong/stale listener receives no credential/token; process tree exits together; non-loopback preview without trusted proxy refuses startup; loopback dev remains usable.

**Implement:** Tauri-owned sidecar child, one-time capability and dynamic loopback URL; fail-closed preview startup. Never log capability/credential.

**Verify:** Rust runtime, Python Coach/auth/desktop tests and Node sidecar tests.

**Stop rule:** fixed secret/port, passing Desktop token where sidecar capability suffices, or requiring a cloud identity service.

## Task 12 - Align frontend adapters with public DTOs

**Allowed files:** `webapp/frontend/lib/api.ts`, `webapp/frontend/lib/types.ts`, `webapp/frontend/lib/contracts.ts`, adapter tests only.

**Tests first:** Storage categories, uploading/recovery state, Provider status, EvidenceSegment playback, training plan/execution/retest and capture command shapes compile and parse from representative backend fixtures.

**Implement:** adapter/types only; no product routes, pages, components or prototype restoration.

**Verify:** frontend type-check/tests/build if build does not require missing routes.

**Stop rule:** create `app/`/`pages/`, redesign UI/UX, or duplicate backend business logic.

## Task 13 - Add reproducible toolchain and CI Gate

**Allowed files:** `.github/workflows/**`, Python/toolchain dependency metadata, `webapp/frontend/package.json`, `docs/DEVELOPMENT.md`, required lock artifacts.

**Tests first:** validate workflow syntax/config locally where possible; prove CI DB/temp paths are isolated and no secret is required.

**Implement:** hosted Linux Python/Coach/frontend jobs and Windows MSVC locked checks; declare supported toolchains; reproducible Python dependency input and audit commands. Do not auto-fix dependency versions without test evidence.

**Verify:** local equivalents for every job, dependency audit reports recorded without claiming unrun remote CI success.

**Stop rule:** introduce production secrets, upload user artifacts, or claim signing/hardware coverage.

## Task 14 - Reconcile losing evidence revisions

**Allowed files:** `webapp/backend/evidence_store.py`, `webapp/backend/worker.py`, focused evidence/worker tests.

**Tests first:** lease loss after publish keeps the winning referenced revision and removes only the losing unreferenced revision; deletion/accounting remain correct.

**Implement:** reachability-aware compensation scoped to one Analysis workspace. Preserve immutable winning revisions.

**Verify:** evidence/worker/lease/deletion tests.

**Stop rule:** broad orphan scan or deletion based only on filename/age.

## Task 15 - Correct project facts and close assessment

**Allowed files:** `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, `docs/PROGRESS.md`, `docs/README.md`, this plan/index, assessment. PRD and UI/UX are read-only.

**Tests first:** link/status searches and current test evidence collected from Tasks 1-14.

**Implement:** reopen invalid backend closeout statements, distinguish dirty-worktree vs committed facts, update timestamps/commands, record remaining external Gates. Do not rewrite product scope.

**Verify:** links exist, `git diff --check`, AGENTS/CLAUDE byte equality, final Git status.

**Stop rule:** modify PRD/UIUX decisions, claim hardware/remote CI/signing/legal completion, or copy volatile test matrices into stable Architecture.

## 4. Final acceptance and commit batches

主审只有在所有可执行 Task verified、剩余项都有外部 Gate 后才能提交：

1. Test/data transaction safety.
2. Capture/recovery/evidence quality.
3. Analysis/storage performance and reliability.
4. Coach/public contracts/security.
5. Frontend adapters/toolchain/CI.
6. Product-state documentation and assessment.

每批 commit 前运行该批 focused tests 与 `git diff --check`；最后运行安全的全量 Python/Coach/frontend/MSVC matrix。不得把失败、未运行或 hardware-only Gate 写成通过。
