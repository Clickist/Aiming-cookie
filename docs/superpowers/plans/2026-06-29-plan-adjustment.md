# ④ Plan Adjustment（动态处方）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `build_progress_report` 加一层基于趋势的确定性 `TrainingPlan`——指标停滞/退步时推渐进-hybrid 交错（带元认知对抗话术），复测过频提示休息，不默认"练不够"。

**Architecture:** 新建 `coach/planning.py` 规则引擎（`build_plan` 消费 trend/comparison/history/findings → `TrainingPlan`），复用 `advice.Prescription` 作场景池；`ProgressReport` 加 `plan`/`plan_narration` 字段（向后兼容）；`narrator` 加可选 plan 翻译；`report.build_progress_report` 编排。规则全学术根基（见 `docs/coach-prescription-manual.md`），LLM 仅翻译不推理。

**Tech Stack:** Python 3.9（本机 3.9.7，`Optional`/`| None` 运行时别名已处理）、dataclasses、pytest（全 mock，不依赖真实 LLM/SDK）。

## Global Constraints

- 分支 `coach/plan-adjustment`，不碰 main、不 push。
- Python 3.9 兼容：dataclass 字段类型用 `list[X]`（3.9 原生支持 PEP 585），`X | None` 在注解里可用（`from __future__ import annotations` 已在所有 coach 模块顶部）。
- 处方场景池复用 `kovaak_tracker.advice.Prescription`（scenario + reason），不新造类型。
- 诊断规则只用学术根基；社区内容只进 narrator 文案（见手册 §0）。
- `Prescription` 导入自 `..advice`（planning.py 在 `coach/` 下，advice 在父包）。
- 命名/copy 沿用现有 coach 模块中文 + 英文术语配人话风格。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `kovaak_tracker/coach/planning.py` | `build_plan` 规则引擎 + `TrainingPlan`/`PlanAdjustment` dataclass | **Create** |
| `kovaak_tracker/coach/progress.py:31-37` | `ProgressReport` 加 `plan`/`plan_narration` 字段（向后兼容）| **Modify** |
| `kovaak_tracker/coach/narrator.py` | `PLAN_SYSTEM_PROMPT` + `generate_plan_narration` | **Modify**（追加）|
| `kovaak_tracker/coach/report.py:44-68` | `build_progress_report` 调 `build_plan` + plan narrator | **Modify** |
| `tests/coach/test_planning.py` | planning 规则全测试（合成历史，mock）| **Create** |
| `tests/coach/test_progress.py` | ProgressReport 新字段向后兼容 | **Modify**（追加）|
| `tests/coach/test_narrator.py` | plan narrator mock | **Modify**（追加）|
| `tests/coach/test_report.py` | `build_progress_report` plan 端到端 | **Modify**（追加）|

依赖：Task 1（planning）→ Task 2（progress 字段）/ Task 3（narrator）可并行 → Task 4（report 集成）。

---

## Task 1: planning.py — 数据模型 + build_plan 规则引擎

**Files:**
- Create: `kovaak_tracker/coach/planning.py`
- Test: `tests/coach/test_planning.py`

**Interfaces:**
- Consumes: `kovaak_tracker.advice.Prescription`（`Prescription(scenario: str, reason: str)`）、`kovaak_tracker.advice.Finding`（`.signal`/`.severity`/`.prescriptions`）、`progress.Session`（`.timestamp`）、`build_comparison` 返回的 row（`{metric, current, baseline, last, ref, verdict}`）
- Produces: `TrainingPlan`、`PlanAdjustment`、`build_plan(trend, comparison, history, findings) -> TrainingPlan`、常量 `N_MIN=3`、`REST_GAP_DAYS=1.0`、`SCHEDULE_NOTE`

- [ ] **Step 1: 写测试文件（8 个测试，完整）**

Create `tests/coach/test_planning.py`:

