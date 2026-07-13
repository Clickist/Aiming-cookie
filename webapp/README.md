# Aiming Cookie Webapp / Runtime

> 本页只提供 `webapp/` 局部开发入口。仓库级环境、Desktop 开发、完整验证矩阵和代码地图以 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) 为准；产品、架构与进度分别看 PRD、Architecture 和 Progress。

## 快速启动

从仓库根目录启动 Coach sidecar 与 API：

```bash
pip install -r webapp/requirements.txt
./scripts/dev-up.sh
```

另开终端启动 worker 与前端：

```bash
python -m webapp.backend.worker
cd webapp/frontend
npm install
npm run dev
```

只启动 API：

```bash
uvicorn webapp.backend.app:app --reload
```

Desktop/Tauri 开发命令见 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md)。

## 测试

```bash
pytest webapp/tests/ -v
cd webapp/frontend && npm run type-check
```

需要 build、Rust 或 Desktop runtime 验证时，使用 [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) 中的命令，避免在多个 README 维护不同版本。

## 常用环境变量

- `DATABASE_URL`：本地默认 SQLite；具体默认值以当前 backend 配置为准；
- `LLM_PROVIDER`：Coach LLM provider；
- `LLM_DAILY_BUDGET_CNY`：开发预算限制；
- `COACH_RUNTIME`：`pi` 或兼容 runtime；
- `COACH_SIDECAR_URL`：Coach sidecar 地址。

环境变量的真实默认值和支持范围以当前代码、测试与示例环境文件为准，本页不冻结版本化配置。
