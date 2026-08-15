# Aiming Cookie Current Progress

> Updated: 2026-08-16. This is a current implementation snapshot, not a product or architecture source. Earlier detailed status is retained in [`archive/history/2026-08-10-progress-prelaunch-history.md`](archive/history/2026-08-10-progress-prelaunch-history.md).

## Current Product Direction

- First activation requires a tested Provider plus enabled Windows Raw Input and KovaaK-window capture. The normal product surfaces are Coach, History, and Settings.
- Coach automatically selects the strongest valid Run tier: `multimodal`, then `input_native`, then `video_fallback`. A Run with no valid tier is not a normal History record; Coach explains the failure and repair path.
- Pre-install KovaaK Stats/Performance files are not imported or displayed. Optional KovaaK score linking remains onboarding context only.

## Implementation Status

- **Architecture rewrite complete**（SQLite → JSON 文件）：canonical 数据以 JSON 文件直接保存在 `DATA_ROOT` 下——`runs/{id}/meta.json`（KovaaKRun）、`sessions/{id}.json`（Analysis Session/job）、`analyses/{session_id}/`（渐进式披露）、`training/`、`config/`、`profile.json`。Coach 用 Pi `JsonlSessionRepo` 把对话持久化为 `conversations/{id}.jsonl`，并直接读写文件系统；SQLite、Python `coach_*` 代理层与 context-attach 机制已移除。
- Documentation has been realigned with the above contract; the retired OpenDesign handoff and historical superpowers materials are reference-only.
- Backend automatic tier selection, Run readiness, server-side Analysis tier selection, first-launch routing, and mandatory onboarding UI are implemented and covered by the focused validation below.
- 场景分派已泛化（2026-08-15 决策）：拆除 exact-hash 白名单，任何场景按多级识别进入大类管线；场景分类记忆持久化于 `config/scenario-overrides.json`。该批改动在 `feat/capture-generalization-knowledge-2026-08-15` 分支上，尚未合并 main。
- Coach 能力实测（2026-08-15）：standalone CLI 直连真实 Provider 逐项验证，核心能力全部通过，实机发现并修复 3 个 bug（场景记忆哈希位数、重分析视频冻结幂等、测试环境令牌）；全量回归零失败。完整记录见 [`archive/history/2026-08-15-coach-capability-live-test.md`](archive/history/2026-08-15-coach-capability-live-test.md)。
- Desktop Coach requests now use the Node sidecar directly. Python remains the local Analysis API/ingestion/worker runtime; it is not a desktop Coach request proxy.
- Real Tauri Provider, KovaaK field capture, hardware load, packaged installer/signing/updater/download, clean-machine onboarding, and cross-vendor capture validation remain release gates.

## Verification

以下数字为 2026-08-13 会话的结果，早于 2026-08-15 批次；2026-08-16 在 main 工作树上的一次重跑见 2026-08-15 会话记录末尾（不构成对该分支的验证）。

- Python full suite: 1154 passed, 5 skipped; the backend suite `pytest webapp/tests` passed 635, 3 skipped. The architecture rewrite removed the SQLite / `coach_*` proxy-era tests.
- Coach runtime TypeScript suite: 152 tests, 150 passed, 2 skipped; Node native Analysis covers all three input tiers, Python worker v3 snapshot reads, cleanup, and auto-discovered KovaaK path derivation.
- Frontend unit/contracts: 179 passed; frontend type-check passed.
- Rust/Tauri MSVC: fmt, check, clippy passed; tests 93 passed, 0 failed, 7 field-only tests ignored.
- `desktop-coach-provider.spec.ts` now exercises the product UI and observes `/v1/agent-runs`, but the real Provider field test was not rerun in this cleanup. Its skip is not counted as passing validation.
- `git diff --check` passed. Full Python, production browser Playwright, real Tauri, real KovaaK, hardware, and Provider field checks remain to be reported separately if run.

## 2026-08-15 Session Changes

实机驱动的一批修复与泛化改造，位于分支 `feat/capture-generalization-knowledge-2026-08-15`（commits `e45c5c7`、`8c6ddb6`、`07eb136`，领先 main 3 个提交，已推送远端、未合并）：

