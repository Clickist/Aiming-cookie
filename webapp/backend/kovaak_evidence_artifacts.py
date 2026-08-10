"""Shared file-system helpers for Run evidence artifact management."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path
from uuid import uuid4


def _file_fingerprint(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def _managed_run_video_path(
    data_root: str | Path,
    run_id: int,
    video_path: str | Path,
) -> tuple[Path, str]:
    run_root = (Path(data_root) / "runs" / str(run_id)).resolve()
    candidate = Path(video_path).resolve()
    try:
        candidate.relative_to(run_root)
    except ValueError as error:
        raise ValueError("video path must stay inside the managed Run root") from error
    match = re.fullmatch(r"video-([A-Za-z0-9_-]{1,64})\.mp4", candidate.name)
    if match is None:
        raise ValueError("managed Run video path has an invalid file name")
    return candidate, match.group(1)


def _video_receipt_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.receipt.json")


def _quarantine_run_video_file(candidate: Path, quarantine_root: Path) -> bool:
    if not candidate.is_file():
        return False
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / candidate.name
    if destination.exists():
        destination = quarantine_root / (
            f"{candidate.stem}-{uuid4().hex}{candidate.suffix}"
        )
    candidate.replace(destination)
    return True


def _is_app_video_artifact(path: Path) -> bool:
    name = path.name
    return bool(
        re.fullmatch(r"video-[A-Za-z0-9_-]{1,64}\.mp4", name)
        or re.fullmatch(r"video-[A-Za-z0-9_-]{1,64}\.receipt\.json", name)
        or (name.startswith(".video-") and ".partial-" in name)
    )


def _managed_evidence_artifact(
    data_root: str | Path,
    run_id: int,
    evidence_kind: str,
    path: str | Path,
) -> tuple[Path, str]:
    root = Path(data_root).resolve()
    run_root = (root / "runs" / str(run_id)).resolve()
    candidate = Path(path).resolve()
    try:
        relative_to_run = candidate.relative_to(run_root)
    except ValueError as error:
        raise ValueError("Run evidence path escapes the managed Run root") from error
    if len(relative_to_run.parts) != 1:
        raise ValueError("Run evidence must be a direct managed Run artifact")
    if evidence_kind == "video":
        _managed_run_video_path(data_root, run_id, candidate)
    elif evidence_kind == "raw":
        if re.fullmatch(r"trace-[A-Za-z0-9_-]{1,128}\.bin", candidate.name) is None:
            raise ValueError("managed Raw artifact has an invalid file name")
    else:
        raise ValueError("evidence kind must be video or raw")
    return candidate, candidate.relative_to(root).as_posix()


def _resolve_evidence_relpath(
    data_root: str | Path,
    run_id: int,
    evidence_kind: str,
    relative_path: str,
) -> Path:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ValueError("stored Run evidence path is invalid")
    root = Path(data_root).resolve()
    candidate = (root / Path(*relative_path.split("/"))).resolve()
    managed, expected_relative = _managed_evidence_artifact(
        data_root, run_id, evidence_kind, candidate,
    )
    if expected_relative != relative_path:
        raise ValueError("stored Run evidence path is not canonical")
    return managed


def _unlink_run_evidence_artifact(path: Path) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise OSError("Run evidence artifact is not a regular file")
    size = metadata.st_size
    path.unlink()
    return size
