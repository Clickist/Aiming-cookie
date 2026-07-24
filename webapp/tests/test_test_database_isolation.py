import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def test_conftest_forces_an_isolated_temp_database(tmp_path: Path):
    sentinel = tmp_path / "external-sentinel.db"
    sentinel.write_bytes(b"external database must remain untouched")
    script = r'''
import importlib.util
import json
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
conftest_path = repo_root / "webapp" / "tests" / "conftest.py"
sys.path.insert(0, str(repo_root))
spec = importlib.util.spec_from_file_location("isolated_conftest", conftest_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    from webapp.backend import config

    print(json.dumps({
        "database_url": os.environ["DATABASE_URL"],
        "db_path": str(config.DB_PATH),
        "test_data_root": str(module.TEST_DATA_ROOT),
    }))
finally:
    module._clean_test_data_root()
'''
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{sentinel.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve().parents[2])],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    observed = json.loads(completed.stdout)
    isolated_db = Path(observed["db_path"])
    test_data_root = Path(observed["test_data_root"])

    assert sentinel.read_bytes() == b"external database must remain untouched"
    assert isolated_db.is_absolute()
    assert isolated_db.parent == test_data_root
    assert isolated_db != sentinel
    assert test_data_root.parent == Path(tempfile.gettempdir()).resolve()
    assert test_data_root.name.startswith("aiming_cookie_test_")
    assert observed["database_url"].endswith(isolated_db.as_posix())
