# KovaaK Runs + Windows Raw Input — 可执行施工图

> **状态：completed（2026-07-12，Task 2–3）**
> **点点授权：** Task 2 与 Task 3 都执行，严格按顺序完成和验证。
> **上游来源：** RefleK’s GPL-3.0 run ingestion、process watcher、Raw Input tracker 与 trace encoding。

## Frozen decisions

1. `KovaaKRun` 是独立于 Analysis Session 的本地记录；没有视频也能存在。未来 Analysis Session 只能引用 run，不反向拥有 run。
2. `KovaaKRun` canonical metadata 存在本地 SQLite；KovaaK 原始 Stats/Performance 文件保持用户所有，本轮只记录绝对路径和解析摘要，不删除、不搬迁源文件。
3. Run source key 使用规范化 Stats/Performance stem；同一用户下唯一，后到的配对文件执行幂等补全。
4. Task 2 只提供 Desktop-local API；不改变 Web 身份模型，不做前端 UI、筛选、删除或云同步。
5. Raw Input 第一版 Windows-only，实现在 Tauri Rust native layer；非 Windows 返回 unsupported，不引入 Go/Wails sidecar。
6. Raw Input 默认关闭；可由环境变量或 Tauri command 明确启用。只采集相对鼠标 `dx/dy`、时间戳和鼠标按钮，不采集键盘或桌面绝对坐标。
7. Raw Input 只在检测到 KovaaK 进程时进入 ring buffer；默认最多保留 10 分钟；禁用时清空未关联 buffer。
8. Rust 通过 `DATA_ROOT/raw-input/buffer.bin` 原子快照与 Python local runtime 交换数据；格式版本化。Python 按 Performance `challenge_start_utc` 与 challenge time limit 截取并写入 `DATA_ROOT/runs/{run_id}/mouse_trace.bin`。
9. trace 永远本地，不进入云端、Coach 请求或普通日志；Task 3 不新增上传 API。
10. 运行中若需要更改现有 Analysis Result、Coach、前端 IA 或云端合同，立即停止。

## Task 2 — KovaaKRun + auto ingestion

### Allowed files

- `docs/superpowers/plans/README.md`
- 本 plan
- `webapp/backend/config.py`
- `webapp/backend/db.py`
- `webapp/backend/kovaak_ingest.py`
- `webapp/backend/kovaak_run_store.py`（新建）
- `webapp/backend/desktop_runtime.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/tests/test_db.py`
- `webapp/tests/test_kovaak_runs.py`（新建）
- `webapp/tests/test_desktop_runtime.py`

### Tests first

- v3 → v4 migration 创建 `kovaak_runs`，重复 init 幂等；
- source key 幂等 upsert，Stats 先到、Performance 后到可以补全；
- list/get 只返回 Desktop local profile 的 runs；
- watcher callback 通过 event-loop-safe bridge upsert run；
- runtime 启停 watcher，不把 watcher 异常变成 API/worker 崩溃；
- `GET /api/kovaak-runs` 与 `GET /api/kovaak-runs/{id}` 受 Desktop token 保护。

### Stop rule

- 必须修改 Analysis Session schema 或 History session API；
- 需要复制或删除用户源文件；
- 需要前端参与才能验证 ingestion；
- watcher 生命周期无法与 desktop runtime 安全绑定。

## Task 3 — Windows Raw Input + run trace pairing

### Allowed files

- 本 plan
- `webapp/frontend/src-tauri/Cargo.toml`
- `webapp/frontend/src-tauri/Cargo.lock`
- `webapp/frontend/src-tauri/src/lib.rs`
- `webapp/frontend/src-tauri/src/raw_input.rs`（新建）
- `webapp/frontend/src-tauri/src/runtime.rs`
- `webapp/backend/config.py`
- `webapp/backend/kovaak_ingest.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/desktop_runtime.py`
- `webapp/tests/test_kovaak_runs.py`

### Tests first

- 跨平台纯 Rust ring buffer：保留窗口、顺序、清空、按钮状态；
- snapshot codec：magic/version/record count，截断和未知版本拒绝；
- 非 Windows backend 明确 unsupported；
- Python 读取 snapshot 并按 `[start_ms, end_ms]` 截取；
- trace 与 Run 配对后更新 `mouse_trace_path`；无 Performance 时间锚时不伪造配对；
- disable 清空 buffer；process gate false 时不记录。

### Stop rule

- Windows Raw Input 需要管理员权限或全局键盘 hook；
- 需要上传 trace 或暴露普通 Web API；
- 无法在现有 Tauri lifecycle 中停止线程；
- 当前机器无法验证 Windows backend 时，必须把 Windows-only 未验证风险写入报告，不宣称发布完成。

## Verify

```bash
.venv/bin/pytest tests webapp/tests -q
cd webapp/frontend/src-tauri
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cd ../../../..
cmp -s AGENTS.md CLAUDE.md
git diff --check
git status --short
```

## 完成定义

- Task 2：Desktop runtime 自动发现并幂等保存 KovaaKRun，最小 API 可读取；
- Task 3：Windows Tauri 可按 opt-in/process gate 采集 Raw Input，ring buffer 快照可由 Python 截取并关联 Run；
- 非 Windows 明确降级；
- Python 与当前平台 Rust 回归通过；
- Windows backend 若未在 Windows 实机验证，明确保留发布 Gate。
