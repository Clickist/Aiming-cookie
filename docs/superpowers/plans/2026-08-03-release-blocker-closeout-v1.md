# Release Blocker Closeout v1 Implementation Plan

> **Status: active.** 点点于 2026-08-03 明确授权 Codex 自主推进当前发布审计中的第 1-4 项：修复 Tauri 编译、修复 Coach primary thread 并发创建、整理并分批提交已验证的前端收尾，以及运行真实 Web 联调 Gate。真实 KovaaK、高 polling-rate、Tracking `<=130s`、installer/signing/updater/download 不在本计划内。

**Goal:** 让当前提交重新具备可编译的 Tauri 壳、并发安全的 Coach 启动路径、可追溯的前端收尾提交和不依赖 Mock 的 Web 联调证据。

**Architecture:** 保留现有 Tauri command、SQLite store、正式前端和 FastAPI/worker 单一实现，不增加平行 runtime 或测试后门。每个 blocker 独立测试、独立提交；真实联调使用临时本地数据根和不存在的 KovaaK 路径，不接触用户产品数据库、Provider secret 或私人训练素材。

**Tech Stack:** Rust/Tauri 2、Python 3.11/aiosqlite/FastAPI、Next.js 16/React 19、Playwright。

---

## Task 1 - Restore the Tauri compile gate

### Allowed files

- `webapp/frontend/src-tauri/src/scenario_launch.rs`

### Tests first

1. 复现 `cargo fmt --check` 的格式差异。
2. 复现 `cargo check --locked --all-targets` 因 `scenario_open` 缺少 Tauri command 宏而失败。

### Implementation

1. 将现有受信任 `scenario_open` 函数声明为 Tauri command，不改变 allowlist、URI 构造或 Windows dispatch 行为。
2. 只格式化该 Rust 文件。

### Verification

```powershell
Push-Location webapp\frontend\src-tauri
cargo +stable-x86_64-pc-windows-msvc fmt --check
cargo +stable-x86_64-pc-windows-msvc check --locked --all-targets
cargo +stable-x86_64-pc-windows-msvc test --locked --all-targets
cargo +stable-x86_64-pc-windows-msvc clippy --locked --all-targets -- -D warnings
Pop-Location
```

### Stop rule

若修复需要放宽 ScenarioProfile allowlist、执行任意 Coach 生成的 URI 或修改 Tauri capability 权限，停止。

## Task 2 - Make primary Coach thread creation atomic

### Allowed files

- `webapp/backend/coach_store.py`
- `webapp/tests/test_coach_store.py`

### Tests first

1. 新增 8 路并发调用 `get_or_create_primary_thread()` 的回归测试。
2. 测试必须断言八次调用全部成功、返回同一 thread id、数据库只存在一行。
3. 先在当前实现上复现唯一约束失败。

### Implementation

用 SQLite `INSERT ... ON CONFLICT(user_id, kind) DO NOTHING` 加同事务回读替换 `SELECT -> INSERT` 竞争窗口；保留传入外部 connection 时不自行 commit 的既有合同。

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_store.py -q
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_desktop_runtime.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

### Stop rule

若需要 schema migration、改变 owner/thread 唯一性或把 retry 扩展到任意数据库错误，停止。

## Task 3 - Commit only verified frontend closeout changes

### Allowed files

- `webapp/frontend/components/kovaak/KovaaKConnectionPanel.tsx`
- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/components/task6/SettingsWorkspace.tsx`
- `webapp/frontend/components/task6/task6.css`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- `webapp/frontend/tests/task6-source.test.ts`

### Verification

```powershell
npm.cmd --prefix webapp\frontend test
npm.cmd --prefix webapp\frontend run type-check
npm.cmd --prefix webapp\frontend run build
git diff --check -- webapp/frontend
```

逐文件核对 staged diff。现有 `e2e/*-snapshots`、未跟踪 review 脚本、`.bak`、研究材料、`artifacts/**` 和 `.firecrawl/**` 不得随本 Task 提交。截图 baseline 只有在当前 E2E 通过且人工对照原稿后才能进入后续独立提交。

### Stop rule

若任一前端文件混入未验证产品范围、Mock DTO 发明、Provider/Steam identity 泄露或需靠更新 baseline 才能通过，保留未提交并报告。

## Task 4 - Run the real Web integration gate

### Allowed files

- 本 Task 默认只运行命令，不修改产品代码。

### Procedure

1. 使用临时 `DATABASE_URL` / `DATA_ROOT` 和不存在的 `KOVAAK_INSTALL_DIR` 启动真实 FastAPI、worker 与 Coach sidecar。
2. 以正常 API 模式启动 production Next，不设置 Mock 环境变量。
3. 验证 product-state、onboarding、Provider catalog、History、Tasks、Settings、Coach primary 和干净退出。
4. 运行不覆盖 screenshot baseline 的 production Browser E2E，并区分环境缺失、fixture 漂移和产品失败。

### Stop rule

若联调需要真实 Provider secret、私人 KovaaK 数据、修改稳定 DTO、创建产品测试后门或覆盖 screenshot baseline，停止并报告。任何必要代码修复必须先登记新的 Allowed files 和失败复现，不在本 Task 临场扩张。

## Closeout

完成 Task 1-4 后，更新本计划状态和 `docs/PROGRESS.md`，报告每个提交、当前验证结果、未闭合的 field/release Gate 与最终 `git status`。正式 release 继续保持 No-Go，直到 Roadmap 的真实 KovaaK、高 polling-rate、Tracking 时延和分发 Gate 分别闭合。