```python
from datetime import datetime, timedelta

from kovaak_tracker.advice import Finding, Prescription
from kovaak_tracker.coach.planning import (
    build_plan, TrainingPlan, PlanAdjustment, N_MIN, REST_GAP_DAYS,
)
from kovaak_tracker.coach.progress import Session


def _session(summary, ts):
    return Session(ts, "v.mp4", 48.0, summary, {}, [], None)


def _row(metric, verdict):
    return {"metric": metric, "current": 1.0, "baseline": 1.0,
            "last": 1.0, "ref": None, "verdict": verdict}


def _finding(signal, severity="fix"):
    return Finding(signal, severity, "diag", [Prescription("pasu", "r"), Prescription("1w6ts", "r2")])


# --- 骨架 + schedule_note ---
def test_build_plan_returns_schedule_note():
    plan = build_plan({}, [], [], [])
    assert isinstance(plan, TrainingPlan)
    assert "每周" in plan.schedule_note or "间隔" in plan.schedule_note


# --- 数据不足降级 ---
def test_build_plan_insufficient_history_note():
    hist = [_session({}, "2026-06-01")]
    plan = build_plan({}, [], hist, [])
    assert any("不足" in n or "N_MIN" in n for n in plan.notes)


# --- stall -> interleave ---
def test_build_plan_stall_triggers_interleave():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]  # 4 sessions >= N_MIN=3
    comp = [_row("sparc", "same")]
    findings = [_finding("sparc low")]
    plan = build_plan({}, comp, hist, findings)
    assert "sparc" in plan.focus_metrics
    inter = [a for a in plan.adjustments if a.kind == "interleave"]
    assert len(inter) == 1 and inter[0].target_metric == "sparc"
    # 元认知对抗话术
    assert "感觉进步快" in inter[0].reason or "过度自信" in inter[0].reason
    # 渐进 hybrid + 元认知锚点
    assert any("hybrid" in a or "渐进" in a for a in plan.evidence_anchors) or \
           any("元认知" in a for a in plan.evidence_anchors)


# --- worse -> regress_focus ---
def test_build_plan_regress_triggers_regress_focus():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("linearity", "worse")]
    findings = [_finding("linearity high")]
    plan = build_plan({}, comp, hist, findings)
    assert "linearity" in plan.focus_metrics
    reg = [a for a in plan.adjustments if a.kind == "regress_focus"]
    assert len(reg) == 1 and reg[0].target_metric == "linearity"


# --- better -> maintain, scenarios 可空 ---
def test_build_plan_better_maintains():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("decel_frac", "better")]
    plan = build_plan({}, comp, hist, [])  # no finding -> scenarios empty
    maint = [a for a in plan.adjustments if a.kind == "maintain"]
    assert len(maint) == 1
    assert maint[0].scenarios == []  # decel_frac in health band -> no finding -> empty
    assert "decel_frac" not in plan.focus_metrics


# --- rest: 间隔 < REST_GAP_DAYS ---
def test_build_plan_rest_high_frequency():
    # 两次 session 同一天（间隔 0 天 < 1.0）
    hist = [
        _session({}, "2026-06-29T10:00:00"),
        _session({}, "2026-06-29T18:00:00"),
        _session({}, "2026-06-29T20:00:00"),
    ]
    plan = build_plan({}, [], hist, [])
    rests = [a for a in plan.adjustments if a.kind == "rest"]
    assert len(rests) == 1
    assert rests[0].target_metric is None


def test_build_plan_no_rest_when_spaced():
    hist = [_session({}, f"2026-06-{d:02d}") for d in (1, 5, 10)]  # 间隔 >=4 天
    plan = build_plan({}, [], hist, [])
    assert not any(a.kind == "rest" for a in plan.adjustments)


# --- focus 按 severity 排序 ---
def test_build_plan_focus_severity_order():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("reverse_ratio", "same"), _row("sparc", "same")]
    findings = [
        _finding("reverse_ratio high", "watch"),  # weight 2
        _finding("sparc low", "fix"),              # weight 3 — 应排前
    ]
    plan = build_plan({}, comp, hist, findings)
    # sparc(fix) 在 reverse_ratio(watch) 前
    assert plan.focus_metrics.index("sparc") < plan.focus_metrics.index("reverse_ratio")
```

- [ ] **Step 2: 跑测试确认全红**

Run: `python -m pytest tests/coach/test_planning.py -v`
Expected: FAIL（`ModuleNotFoundError: kovaak_tracker.coach.planning`）

- [ ] **Step 3: 写 planning.py 完整实现**

Create `kovaak_tracker/coach/planning.py`:

```python
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
_METRIC_SIGNAL = {
    "decel_frac": "decel_frac high",
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
        sig = _METRIC_SIGNAL.get(metric)
        f = findings_by_signal.get(sig) if sig else None
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


def _scenarios_for(metric: str, findings_by_signal: dict) -> list[Prescription]:
    """Pull prescriptions from the finding matching this metric's signal."""
    sig = _METRIC_SIGNAL.get(metric)
    f = findings_by_signal.get(sig) if sig else None
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
    if gap < timedelta(days=REST_GAP_DAYS):
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
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tests/coach/test_planning.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/planning.py tests/coach/test_planning.py
git commit -m "feat(coach): build_plan rule engine + TrainingPlan (④ plan-adjustment T1)"
```

