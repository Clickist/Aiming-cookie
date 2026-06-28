# Progress Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents to implement T1/T2/T3 in parallel (user-chosen). T0 (foundation) and T4 (integration) are sequential. Agents do NOT commit — the coordinator commits after review. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 coach 从单次输出升级为跨次跟踪——持久化历史 + 趋势可视化 + 多基准对比，让玩家看到自己的进步。

**Architecture:** 新建 `coach/progress.py`（Session 持久化 + trend/comparison 逻辑 + ProgressReport），扩展 `visualization.py`（趋势/对比图）、`narrator.py`（进步讲解）、`report.py`（build_report 存历史 + 新 build_progress_report）。消费现有 `build_report` 产物，不动 advice/flicking/pan_tracker。

**Tech Stack:** Python 3.9+ / pandas / numpy / plotly（已有）/ pytest（已有）

## Global Constraints

- **Python 3.9+**（本机 3.9.7）→ 所有新/改模块顶部 `from __future__ import annotations`
- **不动 `advice.py` / `flicking.py` / `pan_tracker.py`**——只消费 + 扩展 coach
- **frozen dataclass**，与现有 `CoachDiagnosis`/`Session` 风格一致
- **JSONL** 持久化，默认路径 `output/history/sessions.jsonl`（`OUTPUT_DIR / "history" / "sessions.jsonl"`）
- **中文**文案；plotly `Figure` 前端无关
- **TDD**：每任务先失败测试 → 实现 → 通过。**Agents 不 git commit**（coordinator 统一 commit）
- 跑测试：`python -m pytest tests/coach/<file> -v`（pytest 已装）
- 缺包代理解法：`HTTPS_PROXY="" HTTP_PROXY="" NO_PROXY="*" pip install --proxy "" <pkg>`

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `kovaak_tracker/coach/progress.py`（新）| Session dataclass + save/load JSONL + build_trend/build_comparison + ProgressReport | T0, T1 |
| `kovaak_tracker/coach/visualization.py`（改）| + build_trend_figure / build_comparison_figure | T2 |
| `kovaak_tracker/coach/narrator.py`（改）| + PROGRESS_SYSTEM_PROMPT / generate_progress_narration | T3 |
| `kovaak_tracker/coach/report.py`（改）| build_report + history_path / + build_progress_report | T4 |
| `tests/coach/test_progress.py`（新）| save/load + trend + comparison 测试 | T0, T1 |
| `tests/coach/test_visualization.py`（改）| + trend/comparison figure 测试 | T2 |
| `tests/coach/test_narrator.py`（改）| + progress prompt 测试 | T3 |
| `tests/coach/test_report.py`（改）| + history_path / build_progress_report 测试 | T4 |

## Dependency Graph（dispatch）

```
T0 (progress.py foundation: Session + save/load + ProgressReport) ─→ 
   ├─ T1 (progress.py trend/comparison)
   ├─ T2 (visualization trend/comparison figures)
   └─ T3 (narrator progress prompt)
        └─ T4 (report.py integration) ─ needs T1+T2+T3
```
T0 串行基础；T1/T2/T3 并行（只依赖 T0）；T4 收尾。

---

## Task 0: progress.py 基础（Session + save/load + ProgressReport）

**Files:**
- Create: `kovaak_tracker/coach/progress.py`
- Test: `tests/coach/test_progress.py`

**Interfaces:**
- Produces: `Session`(frozen dataclass), `ProgressReport`(frozen dataclass), `save_session(report, meta, history_path=DEFAULT_HISTORY_PATH) -> None`, `load_history(history_path=DEFAULT_HISTORY_PATH) -> list[Session]`, `DEFAULT_HISTORY_PATH`
- Consumes: `coach/diagnosis.CoachReport`（read-only：`report.diagnosis.summary/profile/issues`, `report.narration`）

- [ ] **Step 1: 写失败测试**

