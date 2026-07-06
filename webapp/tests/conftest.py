from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 让 `import webapp` 可被测试发现(项目根加入 sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest_asyncio

# 测试 DB(隔离)—— 在 import webapp 模块前设
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./aiming_cookie_test.db")
os.environ.setdefault(
    "VIDEO_TMP_DIR",
    str(Path(tempfile.gettempdir()) / "aiming_cookie_test"),
)


@pytest_asyncio.fixture(autouse=True)
async def isolated_db():
    """每个测试前:重置 test DB(sessions 表清空 + connection 重建)。"""
    from webapp.backend import db

    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    await db.close_conn()
    await db.init_schema()
    yield
    await db.close_conn()
