"""Frozen-source validation contract tests.

Covers the 2026-08-16 deep-test finding (Bug 7): on some filesystems the
observed mtime_ns drifts sub-microsecond between windows while sha256 and
size stay identical, so mtime drift must not fail an otherwise stable source.
"""

import hashlib
from pathlib import Path

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


# --- [perf P0-1 2026-10-11] baseline 定约 + stat 复核合同 ---

from webapp.backend.worker_source_validation import (
    _assert_managed_video_matches_snapshot_with_baseline,
    _establish_managed_video_baseline,
)


def _video_job(path, sha256=None, size=None, mtime_ns=None):
    stat = path.stat()
    return {
        "kovaak_run_id": 1,
        "video_path": str(path),
        "input_mode": "multimodal",
        "input_snapshot": {
            "sources": {
                "video": {
                    "fingerprint": {
                        "sha256": (
                            sha256
                            if sha256 is not None
                            else hashlib.sha256(path.read_bytes()).hexdigest()
                        ),
                        "size": size if size is not None else stat.st_size,
                        "mtime_ns": mtime_ns if mtime_ns is not None else stat.st_mtime_ns,
                    },
                },
            },
        },
    }


def test_baseline_established_from_full_hash(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video-bytes")
    job = _video_job(path)
    opens = []
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        opens.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    baseline = _establish_managed_video_baseline(job, "multimodal")
    assert baseline == (path.stat().st_size, path.stat().st_mtime_ns)
    reads_after_establish = len(opens)
    assert reads_after_establish >= 1
    # stat 命中基线：复核零文件读（旧实现此处再付一次全量哈希）。
    _assert_managed_video_matches_snapshot_with_baseline(job, "multimodal", baseline)
    assert len(opens) == reads_after_establish


def test_baseline_drift_falls_back_to_full_hash(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video-bytes")
    job = _video_job(path)
    baseline = _establish_managed_video_baseline(job, "multimodal")
    # 内容被改写（size/mtime 变化）→ stat 复核触发全量哈希并按合同拒绝。
    path.write_bytes(b"tampered-video-bytes")
    with pytest.raises(SourceSnapshotChangedError, match="revision changed"):
        _assert_managed_video_matches_snapshot_with_baseline(
            job, "multimodal", baseline,
        )


def test_baseline_missing_file_fails_validation(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video-bytes")
    job = _video_job(path)
    baseline = _establish_managed_video_baseline(job, "multimodal")
    path.unlink()
    with pytest.raises(SourceSnapshotChangedError, match="missing or unreadable"):
        _assert_managed_video_matches_snapshot_with_baseline(
            job, "multimodal", baseline,
        )


def test_baseline_none_contract_short_circuits(tmp_path):
    # telemetry_multimodal 无冻结视频别名：无合同，基线为 None 且复核零开销放行。
    job = {"kovaak_run_id": 1, "input_mode": "telemetry_multimodal"}
    assert _establish_managed_video_baseline(job, "telemetry_multimodal") is None
    _assert_managed_video_matches_snapshot_with_baseline(job, "telemetry_multimodal", None)
