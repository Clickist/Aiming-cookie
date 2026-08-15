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


def test_parser_preserves_known_field_presence_and_event_source_order():
    header = b"".join(
        [
            field(1, 2, b"scenario"),
            field(3, 0, 0),
        ]
    )
    first_event = b"".join(
        [
            field(1, 5, f32(0.0)),
            field(2, 2, field(1, 0, 0)),
        ]
    )
    second_event = b"".join(
        [
            field(1, 5, f32(0.0)),
            field(3, 2, field(1, 0, 0)),
        ]
    )

    parsed = parse_performance_bytes(
        b"".join(
            [
                field(1, 2, header),
                field(2, 2, first_event),
                field(2, 2, second_event),
            ]
        )
    )

    assert parsed.header.field_presence == {
        "scenario_name": "present",
        "scenario_hash": "source_absent",
        "challenge_start_utc": "present",
        "schema_version": "source_absent",
        "challenge_profile": "source_absent",
    }
    assert [event.source_event_index for event in parsed.events] == [0, 1]
    assert [event.field_presence["timestamp"] for event in parsed.events] == ["present", "present"]
    assert [event.field_presence["shotsFired"] for event in parsed.events] == ["present", "source_absent"]
    assert [event.field_presence["shotsHit"] for event in parsed.events] == ["source_absent", "present"]


def test_profile_presence_distinguishes_explicit_zero_empty_and_empty_packed_values():
    profile = b"".join(
        [
            field(1, 5, f32(0.0)),
            field(2, 2, b""),
            field(3, 2, b""),
            field(4, 0, 0),
            field(5, 2, b""),
            field(6, 0, 0),
            field(7, 2, b""),
            field(8, 2, b""),
            field(9, 5, f32(0.0)),
            field(10, 5, f32(0.0)),
            field(11, 5, f32(0.0)),
            field(12, 5, f32(0.0)),
        ]
    )

    parsed = parse_performance_bytes(field(1, 2, field(5, 2, profile)))
    parsed_profile = parsed.header.challenge_profile

    assert set(parsed_profile.field_presence.values()) == {"present"}
    assert parsed_profile.player_profile == ""
    assert parsed_profile.added_bots == ("",)
    assert parsed_profile.bot_max_lives == ()
    assert parsed_profile.bot_teams == ()
    assert parsed_profile.time_limit == 0.0


def test_multiple_known_event_payloads_are_rejected():
    event = b"".join(
        [
            field(1, 5, f32(1.25)),
            field(2, 2, field(1, 0, 7)),
            field(5, 2, field(1, 5, f32(0.5))),
        ]
    )

    with pytest.raises(PerformanceParseError, match="multiple.*payload"):
        parse_performance_bytes(field(2, 2, event))


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (field(2, 2, field(1, 0, 7)), "timestamp"),
        (field(1, 5, f32(1.25)), "payload"),
    ],
)
def test_event_requires_timestamp_and_payload(event, message):
    with pytest.raises(PerformanceParseError, match=message):
        parse_performance_bytes(field(2, 2, event))


@pytest.mark.parametrize(
    "timestamp", [float("nan"), float("inf"), float("-inf"), -0.001]
)
def test_event_timestamp_must_be_finite_and_non_negative(timestamp):
    event = b"".join(
        [
            field(1, 5, f32(timestamp)),
            field(2, 2, field(1, 0, 7)),
        ]
    )

    with pytest.raises(PerformanceParseError, match="timestamp"):
        parse_performance_bytes(field(2, 2, event))


def test_event_timestamp_is_half_up_quantized_and_end_exclusive():
    profile = b"".join([field(1, 5, f32(1.0)), field(10, 5, f32(1.0))])
    header = field(1, 2, field(5, 2, profile))
    accepted = b"".join(
        [field(1, 5, f32(0.0005)), field(2, 2, field(1, 0, 1))]
    )

    parsed = parse_performance_bytes(header + field(2, 2, accepted))
    assert parsed.events[0].timestamp_ms == 1

    for rejected_timestamp in (1.0, 1.001):
        rejected = b"".join(
            [
                field(1, 5, f32(rejected_timestamp)),
                field(2, 2, field(1, 0, 1)),
            ]
        )
        with pytest.raises(PerformanceParseError, match="half-open"):
            parse_performance_bytes(header + field(2, 2, rejected))


def test_post_window_events_are_dropped_and_counted():
    profile = b"".join([field(1, 5, f32(1.0)), field(10, 5, f32(1.0))])
    header = field(1, 2, field(5, 2, profile))
    in_window = b"".join(
        [field(1, 5, f32(0.5)), field(2, 2, field(1, 0, 1))]
    )
    post_window = b"".join(
        [field(1, 5, f32(1.5)), field(2, 2, field(1, 0, 2))]
    )

    parsed = parse_performance_bytes(
        b"".join([header, field(2, 2, in_window), field(2, 2, post_window)])
    )

    assert [event.timestamp_ms for event in parsed.events] == [500]
    assert [event.source_event_index for event in parsed.events] == [0]
    assert parsed.source_event_count == 2
    assert parsed.post_window_event_count == 1
    assert parsed.timeline_status == "complete"


def test_events_entirely_outside_window_are_rejected():
    profile = b"".join([field(1, 5, f32(1.0)), field(10, 5, f32(1.0))])
    header = field(1, 2, field(5, 2, profile))
    post_window = b"".join(
        [field(1, 5, f32(1.5)), field(2, 2, field(1, 0, 2))]
    )

    with pytest.raises(PerformanceParseError, match="half-open"):
        parse_performance_bytes(
            b"".join([header, field(2, 2, post_window), field(2, 2, post_window)])
        )


def test_unknown_payload_is_omitted_but_keeps_its_source_order_hole():
    unknown_payload = b"".join(
        [
            field(1, 5, f32(0.25)),
            field(99, 2, field(1, 0, 7)),
        ]
    )
    known_payload = b"".join(
        [
            field(1, 5, f32(0.25)),
            field(2, 2, field(1, 0, 7)),
        ]
    )

    parsed = parse_performance_bytes(
        b"".join(
            [
                field(99, 0, 7),
                field(2, 2, unknown_payload),
                field(2, 2, known_payload),
            ]
        )
    )

    assert parsed.unknown_field_observability == "detected"
    assert parsed.timeline_status == "partial"
    assert parsed.omitted_event_indexes == (0,)
    assert [event.source_event_index for event in parsed.events] == [1]


@pytest.mark.parametrize(
    ("payload_field", "nested_value"),
    [
        (2, field(1, 0, 7)),
        (5, field(1, 5, f32(0.5))),
    ],
)
def test_unknown_nested_field_after_known_value_is_detected(
    payload_field, nested_value,
):
    event = b"".join(
        [
            field(1, 5, f32(0.25)),
            field(payload_field, 2, nested_value + field(99, 0, 7)),
        ]
    )

    parsed = parse_performance_bytes(field(2, 2, event))

    assert parsed.events[0].unknown_field_observability == "detected"
    assert parsed.unknown_field_observability == "detected"


def test_known_only_payload_has_proven_unknown_field_observability_none():
    event = b"".join(
        [
            field(1, 5, f32(0.25)),
            field(2, 2, field(1, 0, 7)),
        ]
    )

    parsed = parse_performance_bytes(field(2, 2, event))

    assert parsed.events[0].unknown_field_observability == "none"
    assert parsed.unknown_field_observability == "none"


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
