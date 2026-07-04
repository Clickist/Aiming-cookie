# Aiming Cookie Webapp

## 后端开发(切片 1)

本地开发用 **SQLite**(零配置,无需 Docker)。部署时换 Postgres(spec §3)。

```bash
pip install -r webapp/requirements.txt

# 初始化 schema(首次;自动建 SQLite 文件)
python -c "import asyncio; from webapp.backend.db import init_schema; asyncio.run(init_schema())"

# 启 API
uvicorn webapp.backend.app:app --reload

# 另开终端启 Worker
python -m webapp.backend.worker

# 跑测试
pytest webapp/tests/ -v
```

环境变量(可选):
- `DATABASE_URL`:默认 `sqlite+aiosqlite:///./aiming_cookie_dev.db`(本地)
- `LLM_PROVIDER`:默认 `deepseek`
- `LLM_DAILY_BUDGET_CNY`:默认 `1.0`
