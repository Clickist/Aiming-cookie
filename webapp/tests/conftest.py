from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 让 `import webapp` 可被测试发现(项目根加入 sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest_asyncio

# 测试数据根(隔离)—— 必须在 import webapp 模块前覆盖外部环境。
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="aiming_cookie_test_")).resolve()
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
    """每个测试前后重置专用测试数据目录(文件存储无 DB)。"""
    from webapp.backend import config

    if Path(config.DATA_ROOT).resolve() != TEST_DATA_ROOT:
        raise RuntimeError("test data root was not isolated before backend import")
    _clean_test_data_root()
    TEST_DATA_ROOT.mkdir(parents=True)
    # queue 的 session 缓存靠 stat 指纹自愈，但每个测试用全新 DATA_ROOT，
    # 先清空避免上个测试的残留条目参与断言（也让缓存计数类断言确定）。
    from webapp.backend import queue as _queue
    _queue._SESSION_CACHE.clear()
    # kovaak_run_store 的 meta/轻缓存与存储台账校验表、workspace 工作区记账
    # 同理：进程级派生缓存，测试间必须清空保证断言确定。
    from webapp.backend import kovaak_run_store as _runs
    from webapp.backend import workspace as _workspace
    _runs._RUN_META_CACHE.clear()
    _runs._SESSION_LIGHT_CACHE.clear()
    _runs._LEDGER_VALIDATED_DIR_MTIME.clear()
    _workspace._WORKSPACE_SIZE_LEDGER.clear()
    try:
        yield
    finally:
        _clean_test_data_root()
