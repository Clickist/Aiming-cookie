import sys

import pytest


SUPPORTED_PYTHON = (3, 11)


def pytest_sessionstart(session: pytest.Session) -> None:
    if (
        sys.implementation.name == "cpython"
        and sys.version_info[:2] == SUPPORTED_PYTHON
    ):
        return
    raise pytest.UsageError(
        "Aiming Cookie tests require CPython 3.11.x. "
        "Create the repository .venv with `py -3.11 -m venv .venv` on Windows "
        "or `python3.11 -m venv .venv` on macOS/Linux, then run "
        "`.\\.venv\\Scripts\\python.exe -m pytest` on Windows or "
        "`.venv/bin/python -m pytest` on macOS/Linux."
    )