---

## Task 2: progress.py — ProgressReport 加 plan/plan_narration 字段

**Files:**
- Modify: `kovaak_tracker/coach/progress.py:31-37`（`ProgressReport` dataclass）
- Test: `tests/coach/test_progress.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `TrainingPlan`（仅类型引用，避免循环导入——用 `TYPE_CHECKING` 或 `Any`）
- Produces: `ProgressReport.plan: TrainingPlan | None`（默认 None）、`ProgressReport.plan_narration: str | None`（默认 None）

- [ ] **Step 1: 写失败测试**

追加到 `tests/coach/test_progress.py` 末尾：

```python
def test_progress_report_plan_fields_default_none():
    """新字段 plan/plan_narration 默认 None，向后兼容旧构造。"""
    from kovaak_tracker.coach.progress import ProgressReport
    rep = ProgressReport(
        trend_figure=None, comparison_figure=None,
        comparison_table=[], progress_narration=None,
    )
    assert rep.plan is None
    assert rep.plan_narration is None
    assert rep.notes == []
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/coach/test_progress.py::test_progress_report_plan_fields_default_none -v`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument` 或字段不存在）

- [ ] **Step 3: 加字段到 ProgressReport**

Edit `kovaak_tracker/coach/progress.py`，把 `ProgressReport` 改为（在 `progress_narration` 后、`notes` 前插入两字段）:

```python
@dataclass(frozen=True)
class ProgressReport:
    trend_figure: Any
    comparison_figure: Any
    comparison_table: list[dict]
    progress_narration: str | None
    plan: Any = None              # TrainingPlan | None（避免循环导入用 Any）
    plan_narration: str | None = None
    notes: list[str] = field(default_factory=list)
```

> 用 `Any` 而非 `TrainingPlan` 避免 `planning.py` ↔ `progress.py` 循环导入（planning 导入 advice，progress 不该导入 planning）。运行时 report.py 传入真实 TrainingPlan。

- [ ] **Step 4: 跑测试确认绿 + 回归**

Run: `python -m pytest tests/coach/test_progress.py tests/coach/test_report.py -v`
Expected: 全 PASS（新测试 + 既有 progress/report 测试不破）

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/progress.py tests/coach/test_progress.py
git commit -m "feat(coach): ProgressReport.plan/plan_narration fields (④ T2)"
```

---

## Task 3: narrator.py — PLAN_SYSTEM_PROMPT + generate_plan_narration

**Files:**
- Modify: `kovaak_tracker/coach/narrator.py`（文件末尾追加）
- Test: `tests/coach/test_narrator.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `TrainingPlan`（结构：`focus_metrics`/`adjustments[].{kind,target_metric,scenarios,reason,evidence}`/`schedule_note`/`evidence_anchors`）、`providers.LLMBackend`
- Produces: `PLAN_SYSTEM_PROMPT`、`generate_plan_narration(plan, backend) -> str`、`build_plan_user_prompt(plan) -> str`

- [ ] **Step 1: 写失败测试**

追加到 `tests/coach/test_narrator.py` 末尾：

```python
from kovaak_tracker.coach.planning import TrainingPlan, PlanAdjustment
from kovaak_tracker.coach.narrator import (
    generate_plan_narration, build_plan_user_prompt, PLAN_SYSTEM_PROMPT,
)


def _plan():
    return TrainingPlan(
        focus_metrics=["sparc"],
        adjustments=[PlanAdjustment(
            kind="interleave", target_metric="sparc", scenarios=[],
            reason="交错多场景长期保留更好。感觉进步快≠长期记住。",
            evidence="§4.2 渐进 hybrid",
        )],
        schedule_note="每周复测 2-3 次、间隔练习。",
        evidence_anchors=["§4.2 渐进 hybrid"],
        notes=[],
    )


def test_plan_system_prompt_has_anti_hallucination():
    assert "不要编造" in PLAN_SYSTEM_PROMPT or "铁律" in PLAN_SYSTEM_PROMPT


def test_build_plan_user_prompt_contains_plan_data():
    payload = build_plan_user_prompt(_plan())
    assert "sparc" in payload
    assert "interleave" in payload


def test_generate_plan_narration_calls_backend():
    class _Mock:
        def __init__(self):
            self.calls = 0
        def generate(self, system, user):
            self.calls += 1
            assert "教练" in system or "flicking" in system
            return "计划讲解文本"
    m = _Mock()
    out = generate_plan_narration(_plan(), m)
    assert out == "计划讲解文本" and m.calls == 1
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/coach/test_narrator.py -v -k plan`
Expected: FAIL（`ImportError: cannot import name 'generate_plan_narration'`）

