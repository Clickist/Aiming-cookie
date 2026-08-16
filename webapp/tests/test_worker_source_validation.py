"""Frozen-source validation contract tests.

Covers the 2026-08-16 deep-test finding (Bug 7): on some filesystems the
observed mtime_ns drifts sub-microsecond between windows while sha256 and
size stay identical, so mtime drift must not fail an otherwise stable source.
"""

import hashlib

import pytest

from webapp.backend.worker_source_validation import (
    SourceSnapshotChangedError,
    _read_frozen_source_bytes,
)


def _source(path, sha256=None, size=None, mtime_ns=None):
    stat = path.stat()
    return {
        "path": str(path),
        "fingerprint": {
            "sha256": sha256 if sha256 is not None else hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": size if size is not None else stat.st_size,
            "mtime_ns": mtime_ns if mtime_ns is not None else stat.st_mtime_ns,
        },
    }


def test_same_sha_and_size_pass_despite_mtime_drift(tmp_path):
    path = tmp_path / "stats.csv"
    path.write_bytes(b"stable-content")
    source = _source(path, mtime_ns=path.stat().st_mtime_ns + 700)
    assert _read_frozen_source_bytes("stats", source) == b"stable-content"


def test_changed_content_still_fails_validation(tmp_path):
    path = tmp_path / "stats.csv"
    path.write_bytes(b"new-content")
    source = _source(path, sha256=hashlib.sha256(b"old-content").hexdigest())
    with pytest.raises(SourceSnapshotChangedError, match="revision changed"):
        _read_frozen_source_bytes("stats", source)


def test_size_mismatch_still_fails_validation(tmp_path):
    path = tmp_path / "stats.csv"
    path.write_bytes(b"content")
    source = _source(path, size=path.stat().st_size + 1)
    with pytest.raises(SourceSnapshotChangedError, match="revision changed"):
        _read_frozen_source_bytes("stats", source)
