from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 让 `import webapp` 可被测试发现(项目根加入 sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest_asyncio

# 测试 DB(隔离)—— 必须在 import webapp 模块前覆盖外部环境。
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="aiming_cookie_test_")).resolve()
TEST_DB_PATH = (TEST_DATA_ROOT / "aiming_cookie_test.db").resolve()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["VIDEO_TMP_DIR"] = str(TEST_DATA_ROOT)
os.environ["DATA_ROOT"] = str(TEST_DATA_ROOT)
# Unit tests must never auto-discover or read a developer's live KovaaK install.
os.environ["KOVAAK_INSTALL_DIR"] = str(TEST_DATA_ROOT / "missing-kovaak")


def _clean_test_data_root() -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        TEST_DATA_ROOT.parent != temp_root
        or not TEST_DATA_ROOT.name.startswith("aiming_cookie_test_")
    ):
        raise RuntimeError("refusing to clean a non-test data root")
    if TEST_DATA_ROOT.is_symlink():
        TEST_DATA_ROOT.unlink()
    elif TEST_DATA_ROOT.exists():
        shutil.rmtree(TEST_DATA_ROOT)


@pytest_asyncio.fixture(autouse=True)
async def isolated_db():
    """每个测试前后重置 test DB 和专用测试数据目录。"""
    from webapp.backend import config, db

    if Path(config.DB_PATH).resolve() != TEST_DB_PATH:
        raise RuntimeError("test database path was not isolated before backend import")
    await db.close_conn()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    _clean_test_data_root()
    TEST_DATA_ROOT.mkdir(parents=True)
    await db.init_schema()
    try:
        yield
    finally:
        await db.close_conn()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        _clean_test_data_root()
