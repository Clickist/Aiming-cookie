"""Encode and decode the binary Raw Input snapshot format."""

from __future__ import annotations

import os
import struct
from pathlib import Path
from uuid import uuid4


SNAPSHOT_MAGIC = b"ACRI"
SNAPSHOT_VERSION = 2
LEGACY_SNAPSHOT_VERSION = 1
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({LEGACY_SNAPSHOT_VERSION, SNAPSHOT_VERSION})
SNAPSHOT_HEADER_SIZE = 12
SNAPSHOT_RECORD_SIZE = 20
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_POINTS = 1_000_000
MAX_SNAPSHOT_SPAN_MS = 10 * 60 * 1000
SUPPORTED_BUTTON_MASK = 0b111


def _validate_snapshot_points(points: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(points) > MAX_SNAPSHOT_POINTS:
        raise ValueError("raw input snapshot has too many points")

    normalized: list[dict[str, int]] = []
    first_timestamp: int | None = None
    previous_timestamp: int | None = None
    for point in points:
        timestamp_ms = int(point["timestamp_ms"])
        dx = int(point["dx"])
        dy = int(point["dy"])
        buttons = int(point.get("buttons", 0))
        if not -(2**63) <= timestamp_ms < 2**63:
            raise ValueError("raw input timestamp is outside i64 range")
        if not -(2**31) <= dx < 2**31 or not -(2**31) <= dy < 2**31:
            raise ValueError("raw input delta is outside i32 range")
        if buttons < 0 or buttons & ~SUPPORTED_BUTTON_MASK:
            raise ValueError("raw input buttons use unsupported bits")
        if previous_timestamp is not None and timestamp_ms < previous_timestamp:
            raise ValueError("raw input timestamps are not monotonic")
        if first_timestamp is None:
            first_timestamp = timestamp_ms
        elif timestamp_ms - first_timestamp > MAX_SNAPSHOT_SPAN_MS:
            raise ValueError("raw input snapshot exceeds time span limit")
        previous_timestamp = timestamp_ms
        normalized.append({
            "timestamp_ms": timestamp_ms,
            "dx": dx,
            "dy": dy,
            "buttons": buttons,
        })
    return normalized


def _decode_mouse_snapshot_bytes_with_version(
    data: bytes,
) -> tuple[int, list[dict[str, int]]]:
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("raw input snapshot exceeds byte limit")
    if len(data) < SNAPSHOT_HEADER_SIZE or data[:4] != SNAPSHOT_MAGIC:
        raise ValueError("invalid raw input snapshot")
    version = data[4]
    if version not in SUPPORTED_SNAPSHOT_VERSIONS:
        raise ValueError("unsupported raw input snapshot version")
    if data[5:8] != b"\0\0\0":
        raise ValueError("unsupported raw input snapshot header")
    count = struct.unpack_from("<I", data, 8)[0]
    if count > MAX_SNAPSHOT_POINTS:
        raise ValueError("raw input snapshot has too many points")
    expected = SNAPSHOT_HEADER_SIZE + count * SNAPSHOT_RECORD_SIZE
    if len(data) != expected:
        raise ValueError("truncated raw input snapshot")
    points = []
    offset = SNAPSHOT_HEADER_SIZE
    for _ in range(count):
        timestamp_ms, dx, dy, buttons = struct.unpack_from("<qiiI", data, offset)
        points.append({"timestamp_ms": timestamp_ms, "dx": dx, "dy": dy, "buttons": buttons})
        offset += SNAPSHOT_RECORD_SIZE
    return version, _validate_snapshot_points(points)


def decode_mouse_snapshot_bytes(data: bytes) -> list[dict[str, int]]:
    """Decode exact Raw Input bytes after their source fingerprint is accepted."""
    return _decode_mouse_snapshot_bytes_with_version(data)[1]


def _canonicalize_mouse_points(points: list[dict[str, int]]) -> list[dict[str, int]]:
    source = _validate_snapshot_points(points)
    normalized: list[dict[str, int]] = []
    bucket: dict[str, int] | None = None
    buttons = 0

    def flush() -> None:
        nonlocal bucket
        if bucket is not None and (bucket["dx"] != 0 or bucket["dy"] != 0):
            normalized.append(bucket)
        bucket = None

    for point in source:
        timestamp_ms = point["timestamp_ms"]
        if bucket is not None and timestamp_ms > bucket["timestamp_ms"]:
            flush()
        if point["dx"] != 0 or point["dy"] != 0:
            if bucket is None:
                bucket = {
                    "timestamp_ms": timestamp_ms,
                    "dx": point["dx"],
                    "dy": point["dy"],
                    "buttons": buttons,
                }
            else:
                next_dx = bucket["dx"] + point["dx"]
                next_dy = bucket["dy"] + point["dy"]
                if not -(2**31) <= next_dx < 2**31 or not -(2**31) <= next_dy < 2**31:
                    raise ValueError("raw input aggregate is outside i32 range")
                bucket["dx"] = next_dx
                bucket["dy"] = next_dy

        changed = buttons ^ point["buttons"]
        for mask in (1, 2, 4):
            if not changed & mask:
                continue
            if point["buttons"] & mask:
                buttons |= mask
            else:
                buttons &= ~mask
            if bucket is not None:
                bucket["buttons"] = buttons
            normalized.append({
                "timestamp_ms": timestamp_ms,
                "dx": 0,
                "dy": 0,
                "buttons": buttons,
            })
    flush()
    return _validate_snapshot_points(normalized)


def read_mouse_snapshot(path: str | Path) -> list[dict[str, int]]:
    """Read and validate the versioned Rust Raw Input snapshot format."""
    source = Path(path)
    with source.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    return decode_mouse_snapshot_bytes(data)


def read_mouse_snapshot_with_version(
    path: str | Path,
) -> tuple[int, list[dict[str, int]]]:
    """Read a Raw Input snapshot while retaining its actual on-disk version."""
    source = Path(path)
    with source.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    return _decode_mouse_snapshot_bytes_with_version(data)


def _has_valid_mouse_snapshot_header(path: str | Path) -> bool:
    """Check a snapshot's bounded structural header without decoding its points."""
    source = Path(path)
    size = source.stat().st_size
    if size < SNAPSHOT_HEADER_SIZE or size > MAX_SNAPSHOT_BYTES:
        return False
    with source.open("rb") as stream:
        header = stream.read(SNAPSHOT_HEADER_SIZE)
    if len(header) != SNAPSHOT_HEADER_SIZE or header[:4] != SNAPSHOT_MAGIC:
        return False
    if header[4] not in SUPPORTED_SNAPSHOT_VERSIONS or header[5:8] != b"\0\0\0":
        return False
    count = struct.unpack_from("<I", header, 8)[0]
    return (
        count <= MAX_SNAPSHOT_POINTS
        and size == SNAPSHOT_HEADER_SIZE + count * SNAPSHOT_RECORD_SIZE
    )


def write_mouse_snapshot(path: str | Path, points: list[dict[str, int]]) -> None:
    """Write a validated trace snapshot atomically in the Rust-compatible format."""
    normalized = _canonicalize_mouse_points(points)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray(SNAPSHOT_MAGIC + bytes([SNAPSHOT_VERSION, 0, 0, 0]))
    payload.extend(struct.pack("<I", len(normalized)))
    for point in normalized:
        payload.extend(struct.pack(
            "<qiiI", point["timestamp_ms"], point["dx"], point["dy"], point["buttons"],
        ))
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def extract_mouse_snapshot_window(
    path: str | Path, start_ms: int, end_ms: int, destination: str | Path,
) -> int:
    if end_ms < start_ms:
        raise ValueError("raw input window end precedes start")
    points = [
        point for point in read_mouse_snapshot(path)
        if start_ms <= point["timestamp_ms"] < end_ms
    ]
    if points:
        write_mouse_snapshot(destination, points)
    return len(points)
