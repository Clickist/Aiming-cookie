from __future__ import annotations

import hashlib
import os
from pathlib import Path


class SourceSnapshotChangedError(ValueError):
    """Frozen Analysis source is missing, unidentified, or no longer the same revision."""


def _read_frozen_source_bytes(kind: str, source: object) -> bytes:
    if not isinstance(source, dict):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, dict) or any(
        fingerprint.get(field) is None for field in ("sha256", "size", "mtime_ns")
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    expected = {
        "sha256": fingerprint["sha256"],
        "size": fingerprint["size"],
        "mtime_ns": fingerprint["mtime_ns"],
    }
    if (
        not isinstance(expected["sha256"], str)
        or isinstance(expected["size"], bool)
        or not isinstance(expected["size"], int)
        or expected["size"] < 0
        or isinstance(expected["mtime_ns"], bool)
        or not isinstance(expected["mtime_ns"], int)
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    path = source.get("path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} path missing")
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(expected["size"] + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} file missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} changed while reading"
        )
    actual = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mtime_ns": after.st_mtime_ns,
    }
    if actual != expected:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} revision changed")
    return data


def _managed_video_contract(job: dict, input_mode: str) -> tuple[str, str, int] | None:
    if input_mode not in {"multimodal", "video_fallback"}:
        return None
    if job.get("kovaak_run_id") is None:
        return None
    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    video = sources.get("video")
    if not isinstance(video, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    if "fingerprint" not in video:
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    fingerprint = video.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if (
        not isinstance(expected_sha, str)
        or not expected_sha
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    path = job.get("video_path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError("source_unavailable: managed video missing")
    return path, expected_sha, expected_size


def _assert_managed_video_matches_snapshot(job: dict, input_mode: str) -> None:
    contract = _managed_video_contract(job, input_mode)
    if contract is None:
        return
    path, expected_sha, expected_size = contract
    digest = hashlib.sha256()
    observed_size = 0
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != expected_size:
                raise SourceSnapshotChangedError(
                    "source_unavailable: managed video revision changed"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                observed_size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except SourceSnapshotChangedError:
        raise
    except OSError as exc:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video changed while reading"
        )
    if observed_size != expected_size or digest.hexdigest() != expected_sha:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video revision changed"
        )