- [ ] **Step 3: 追加 narrator 实现**

在 `kovaak_tracker/coach/narrator.py` 末尾追加（`from .planning import TrainingPlan` 加到文件顶部 import 区）:

```python
from .planning import TrainingPlan

PLAN_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论（min-jerk / Becker 减速段 / "
    "submovement / Fitts / SPARC / contextual interference）+ Voltaic 社区实践。\n"
    "你会收到玩家的训练计划结构（JSON）：焦点指标、调整项（交错/退步/保持/休息）、"
    "复测频率建议。请用中文写一段「下次该怎么练」的讲解（150-300 字）："
    "先说清楚下阶段的训练重点（哪些指标停滞/退步→为什么换结构而非加量），"
    "再给具体场景编排（强调交错而非磨单一场景），最后提醒复测节奏与休息。\n\n"
    "【关键话术】（理论支撑，必带相关项）：\n"
    "- 交错练习（interleaved）：多个场景交替练，虽当下手感差，但长期保留/迁移更好"
    "（contextual interference）；新手可先块状几轮建模式再交错（渐进 hybrid）\n"
    "- 元认知对抗：「感觉进步快≠长期记住」——磨单一场景会让人过度自信（Simon & Bjork 2001）\n"
    "- 外部注意焦点（external focus）：提示看准星/目标/命中点，不是看手/腕——更省力、"
    "张力浪费更少；但应在准备阶段施加（<200ms 弹道动作衰减）\n"
    "- 休息也是训练：过度练习有 staleness/burnout 风险（Ericsson 1993）；"
    "反馈过频损害长期学习（guidance hypothesis）\n\n"
    "**英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。\n"
    "铁律：只基于提供的计划数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)


def generate_plan_narration(plan: TrainingPlan, backend) -> str:
    return backend.generate(PLAN_SYSTEM_PROMPT, build_plan_user_prompt(plan))


def build_plan_user_prompt(plan: TrainingPlan) -> str:
    import json
    from dataclasses import asdict
    payload = {
        "focus_metrics": plan.focus_metrics,
        "adjustments": [
            {"kind": a.kind, "target_metric": a.target_metric,
             "scenarios": [{"scenario": p.scenario, "reason": p.reason} for p in a.scenarios],
             "reason": a.reason, "evidence": a.evidence}
            for a in plan.adjustments
        ],
        "schedule_note": plan.schedule_note,
        "evidence_anchors": plan.evidence_anchors,
        "notes": plan.notes,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
```

> `import json` / `asdict` 放函数内，与文件既有 `_build_progress_user_prompt` 风格一致（该函数也文件顶部 import 了 json/asdict——检查 narrator.py 顶部已有 `import json` 与 `from dataclasses import asdict`，若已有则函数内不必重复 import；没有则放函数内）。顶部 `from .planning import TrainingPlan` 注意循环导入：planning 不导入 narrator，narrator 导入 planning——单向，安全。

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/coach/test_narrator.py -v -k plan`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/narrator.py tests/coach/test_narrator.py
git commit -m "feat(coach): PLAN_SYSTEM_PROMPT + generate_plan_narration (④ T3)"
```

---

## Task 4: report.py — build_progress_report 集成 build_plan

**Files:**
- Modify: `kovaak_tracker/coach/report.py:44-68`（`build_progress_report`）
- Test: `tests/coach/test_report.py`（追加）

**Interfaces:**
- Consumes: Task 1 `build_plan`、Task 2 `ProgressReport.plan/plan_narration`、Task 3 `generate_plan_narration`、`advice.advise`
- Produces: `build_progress_report(...)` 返回的 `ProgressReport` 含 `plan`（非空）+ `plan_narration`（backend 在时）

- [ ] **Step 1: 写失败测试**

追加到 `tests/coach/test_report.py` 末尾：

