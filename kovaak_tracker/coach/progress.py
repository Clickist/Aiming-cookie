"""Progress loop: persist sessions, build trend/comparison, ProgressReport.

Scope B (persistence + trend + comparison). Plan adjustment (dynamic
prescriptions) is a later spec. See
docs/superpowers/specs/2026-06-28-progress-loop-design.md."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..advice import THRESHOLDS
from ..settings import OUTPUT_DIR

DEFAULT_HISTORY_PATH = OUTPUT_DIR / "history" / "sessions.jsonl"

# decel_frac 健康带（与 advice.advise 带状判定同源，advice 是权威语义：
# <low 发 watch"刹车太急"，>high 发 fix"减速拖沓"，中间健康）。
# 带外为病态值：progress 不发 better/worse（返回 info），让 advice 的 watch/fix 主导。
_DECEL_FRAC_BAND = (THRESHOLDS["decel_frac_low"], THRESHOLDS["decel_frac_high"])


@dataclass(frozen=True)
class Session:
    """One persisted analysis (one JSONL line)."""
    timestamp: str
    video_ref: str
    cm_per_360: float | None
    summary: dict
    profile: dict
    issues: list[dict]
    narration: str | None


@dataclass(frozen=True)
class ProgressReport:
    trend_figure: Any
    comparison_figure: Any
    comparison_table: list[dict]
    progress_narration: str | None
    plan: Any = None              # TrainingPlan | None（分层：progress 数据层不依赖 planning，用 Any）
    plan_narration: str | None = None
    notes: list[str] = field(default_factory=list)


def save_session(report, meta, history_path=DEFAULT_HISTORY_PATH) -> None:
    """Extract a Session from a CoachReport + meta and append it to JSONL."""
    session = _report_to_session(report, meta)
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(session), ensure_ascii=False) + "\n")


def load_history(history_path=DEFAULT_HISTORY_PATH) -> list[Session]:
    """Read JSONL -> list[Session]; skip blank/malformed lines."""
    path = Path(history_path)
    if not path.exists():
        return []
    out: list[Session] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Session(**json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return out


def _report_to_session(report, meta):
    meta = meta or {}
    diag = report.diagnosis
    return Session(
        timestamp=meta.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
        video_ref=meta.get("video_ref", ""),
        cm_per_360=meta.get("cm_per_360"),
        summary=dict(diag.summary),
        profile={
            "archetype_id": diag.profile.archetype_id,
            "label": diag.profile.label,
            "confidence": diag.profile.confidence,
            "secondary_tags": list(diag.profile.secondary_tags),
        },
        issues=[{"signal": i.signal, "severity": i.severity, "priority": i.priority}
                for i in diag.issues],
        narration=report.narration,
    )


TREND_METRICS = ("linearity", "sparc", "decel_frac", "reverse_ratio", "peak_speed_deg")


def build_trend(history, metrics=TREND_METRICS) -> dict:
    """{metric: [(timestamp, med), ...]} skipping NaN/missing."""
    out: dict[str, list] = {}
    for m in metrics:
        series = []
        for s in history:
            v = _med(_summary_of(s), m)
            if v is not None:
                series.append((s.timestamp, v))
        out[m] = series
    return out


def build_comparison(history, current, ref_summary=None) -> list[dict]:
    """Per-metric rows: current vs baseline(history[0]) / last(history[-1]) / ref.

    verdict: better/worse/same (±5% vs baseline, direction-aware) or info (missing).

    Each history item may be a Session or a bare summary dict (duck-typed):
    production passes Sessions from load_history; tests pass summary dicts.
    """
    baseline = _summary_of(history[0]) if history else {}
    last = _summary_of(history[-1]) if history else {}
    rows = []
    for m in TREND_METRICS:
        cur = _med(current, m)
        base = _med(baseline, m)
        lst = _med(last, m)
        ref = _med(ref_summary or {}, m)
        rows.append({
            "metric": m, "current": cur, "baseline": base,
            "last": lst, "ref": ref, "verdict": _verdict(m, cur, base),
        })
    return rows


def _summary_of(item):
    """Return the summary dict from a Session (s.summary) or pass a dict through."""
    return item.summary if hasattr(item, "summary") else item


def _med(summary, key):
    v = summary.get(key)
    if isinstance(v, dict):
        v = v.get("med")
    return v if isinstance(v, (int, float)) and v == v else None  # NaN guard


def _verdict(metric, current, baseline):
    if current is None or baseline is None:
        return "info"
    # decel_frac 是带状指标（非单调），单独走健康带判定：
    # 都在带内才比"朝中心收敛"，任一病态返回 info（让 advice 主导）。
    if metric == "decel_frac":
        return _decel_frac_verdict(current, baseline)
    # higher-is-better: sparc (closer to 0), peak_speed; lower-is-better: the rest
    higher_better = metric in ("sparc", "peak_speed_deg")
    # Use a difference normalized by |baseline| so the direction holds for
    # signed values (a plain current/baseline ratio flips sign for negatives,
    # e.g. sparc -5 vs -7 is an improvement but ratio 0.71 < 1).
    mag = abs(baseline) if baseline != 0 else 1.0
    delta = (current - baseline) / mag
    rel = delta if higher_better else -delta
    if rel > 0.05:
        return "better"
    if rel < -0.05:
        return "worse"
    return "same"


def _decel_frac_verdict(current, baseline):
    """健康带内单调：self 与 baseline 都在 [low, high] 内时，朝带中心
    (low+high)/2 收敛 = better，远离 = worse；任一带外（病态）= info。

    消除"病态值被误判进步"：self=0.30（刹车太急）/ baseline=0.50 不再判 better。
    """
    low, high = _DECEL_FRAC_BAND
    if not (low <= current <= high) or not (low <= baseline <= high):
        return "info"
    center = (low + high) / 2
    dist_cur = abs(current - center)
    dist_base = abs(baseline - center)
    # 归一化参照 baseline 到中心的距离；为 0（baseline 正中）时取带半宽，避免除零。
    half_width = (high - low) / 2
    mag = dist_base if dist_base > 0 else half_width
    rel = (dist_base - dist_cur) / mag  # 正 = current 更近中心 = better
    if rel > 0.05:
        return "better"
    if rel < -0.05:
        return "worse"
    return "same"
