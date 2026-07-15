import struct

import pytest

from kovaak_tracker.performance_parser import (
    PerformanceParseError,
    parse_performance_bytes,
)


def varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number: int, wire_type: int, raw: bytes | int) -> bytes:
    out = varint((number << 3) | wire_type)
    if wire_type == 0:
        return out + varint(int(raw))
    if wire_type == 2:
        return out + varint(len(raw)) + raw
    if wire_type == 5:
        return out + bytes(raw)
    raise AssertionError(wire_type)


def f32(value: float) -> bytes:
    return struct.pack("<f", value)


def test_parse_header_profile_and_event_payloads():
    profile = b"".join(
        [
            field(1, 5, f32(60.0)),
            field(2, 2, b"default"),
            field(3, 2, b"target"),
            field(5, 2, varint(3) + varint(4)),
            field(8, 2, b"map"),
            field(9, 5, f32(1.5)),
        ]
    )
    header = b"".join(
        [
            field(1, 2, b"1w6ts reload v2"),
            field(2, 2, b"hash"),
            field(3, 0, 1234),
            field(4, 0, 2),
            field(5, 2, profile),
        ]
    )
    event = b"".join(
        [
            field(1, 5, f32(1.25)),
            field(2, 2, field(1, 0, 7)),
            field(5, 2, field(1, 5, f32(0.5))),
        ]
    )
    parsed = parse_performance_bytes(
        b"".join([field(1, 2, header), field(2, 2, event)])
    )

    assert parsed.header.scenario_name == "1w6ts reload v2"
    assert parsed.header.challenge_start_utc == 1234
    assert parsed.header.challenge_profile.time_limit == pytest.approx(60.0)
    assert parsed.header.challenge_profile.bot_max_lives == (3, 4)
    assert parsed.events[0].payload_type == "damageDone"
    assert parsed.events[0].timestamp == pytest.approx(1.25)
    assert parsed.events[0].delta == pytest.approx(0.5)


def test_unknown_fields_are_skipped():
    parsed = parse_performance_bytes(
        field(99, 0, 7) + field(1, 2, field(98, 0, 7))
    )
    assert parsed.header.scenario_name == ""
    assert parsed.events == ()


def test_unknown_groups_are_skipped():
    unknown_group = b"".join(
        [
            varint((99 << 3) | 3),
            field(100, 0, 7),
            varint((99 << 3) | 4),
        ]
    )
    parsed = parse_performance_bytes(
        unknown_group + field(1, 2, field(1, 2, b"scenario"))
    )
    assert parsed.header.scenario_name == "scenario"


def test_known_field_with_wrong_wire_type_is_rejected():
    with pytest.raises(PerformanceParseError, match="wire type"):
        parse_performance_bytes(field(1, 0, 7))


def test_oversized_varint_is_rejected():
    with pytest.raises(PerformanceParseError, match="varint"):
        parse_performance_bytes(field(99, 0, (1 << 64)))


def test_truncated_payload_is_rejected():
    with pytest.raises(PerformanceParseError):
        parse_performance_bytes(field(1, 2, b"unterminated")[:-2])


@pytest.mark.parametrize(
    ("field_number", "payload_type"),
    [
        (2, "shotsFired"),
        (3, "shotsHit"),
        (4, "shotsMissed"),
        (8, "kills"),
        (10, "overshots"),
    ],
)
def test_count_events_remain_timestamped_source_facts(field_number, payload_type):
    event = b"".join(
        [
            field(1, 5, f32(0.125)),
            field(field_number, 2, field(1, 0, 7)),
        ]
    )

    parsed = parse_performance_bytes(field(2, 2, event))

    assert len(parsed.events) == 1
    parsed_event = parsed.events[0]
    assert parsed_event.timestamp == pytest.approx(0.125)
    assert parsed_event.payload_type == payload_type
    assert parsed_event.count == 7
    assert parsed_event.delta is None
    assert parsed_event.value is None
    assert not hasattr(parsed_event, "target_center")
    assert not hasattr(parsed_event, "overshoot_distance")


def test_target_size_remains_a_value_fact_not_target_position():
    event = b"".join(
        [
            field(1, 5, f32(0.25)),
            field(16, 2, field(1, 5, f32(2.5))),
        ]
    )

    parsed = parse_performance_bytes(field(2, 2, event))

    parsed_event = parsed.events[0]
    assert parsed_event.timestamp == pytest.approx(0.25)
    assert parsed_event.payload_type == "targetSize"
    assert parsed_event.value == pytest.approx(2.5)
    assert parsed_event.count is None
    assert parsed_event.delta is None
    assert not hasattr(parsed_event, "target_center")
    assert not hasattr(parsed_event, "target_error")