```python
def test_build_progress_report_includes_plan(tmp_path):
    """build_progress_report 应产出 TrainingPlan + schedule_note。"""
    from kovaak_tracker.coach.report import build_progress_report
    p = tmp_path / "sessions.jsonl"
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
    assert rep.plan is not None
    assert "每周" in rep.plan.schedule_note or "间隔" in rep.plan.schedule_note
    assert rep.plan_narration is None  # backend=None


def test_build_progress_report_plan_narration_best_effort(tmp_path):
    """backend 失败时 plan_narration=None + note，plan 结构照常返回。"""
    from kovaak_tracker.coach.report import build_progress_report
    p = tmp_path / "sessions.jsonl"
    p.write_text(
        '{"timestamp":"2026-06-01","video_ref":"v.mp4","cm_per_360":48,'
        '"summary":{"linearity":{"med":0.20}},'
        '"profile":{},"issues":[],"narration":null}\n',
        encoding="utf-8",
    )
    class _Boom:
        def generate(self, s, u):
            raise RuntimeError("down")
    rep = build_progress_report(p, {"linearity": {"med": 0.18}}, backend=_Boom())
    assert rep.plan is not None
    # progress_narration 或 plan_narration 之一会因 _Boom 失败 -> note
    assert any("不可用" in n for n in rep.notes)
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/coach/test_report.py -v -k "includes_plan or plan_narration_best_effort"`
Expected: FAIL（`rep.plan is None` 或 AttributeError）

- [ ] **Step 3: 改 build_progress_report**

Edit `kovaak_tracker/coach/report.py` 的 `build_progress_report`，在 `comparison = build_comparison(...)` 之后、return 之前插入 plan 逻辑，并改 return：

```python
def build_progress_report(history_path, current_summary, ref_summary=None,
                          meta=None, backend: LLMBackend | None = None) -> ProgressReport:
    """Trend + comparison + plan + narrations over saved history."""
    meta = meta or {}
    history = load_history(history_path)
    trend = build_trend(history)
    comparison = build_comparison(history, current_summary, ref_summary)

    # 处方场景池：现跑 advise（自包含，纯函数开销可忽略）
    findings = advise(current_summary, ref_summary, cm_per_360=meta.get("cm_per_360"))
    plan = build_plan(trend, comparison, history, findings)

    notes: list[str] = []
    if not history:
        notes.append("首次分析，无历史可比")

    progress_narration = None
    plan_narration = None
    if backend is not None:
        try:
            progress_narration = generate_progress_narration(trend, comparison, backend)
        except Exception as e:
            notes.append(f"进步讲解不可用: {e}")
        try:
            plan_narration = generate_plan_narration(plan, backend)
        except Exception as e:
            notes.append(f"计划讲解不可用: {e}")

    return ProgressReport(
        trend_figure=build_trend_figure(trend),
        comparison_figure=build_comparison_figure(comparison),
        comparison_table=comparison,
        progress_narration=progress_narration,
        plan=plan,
        plan_narration=plan_narration,
        notes=notes,
    )
```

文件顶部 import 区追加：
```python
from .planning import build_plan
from .narrator import generate_plan_narration
```
（`generate_narration`/`generate_progress_narration` 已在既有 import；`advise` 已从 `..advice` 导入——确认 report.py 顶部已有 `from ..advice import advise, compare_table`，复用。）

- [ ] **Step 4: 跑全 coach 测试确认绿 + 无回归**

Run: `python -m pytest tests/coach/ tests/test_progress_a.py -v`
Expected: 全 PASS（含原 43 测试 + 新增 ④ 测试）

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/report.py tests/coach/test_report.py
git commit -m "feat(coach): build_progress_report integrates build_plan (④ T4)"
```

---

## Self-Review（plan 自审，执行前跑过）

**1. Spec coverage：** spec §5 数据模型 → Task 1；§6 规则（stall/regress/maintain/rest/数据不足/focus排序）→ Task 1 测试全覆盖；§7 集成（ProgressReport 字段 + build_progress_report）→ Task 2/4；§8 narrator → Task 3；§9 边界（历史空/NaN/LLM失败）→ Task 1（notes）+ Task 4（backend 失败降级）。✅
**2. Placeholder scan：** 无 TBD/TODO；每 step 有真实代码/命令。✅
**3. Type consistency：** `TrainingPlan`/`PlanAdjustment` 字段在 Task 1-4 引用一致；`build_plan(trend, comparison, history, findings)` 签名一致；`ProgressReport(plan=..., plan_narration=...)` 一致。✅
**4. 循环导入：** planning→advice（单向）；narrator→planning（单向）；report→planning+narrator（单向）；progress 用 `Any` 不导入 planning。✅
**5. 3.9 兼容：** `from __future__ import annotations` 顶部已有；`list[X]`/`X | None` 仅在注解。✅

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-29-plan-adjustment.md`.

点点已授权"自己推进 + 最后 dispatch"，采用 **Subagent-Driven（推荐）**：dispatch fresh subagent 逐 task，两阶段 review，Task 1 先行 → Task 2/3 并行 → Task 4 集成 → 全测试验证。
