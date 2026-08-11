from kovaak_tracker.metric_definitions import METRIC_DEFINITIONS, get_metric_definition


STATIC_PUBLIC_KEYS = {
    "direction_reverse_ratio",
    "displacement",
    "flick_count",
    "flick_path_length",
    "mean_acceleration",
    "mean_speed",
    "movement_duration_ms",
    "path_length",
    "straightness",
    "time_to_peak_ms",
    "trough_depth_ratio",
}


def test_definitions_are_display_only():
    assert all(set(definition) == {"name", "description"} for definition in METRIC_DEFINITIONS.values())
    assert all(all(isinstance(value, str) and value for value in definition.values()) for definition in METRIC_DEFINITIONS.values())


def test_static_public_metric_keys_have_legacy_display_definitions():
    assert STATIC_PUBLIC_KEYS <= METRIC_DEFINITIONS.keys()


def test_high_risk_descriptions_match_the_measured_fact():
    assert get_metric_definition("target_switching.transition_time_ms") == {
        "name": "切换移动时长",
        "description": "从离开上一目标到捕获下一目标的时长",
    }
    assert get_metric_definition("dynamic_clicking.target_state_accuracy") == {
        "name": "关联目标成功比例",
        "description": "已关联且具有结果的点击中，记录为成功的比例",
    }
    assert "预判" not in get_metric_definition("dynamic_clicking.predictive_lead")["description"]
    assert "目标" not in get_metric_definition("settle_duration_ms")["description"]
    assert "不从跟踪样本或采集对齐结果推断" in get_metric_definition(
        "continuous_tracking.human_response_latency_ms"
    )["description"]