- **采集链路修复（实机复现并修复）**：Rust 侧退出时等待在途导出连接（根除 WinError 10053）、帧队列 8→32、视频缺口 100ms 容差；Python 侧赛后残留正确切分（窗口内全收/窗外剔除）、watcher 重试上限、视频三路径失败不再连坐 Raw trace、45 秒 release 死锁、exportReplay 客户端重试环。
- **场景分派泛化**：拆除 4 场景 exact-hash 白名单，任何场景按多级识别进入大类管线；新增开火模式判定器（追踪按住 vs 点击点射）；分析完成且 Provider 可用时自动开讲一次；baseline 档挂视频回放与文案纠偏。
- **场景分类记忆**：`config/scenario-overrides.json` + `scenario_memory.set` native 写命令 + 用户纠正后重分析闭环。
- **知识库 v7**：registry v7 补挂基础档指标标签（decel_frac 等）；检索 metric 归一化（bare / family / `metric:` 三形态命中）。
- **guided teaching 重建**：`teaching/session.json`（`coach_teaching_session.v1`）+ `teaching` skill + `teaching_session.update` native 写命令，替代 per-run TeachingTurnContract 快照。
- **架构重写审计 14 项修复**：视频透传、幽灵命令、重试去重、原子写、子进程超时、长回复截断等；另有 Steam 成绩查询接通、Pi skill 加载器 Windows 路径修复、话术收敛与死代码清理。
- **前端 Coach-first IA 重写**：既有 WIP 与本批改动交织，随 `8c6ddb6` 一并提交（AppShell/History/Settings/SessionRail 等大面积改动，`components/task3` 下架）。
- **实机后续修复（`07eb136`）**：`scenario_memory.set` 哈希校验位数写错（64→32，真实场景哈希为 32 位 MD5）；重分析已分析过的 Run 时视频冻结 `os.link` 非幂等；新增 `scripts/coach-cli.mjs` 命令行 Coach 客户端。

验证状态：本批以真实 KovaaK 实机会话驱动发现并修复问题；分支本身尚未在干净工作树上重跑统一套件。2026-08-16 的一次重跑因会话中工作树被外部切到 main（445fc5a），全部数字归属 main，**不构成对本分支的验证**：Python 全量 1109 passed / 5 skipped（与 2026-08-13 的差值由 4c0667f 迁移删除 provider 测试解释）；coach-runtime focused 170 tests（168 passed / 2 skipped）；frontend unit+contracts 170 passed / 0 failed、type-check 干净；Rust MSVC test 93 passed / 0 failed / 7 ignored、clippy `-D warnings` 干净；**main 的 `cargo fmt --check` 失败**（`runtime.rs` 多处格式偏差，445fc5a/4c0667f 引入，需 `cargo fmt` 修复或随本分支合并覆盖）。另 main 缺少 e45c5c7 新增的 `app-data/`、`viscose-youtube/` ignore 条目，合并前 main 工作树会显示这些目录为未跟踪。未运行：frontend E2E（`test:e2e`）、production build、Pi packages/ai workspace 测试、compileall。工作区另有 4 张未提交的 Playwright 截图基线（`screenshots.spec.ts` 的 `toHaveScreenshot` 基线，Coach-first IA 重写后生成）与根目录 `logo.jfif`，去向待定。

## 2026-08-13 Session Changes

- Completed the authorized SQLite → JSON-file architecture rewrite: removed `db.py`/SQLite, the Python `coach_*` proxy modules, and the context-attach mechanism; Coach now reads/writes `DATA_ROOT` directly and persists conversations via Pi `JsonlSessionRepo`.
- Made Analysis Session reservation atomic across Python upload/import and Node `analysis.create_from_run`, with failed input setup removing the reservation and workspace.
- Implemented Node-native Run-to-Analysis input freezing with `multimodal > input_native > video_fallback`, canonical v3 snapshots, worker-readable fingerprints, scenario resolution, and auto-discovered KovaaK path reuse.
- Fixed Agent Run retry message reuse, Provider-wait recovery, TeachingSession stale-CAS handling, canonical context projection, Python-compatible context dedupe keys, and duplicate Agent Run readers.
- Kept the desktop Coach product adapter on Node sidecar routes and removed the unused frontend Python soft-start adapter. Browser-only fallback and Python Analysis routes remain compatibility surfaces.
- Hardened frontend sidecar reconnection, stale batch workflows, context clearing, frameless window controls, and SessionRail contract preservation.
- Added product-path Provider E2E coverage without copying credentials from a developer DB; the real Provider field run remains open.

This validation does not prove packaged release readiness. It does not cover installer packaging, signing, updater, clean-machine onboarding, real KovaaK four-source field capture, long-running hardware load, or cross-vendor GPU behavior.

## 2026-08-11 Session Changes

Full-codebase audit completed across 5 subsystems with ~70 findings. Key fixes applied this session:

- **Backend deduplication**: monolithic `worker.py` / `coach_commands.py` / `kovaak_run_store.py` split into focused modules (source validation, family analysis, visual producers, run projection, snapshot codec, context refs, confirmations, guidance, etc.).
- **v0 turn schema cleanup**: unified to a single v1 turn contract path; removed obsolete v0 multi-path branching in `teaching-policy.ts` and `turn.ts`.
- **v3 diagnosis context injection fix**: Coach can now see analysis data (diagnosis, events, metrics) through the evidence bridge; previously the context was assembled but not reaching the LLM turn.
- **Metric localization**: `metric_definitions.py` is now the single source of truth for metric display names (Chinese); frontend and backend both consume it.
- **Coach tool calling fix**: product command tools are now retryable and inject context correctly; `teaching_session.update` is registered as a product command.
- **Video evidence navigation**: added 2-second seek padding so clicking an evidence segment lands slightly before the event, not after.
- **FOV/DPI/sensitivity KeyError tolerance**: missing optional fields no longer crash analysis or frontend rendering.
- **Codex review regression fixes**: schema synchronization between frontend contracts and backend DTOs; deletion confirmation flow restored.
