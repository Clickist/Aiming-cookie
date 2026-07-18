from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 让 `import webapp` 可被测试发现(项目根加入 sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest_asyncio

# 测试 DB(隔离)—— 在 import webapp 模块前设
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./aiming_cookie_test.db")
TEST_DATA_ROOT = Path(tempfile.gettempdir()).resolve() / "aiming_cookie_test"
os.environ["VIDEO_TMP_DIR"] = str(TEST_DATA_ROOT)
os.environ["DATA_ROOT"] = str(TEST_DATA_ROOT)
# Unit tests must never auto-discover or read a developer's live KovaaK install.
os.environ["KOVAAK_INSTALL_DIR"] = str(TEST_DATA_ROOT / "missing-kovaak")


def _clean_test_data_root() -> None:
    if TEST_DATA_ROOT.is_symlink():
        TEST_DATA_ROOT.unlink()
    elif TEST_DATA_ROOT.exists():
        shutil.rmtree(TEST_DATA_ROOT)


@pytest_asyncio.fixture(autouse=True)
async def isolated_db():
    """每个测试前后重置 test DB 和专用测试数据目录。"""
    from webapp.backend import db

    _clean_test_data_root()
    TEST_DATA_ROOT.mkdir(parents=True)
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    await db.close_conn()
    await db.init_schema()
    try:
        yield
    finally:
        await db.close_conn()
        _clean_test_data_root()
