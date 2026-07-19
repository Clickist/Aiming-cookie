from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from webapp.backend.native_capture_client import (
    NativeCaptureClient,
    NativeCaptureProtocolError,
    NativeCaptureRetryableError,
    NativeCaptureTerminalError,
)


def _status_response() -> dict:
    return {
        "type": "statusResult",
        "ok": True,
        "status": {
            "enabled": True,
            "phase": "capturing",
            "captureSessionId": "session-1",
            "kovaakProcessPresent": True,
            "windowHandle": 123,
            "reason": None,
            "raw": {"state": "capturing", "reason": None},
            "video": {"state": "capturing", "reason": None},
        },
    }


def _serve_once(
    response: bytes,
    *,
    delay_seconds: float = 0.0,
) -> tuple[str, dict, threading.Thread]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()
    captured: dict = {}

    def serve() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                payload = bytearray()
                while not payload.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    payload.extend(chunk)
                if payload:
                    captured.update(json.loads(payload))
                if delay_seconds:
                    time.sleep(delay_seconds)
                try:
                    connection.sendall(response)
                except OSError:
                    pass
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"{host}:{port}", captured, thread


def _json_line(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def test_native_client_enforces_loopback_secret_and_strict_status_schema() -> None:
    with pytest.raises(ValueError, match="loopback"):
        NativeCaptureClient("192.0.2.10:1234", "a" * 64)
    with pytest.raises(ValueError, match="secret"):
        NativeCaptureClient("127.0.0.1:1234", "short")

    address, captured, thread = _serve_once(_json_line(_status_response()))
    client = NativeCaptureClient(address, "a" * 64)
    status = client.status()
    thread.join(timeout=1)

    assert status["phase"] == "capturing"
    assert status["captureSessionId"] == "session-1"
    assert captured == {"type": "status", "secret": "a" * 64}

    invalid = _status_response()
    invalid["status"]["privatePath"] = "C:/private/capture.mp4"
    address, _captured, thread = _serve_once(_json_line(invalid))
    with pytest.raises(NativeCaptureProtocolError, match="schema"):
        NativeCaptureClient(address, "a" * 64).status()
    thread.join(timeout=1)


def test_native_client_export_and_release_never_send_paths() -> None:
    export_response = {
        "type": "exportReplayResult",
        "ok": True,
        "requestDigest": "b" * 64,
        "captureSessionId": "session-1",
        "requestedStartEpochMs": 1_000,
        "requestedEndEpochMs": 2_000,
        "replay": {
            "requestedStart100ns": 10,
            "requestedEnd100ns": 20,
            "decodeStart100ns": 0,
            "visibleDuration100ns": 10,
            "decodePreroll100ns": 10,
            "packetCount": 1,
            "encodedBytes": 2,
            "reencodedFrames": 0,
            "captureClock": {
                "utcEpochMs": 1_000,
                "qpcNs": 1,
                "clockSource": "utc_epoch_ms+qpc+wgc_system_relative_time",
                "timebaseVersion": "time_alignment.v2",
            },
        },
        "file": {"size": 2, "digest": "c" * 64},
    }
    address, captured, thread = _serve_once(_json_line(export_response))
    result = NativeCaptureClient(address, "a" * 64).export_replay(
        request_id="request-1",
        run_id=7,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    )
    thread.join(timeout=1)

    assert result["requestDigest"] == "b" * 64
    assert captured == {
        "type": "exportReplay",
        "secret": "a" * 64,
        "requestId": "request-1",
        "runId": 7,
        "captureSessionId": "session-1",
        "startEpochMs": 1_000,
        "endEpochMs": 2_000,
    }
    assert "path" not in json.dumps(captured).lower()

    released_status = _status_response()["status"]
    released_status.update({
        "phase": "waiting_for_kovaak",
        "captureSessionId": None,
        "kovaakProcessPresent": False,
        "windowHandle": None,
        "raw": {"state": "waiting", "reason": None},
        "video": {"state": "waiting", "reason": None},
    })
    address, captured, thread = _serve_once(_json_line({
        "type": "releaseCaptureSessionResult",
        "ok": True,
        "status": released_status,
    }))
    status = NativeCaptureClient(address, "a" * 64).release_capture_session(
        "session-1"
    )
    thread.join(timeout=1)
    assert status["captureSessionId"] is None
    assert captured == {
        "type": "releaseCaptureSession",
        "secret": "a" * 64,
        "captureSessionId": "session-1",
    }


def test_native_client_flushes_raw_snapshot_with_strict_coverage_receipt() -> None:
    response = {
        "type": "flushRawSnapshotResult",
        "ok": True,
        "captureSessionId": "session-1",
        "snapshot": {
            "coveredThroughEpochMs": 2_000,
            "snapshotAtEpochMs": 2_001,
            "pointCount": 17,
            "clockSource": "utc_epoch_ms+qpc",
            "timebaseVersion": "time_alignment.v2",
        },
    }
    address, captured, thread = _serve_once(_json_line(response))

    snapshot = NativeCaptureClient(address, "a" * 64).flush_raw_snapshot("session-1")

    thread.join(timeout=1)
    assert snapshot == response["snapshot"]
    assert captured == {
        "type": "flushRawSnapshot",
        "secret": "a" * 64,
        "captureSessionId": "session-1",
    }

    invalid = dict(response)
    invalid["snapshot"] = dict(response["snapshot"])
    invalid["snapshot"]["coveredThroughEpochMs"] = 2_002
    address, _captured, thread = _serve_once(_json_line(invalid))
    with pytest.raises(NativeCaptureProtocolError, match="schema"):
        NativeCaptureClient(address, "a" * 64).flush_raw_snapshot("session-1")
    thread.join(timeout=1)

    address, _captured, thread = _serve_once(_json_line({
        "type": "flushRawSnapshotResult",
        "ok": False,
        "code": "raw_snapshot_busy",
    }))
    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        NativeCaptureClient(address, "a" * 64).flush_raw_snapshot("session-1")
    thread.join(timeout=1)
    assert exc_info.value.code == "raw_snapshot_busy"


def test_native_client_classifies_terminal_codes_and_transport_failures() -> None:
    address, _captured, thread = _serve_once(_json_line({
        "type": "exportReplayResult",
        "ok": False,
        "code": "capture_coverage_gap",
    }))
    client = NativeCaptureClient(address, "a" * 64)
    with pytest.raises(NativeCaptureTerminalError) as exc_info:
        client.export_replay(
            request_id="request-1",
            run_id=7,
            capture_session_id="session-1",
            start_epoch_ms=1_000,
            end_epoch_ms=2_000,
        )
    thread.join(timeout=1)
    assert exc_info.value.code == "capture_coverage_gap"
    assert exc_info.value.retryable is False

    address, _captured, thread = _serve_once(
        _json_line(_status_response()),
        delay_seconds=0.1,
    )
    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        NativeCaptureClient(
            address,
            "a" * 64,
            connect_timeout_seconds=0.05,
            read_timeout_seconds=0.01,
        ).status()
    thread.join(timeout=1)
    assert exc_info.value.code == "capture_control_timeout"
    assert exc_info.value.retryable is True

    address, _captured, thread = _serve_once(b"x" * (16 * 1024 + 1))
    with pytest.raises(NativeCaptureProtocolError) as exc_info:
        NativeCaptureClient(address, "a" * 64).status()
    thread.join(timeout=1)
    assert exc_info.value.code == "capture_control_response_invalid"