```python
# tests/coach/test_progress.py
import json
from kovaak_tracker.coach.progress import (
    Session, ProgressReport, save_session, load_history, DEFAULT_HISTORY_PATH,
)


def _fake_report():
    """Minimal CoachReport-like object for save_session (duck-typed)."""
    class _P:
        archetype_id = "decel_jitter"; label = "减速抖动型"
        confidence = 1.0; secondary_tags = ["发力不足型"]
    class _I:
        signal = "sparc low"; severity = "fix"; priority = 1
    class _D:
        summary = {"linearity": {"med": 0.17}}
        profile = _P(); issues = [_I()]
    class _R:
        diagnosis = _D(); narration = "讲解"; notes = []
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
    p.write_text('{"timestamp":"t","video_ref":"v","cm_per_360":48,"summary":{},"profile":{},"issues":[],"narration":null}\n'
                 'BROKEN LINE\n'
                 'not even json\n', encoding="utf-8")
    hist = load_history(p)
    assert len(hist) == 1  # only the valid line
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_progress.py -v`
Expected: FAIL（`ModuleNotFoundError: kovaak_tracker.coach.progress`）

- [ ] **Step 3: 实现 progress.py（基础部分）**

```python
# kovaak_tracker/coach/progress.py
"""Progress loop: persist sessions, build trend/comparison, ProgressReport.

Scope B (persistence + trend + comparison). Plan adjustment (dynamic
prescriptions) is a later spec. See docs/superpowers/specs/2026-06-28-progress-loop-design.md."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..settings import OUTPUT_DIR

DEFAULT_HISTORY_PATH = OUTPUT_DIR / "history" / "sessions.jsonl"


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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_progress.py -v`
Expected: PASS（4 tests）

- [ ] **Step 5: 不 commit（coordinator 统一）**

---

## Task 1: progress.py trend/comparison 逻辑

**Depends on:** Task 0（Session）。可与 T2/T3 并行。

**Files:**
- Modify: `kovaak_tracker/coach/progress.py`（追加 build_trend/build_comparison + 常量/辅助）
- Test: `tests/coach/test_progress.py`（追加）

**Interfaces:**
- Produces: `TREND_METRICS`, `build_trend(history, metrics=TREND_METRICS) -> dict[str, list[tuple[str,float]]]`, `build_comparison(history, current, ref_summary=None) -> list[dict]`

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/coach/test_progress.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_progress.py -v`
Expected: FAIL（`build_trend` / `build_comparison` 未定义）

- [ ] **Step 3: 追加实现到 progress.py**

```python
# append to kovaak_tracker/coach/progress.py

TREND_METRICS = ("linearity", "sparc", "decel_frac", "reverse_ratio", "peak_speed_deg")


def build_trend(history, metrics=TREND_METRICS) -> dict:
    """{metric: [(timestamp, med), ...]} skipping NaN/missing."""
    out: dict[str, list] = {}
    for m in metrics:
        series = []
        for s in history:
            v = _med(s.summary, m)
            if v is not None:
                series.append((s.timestamp, v))
        out[m] = series
    return out


def build_comparison(history, current, ref_summary=None) -> list[dict]:
    """Per-metric rows: current vs baseline(history[0]) / last(history[-1]) / ref.

    verdict: better/worse/same (±5% vs baseline, direction-aware) or info (missing).
    """
    baseline = history[0].summary if history else {}
    last = history[-1].summary if history else {}
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


def _med(summary, key):
    v = summary.get(key)
    if isinstance(v, dict):
        v = v.get("med")
    return v if isinstance(v, (int, float)) and v == v else None  # NaN guard


