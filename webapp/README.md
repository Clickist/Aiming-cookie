# Aiming Cookie Webapp / Runtime

> 本页只提供 `webapp/` 局部开发入口。仓库级环境、Desktop 开发、完整验证矩阵和代码地图以 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) 为准；产品、架构与进度分别看 PRD、Architecture 和 Progress。

## 快速启动

先按 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) 使用 CPython 3.11 创建仓库 `.venv`。macOS / Linux 从仓库根目录启动 Coach sidecar 与 API：

```bash
.venv/bin/python -m pip install -r webapp/requirements.txt
./scripts/dev-up.sh
```

另开终端启动 worker 与前端：

```bash
.venv/bin/python -m webapp.backend.worker
cd webapp/frontend
npm install
npm run dev
```

只启动 API：

```bash
.venv/bin/python -m uvicorn webapp.backend.app:app --reload
```

Windows PowerShell 使用对应的 venv 解释器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt
.\.venv\Scripts\python.exe -m webapp.backend.worker
.\.venv\Scripts\python.exe -m uvicorn webapp.backend.app:app --reload
```

Desktop/Tauri 开发命令见 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md)。

## 测试

```bash
.venv/bin/python -m pytest webapp/tests/ -v
cd webapp/frontend && npm run type-check
```

Windows PowerShell 的 Python 测试入口为 `.\.venv\Scripts\python.exe -m pytest webapp\tests -v`。

需要 build、Rust 或 Desktop runtime 验证时，使用 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) 中的命令，避免在多个 README 维护不同版本。

## 常用环境变量

- `DATABASE_URL`：本地默认 SQLite；具体默认值以当前 backend 配置为准；
- `LLM_PROVIDER` / `LLM_DAILY_BUDGET_CNY`：仅供旧环境兼容；active Coach/Analysis narration 使用本地 owner-scoped Provider profile，不使用固定 DeepSeek/CNY gate；
- `COACH_RUNTIME`：`pi` 或兼容 runtime；
- `COACH_SIDECAR_URL`：Coach sidecar 地址。

环境变量的真实默认值和支持范围以当前代码、测试与示例环境文件为准，本页不冻结版本化配置。
