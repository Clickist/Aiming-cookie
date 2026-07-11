# Aiming Cookie Webapp

## 后端开发(切片 1)

本地开发用 **SQLite**(零配置,无需 Docker)。部署时换 Postgres(spec §3)。

**推荐一键起 API + coach sidecar**（避免 `/coach` 每轮冷启动 Node）：

```bash
pip install -r webapp/requirements.txt

# 初始化 schema(首次;自动建 SQLite 文件)
python -c "import asyncio; from webapp.backend.db import init_schema; asyncio.run(init_schema())"

# 仓库根：sidecar 后台 + API 前台（Ctrl+C 会停 sidecar）
./scripts/dev-up.sh
```

仅启 API（无 sidecar，Pi 模式会走较慢路径）：

```bash
uvicorn webapp.backend.app:app --reload
```

另开终端：

```bash
python -m webapp.backend.worker
cd webapp/frontend && npm run dev
```

跑测试：

```bash
pytest webapp/tests/ -v
```

环境变量(可选):
- `DATABASE_URL`:默认 `sqlite+aiosqlite:///./aiming_cookie_dev.db`(本地)
- `LLM_PROVIDER`:默认 `deepseek`
- `LLM_DAILY_BUDGET_CNY`:默认 `1.0`
- `COACH_RUNTIME`: `pi`（默认）或 `python`
- `COACH_SIDECAR_URL`: 默认 `http://127.0.0.1:8765`（与 `dev-up.sh` / `run-coach-sidecar.sh` 一致）