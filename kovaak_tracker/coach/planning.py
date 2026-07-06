"""Plan adjustment: trend/comparison -> adaptive TrainingPlan.

Progress loop scope ④. Deterministic rule engine (academic roots, see
docs/coach-prescription-manual.md); the LLM only optionally translates
(narrator), never reasons about diagnosis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..advice import Prescription

N_MIN = 3
REST_GAP_DAYS = 1.0

SCHEDULE_NOTE = (
    "建议每周复测 2-3 次、间隔练习（而非每天）——给隐式适应与自我觉察留空间"
    "（guidance hypothesis：反馈过频损害长期学习）。"
)

_INTERLEAVE_REASON = (
    "相对基线无变化——换结构而非加量：交错多场景（块状几轮→递增交错）长期保留更好。"
    "注意：感觉进步快≠长期记住，块状练习会让人过度自信（Simon & Bjork 2001）。"
)

# metric (TREND_METRICS) -> advice Finding.signal
# decel_frac 是带状指标：high(>0.65)=fix"减速拖沓" / low(<0.40)=watch"刹车太急"，
# 实际发哪个由 advice 依当前值决定，这里给出 fallback 元组让 planning 两个都能找到。
_METRIC_SIGNAL = {
    "decel_frac": ("decel_frac high", "decel_frac low"),
    "sparc": "sparc low",
    "reverse_ratio": "reverse_ratio high",
    "linearity": "linearity high",
    "peak_speed_deg": "peak_speed below reference",
}

_SEVERITY_WEIGHT = {"fix": 3, "watch": 2, "info": 1}


@dataclass(frozen=True)
class PlanAdjustment:
    """One training-structure adjustment for one signal."""
    kind: str                       # "interleave" | "regress_focus" | "maintain" | "rest"
    target_metric: str | None       # TREND_METRICS 之一；rest=None
    scenarios: list[Prescription]   # 交错编排的训练场景（来自 advice 处方池）
    reason: str                     # 人话理由
    evidence: str                   # 理论锚点引用


@dataclass(frozen=True)
class TrainingPlan:
    focus_metrics: list[str]                # stall/worse 指标（下阶段重点）
    adjustments: list[PlanAdjustment]
    schedule_note: str                      # 复测频率建议（§2.2 guidance）
    evidence_anchors: list[str]             # 本 plan 引用的理论条目
    notes: list[str] = field(default_factory=list)


def build_plan(trend, comparison, history, findings) -> TrainingPlan:
    """trend/comparison/history -> adaptive TrainingPlan.

    `trend` 目前未参与规则判定（stall 用 comparison.verdict，见 spec §11 Q2），
    保留签名供未来 slope-based 停滞检测。

    Rules (see docs/coach-prescription-manual.md):
      verdict=worse                       -> regress_focus (换/补处方, 交错)
      verdict=same & len(history)>=N_MIN  -> interleave (渐进 hybrid + 元认知对抗)
      verdict=better                      -> maintain (scenarios 可空)
      最近两次 session 间隔 < REST_GAP_DAYS -> rest (间隔练习 + 休息)
      len(history) < N_MIN                -> 不判 stall/regress, notes 标注
    """
    findings_by_signal = {f.signal: f for f in findings}
    focus_metrics: list[str] = []
    adjustments: list[PlanAdjustment] = []
    anchors: list[str] = []

    def add_anchor(a: str) -> None:
        if a not in anchors:
            anchors.append(a)

    # rest（独立于 metric，看历史复测间隔）
    rest = _maybe_rest(history)
    if rest is not None:
        adjustments.append(rest)
        add_anchor("§1.2 Ericsson 1993 训练量上限")
        add_anchor("§2.2 Salmoni 1984 guidance hypothesis")

    # metric 按 severity 排序（fix>watch>info），focus 优先
    def severity(metric: str) -> int:
        f = _finding_for(metric, findings_by_signal)
        return _SEVERITY_WEIGHT.get(f.severity if f else "watch", 2)

    rows = sorted(comparison, key=lambda r: -severity(r["metric"]))

    for row in rows:
        metric = row["metric"]
        verdict = row["verdict"]
        if verdict not in ("worse", "same", "better"):
            continue  # info / missing
        scenarios = _scenarios_for(metric, findings_by_signal)
        if verdict == "worse":
            focus_metrics.append(metric)
            adjustments.append(PlanAdjustment(
                kind="regress_focus", target_metric=metric, scenarios=scenarios,
                reason=f"{metric} 相对基线退步——换/补处方场景，交错练习。",
                evidence="§1.3 CI 交错 + §1.1 制动代价（coach-prescription-manual.md）",
            ))
            add_anchor("§1.3 contextual interference")
            add_anchor("§1.1 制动代价")
        elif verdict == "same" and len(history) >= N_MIN:
            focus_metrics.append(metric)
            adjustments.append(PlanAdjustment(
                kind="interleave", target_metric=metric, scenarios=scenarios,
                reason=_INTERLEAVE_REASON,
                evidence="§4.2 渐进 hybrid + §2.2 元认知对抗",
            ))
            add_anchor("§4.2 渐进 hybrid")
            add_anchor("§2.2 元认知过度自信")
            add_anchor("§1.2 Ericsson 训练量上限")
        elif verdict == "better":
            adjustments.append(PlanAdjustment(
                kind="maintain", target_metric=metric, scenarios=scenarios,
                reason=f"{metric} 进步——保持当前训练，别乱改。",
                evidence="",
            ))

    notes: list[str] = []
    if len(history) < N_MIN:
        notes.append(f"历史 {len(history)} 次 < N_MIN={N_MIN}，仅观测不判停滞。")

    return TrainingPlan(
        focus_metrics=focus_metrics,
        adjustments=adjustments,
        schedule_note=SCHEDULE_NOTE,
        evidence_anchors=anchors,
        notes=notes,
    )


def _signals_for(metric: str):
    """_METRIC_SIGNAL 值归一为 tuple：str 包成单元素，tuple 原样返回。"""
    sig = _METRIC_SIGNAL.get(metric)
    if sig is None:
        return ()
    return sig if isinstance(sig, tuple) else (sig,)


def _finding_for(metric: str, findings_by_signal: dict):
    """按 _METRIC_SIGNAL 顺序找第一个命中的 finding（支持 fallback 元组）。"""
    for sig in _signals_for(metric):
        f = findings_by_signal.get(sig)
        if f is not None:
            return f
    return None


def _scenarios_for(metric: str, findings_by_signal: dict) -> list[Prescription]:
    """Pull prescriptions from the finding matching this metric's signal."""
    f = _finding_for(metric, findings_by_signal)
    return list(f.prescriptions) if f else []


def _maybe_rest(history) -> PlanAdjustment | None:
    """If the last two sessions are < REST_GAP_DAYS apart, suggest rest."""
    if len(history) < 2:
        return None
    last = _parse_ts(history[-1].timestamp)
    prev = _parse_ts(history[-2].timestamp)
    if last is None or prev is None:
        return None
    gap = last - prev
    # gap>=0 防逆序/未来时间戳的 history 假触发（save_session 时序下不发生，防御）
    if timedelta(0) <= gap < timedelta(days=REST_GAP_DAYS):
        return PlanAdjustment(
            kind="rest", target_metric=None, scenarios=[],
            reason=f"最近两次复测间隔 {gap.total_seconds()/86400:.1f} 天 < {REST_GAP_DAYS}"
            f"——间隔练习 + 休息（过度练习有 staleness/burnout 风险）。",
            evidence="§1.2 Ericsson + §2.2 guidance",
        )
    return None


def _parse_ts(s: str):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
