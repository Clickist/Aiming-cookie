# Aiming Cookie 开发指南

本文件只维护本地开发、运行、测试和代码入口。产品范围看 [`PRD.md`](PRD.md)，架构边界看 [`ARCHITECTURE.md`](ARCHITECTURE.md)，当前状态看 [`PROGRESS.md`](PROGRESS.md)。

## 1. 环境与依赖

当前仓库包含 Python、Node.js/Next.js、Pi runtime 和 Rust/Tauri 组件。具体版本约束以各自依赖文件和 lockfile 为准，不在本文重复固定易过期的版本号。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r webapp/requirements.txt

cd webapp/frontend
npm install
```

桌面壳开发还需要本机 Rust toolchain 和 Tauri 2 的平台依赖。

## 2. 本地 Web 开发

推荐分三个终端启动：

```bash
# 终端 1：Coach sidecar + FastAPI API
source .venv/bin/activate
./scripts/dev-up.sh
```

```bash
# 终端 2：分析 worker
source .venv/bin/activate
python -m webapp.backend.worker
```

```bash
# 终端 3：Next.js 前端
cd webapp/frontend
npm run dev
```

`./scripts/dev-up.sh --help` 列出可配置端口和 Coach sidecar 环境变量。

## 3. Desktop 开发

Tauri 壳位于 `webapp/frontend/src-tauri/`。它负责启动本地 Python runtime，并向 WebView 提供本次启动的 loopback 连接信息。

```bash
cd webapp/frontend
npm run tauri dev
```

Desktop 的打包、签名、公证和更新链路尚未构成稳定发布流程；当前状态与阻塞以 `PROGRESS.md` 为准。

KovaaK 本地 ingestion 由 Desktop runtime 在启动时管理：

- Windows 默认从 KovaaK 安装目录推导 `FPSAimTrainer/stats` 与 `FPSAimTrainer/performances`；
- 可用 `KOVAAK_INSTALL_DIR`、`KOVAAK_STATS_DIR`、`KOVAAK_PERFORMANCE_DIR` 覆盖路径；
- `KOVAAK_WATCH_POLL_SECONDS` 控制 watcher 轮询间隔；
- Windows Raw Input 默认关闭，可用 `AIMING_COOKIE_RAW_INPUT_ENABLED=1` 做开发启动 opt-in，正式产品必须通过带说明的 UI 授权；
- `KovaaKRun` 由 `GET /api/kovaak-runs` 和 `GET /api/kovaak-runs/{id}` 读取，接口受 Desktop launch token 保护；
- Raw Input 只支持 Windows；macOS/Linux 开发环境必须验证 video fallback，不得把 unsupported 当成捕获成功。

## 4. 常用验证

按改动范围运行最小相关检查；以下命令是仓库当前可用的主要入口：

```bash
# Python 全量或相关测试
pytest
pytest webapp/tests

# Frontend
cd webapp/frontend
npm run type-check
npm test
npm run build

# Tauri / Rust
cd webapp/frontend/src-tauri
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cargo clippy --locked --all-targets -- -D warnings

# Windows-only Raw Input module condition check (host can be non-Windows;
# full Tauri packaging additionally requires Windows resource tooling/icons)
cargo check --tests --target x86_64-pc-windows-gnu
```

文档改动至少运行：

```bash
cmp -s AGENTS.md CLAUDE.md
git diff --check
git status --short
```

不要把历史测试数字写入本文；一次验证的实际结果写入 `PROGRESS.md` 或当前任务报告。

## 5. 代码入口

| 路径 | 入口或职责 |
|---|---|
| `kovaak_tracker/` | 领域分析、flicking/tracking 指标、确定性诊断与报告 |
| `webapp/backend/app.py` | FastAPI 应用入口 |
| `webapp/backend/routes.py` | HTTP 路由与产品 API |
| `webapp/backend/worker.py` | 异步分析 worker |
| `webapp/backend/desktop_runtime.py` | Tauri 管理的本地 API/worker 生命周期 |
| `webapp/backend/kovaak_ingest.py` | KovaaK Stats / Performance watcher 与稳定文件发现 |
| `webapp/backend/kovaak_run_store.py` | `KovaaKRun` 本地 SQLite upsert、snapshot 解码和 trace 配对 |
| `webapp/backend/contracts.py` | Web/Desktop 共享的分析合同 |
| `webapp/backend/coach_*` | Coach runtime、编排、服务和持久化 |
| `webapp/frontend/` | Next.js 前端、API client 和桌面适配层 |
| `webapp/frontend/src-tauri/` | Tauri shell、进程生命周期与 native commands |
| `webapp/frontend/src-tauri/src/raw_input.rs` | Windows Raw Input、process gate、ring buffer 和本地 snapshot |
| `third_party/pi/` | Coach runtime 使用的 Pi 源码基线 |
| `scripts/` | 开发启动和运行辅助脚本 |

## 6. 数据与本地文件

- 开发/测试数据目录由 backend 配置解析；Desktop 生产形态默认使用平台 App Data。
- 分析输入和 artifacts 的所有权、删除和生命周期由 `ARCHITECTURE.md` 定义，不能仅凭本文或脚本行为推导产品规则。
- `output/` 常含本地运行产物，不应因为文档或无关任务被重写或清理。
