"""Parser for KovaaK's ``.perf`` performance files.

The wire format mapping is adapted from RefleK's GPL-3.0 implementation
(``internal/runs/kovaaks/performances.go``).  This is a small, dependency-free
Python parser so the local analysis runtime does not need a Go/Wails process.
Unknown protobuf fields are skipped for forward compatibility.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ChallengeProfile:
    time_limit: float = 0.0
    player_profile: str = ""
    added_bots: tuple[str, ...] = ()
    player_max_lives: int = 0
    bot_max_lives: tuple[int, ...] = ()
    player_team: int = 0
    bot_teams: tuple[int, ...] = ()
    map_name: str = ""
    map_scale: float = 0.0
    timescale: float = 0.0
    end_challenge_after_kills: float = 0.0
    end_challenge_after_damage: float = 0.0


@dataclass(frozen=True)
class PerformanceHeader:
    scenario_name: str = ""
    scenario_hash: str = ""
    challenge_start_utc: int = 0
    schema_version: int = 0
    challenge_profile: ChallengeProfile = field(default_factory=ChallengeProfile)


@dataclass(frozen=True)
class PerformanceEvent:
    timestamp: float = 0.0
    payload_type: str = ""
    count: Optional[int] = None
    delta: Optional[float] = None
    value: Optional[float] = None


@dataclass(frozen=True)
class PerformanceData:
    header: PerformanceHeader = field(default_factory=PerformanceHeader)
    events: tuple[PerformanceEvent, ...] = ()


class PerformanceParseError(ValueError):
    """Raised when a KovaaK performance payload is malformed."""


def parse_performance_file(path: str | Path) -> PerformanceData:
    """Read and parse a KovaaK ``.perf`` file."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise PerformanceParseError(f"unable to read performance file: {path}") from exc
    return parse_performance_bytes(data)


def parse_performance_bytes(data: bytes) -> PerformanceData:
    """Parse a performance protobuf-wire payload."""
    header = PerformanceHeader()
    events: list[PerformanceEvent] = []
    for field_number, wire_type, value in _iter_fields(data):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 2)
            header = _parse_header(value)
        elif field_number == 2:
            _expect_wire_type(field_number, wire_type, 2)
            events.append(_parse_event(value))
    return PerformanceData(header=header, events=tuple(events))


def _parse_header(data: bytes) -> PerformanceHeader:
    values: dict[int, object] = {}
    profile = ChallengeProfile()
    for field_number, wire_type, value in _iter_fields(data):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 2)
            values[1] = _decode_string(value)
        elif field_number == 2:
            _expect_wire_type(field_number, wire_type, 2)
            values[2] = _decode_string(value)
        elif field_number == 3:
            _expect_wire_type(field_number, wire_type, 0)
            values[3] = _decode_varint(value)
        elif field_number == 4:
            _expect_wire_type(field_number, wire_type, 0)
            values[4] = _decode_varint(value)
        elif field_number == 5:
            _expect_wire_type(field_number, wire_type, 2)
            profile = _parse_profile(value)
    return PerformanceHeader(
        scenario_name=str(values.get(1, "")),
        scenario_hash=str(values.get(2, "")),
        challenge_start_utc=int(values.get(3, 0)),
        schema_version=int(values.get(4, 0)),
        challenge_profile=profile,
    )


def _parse_profile(data: bytes) -> ChallengeProfile:
    added_bots: list[str] = []
    bot_max_lives: list[int] = []
    bot_teams: list[int] = []
    values: dict[int, object] = {}
    for field_number, wire_type, value in _iter_fields(data):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 5)
            values[1] = _decode_float32(value)
        elif field_number == 2:
            _expect_wire_type(field_number, wire_type, 2)
            values[2] = _decode_string(value)
        elif field_number == 3:
            _expect_wire_type(field_number, wire_type, 2)
            added_bots.append(_decode_string(value))
        elif field_number == 4:
            _expect_wire_type(field_number, wire_type, 0)
            values[4] = _decode_int32(_decode_varint(value))
        elif field_number == 5:
            bot_max_lives.extend(_decode_int32_values(wire_type, value))
        elif field_number == 6:
            _expect_wire_type(field_number, wire_type, 0)
            values[6] = _decode_int32(_decode_varint(value))
        elif field_number == 7:
            bot_teams.extend(_decode_int32_values(wire_type, value))
        elif field_number == 8:
            _expect_wire_type(field_number, wire_type, 2)
            values[8] = _decode_string(value)
        elif field_number == 9:
            _expect_wire_type(field_number, wire_type, 5)
            values[9] = _decode_float32(value)
        elif field_number == 10:
            _expect_wire_type(field_number, wire_type, 5)
            values[10] = _decode_float32(value)
        elif field_number == 11:
            _expect_wire_type(field_number, wire_type, 5)
            values[11] = _decode_float32(value)
        elif field_number == 12:
            _expect_wire_type(field_number, wire_type, 5)
            values[12] = _decode_float32(value)
    return ChallengeProfile(
        time_limit=float(values.get(1, 0.0)),
        player_profile=str(values.get(2, "")),
        added_bots=tuple(added_bots),
        player_max_lives=int(values.get(4, 0)),
        bot_max_lives=tuple(bot_max_lives),
        player_team=int(values.get(6, 0)),
        bot_teams=tuple(bot_teams),
        map_name=str(values.get(8, "")),
        map_scale=float(values.get(9, 0.0)),
        timescale=float(values.get(10, 0.0)),
        end_challenge_after_kills=float(values.get(11, 0.0)),
        end_challenge_after_damage=float(values.get(12, 0.0)),
    )


