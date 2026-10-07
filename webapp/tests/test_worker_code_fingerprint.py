from __future__ import annotations

import hashlib
from pathlib import Path

from webapp.backend import worker


def _expected_digest(entries: list[tuple[str, Path]]) -> str:
    parts = [
        f"{name}:{int(path.stat().st_mtime)}:{path.stat().st_size}"
        for name, path in entries
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def test_fingerprint_stable_for_same_input(tmp_path):
    a = tmp_path / "a.py"
    a.write_bytes(b"alpha")
    b = tmp_path / "b.py"
    b.write_bytes(b"beta")
    entries = [("a.py", a), ("b.py", b)]

    digest1, manifest1 = worker.analysis_code_fingerprint(entries)
    digest2, manifest2 = worker.analysis_code_fingerprint(entries)

    assert digest1 == digest2 == _expected_digest(entries)
    assert manifest1 == manifest2
    assert len(digest1) == 12


def test_fingerprint_changes_when_content_changes(tmp_path):
    path = tmp_path / "a.py"
    path.write_bytes(b"short")
    digest_before, _ = worker.analysis_code_fingerprint([("a.py", path)])

    path.write_bytes(b"longer content")  # size 变化，指纹必变
    digest_after, _ = worker.analysis_code_fingerprint([("a.py", path)])

    assert digest_before != digest_after


def test_fingerprint_skips_missing_files(tmp_path):
    existing = tmp_path / "exists.py"
    existing.write_bytes(b"data")
    missing = tmp_path / "missing.py"
    entries = [("exists.py", existing), ("missing.py", missing)]

    digest, manifest = worker.analysis_code_fingerprint(entries)

    assert manifest == [
        f"exists.py:{int(existing.stat().st_mtime)}:{existing.stat().st_size}"
    ]
    assert digest == _expected_digest([("exists.py", existing)])


def test_fingerprint_all_missing_yields_empty_hash(tmp_path):
    digest, manifest = worker.analysis_code_fingerprint(
        [("x.py", tmp_path / "x.py")]
    )

    assert manifest == []
    assert digest == hashlib.sha256(b"").hexdigest()[:12]


def test_default_fingerprint_covers_analysis_chain():
    digest, manifest = worker._analysis_code_fingerprint()

    assert len(digest) == 12
    assert len(manifest) == len(worker.ANALYSIS_CODE_FINGERPRINT_FILES)
    for rel in worker.ANALYSIS_CODE_FINGERPRINT_FILES:
        assert any(entry.startswith(f"{rel}:") for entry in manifest)
    # 进程内缓存：同一进程两次调用返回同值（同 hash 即同代码的前提）。
    assert worker._analysis_code_fingerprint() == (digest, manifest)
