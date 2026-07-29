"""Strict launch-scoped client for the native capture control plane."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any

from .kovaak_ingest import NonRetryableIngestionError, RetryableIngestionError

CONTROL_MAX_MESSAGE_BYTES = 16 * 1024
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PHASES = {
    "disabled",
    "waiting_for_kovaak",
    "capturing",
    "finalizing",
    "degraded",
    "error",
}
_SOURCE_STATES = {
    "disabled",
    "waiting",
    "capturing",
    "finalizing",
    "degraded",
    "unavailable",
}
_RETRYABLE_CODES = {
    "capture_unavailable",
    "capture_export_busy",
    "capture_export_cancelled",
    "capture_export_timed_out",
    "capture_export_failed",
    "raw_snapshot_busy",
    "raw_snapshot_timed_out",
    "raw_snapshot_failed",
    "raw_snapshot_unavailable",
}


class NativeCaptureRetryableError(RetryableIngestionError):
    def __init__(self, code: str) -> None:
        self.code = code
        self.retryable = True
        super().__init__(code)


class NativeCaptureTerminalError(NonRetryableIngestionError):
    def __init__(self, code: str) -> None:
        self.code = code
        self.retryable = False
        super().__init__(code)


class NativeCaptureProtocolError(NativeCaptureTerminalError):
    pass


def _exact_object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise NativeCaptureProtocolError(
            f"capture_control_{label}_schema_invalid"
        )
    return value


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NativeCaptureProtocolError(
            f"capture_control_{label}_schema_invalid"
        )
    return value


def _optional_code(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise NativeCaptureProtocolError(
            f"capture_control_{label}_schema_invalid"
        )
    return value


def _validate_source_status(value: object) -> dict[str, object]:
    source = _exact_object(value, {"state", "reason"}, "status")
    if source.get("state") not in _SOURCE_STATES:
        raise NativeCaptureProtocolError("capture_control_status_schema_invalid")
    _optional_code(source.get("reason"), "status")
    return source


def _validate_status(value: object) -> dict[str, object]:
    status = _exact_object(
        value,
        {
            "enabled",
            "phase",
            "captureSessionId",
            "kovaakProcessPresent",
            "windowHandle",
            "reason",
            "raw",
            "video",
        },
        "status",
    )
    if not isinstance(status.get("enabled"), bool):
        raise NativeCaptureProtocolError("capture_control_status_schema_invalid")
    if status.get("phase") not in _PHASES:
        raise NativeCaptureProtocolError("capture_control_status_schema_invalid")
    capture_session_id = status.get("captureSessionId")
    if capture_session_id is not None and (
        not isinstance(capture_session_id, str)
        or not 8 <= len(capture_session_id) <= 128
        or _IDENTIFIER.fullmatch(capture_session_id) is None
    ):
        raise NativeCaptureProtocolError("capture_control_status_schema_invalid")
    if not isinstance(status.get("kovaakProcessPresent"), bool):
        raise NativeCaptureProtocolError("capture_control_status_schema_invalid")
    window_handle = status.get("windowHandle")
    if window_handle is not None:
        _strict_int(window_handle, "status", minimum=1)
    _optional_code(status.get("reason"), "status")
    _validate_source_status(status.get("raw"))
    _validate_source_status(status.get("video"))
    return status


def _validate_snapshot_barrier(value: object) -> dict[str, object]:
    legacy_keys = {
        "coveredThroughEpochMs",
        "snapshotAtEpochMs",
        "pointCount",
        "clockSource",
        "timebaseVersion",
    }
    v2_keys = legacy_keys | {
        "receiptVersion",
        "captureSessionStartEpochMs",
        "queueDroppedPoints",
        "queueDropFirstEpochMs",
        "queueDropLastEpochMs",
        "ringExpiredPoints",
        "ringExpiredThroughEpochMs",
    }
    if not isinstance(value, dict):
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    snapshot = value
    keys = set(snapshot)
    if keys == v2_keys:
        if snapshot.get("receiptVersion") != "raw_snapshot_receipt.v2":
            raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        _strict_int(snapshot.get("captureSessionStartEpochMs"), "response")
        queue_dropped = _strict_int(
            snapshot.get("queueDroppedPoints"), "response", minimum=0,
        )
        queue_first = snapshot.get("queueDropFirstEpochMs")
        queue_last = snapshot.get("queueDropLastEpochMs")
        if queue_dropped == 0:
            if queue_first is not None or queue_last is not None:
                raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        else:
            first = _strict_int(queue_first, "response")
            last = _strict_int(queue_last, "response")
            if first > last:
                raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        ring_expired = _strict_int(
            snapshot.get("ringExpiredPoints"), "response", minimum=0,
        )
        ring_through = snapshot.get("ringExpiredThroughEpochMs")
        if ring_expired == 0:
            if ring_through is not None:
                raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        else:
            _strict_int(ring_through, "response")
    elif keys != legacy_keys:
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    covered_through = _strict_int(
        snapshot.get("coveredThroughEpochMs"), "response",
    )
    snapshot_at = _strict_int(snapshot.get("snapshotAtEpochMs"), "response")
    _strict_int(snapshot.get("pointCount"), "response", minimum=0)
    if covered_through > snapshot_at:
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    if snapshot.get("clockSource") != "utc_epoch_ms+qpc":
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    if snapshot.get("timebaseVersion") != "time_alignment.v2":
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    return snapshot


def _validate_replay(value: object) -> dict[str, object]:
    replay = _exact_object(
        value,
        {
            "requestedStart100ns",
            "requestedEnd100ns",
            "decodeStart100ns",
            "visibleDuration100ns",
            "decodePreroll100ns",
            "packetCount",
            "encodedBytes",
            "reencodedFrames",
            "captureClock",
        },
        "response",
    )
    start = _strict_int(replay.get("requestedStart100ns"), "response")
    end = _strict_int(replay.get("requestedEnd100ns"), "response", minimum=1)
    if end <= start:
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    for key in (
        "decodeStart100ns",
        "decodePreroll100ns",
        "encodedBytes",
        "reencodedFrames",
    ):
        _strict_int(replay.get(key), "response")
    _strict_int(replay.get("visibleDuration100ns"), "response", minimum=1)
    _strict_int(replay.get("packetCount"), "response", minimum=1)
    clock = _exact_object(
        replay.get("captureClock"),
        {"utcEpochMs", "qpcNs", "clockSource", "timebaseVersion"},
        "response",
    )
    _strict_int(clock.get("utcEpochMs"), "response")
    _strict_int(clock.get("qpcNs"), "response")
    if clock.get("clockSource") != "utc_epoch_ms+qpc+wgc_system_relative_time":
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    if clock.get("timebaseVersion") != "time_alignment.v2":
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    return replay


def _validate_file(value: object) -> dict[str, object]:
    file = _exact_object(value, {"size", "digest"}, "response")
    _strict_int(file.get("size"), "response")
    digest = file.get("digest")
    if not isinstance(digest, str) or _HEX_DIGEST.fullmatch(digest) is None:
        raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
    return file


class NativeCaptureClient:
    def __init__(
        self,
        address: str,
        secret: str,
        *,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 65.0,
    ) -> None:
        try:
            host, port_text = address.rsplit(":", 1)
            ip = ipaddress.ip_address(host)
            port = int(port_text)
        except (AttributeError, ValueError) as error:
            raise ValueError("native capture address must be a loopback host and port") from error
        if not ip.is_loopback or ip.version != 4 or not 1 <= port <= 65535:
            raise ValueError("native capture address must be loopback")
        if not isinstance(secret, str) or _HEX_DIGEST.fullmatch(secret) is None:
            raise ValueError("native capture secret is invalid")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("native capture timeouts must be positive")
        self._address = (str(ip), port)
        self._secret = secret
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        self._read_timeout_seconds = float(read_timeout_seconds)

    def status(self) -> dict[str, object]:
        for attempt in range(3):
            try:
                response = self._request(
                    {"type": "status", "secret": self._secret},
                    "statusResult",
                )
                break
            except NativeCaptureRetryableError as error:
                if attempt < 2 and error.code in {
                    "capture_unavailable",
                    "capture_control_unavailable",
                    "capture_control_response_lost",
                }:
                    continue
                raise
        response = _exact_object(
            response,
            {"type", "ok", "status"},
            "status",
        )
        return _validate_status(response.get("status"))

    def flush_raw_snapshot(self, capture_session_id: str) -> dict[str, object]:
        if (
            not isinstance(capture_session_id, str)
            or not 8 <= len(capture_session_id) <= 128
            or _IDENTIFIER.fullmatch(capture_session_id) is None
        ):
            raise ValueError("capture session id is invalid")
        response = self._request(
            {
                "type": "flushRawSnapshot",
                "secret": self._secret,
                "captureSessionId": capture_session_id,
            },
            "flushRawSnapshotResult",
        )
        response = _exact_object(
            response,
            {"type", "ok", "captureSessionId", "snapshot"},
            "response",
        )
        if response.get("captureSessionId") != capture_session_id:
            raise NativeCaptureProtocolError(
                "capture_control_response_schema_invalid"
            )
        return _validate_snapshot_barrier(response.get("snapshot"))

    def export_replay(
        self,
        *,
        request_id: str,
        run_id: int,
        capture_session_id: str,
        start_epoch_ms: int,
        end_epoch_ms: int,
    ) -> dict[str, object]:
        if (
            not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 64
            or _IDENTIFIER.fullmatch(request_id) is None
            or isinstance(run_id, bool)
            or not isinstance(run_id, int)
            or run_id <= 0
            or not isinstance(capture_session_id, str)
            or not 8 <= len(capture_session_id) <= 128
            or _IDENTIFIER.fullmatch(capture_session_id) is None
            or isinstance(start_epoch_ms, bool)
            or isinstance(end_epoch_ms, bool)
            or not isinstance(start_epoch_ms, int)
            or not isinstance(end_epoch_ms, int)
            or end_epoch_ms <= start_epoch_ms
        ):
            raise ValueError("native capture export request is invalid")
        response = self._request(
            {
                "type": "exportReplay",
                "secret": self._secret,
                "requestId": request_id,
                "runId": run_id,
                "captureSessionId": capture_session_id,
                "startEpochMs": start_epoch_ms,
                "endEpochMs": end_epoch_ms,
            },
            "exportReplayResult",
        )
        response = _exact_object(
            response,
            {
                "type",
                "ok",
                "requestDigest",
                "captureSessionId",
                "requestedStartEpochMs",
                "requestedEndEpochMs",
                "replay",
                "file",
            },
            "response",
        )
        digest = response.get("requestDigest")
        if not isinstance(digest, str) or _HEX_DIGEST.fullmatch(digest) is None:
            raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        if (
            response.get("captureSessionId") != capture_session_id
            or response.get("requestedStartEpochMs") != start_epoch_ms
            or response.get("requestedEndEpochMs") != end_epoch_ms
        ):
            raise NativeCaptureProtocolError("capture_control_response_schema_invalid")
        _validate_replay(response.get("replay"))
        _validate_file(response.get("file"))
        return {
            key: value
            for key, value in response.items()
            if key not in {"type", "ok"}
        }

    def release_capture_session(self, capture_session_id: str) -> dict[str, object]:
        if (
            not isinstance(capture_session_id, str)
            or not 8 <= len(capture_session_id) <= 128
            or _IDENTIFIER.fullmatch(capture_session_id) is None
        ):
            raise ValueError("capture session id is invalid")
        response = self._request(
            {
                "type": "releaseCaptureSession",
                "secret": self._secret,
                "captureSessionId": capture_session_id,
            },
            "releaseCaptureSessionResult",
        )
        response = _exact_object(
            response,
            {"type", "ok", "status"},
            "response",
        )
        return _validate_status(response.get("status"))

    def _request(
        self,
        request: dict[str, object],
        expected_type: str,
    ) -> dict[str, object]:
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > CONTROL_MAX_MESSAGE_BYTES:
            raise ValueError("native capture request exceeds the message limit")
        try:
            with socket.create_connection(
                self._address,
                timeout=self._connect_timeout_seconds,
            ) as connection:
                connection.settimeout(self._read_timeout_seconds)
                connection.sendall(payload)
                response_bytes = self._read_response(connection)
        except socket.timeout as error:
            raise NativeCaptureRetryableError("capture_control_timeout") from error
        except OSError as error:
            raise NativeCaptureRetryableError("capture_control_unavailable") from error
        try:
            response = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NativeCaptureProtocolError(
                "capture_control_response_invalid"
            ) from error
        if not isinstance(response, dict):
            raise NativeCaptureProtocolError("capture_control_response_invalid")
        if response.get("ok") is False:
            error_response = _exact_object(
                response,
                {"type", "ok", "code"},
                "response",
            )
            if error_response.get("type") not in {expected_type, "controlError"}:
                raise NativeCaptureProtocolError("capture_control_response_invalid")
            code = error_response.get("code")
            if not isinstance(code, str) or _CODE.fullmatch(code) is None:
                raise NativeCaptureProtocolError("capture_control_response_invalid")
            if code in _RETRYABLE_CODES:
                raise NativeCaptureRetryableError(code)
            raise NativeCaptureTerminalError(code)
        if response.get("type") != expected_type or response.get("ok") is not True:
            raise NativeCaptureProtocolError("capture_control_response_invalid")
        return response

    @staticmethod
    def _read_response(connection: socket.socket) -> bytes:
        response = bytearray()
        while True:
            chunk = connection.recv(1024)
            if not chunk:
                raise NativeCaptureRetryableError(
                    "capture_control_response_lost"
                )
            newline = chunk.find(b"\n")
            if newline >= 0:
                if newline + 1 != len(chunk):
                    raise NativeCaptureProtocolError(
                        "capture_control_response_invalid"
                    )
                response.extend(chunk[:newline])
                break
            response.extend(chunk)
            if len(response) >= CONTROL_MAX_MESSAGE_BYTES:
                raise NativeCaptureProtocolError(
                    "capture_control_response_invalid"
                )
        if not response or len(response) + 1 > CONTROL_MAX_MESSAGE_BYTES:
            raise NativeCaptureProtocolError("capture_control_response_invalid")
        return bytes(response)


__all__ = [
    "NativeCaptureClient",
    "NativeCaptureProtocolError",
    "NativeCaptureRetryableError",
    "NativeCaptureTerminalError",
]