def _parse_event(data: bytes) -> PerformanceEvent:
    timestamp = 0.0
    payload_type = ""
    count: Optional[int] = None
    delta: Optional[float] = None
    value: Optional[float] = None
    count_fields = {2, 3, 4, 8, 9, 10, 12, 13}
    delta_fields = {5, 6, 7, 11, 14, 15}
    value_fields = {16, 17, 18}
    for field_number, wire_type, raw in _iter_fields(data):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 5)
            timestamp = _decode_float32(raw)
        elif field_number in count_fields:
            _expect_wire_type(field_number, wire_type, 2)
            count = _decode_nested_int32(raw)
            payload_type = _payload_type(field_number)
        elif field_number in delta_fields:
            _expect_wire_type(field_number, wire_type, 2)
            delta = _decode_nested_float32(raw)
            payload_type = _payload_type(field_number)
        elif field_number in value_fields:
            _expect_wire_type(field_number, wire_type, 2)
            value = _decode_nested_float32(raw)
            payload_type = _payload_type(field_number)
    return PerformanceEvent(
        timestamp=timestamp,
        payload_type=payload_type,
        count=count,
        delta=delta,
        value=value,
    )


def _payload_type(field_number: int) -> str:
    return {
        2: "shotsFired",
        3: "shotsHit",
        4: "shotsMissed",
        5: "damageDone",
        6: "damagePossible",
        7: "score",
        8: "kills",
        9: "deaths",
        10: "overshots",
        11: "playerDamageTaken",
        12: "reloads",
        13: "pauseCount",
        14: "distanceTraveled",
        15: "mbsPoints",
        16: "targetSize",
        17: "targetSpeed",
        18: "randomSensScale",
    }.get(field_number, "")


def _iter_fields(data: bytes):
    offset = 0
    while offset < len(data):
        field_start = offset
        tag, offset = _read_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number <= 0:
            raise PerformanceParseError(f"invalid field tag at byte {field_start}")
        if wire_type == 3:
            offset = _skip_group(data, offset, field_number)
            continue
        if wire_type == 4:
            raise PerformanceParseError("unexpected protobuf end-group tag")
        raw, offset = _read_wire_value(data, offset, wire_type)
        yield field_number, wire_type, raw


def _expect_wire_type(field_number: int, actual: int, expected: int) -> None:
    if actual != expected:
        raise PerformanceParseError(
            f"field {field_number} has wire type {actual}, expected {expected}"
        )


def _read_wire_value(data: bytes, offset: int, wire_type: int) -> tuple[object, int]:
    if wire_type == 0:
        return _read_varint(data, offset)
    if wire_type == 1:
        return _read_bytes(data, offset, 8)
    if wire_type == 2:
        length, offset = _read_varint(data, offset)
        return _read_bytes(data, offset, length)
    if wire_type == 5:
        return _read_bytes(data, offset, 4)
    raise PerformanceParseError(f"unsupported protobuf wire type: {wire_type}")


def _skip_group(data: bytes, offset: int, group_number: int) -> int:
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number <= 0:
            raise PerformanceParseError("invalid field tag inside protobuf group")
        if wire_type == 4:
            if field_number != group_number:
                raise PerformanceParseError("mismatched protobuf end-group tag")
            return offset
        if wire_type == 3:
            offset = _skip_group(data, offset, field_number)
        else:
            _, offset = _read_wire_value(data, offset, wire_type)
    raise PerformanceParseError("truncated protobuf group")


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise PerformanceParseError("truncated or oversized protobuf varint")
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, offset
        shift += 7
    raise PerformanceParseError("truncated or oversized protobuf varint")


def _read_bytes(data: bytes, offset: int, length: int) -> tuple[bytes, int]:
    end = offset + int(length)
    if length < 0 or end > len(data):
        raise PerformanceParseError("truncated protobuf field")
    return data[offset:end], end


def _decode_varint(raw: int) -> int:
    return int(raw)


def _decode_int32(raw: int) -> int:
    raw &= 0xFFFFFFFF
    return raw - 0x100000000 if raw >= 0x80000000 else raw


def _decode_string(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PerformanceParseError("invalid UTF-8 string in performance payload") from exc


def _decode_float32(raw: bytes) -> float:
    if len(raw) != 4:
        raise PerformanceParseError("expected a 32-bit float")
    return struct.unpack("<f", raw)[0]


def _decode_int32_values(wire_type: int, raw: object) -> list[int]:
    if wire_type == 0:
        return [_decode_int32(int(raw))]
    if wire_type == 2:
        return [_decode_int32(value) for value, _ in _iter_varints(bytes(raw))]
    raise PerformanceParseError(f"invalid wire type {wire_type} for repeated int32 field")


def _iter_varints(data: bytes):
    offset = 0
    while offset < len(data):
        value, offset = _read_varint(data, offset)
        yield value, offset


def _decode_nested_int32(raw: bytes) -> int:
    for field_number, wire_type, value in _iter_fields(raw):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 0)
            return _decode_int32(_decode_varint(value))
    return 0


def _decode_nested_float32(raw: bytes) -> float:
    for field_number, wire_type, value in _iter_fields(raw):
        if field_number == 1:
            _expect_wire_type(field_number, wire_type, 5)
            return _decode_float32(value)
    return 0.0


__all__ = [
    "ChallengeProfile",
    "PerformanceData",
    "PerformanceEvent",
    "PerformanceHeader",
    "PerformanceParseError",
    "parse_performance_bytes",
    "parse_performance_file",
]
