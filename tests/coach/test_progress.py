from kovaak_tracker.coach.progress import (
    Session, ProgressReport, save_session, load_history,
)


def _fake_report():
    """Minimal CoachReport-like object for save_session (duck-typed)."""
    class _P:
        archetype_id = "decel_jitter"
        label = "减速抖动型"
        confidence = 1.0
        secondary_tags = ["发力不足型"]
    class _I:
        signal = "sparc low"
        severity = "fix"
        priority = 1
    class _D:
        summary = {"linearity": {"med": 0.17}}
        profile = _P()
        issues = [_I()]
    class _R:
        diagnosis = _D()
        narration = "讲解"
        notes = []
    return _R()


def test_session_frozen():
    s = Session("2026-06-28T10:00", "v.mp4", 48.0, {}, {}, [], None)
    try:
        s.timestamp = "x"  # type: ignore[misc]
        assert False
    except Exception:
        pass


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "sessions.jsonl"
    save_session(_fake_report(), {"video_ref": "v.mp4", "cm_per_360": 48.0}, history_path=p)
    save_session(_fake_report(), {"video_ref": "v2.mp4", "cm_per_360": 48.0}, history_path=p)
    hist = load_history(p)
    assert len(hist) == 2
    assert hist[0].video_ref == "v.mp4"
    assert hist[1].profile["label"] == "减速抖动型"
    assert hist[0].cm_per_360 == 48.0


def test_load_history_missing_file(tmp_path):
    assert load_history(tmp_path / "nope.jsonl") == []


def test_load_history_skips_malformed(tmp_path):
    p = tmp_path / "sessions.jsonl"
    p.write_text(
        '{"timestamp":"t","video_ref":"v","cm_per_360":48,"summary":{},"profile":{},"issues":[],"narration":null}\n'
        "BROKEN LINE\n"
        "not even json\n",
        encoding="utf-8",
    )
    hist = load_history(p)
    assert len(hist) == 1  # only the valid line


from kovaak_tracker.coach.progress import build_trend, build_comparison


def _session(summary, ts="2026-06-01"):
    return Session(ts, "v.mp4", 48.0, summary, {}, [], None)


def test_trend_collects_med_per_metric():
    hist = [
        _session({"linearity": {"med": 0.20}}),
        _session({"linearity": {"med": 0.17}}),
        _session({"linearity": {"med": 0.15}}),
    ]
    trend = build_trend(hist, metrics=("linearity",))
    assert [v for _, v in trend["linearity"]] == [0.20, 0.17, 0.15]


def test_trend_skips_nan_and_missing():
    hist = [
        _session({"linearity": {"med": 0.20}}),
        _session({"linearity": {"med": float("nan")}}),
        _session({}),  # metric absent
    ]
    trend = build_trend(hist, metrics=("linearity",))
    assert len(trend["linearity"]) == 1  # only first survives


def test_comparison_verdicts_vs_baseline():
    baseline = [{"linearity": {"med": 0.20}, "sparc": {"med": -7.0},
                 "peak_speed_deg": {"med": 100}}]
    current = {"linearity": {"med": 0.15}, "sparc": {"med": -5.0},
               "peak_speed_deg": {"med": 120}}
    rows = {r["metric"]: r for r in build_comparison(baseline, current)}
    assert rows["linearity"]["verdict"] == "better"   # lower better, 0.15 < 0.20
    assert rows["sparc"]["verdict"] == "better"        # higher(≈0) better, -5 > -7
    assert rows["peak_speed_deg"]["verdict"] == "better"  # higher better, 120 > 100
    assert rows["linearity"]["baseline"] == 0.20
    assert rows["linearity"]["last"] == 0.20  # single-history: last == baseline
    assert rows["linearity"]["ref"] is None   # no ref_summary


def test_comparison_empty_history_info_verdict():
    rows = build_comparison([], {"linearity": {"med": 0.15}})
    assert rows[0]["verdict"] == "info"