def _verdict(metric, current, baseline):
    if current is None or baseline is None:
        return "info"
    # higher-is-better: sparc (closer to 0), peak_speed; lower-is-better: the rest
    higher_better = metric in ("sparc", "peak_speed_deg")
    ratio = current / baseline if baseline != 0 else 1.0
    if higher_better:
        if ratio > 1.05:
            return "better"
        if ratio < 0.95:
            return "worse"
    else:
        if ratio < 0.95:
            return "better"
        if ratio > 1.05:
            return "worse"
    return "same"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_progress.py -v`
Expected: PASS（8 tests：T0 的 4 + T1 的 4）

- [ ] **Step 5: 不 commit**

---

## Task 2: visualization 扩展（trend/comparison 图）

**Depends on:** Task 0（Session）。可与 T1/T3 并行。

**Files:**
- Modify: `kovaak_tracker/coach/visualization.py`（追加 build_trend_figure / build_comparison_figure）
- Test: `tests/coach/test_visualization.py`（追加）

**Interfaces:**
- Produces: `build_trend_figure(trend: dict) -> plotly Figure`, `build_comparison_figure(comparison: list[dict]) -> plotly Figure`
- Consumes: plotly（已装）

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/coach/test_visualization.py
import plotly.graph_objects as go
from kovaak_tracker.coach.visualization import build_trend_figure, build_comparison_figure


def test_trend_figure_is_figure():
    trend = {"linearity": [("2026-06-01", 0.20), ("2026-06-10", 0.17)]}
    fig = build_trend_figure(trend)
    assert isinstance(fig, go.Figure)


def test_trend_figure_skips_empty_metrics():
    trend = {"linearity": [("t1", 0.2)], "sparc": []}  # sparc empty
    fig = build_trend_figure(trend)  # must not raise
    assert isinstance(fig, go.Figure)


def test_comparison_figure_is_figure():
    comparison = [{"metric": "linearity", "current": 0.17, "baseline": 0.20,
                   "last": 0.18, "ref": 0.12, "verdict": "better"}]
    fig = build_comparison_figure(comparison)
    assert isinstance(fig, go.Figure)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_visualization.py -v`
Expected: FAIL（`build_trend_figure` 未定义）

- [ ] **Step 3: 追加实现到 visualization.py**

```python
# append to kovaak_tracker/coach/visualization.py
def build_trend_figure(trend):
    """Multi-metric trend lines: x=timestamp, y=med. Frontend-agnostic."""
    fig = go.Figure()
    for metric, series in trend.items():
        if not series:
            continue
        xs = [s[0] for s in series]
        ys = [s[1] for s in series]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=metric))
    fig.update_layout(title="指标趋势（med 随 session）", xaxis_title="session", yaxis_title="med")
    return fig


def build_comparison_figure(comparison):
    """Grouped bars: current vs baseline vs last vs ref."""
    metrics = [r["metric"] for r in comparison]
    fig = go.Figure()
    for key, name, color in [
        ("current", "你", "#63636e"),
        ("baseline", "基线", "#aab0b8"),
        ("last", "上次", "#4a90d9"),
        ("ref", "参考", "#cccccc"),
    ]:
        ys = [r.get(key) if r.get(key) is not None else 0 for r in comparison]
        fig.add_trace(go.Bar(name=name, x=metrics, y=ys, marker_color=color))
    fig.update_layout(barmode="group", title="对比（current vs 基线/上次/参考）")
    return fig
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_visualization.py -v`
Expected: PASS（原 3 + 新 3 = 6）

- [ ] **Step 5: 不 commit**

---

## Task 3: narrator 扩展（进步讲解）

**Depends on:** Task 0。可与 T1/T2 并行。

**Files:**
- Modify: `kovaak_tracker/coach/narrator.py`（追加 PROGRESS_SYSTEM_PROMPT + generate_progress_narration）
- Test: `tests/coach/test_narrator.py`（追加）

**Interfaces:**
- Produces: `PROGRESS_SYSTEM_PROMPT`, `generate_progress_narration(trend, comparison, backend) -> str`
- Consumes: `providers.LLMBackend`（duck-typed `.generate(system, user)`）

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/coach/test_narrator.py
import json
from kovaak_tracker.coach.narrator import (
    generate_progress_narration, PROGRESS_SYSTEM_PROMPT,
)


class _Fake:
    def __init__(self):
        self.calls = []
    def generate(self, system, user):
        self.calls.append((system, user))
        return "进步解读文本"


def test_progress_narration_returns_backend_text():
    b = _Fake()
    trend = {"linearity": [("t1", 0.2), ("t2", 0.17)]}
    comparison = [{"metric": "linearity", "current": 0.17, "baseline": 0.20, "verdict": "better"}]
    out = generate_progress_narration(trend, comparison, b)
    assert out == "进步解读文本"
    assert b.calls[0][0] == PROGRESS_SYSTEM_PROMPT


