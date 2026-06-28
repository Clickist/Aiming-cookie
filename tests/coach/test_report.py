from kovaak_tracker.coach.report import build_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


def test_build_report_without_backend():
    r = build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None)
    assert r.diagnosis.profile.archetype_id in ("long_decel", "decel_jitter", "unclassified")
    assert r.narration is None
    assert "radar" in r.figures
    assert r.notes == []


def test_build_report_llm_failure_degrades():
    class _Boom:
        def generate(self, s, u):
            raise RuntimeError("network down")
    r = build_report(_summary(), None, {}, backend=_Boom())
    assert r.narration is None
    assert any("讲解不可用" in n for n in r.notes)


def test_build_report_with_reference():
    ref = _summary()
    ref["decel_frac"] = {"med": 0.45}
    r = build_report(_summary(), ref, {}, backend=None)
    assert r.diagnosis.comparison is not None
    assert len(r.diagnosis.comparison) > 0


# --- progress loop tests (Task 4) ---
from kovaak_tracker.coach.report import build_progress_report


def test_build_report_persists_history(tmp_path):
    p = tmp_path / "sessions.jsonl"
    build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None, history_path=p)
    build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None, history_path=p)
    from kovaak_tracker.coach.progress import load_history
    assert len(load_history(p)) == 2


def test_build_report_no_history_path_no_save(tmp_path):
    p = tmp_path / "sessions.jsonl"
    build_report(_summary(), None, {}, backend=None)  # no history_path
    assert not p.exists()


def test_build_progress_report_end_to_end(tmp_path):
    p = tmp_path / "sessions.jsonl"
    # seed one older, worse session directly as a JSONL line
    p.write_text(
        '{"timestamp":"2026-06-01","video_ref":"old.mp4","cm_per_360":48,'
        '"summary":{"linearity":{"med":0.25},"sparc":{"med":-9.0},'
        '"decel_frac":{"med":0.80},"reverse_ratio":{"med":0.30},'
        '"peak_speed_deg":{"med":90}},'
        '"profile":{},"issues":[],"narration":null}\n',
        encoding="utf-8",
    )
    cur = {k: {"med": v} for k, v in {
        "linearity": 0.17, "sparc": -6.0, "decel_frac": 0.74,
        "reverse_ratio": 0.22, "peak_speed_deg": 110,
    }.items()}
    rep = build_progress_report(p, cur, ref_summary=None, backend=None)
    assert rep.progress_narration is None
    assert len(rep.comparison_table) == 5
    assert any(r["metric"] == "linearity" and r["verdict"] == "better"
               for r in rep.comparison_table)
    assert rep.trend_figure is not None and rep.comparison_figure is not None
    assert not any("首次" in n for n in rep.notes)  # history was seeded


def test_build_progress_report_empty_history(tmp_path):
    p = tmp_path / "nope.jsonl"
    rep = build_progress_report(p, _summary(), backend=None)
    assert any("首次" in n for n in rep.notes)

