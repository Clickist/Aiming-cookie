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


def test_native_status_retries_lost_transport_without_retrying_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = NativeCaptureClient("127.0.0.1:1234", "a" * 64)
    calls = 0

    def recover(_request: dict, _expected_type: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NativeCaptureRetryableError("capture_control_unavailable")
        return _status_response()

    monkeypatch.setattr(client, "_request", recover)
    assert client.status()["phase"] == "capturing"
    assert calls == 2

    calls = 0

    def recover_server_read(_request: dict, _expected_type: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NativeCaptureRetryableError("capture_unavailable")
        return _status_response()

    monkeypatch.setattr(client, "_request", recover_server_read)
    assert client.status()["phase"] == "capturing"
    assert calls == 2

    calls = 0

    def remain_unavailable(_request: dict, _expected_type: str) -> dict:
        nonlocal calls
        calls += 1
        raise NativeCaptureRetryableError("capture_control_response_lost")

    monkeypatch.setattr(client, "_request", remain_unavailable)
    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        client.status()
    assert exc_info.value.code == "capture_control_response_lost"
    assert calls == 3


def _export_response() -> dict:
    return {
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


def test_native_export_retries_lost_transport_without_retrying_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = NativeCaptureClient("127.0.0.1:1234", "a" * 64)
    calls = 0

    def recover(request: dict, expected_type: str, **_kwargs) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NativeCaptureRetryableError("capture_control_unavailable")
        return _export_response()

    monkeypatch.setattr(client, "_request_oneshot", recover)
    result = client.export_replay(
        request_id="request-1",
        run_id=7,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    )
    assert result["requestDigest"] == "b" * 64
    assert calls == 2

    calls = 0

    def remain_lost(_request: dict, _expected_type: str, **_kwargs) -> dict:
        nonlocal calls
        calls += 1
        raise NativeCaptureRetryableError("capture_control_response_lost")

    monkeypatch.setattr(client, "_request_oneshot", remain_lost)
    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        client.export_replay(
            request_id="request-1",
            run_id=7,
            capture_session_id="session-1",
            start_epoch_ms=1_000,
            end_epoch_ms=2_000,
        )
    assert exc_info.value.code == "capture_control_response_lost"
    assert calls == 3

    calls = 0

    def stay_busy(_request: dict, _expected_type: str, **_kwargs) -> dict:
        nonlocal calls
        calls += 1
        raise NativeCaptureRetryableError("capture_export_busy")

    monkeypatch.setattr(client, "_request_oneshot", stay_busy)
    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        client.export_replay(
            request_id="request-1",
            run_id=7,
            capture_session_id="session-1",
            start_epoch_ms=1_000,
            end_epoch_ms=2_000,
        )
    assert exc_info.value.code == "capture_export_busy"
    assert calls == 1


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


_GEOMETRY_EVENT_FIXTURE = {
    "atUtcMs": 1_791_280_000_000,
    "canonicalMs": 123_456,
    "srcWidth": 1920,
    "srcHeight": 1080,
    "dstX": 320,
    "dstY": 0,
    "dstWidth": 1280,
    "dstHeight": 1600,
    "scale": 0.667,
    "followed": True,
}


def _export_replay_via_server(replay_overrides: dict) -> dict:
    response = _export_response()
    response["replay"].update(replay_overrides)
    address, _captured, thread = _serve_once(_json_line(response))
    result = NativeCaptureClient(address, "a" * 64).export_replay(
        request_id="request-1",
        run_id=7,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    )
    thread.join(timeout=1)
    return result


def test_native_export_accepts_optional_geometry_events_in_replay() -> None:
    """(b)(c) geometryEvents 是 replay 内可选加性字段：带与不带都通过。"""
    # (b) 空数组：字段存在但无漂移事件，照常通过。
    empty = _export_replay_via_server({"geometryEvents": []})
    assert empty["replay"]["geometryEvents"] == []
    # (c) 合同形态事件列表：camelCase 字段原样通过，不触发
    # capture_control_response_schema_invalid。
    with_events = _export_replay_via_server(
        {"geometryEvents": [_GEOMETRY_EVENT_FIXTURE]},
    )
    assert with_events["replay"]["geometryEvents"] == [_GEOMETRY_EVENT_FIXTURE]
    # (d) 白名单只到顶层键：条目缺合同字段不做深度校验（消费端 fail-safe）。
    malformed = _export_replay_via_server(
        {"geometryEvents": [{"canonicalMs": None}, "not-a-dict"]},
    )
    assert malformed["replay"]["geometryEvents"] == [
        {"canonicalMs": None}, "not-a-dict",
    ]
    # 非列表形态同样只做顶层键检查。
    non_list = _export_replay_via_server({"geometryEvents": "not-a-list"})
    assert non_list["replay"]["geometryEvents"] == "not-a-list"
    # (a) 不含该字段：旧形状契约不变（_export_response fixture 本身不带）。


def test_native_client_flushes_raw_snapshot_with_strict_coverage_receipt() -> None:
    response = {
        "type": "flushRawSnapshotResult",
        "ok": True,
        "captureSessionId": "session-1",
        "snapshot": {
            "receiptVersion": "raw_snapshot_receipt.v2",
            "captureSessionStartEpochMs": 1_000,
            "coveredThroughEpochMs": 2_000,
            "snapshotAtEpochMs": 2_001,
            "pointCount": 17,
            "queueDroppedPoints": 0,
            "queueDropFirstEpochMs": None,
            "queueDropLastEpochMs": None,
            "ringExpiredPoints": 0,
            "ringExpiredThroughEpochMs": None,
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

    legacy = dict(response)
    legacy["snapshot"] = {
        key: value
        for key, value in response["snapshot"].items()
        if key in {
            "coveredThroughEpochMs",
            "snapshotAtEpochMs",
            "pointCount",
            "clockSource",
            "timebaseVersion",
        }
    }
    address, _captured, thread = _serve_once(_json_line(legacy))
    assert NativeCaptureClient(address, "a" * 64).flush_raw_snapshot("session-1") == legacy["snapshot"]
    thread.join(timeout=1)

    incomplete = dict(response)
    incomplete["snapshot"] = dict(response["snapshot"])
    incomplete["snapshot"].pop("ringExpiredPoints")
    address, _captured, thread = _serve_once(_json_line(incomplete))
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


# ---- 持久控制连接（0930 提案 C）----


def _serve_persistent(serve) -> tuple[str, dict, socket.socket]:
    """Listener that accepts multiple connections; each is handed to serve().

    ``serve(connection, index)`` owns the connection lifecycle, so tests can
    model both a long-lived control link and one-shot/kill behaviours.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    host, port = listener.getsockname()
    state = {"connections": 0, "requests": []}

    def accept_loop() -> None:
        while True:
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            state["connections"] += 1
            threading.Thread(
                target=serve,
                args=(connection, state["connections"]),
                daemon=True,
            ).start()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    return f"{host}:{port}", state, listener


def _read_request_line(connection: socket.socket) -> dict | None:
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            return None
        payload.extend(chunk)
    return json.loads(payload)


def _serve_line_protocol(request_log: list, respond) -> object:
    """Default serve() that answers every line with respond(request) and
    records requests into request_log (mirrors the Rust side's per-request dlog)."""

    def serve(connection: socket.socket, _index: int) -> None:
        with connection:
            while True:
                request = _read_request_line(connection)
                if request is None:
                    return
                request_log.append(request)
                connection.sendall(respond(request))

    return serve


def test_control_connection_is_reused_across_requests() -> None:
    status_line = _json_line(_status_response())
    request_log: list = []
    serve = _serve_line_protocol(request_log, lambda _request: status_line)
    address, state, listener = _serve_persistent(serve)
    client = NativeCaptureClient(address, "a" * 64)
    try:
        for _ in range(3):
            assert client.status()["phase"] == "capturing"
        # 三次请求只建立一条连接：持久控制连接复用，回环建连次数降到 1。
        assert state["connections"] == 1
        assert len(request_log) == 3
        assert request_log[0] == {"type": "status", "secret": "a" * 64}
        # 客户端 socket 设置了 TCP_NODELAY。
        assert client._control_connection is not None
        assert (
            client._control_connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            == 1
        )
    finally:
        client._discard_control_connection()
        listener.close()


def test_persistent_connection_reconnects_after_transport_failure() -> None:
    status_line = _json_line(_status_response())

    def serve(connection: socket.socket, index: int) -> None:
        request = _read_request_line(connection)
        if request is None:
            return
        state["requests"].append((index, request))
        connection.sendall(status_line)
        # 第 1 条连接回完首个响应即被杀（模拟安全软件 RST/进程收尾；
        # 与 Rust 服务端一样显式关断，对端表现为 EOF/RST）。
        connection.close()

    address, state, listener = _serve_persistent(serve)
    client = NativeCaptureClient(address, "a" * 64)
    assert client.status()["phase"] == "capturing"
    # 连接被杀后的下一次请求自动重连并成功（response_lost 走内部重试）。
    assert client.status()["phase"] == "capturing"
    assert state["connections"] == 2
    assert len(state["requests"]) == 2
    client._discard_control_connection()
    listener.close()


def test_export_replay_uses_an_independent_one_shot_connection() -> None:
    status_line = _json_line(_status_response())
    export_line = _json_line(_export_response())

    def serve(connection: socket.socket, _index: int) -> None:
        with connection:
            while True:
                request = _read_request_line(connection)
                if request is None:
                    return
                state["requests"].append(request)
                if request["type"] == "status":
                    connection.sendall(status_line)
                else:
                    # 一次性连接：export 回完即关（老客户端兼容行为）。
                    connection.sendall(export_line)
                    break

    address, state, listener = _serve_persistent(serve)
    client = NativeCaptureClient(address, "a" * 64)
    try:
        assert client.status()["phase"] == "capturing"
        result = client.export_replay(
            request_id="request-1",
            run_id=7,
            capture_session_id="session-1",
            start_epoch_ms=1_000,
            end_epoch_ms=2_000,
        )
        assert result["requestDigest"] == "b" * 64
        # export 必须开独立连接：持久控制连接不被占用。
        assert state["connections"] == 2
        assert [request["type"] for request in state["requests"]] == [
            "status",
            "exportReplay",
        ]
        # export 之后持久控制连接仍可直接复用（不被导出饿死/污染）。
        assert client.status()["phase"] == "capturing"
        assert state["connections"] == 2
    finally:
        client._discard_control_connection()
        listener.close()


def test_persistent_connection_serializes_concurrent_requests() -> None:
    status_line = _json_line(_status_response())
    inflight = {"current": 0, "max": 0}
    inflight_lock = threading.Lock()

    def serve(connection: socket.socket, _index: int) -> None:
        with connection:
            while True:
                request = _read_request_line(connection)
                if request is None:
                    return
                with inflight_lock:
                    inflight["current"] += 1
                    inflight["max"] = max(inflight["max"], inflight["current"])
                time.sleep(0.1)
                connection.sendall(status_line)
                with inflight_lock:
                    inflight["current"] -= 1

    address, state, listener = _serve_persistent(serve)
    client = NativeCaptureClient(address, "a" * 64)
    results: list[str] = []
    errors: list[BaseException] = []

    def poll() -> None:
        try:
            results.append(str(client.status()["phase"]))
        except BaseException as error:  # noqa: BLE001 - test thread funnel
            errors.append(error)

    threads = [threading.Thread(target=poll, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    try:
        assert errors == []
        assert results == ["capturing", "capturing"]
        # 行协议严格一问一答：锁必须把并发请求串行化到同一条连接上，
        # 服务端任一时刻至多处理一个请求。
        assert state["connections"] == 1
        assert inflight["max"] == 1
    finally:
        client._discard_control_connection()
        listener.close()


def test_transport_corruption_codes_are_retryable() -> None:
    # 0930 提案 D：传输层被腐蚀的症状按可重试分类，收尾管线稍后自动重试，
    # 不把整局视频判成终态失败。
    for code in ("control_read_failed", "control_message_invalid"):
        address, _captured, thread = _serve_once(
            _json_line({"type": "exportReplayResult", "ok": False, "code": code})
        )
        with pytest.raises(NativeCaptureRetryableError) as exc_info:
            NativeCaptureClient(address, "a" * 64).export_replay(
                request_id="request-1",
                run_id=7,
                capture_session_id="session-1",
                start_epoch_ms=1_000,
                end_epoch_ms=2_000,
            )
        thread.join(timeout=1)
        assert exc_info.value.code == code
        assert exc_info.value.retryable is True