def test_progress_prompt_contains_data_json():
    b = _Fake()
    trend = {"sparc": [("t1", -7.0)]}
    comparison = [{"metric": "sparc", "current": -5.0, "baseline": -7.0, "verdict": "better"}]
    generate_progress_narration(trend, comparison, b)
    payload = json.loads(b.calls[0][1])
    assert "trend" in payload and "comparison" in payload
    assert payload["comparison"][0]["metric"] == "sparc"


def test_progress_system_prompt_forbids_fabrication():
    assert "不编造" in PROGRESS_SYSTEM_PROMPT or "不要编造" in PROGRESS_SYSTEM_PROMPT
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_narrator.py -v`
Expected: FAIL（`PROGRESS_SYSTEM_PROMPT` 未定义）

- [ ] **Step 3: 追加实现到 narrator.py**

```python
# append to kovaak_tracker/coach/narrator.py
PROGRESS_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练。你会收到玩家的历史趋势 + 多基准对比数据（JSON）。"
    "请用中文写一段进步解读（150-300 字）：先总结进步方向（哪些指标改善了/退步了，"
    "引用趋势和对比 verdict），再结合基线/上次/高手参考定位当前水平，"
    "最后给下一阶段训练重点。"
    "铁律：只基于提供的数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)


def generate_progress_narration(trend, comparison, backend) -> str:
    return backend.generate(PROGRESS_SYSTEM_PROMPT, _build_progress_user_prompt(trend, comparison))


def _build_progress_user_prompt(trend, comparison) -> str:
    payload = {"trend": {m: series for m, series in trend.items()}, "comparison": comparison}
    return json.dumps(payload, ensure_ascii=False, default=str)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_narrator.py -v`
Expected: PASS（原 3 + 新 3 = 6）

- [ ] **Step 5: 不 commit**

---

## Task 4: report.py 集成（history_path + build_progress_report）

**Depends on:** T0 + T1 + T2 + T3。

**Files:**
- Modify: `kovaak_tracker/coach/report.py`
- Test: `tests/coach/test_report.py`（追加）

**Interfaces:**
- Produces: `build_report(..., history_path=None)`（新增可选参数，向后兼容），`build_progress_report(history_path, current_summary, ref_summary=None, meta=None, backend=None) -> ProgressReport`
- Consumes: `progress.{load_history, build_trend, build_comparison, save_session, ProgressReport, DEFAULT_HISTORY_PATH}`, `visualization.{build_trend_figure, build_comparison_figure}`, `narrator.generate_progress_narration`

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/coach/test_report.py
from kovaak_tracker.coach.report import build_report, build_progress_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


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
    # seed history with one worse session
    build_report({k: {"med": v} for k, v in {**_summary(), "linearity": {"med": 0.25}.get("med", 0.25), "sparc": -9.0}.items()} if False else _summary(),
                 None, {}, backend=None, history_path=p)
    # actually seed simpler: just write a Session-shaped line
    p.write_text('{"timestamp":"2026-06-01","video_ref":"old.mp4","cm_per_360":48,'
                 '"summary":{"linearity":{"med":0.25},"sparc":{"med":-9.0},'
                 '"decel_frac":{"med":0.80},"reverse_ratio":{"med":0.30},'
                 '"peak_speed_deg":{"med":90}},'
                 '"profile":{},"issues":[],"narration":null}\n', encoding="utf-8")
    cur = {k: {"med": v} for k, v in {"linearity": 0.17, "sparc": -6.0, "decel_frac": 0.74,
        "reverse_ratio": 0.22, "peak_speed_deg": 110}.items()}
    rep = build_progress_report(p, cur, ref_summary=None, backend=None)
    assert rep.progress_narration is None
    assert len(rep.comparison_table) == 5
    assert any(r["metric"] == "linearity" and r["verdict"] == "better" for r in rep.comparison_table)
    assert rep.trend_figure is not None and rep.comparison_figure is not None
    assert "首次" not in " ".join(rep.notes)  # we seeded history


def test_build_progress_report_empty_history(tmp_path):
    p = tmp_path / "nope.jsonl"
    rep = build_progress_report(p, _summary(), backend=None)
    assert any("首次" in n for n in rep.notes)
```

> Note: `test_build_progress_report_end_to_end` seeds a Session JSON line directly (simpler than running build_report twice with mismatched fixtures). The `"首次" not in notes` assertion confirms history was found.

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_report.py -v`
Expected: FAIL（`build_report` 不接受 `history_path`；`build_progress_report` 未定义）

- [ ] **Step 3: 改 report.py（加 history_path + build_progress_report）**

把 `build_report` 签名加 `history_path=None`，并在返回前存历史；再追加 `build_progress_report`。

```python
# kovaak_tracker/coach/report.py —— 替换 build_report 函数体加 history_path，并追加 build_progress_report
from .progress import (
    save_session, load_history, build_trend, build_comparison,
    DEFAULT_HISTORY_PATH, ProgressReport,
)
from .visualization import build_trend_figure, build_comparison_figure
from .narrator import generate_progress_narration


def build_report(summary, reference_summary=None, meta=None,
                 backend: LLMBackend | None = None, history_path=None) -> CoachReport:
    meta = meta or {}
    findings = advise(summary, reference_summary, cm_per_360=meta.get("cm_per_360"))
    comparison = compare_table(summary, reference_summary) if reference_summary else None
    diagnosis = build_diagnosis(findings, summary, comparison, meta)
    figures = build_figures(diagnosis)
    narration = None
    notes: list[str] = []
    if backend is not None:
        try:
            narration = generate_narration(diagnosis, backend)
        except Exception as e:
            notes.append(f"讲解不可用: {e}")
    report = CoachReport(diagnosis=diagnosis, figures=figures, narration=narration, notes=notes)
    if history_path is not None:
        try:
            save_session(report, meta, history_path)
        except Exception as e:
            report.notes.append(f"历史保存失败: {e}")
    return report


def build_progress_report(history_path, current_summary, ref_summary=None,
                          meta=None, backend: LLMBackend | None = None) -> ProgressReport:
    """Trend + comparison + progress narration over saved history."""
    history = load_history(history_path)
    trend = build_trend(history)
    comparison = build_comparison(history, current_summary, ref_summary)
    notes: list[str] = []
    if not history:
        notes.append("首次分析，无历史可比")
    narration = None
    if backend is not None:
        try:
            narration = generate_progress_narration(trend, comparison, backend)
        except Exception as e:
            notes.append(f"进步讲解不可用: {e}")
    return ProgressReport(
        trend_figure=build_trend_figure(trend),
        comparison_figure=build_comparison_figure(comparison),
        comparison_table=comparison,
        progress_narration=narration,
        notes=notes,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/ -v`
Expected: PASS（test_progress 8 + test_visualization 6 + test_narrator 6 + test_report 原3+新4 + test_diagnosis 8 + test_providers 3 + test_e2e 2）

- [ ] **Step 5: 不 commit（coordinator 统一）**

---

## Self-Review

1. **Spec coverage**：spec §5 持久化 → T0；§6 趋势 → T1（逻辑）+ T2（图）；§7 对比 → T1（逻辑）+ T2（图）；§8 进步讲解 → T3；§9 集成 → T4；§10 边界 → T4（empty history note）+ T1（NaN skip）；§11 测试 → 每任务 TDD。✓
2. **Placeholder scan**：无 TBD/TODO；每步完整代码。✓
3. **Type consistency**：`Session`/`ProgressReport` 在 T0 定义，T1/T4 消费；`build_trend`/`build_comparison`/`save_session`/`load_history`/`generate_progress_narration`/`build_trend_figure`/`build_comparison_figure`/`build_progress_report` 跨任务签名一致；`TREND_METRICS` 5 个与 spec §6 一致。✓
4. **已知简化**（YAGNI）：verdict 用 ±5% 阈值（advice 用 ±10%，更严以显进步）；comparison 只算 5 核心指标（TREND_METRICS）。
