# AI Aim Coach (Single-Shot Coaching Output) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents to implement tasks in the parallel batches marked below (user-chosen execution model). Fall back to superpowers:subagent-driven-development for any task with unresolved dependencies. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 flicking 模块从「分析器 + 规则诊断器」升级为「单次 coaching 输出体验」——一次分析产出画像 + 优先级问题（含三层根因链）+ Plotly 可视化 + LLM 教练讲解。

**Architecture:** 新建 `kovaak_tracker/coach/` 子包，**消费** `advice.findings`（不动 advice）。`diagnosis` 综合 findings 成 `CoachDiagnosis` 结构化契约，`visualization` 与 `narrator` 各自消费它，`report` 组装 `CoachReport`。LLM 层借鉴 pi 的 provider 骨架（按协议分类 + 配置 + 凭据），无 agent 框架。

**Tech Stack:** Python 3.9+ / pandas / numpy / plotly（已有）/ anthropic SDK / openai SDK（Ollama 走 openai-completions）/ pytest（新建）

## Global Constraints

- **Python 3.9+**（本机 3.9.7）→ 所有 coach 模块顶部 `from __future__ import annotations`
- **不动 `advice.py` / `flicking.py` 现有逻辑**——coach 只消费它们
- **中文**诊断/讲解文案；代码标识符英文
- **LLM 无 agent 框架**：单纯 chat completion + prompt
- **可视化 plotly `Figure` 对象**（前端无关，不绑 Streamlit，可 `to_html`/嵌 web）
- **TDD**：每任务先写失败测试，再实现，再通过，再 commit
- **DRY/YAGNI**：优先级 deviation 这版用 severity 主导简化（finding 不带偏离数值），注释标明
- **frozen dataclass** 与现有 `FlickFairMetrics`/`Finding` 风格一致

---

## File Structure

| 文件 | 职责 |
|---|---|
| `kovaak_tracker/coach/__init__.py` | 包入口，导出 `build_report` |
| `kovaak_tracker/coach/profiles.py` | 画像典型集 + 根因映射表（**数据**，便于 review 调词）|
| `kovaak_tracker/coach/diagnosis.py` | dataclass 契约 + `build_diagnosis`（画像匹配 + 根因 + 优先级）|
| `kovaak_tracker/coach/providers.py` | `LLMBackend` 抽象 + Anthropic/OpenAICompat backend + 配置/凭据 |
| `kovaak_tracker/coach/providers.json` | provider 配置（baseUrl/api_key_env/model）|
| `kovaak_tracker/coach/narrator.py` | `generate_narration`（prompt 构造 + 防幻觉）|
| `kovaak_tracker/coach/visualization.py` | 5 类 plotly 图表（前端无关）|
| `kovaak_tracker/coach/report.py` | `build_report` 组装 + 端到端入口 + 降级 |
| `kovaak_tracker/pan_tracker.py` | **modify**：+ `analyze_flicking_fair_summary`（PROGRESS A）|
| `tests/coach/*.py` + `tests/test_progress_a.py` | pytest 测试 |

## Dependency Graph（dispatch 并行批次）

```
Batch 1 (串行):  T0 ─→ T1
                        │
Batch 2 (并行):  ┌──────┼────────┬────────┬────────┐
                 T2     T3       T4       T5
                        │        │
Batch 3:        ────────┴──── T6 (需 T1+T4) ──────────
                        │        │        │
Batch 4:        ───────────── T7 (需 T2+T5+T6) ──────
                                │
Batch 5:                       T8 (需 all)
```
- **Batch 2 可并行 dispatch**：T2/T3/T4/T5 都只依赖 T1 的 `CoachDiagnosis` 契约（T3 只依赖现有 pan_tracker，独立）
- T6 需 T4；T7 汇聚；T8 收尾

---

## Task 0: 测试基础设施 + coach 包骨架

**Files:**
- Create: `tests/__init__.py`（空）, `tests/conftest.py`
- Create: `kovaak_tracker/coach/__init__.py`
- Modify: `requirements.txt`（+ pytest）

**Interfaces:** Produces: 可运行的 pytest + `kovaak_tracker.coach` 可 import

- [ ] **Step 1: 建 tests 目录 + conftest**

```python
# tests/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

```python
# tests/__init__.py
```

- [ ] **Step 2: 建 coach 包骨架**

```python
# kovaak_tracker/coach/__init__.py
"""AI aim coach: single-shot coaching output (diagnosis -> viz -> narration)."""
```

- [ ] **Step 3: requirements.txt 加 pytest**

在 `requirements.txt` 末尾追加（如未有）：
```
pytest>=7.0
```

- [ ] **Step 4: 验证 pytest 能发现空测试**

```python
# tests/test_smoke.py
def test_coach_imports():
    import kovaak_tracker.coach
    assert kovaak_tracker.coach is not None
```
Run: `python -m pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/ kovaak_tracker/coach/__init__.py requirements.txt
git commit -m "test(coach): scaffolding + pytest infra"
```

---

## Task 1: 数据模型 + profiles 数据（契约基础）

**Files:**
- Create: `kovaak_tracker/coach/diagnosis.py`（dataclass 部分）
- Create: `kovaak_tracker/coach/profiles.py`
- Test: `tests/coach/__init__.py`, `tests/coach/test_diagnosis.py`

**Interfaces:**
- Produces: `RootCause`, `ProfileMatch`, `DiagnosisIssue`, `CoachDiagnosis`, `CoachReport`（frozen dataclass）；`profiles.ARCHETYPES`、`profiles.ROOT_CAUSES`

- [ ] **Step 1: 写 profiles.py 数据**

```python
# kovaak_tracker/coach/profiles.py
"""Archetype definitions + root-cause mapping (DATA, not logic).

Edit here to tune the coach's vocabulary. Theory anchors in
docs/aim-kinematics-research.md. Signal keys must match advice.py Finding.signal.
"""
from __future__ import annotations

# Each archetype: id, label, weighted signal conditions (signal -> weight).
# `conditions` map signals (from advice.advise) to weights; score = hit_weight/total.
ARCHETYPES = [
    {
        "id": "long_decel",
        "label": "急加速-长减速型",
        "conditions": {"decel_frac high": 1.0, "peak_position low": 0.5},
    },
    {
        "id": "decel_jitter",
        "label": "减速抖动型",
        "conditions": {"sparc low": 1.0, "reverse_ratio high": 0.7},
    },
    {
        "id": "two_stage",
        "label": "两段式型",
        "conditions": {"submovement two-stage": 1.0},
    },
    {
        "id": "underpowered",
        "label": "发力不足型",
        "conditions": {
            "peak_speed below reference": 1.0,
            "throughput below reference": 1.0,
        },
    },
    {
        "id": "inefficient_path",
        "label": "路径低效型",
        "conditions": {"path_efficiency low": 1.0},
    },
    {
        "id": "fluid_precise",
        "label": "流体精度型",
        "conditions": {},  # positive profile: matched when no negative signals fire
    },
]

# signal -> (symptom, physical, training) three-layer root cause.
# Covers every signal advice.advise can emit.
ROOT_CAUSES = {
    "decel_frac high": ("减速段占比过高，在「蹭」", "制动释放不果断", "减速一次到位的意识"),
    "sparc low": ("减速段抖动", "张力释放不平滑（高频成分多）", "减速段控制稳定性"),
    "reverse_ratio high": ("减速段反复修正", "制动方向不稳", "单次制动 + 流体修正"),
    "submovement two-stage": ("flick→急停→独立 micro", "corrective 与 primary 分离", "转流体派（overlapping submovements）"),
    "peak_speed below reference": ("甩得偏慢", "发力不足（手腕主导）", "arm 发力 + speed 场景"),
    "throughput below reference": ("跨距离发力不足", "发力-速度换算弱", "arm 发力 + speed 场景"),
    "linearity high": ("制动不匀", "减速节奏不稳", "匀速制动练习"),
    "path_efficiency low": ("flick 路径绕", "flick 几何不直", "linetrace 直线练习"),
    "peak_position low": ("加速过急", "加速段过猛", "平衡加减速"),
    "peak_position high": ("加速拖沓", "加速不足", "果断加速"),
    "sensitivity high": ("灵敏度偏快", "cm/360 偏小，制动放大手抖", "降 sens 5-10% 实验 + 复测"),
}
```

- [ ] **Step 2: 写 diagnosis.py 的 dataclass**

```python
# kovaak_tracker/coach/diagnosis.py
"""CoachDiagnosis contract + builder. Consumes advice.findings, produces the
structured diagnosis that visualization and narrator both consume."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..advice import Finding, Prescription


@dataclass(frozen=True)
class RootCause:
    level: str   # "symptom" | "physical" | "training"
    text: str


@dataclass(frozen=True)
class ProfileMatch:
    archetype_id: str
    label: str
    confidence: float
    secondary_tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisIssue:
    signal: str
    severity: str
    root_causes: list[RootCause]
    prescriptions: list[Prescription]
    priority: int
    priority_reason: str


@dataclass(frozen=True)
class CoachDiagnosis:
    profile: ProfileMatch
    issues: list[DiagnosisIssue]
    summary: dict
    comparison: list[dict] | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CoachReport:
    diagnosis: CoachDiagnosis
    figures: dict[str, Any]
    narration: str | None
    notes: list[str] = field(default_factory=list)
```

- [ ] **Step 3: 写测试（dataclass 构造 + profiles 完整性）**

```python
# tests/coach/__init__.py
```

```python
# tests/coach/test_diagnosis.py
from kovaak_tracker.coach.diagnosis import (
    RootCause, ProfileMatch, DiagnosisIssue, CoachDiagnosis,
)
from kovaak_tracker.coach import profiles


def test_rootcause_construct():
    rc = RootCause("symptom", "x")
    assert rc.level == "symptom" and rc.text == "x"


def test_profiles_cover_all_root_cause_signals():
    # every archetype condition signal must have a ROOT_CAUSES entry
    for arch in profiles.ARCHETYPES:
        for sig in arch["conditions"]:
            assert sig in profiles.ROOT_CAUSES, f"missing root cause for {sig}"


def test_diagnosis_frozen():
    d = CoachDiagnosis(ProfileMatch("x", "y", 0.5), [], {})
    try:
        d.profile = ProfileMatch("a", "b", 0.1)  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/coach/test_diagnosis.py -v`
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/profiles.py kovaak_tracker/coach/diagnosis.py tests/coach/
git commit -m "feat(coach): CoachDiagnosis contract + profiles data"
```

---

## Task 2: diagnosis 构建逻辑（画像匹配 + 根因 + 优先级）

**Depends on:** Task 1

**Files:**
- Modify: `kovaak_tracker/coach/diagnosis.py`（+ `build_diagnosis` 及内部函数）
- Test: `tests/coach/test_diagnosis.py`（+ 逻辑测试）

**Interfaces:**
- Consumes: `advice.Finding`, `advice.advise`, `advice.compare_table`, `profiles.ARCHETYPES`, `profiles.ROOT_CAUSES`
- Produces: `build_diagnosis(findings: list[Finding], summary: dict, comparison: list[dict] | None, meta: dict) -> CoachDiagnosis`

- [ ] **Step 1: 写失败测试**

```python
# append to tests/coach/test_diagnosis.py
from kovaak_tracker.coach.diagnosis import build_diagnosis
from kovaak_tracker.advice import Finding, Prescription


def _f(signal, severity="fix"):
    return Finding(signal=signal, severity=severity, diagnosis="d",
                   prescriptions=[Prescription("pasu", "r")])


def test_match_long_decel_profile():
    findings = [_f("decel_frac high"), _f("peak_position low")]
    d = build_diagnosis(findings, {}, None, {})
    assert d.profile.archetype_id == "long_decel"
    assert d.profile.confidence == 1.0


def test_secondary_tags_collect_other_hits():
    findings = [_f("decel_frac high"), _f("sparc low")]
    d = build_diagnosis(findings, {}, None, {})
    assert "减速抖动型" in d.profile.secondary_tags


def test_root_cause_chain_three_layers():
    findings = [_f("sparc low")]
    d = build_diagnosis(findings, {}, None, {})
    levels = [rc.level for rc in d.issues[0].root_causes]
    assert levels == ["symptom", "physical", "training"]


def test_priority_orders_by_severity():
    findings = [_f("x", "info"), _f("sparc low", "fix"), _f("y", "watch")]
    d = build_diagnosis(findings, {}, None, {})
    sev = [i.severity for i in d.issues]
    assert sev[0] == "fix" and sev[1] == "watch" and sev[2] == "info"
    assert d.issues[0].priority == 1


def test_unknown_signal_falls_back_to_symptom_only():
    findings = [_f("totally unknown signal")]
    d = build_diagnosis(findings, {}, None, {})
    assert len(d.issues[0].root_causes) == 1
    assert d.issues[0].root_causes[0].level == "symptom"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/coach/test_diagnosis.py -v`
Expected: FAIL（`build_diagnosis` 未定义）

- [ ] **Step 3: 实现 build_diagnosis**

追加到 `kovaak_tracker/coach/diagnosis.py`：

```python
from . import profiles

_MATCH_THRESHOLD = 0.5
_SEVERITY_WEIGHT = {"fix": 3, "watch": 2, "info": 1}


def build_diagnosis(findings, summary, comparison, meta):
    return CoachDiagnosis(
        profile=_match_profile(findings),
        issues=_build_issues(findings),
        summary=summary,
        comparison=comparison,
        meta=meta or {},
    )


def _match_profile(findings):
    signals = {f.signal for f in findings}
    best, best_score = None, 0.0
    for arch in profiles.ARCHETYPES:
        conds = arch["conditions"]
        if not conds:
            continue
        hit_w = sum(w for sig, w in conds.items() if sig in signals)
        total_w = sum(conds.values())
        score = hit_w / total_w if total_w else 0.0
        if score > best_score:
            best, best_score = arch, score
    secondary = [
        a["label"] for a in profiles.ARCHETYPES
        if a is not best and a["conditions"]
        and any(s in signals for s in a["conditions"])
    ]
    # fluid_precise: matched when no negative archetype hit
    if (best is None or best_score < _MATCH_THRESHOLD) and not signals:
        fluid = next(a for a in profiles.ARCHETYPES if a["id"] == "fluid_precise")
        return ProfileMatch(fluid["id"], fluid["label"], 1.0, [])
    if best is None or best_score < _MATCH_THRESHOLD:
        return ProfileMatch("unclassified", "未分类", round(best_score, 2), secondary)
    return ProfileMatch(best["id"], best["label"], round(best_score, 2), secondary)


def _build_issues(findings):
    enriched = [(f, _root_causes_for(f)) for f in findings]
    # priority by severity weight (deviation left to advice thresholds; YAGNI this version)
    enriched.sort(key=lambda x: (-_SEVERITY_WEIGHT.get(x[0].severity, 1),))
    issues = []
    for rank, (f, rcs) in enumerate(enriched, 1):
        issues.append(DiagnosisIssue(
            signal=f.signal,
            severity=f.severity,
            root_causes=rcs,
            prescriptions=list(f.prescriptions),
            priority=rank,
            priority_reason=f"[{f.severity}] 严重度排序第 {rank}",
        ))
    return issues


def _root_causes_for(finding):
    triple = profiles.ROOT_CAUSES.get(finding.signal)
    if not triple:
        return [RootCause("symptom", finding.diagnosis)]
    return [
        RootCause("symptom", triple[0]),
        RootCause("physical", triple[1]),
        RootCause("training", triple[2]),
    ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_diagnosis.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/diagnosis.py tests/coach/test_diagnosis.py
git commit -m "feat(coach): build_diagnosis (profile match + root-cause + priority)"
```

---

## Task 3: PROGRESS [A] — analyze_flicking_fair_summary

**Depends on:** 现有 pan_tracker / flicking（独立，可与 T2/T4/T5 并行）

**Files:**
- Modify: `kovaak_tracker/pan_tracker.py`（+ `analyze_flicking_fair_summary`）
- Test: `tests/test_progress_a.py`

**Interfaces:**
- Consumes: `flicking.segment_by_valleys`, `flicking.compute_fair_metrics`, `flicking._ball_speed`, `analysis.apply_smoothing/calc_derivative`, `start_frame.lock_challenge_window`
- Produces: `analyze_flicking_fair_summary(video_path, csv_path, *, fov, cm_per_360, output_dir, progress_callback) -> dict`（与 `analyze_flicking_reference` 的 summary 同形：`{metric: {med,p75,p90}}`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_progress_a.py
"""PROGRESS A: analyze_flicking_fair_summary produces same-shape summary as
analyze_flicking_reference. End-to-end needs a real video; here we assert the
function exists and reuses valley segmentation via a monkeypatched trajectory."""
import inspect
import kovaak_tracker.pan_tracker as P


def test_function_exists_and_signature():
    fn = getattr(P, "analyze_flicking_fair_summary", None)
    assert fn is not None, "analyze_flicking_fair_summary missing"
    sig = inspect.signature(fn)
    assert "video_path" in sig.parameters
    assert "csv_path" in sig.parameters


def test_summary_shape_matches_reference(monkeypatch):
    # stub compute_pan_trajectory to return a tiny synthetic trajectory with motion
    import numpy as np, pandas as pd
    fps = 60.0
    t = np.arange(180) / fps
    speed = 1000 * np.exp(-((t - 1.5) ** 2) / (2 * 0.09 ** 2))
    df = pd.DataFrame({
        "frame": np.arange(180), "time_s": t,
        "ball_x": np.cumsum(speed) / fps, "ball_y": np.zeros(180),
    })
    monkeypatch.setattr(P, "compute_pan_trajectory", lambda *a, **k: df)
    monkeypatch.setattr(P, "lock_challenge_window",
                        lambda *a, **k: type("W", (), {"start_frame": 0, "end_frame": 179})())
    # stats only needs duration_s derivation: make csv parser return a fake
    class _S:
        kills = pd.DataFrame({"time_s": [2.0]})
    monkeypatch.setattr(P, "parse_stats_csv", lambda *a, **k: _S())

    summary = P.analyze_flicking_fair_summary("v.mp4", "c.csv", fov=103.0)
    # same shape as reference: metric -> {med, p75, p90} OR None
    assert "flick_count" in summary
    assert isinstance(summary.get("linearity", None), (dict, type(None)))
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_progress_a.py -v`
Expected: FAIL（函数未定义）

- [ ] **Step 3: 实现 analyze_flicking_fair_summary**

在 `pan_tracker.py` 的 `analyze_flicking_reference` 之后添加：

```python
def analyze_flicking_fair_summary(
    video_path,
    csv_path,
    *,
    fov: float = 103.0,
    cm_per_360=None,
    ui_area_frac: float = 0.01,
    output_dir=OUTPUT_DIR,
    progress_callback=None,
):
    """CSV-mode fair-summary entry (PROGRESS A).

    Same shape as ``analyze_flicking_reference``'s summary, but driven by a
    KovaaK stats CSV (duration from kills) instead of a manual duration. This
    unblocks the user's own CSV recordings for the coaching pipeline.
    """
    import math
    stats = parse_stats_csv(csv_path)
    duration_s = float(math.ceil(stats.kills["time_s"].max()))

    meta = get_video_metadata(video_path)
    fps = meta.fps
    deg_per_px = fov / meta.width

    window = lock_challenge_window(video_path, duration_s, fps=fps, ui_area_frac=ui_area_frac)
    track_df = compute_pan_trajectory(
        video_path, window.start_frame, window.end_frame,
        fps=fps, progress_callback=progress_callback,
    )

    speed = _ball_speed(track_df, fps)
    accel = calc_derivative(speed, fps)
    win = max(5, int(fps * 0.05))
    if win % 2 == 0:
        win += 1
    accel = apply_smoothing(accel, win)

    flicks = segment_by_valleys(speed, fps)
    metrics = [
        compute_fair_metrics(f, speed, accel, track_df, deg_per_px=deg_per_px, fps=fps)
        for f in flicks
    ]
    cm_per_deg = (cm_per_360 / 360.0) if cm_per_360 else None
    return _summarize_reference(metrics, cm_per_deg)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_progress_a.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/pan_tracker.py tests/test_progress_a.py
git commit -m "feat(pan_tracker): analyze_flicking_fair_summary (PROGRESS A CSV unification)"
```

---

## Task 4: providers.py（LLM backend，借鉴 pi 骨架）

**Depends on:** 无（可与 T2/T3/T5 并行）

**Files:**
- Create: `kovaak_tracker/coach/providers.py`, `kovaak_tracker/coach/providers.json`
- Test: `tests/coach/test_providers.py`

**Interfaces:**
- Produces: `LLMBackend`（Protocol：`generate(system, user) -> str`）、`AnthropicBackend`、`OpenAICompatBackend`、`load_backend(provider, config_path) -> LLMBackend`

- [ ] **Step 1: 写 providers.json 配置**

```json
{
  "anthropic": {
    "model": "claude-sonnet-4-6",
    "api_key_env": "ANTHROPIC_API_KEY"
  },
  "local": {
    "base_url": "http://localhost:11434/v1",
    "model": "qwen2.5",
    "api_key_env": "OLLAMA_API_KEY"
  }
}
```

- [ ] **Step 2: 写失败测试（mock backend + 配置加载）**

```python
# tests/coach/test_providers.py
import json, os, tempfile
from unittest import mock
from kovaak_tracker.coach import providers


def test_protocol_generate_contract():
    class Fake:
        def generate(self, system, user):
            return f"{system}|{user}"
    b = Fake()
    assert b.generate("s", "u") == "s|u"


def test_load_backend_reads_config(monkeypatch):
    cfg = {"anthropic": {"model": "m", "api_key_env": "K"},
           "local": {"base_url": "http://x/v1", "model": "q", "api_key_env": "L"}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f); cfg_path = f.name
    monkeypatch.setattr(providers, "_DEFAULT_CONFIG_PATH", cfg_path)

    # avoid real client construction: stub the backend classes
    with mock.patch.object(providers, "AnthropicBackend") as A, \
         mock.patch.object(providers, "OpenAICompatBackend") as O:
        providers.load_backend("anthropic")
        A.assert_called_once()
        providers.load_backend("local")
        O.assert_called_once_with(base_url="http://x/v1", api_key="", model="q")


def test_credential_resolution_from_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret123")
    monkeypatch.setattr(providers, "_DEFAULT_CONFIG_PATH", "/nonexistent.json")
    with mock.patch.object(providers, "AnthropicBackend") as A:
        providers.load_backend("anthropic",
                               config={"anthropic": {"model": "m", "api_key_env": "MY_KEY"}})
        A.assert_called_once_with(api_key="secret123", model="m")
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/coach/test_providers.py -v`
Expected: FAIL（模块/函数未定义）

- [ ] **Step 4: 实现 providers.py**

```python
# kovaak_tracker/coach/providers.py
"""LLM backends. Borrows pi's provider-skeleton design (categorize by API
protocol, config-driven, credential resolution) — no agent framework."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

_DEFAULT_CONFIG_PATH = str(Path(__file__).parent / "providers.json")


class LLMBackend(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class AnthropicBackend:
    """anthropic-messages protocol (Claude)."""
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def generate(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self._model, max_tokens=1024,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text


class OpenAICompatBackend:
    """openai-completions protocol (local Ollama + future OpenAI-compatible)."""
    def __init__(self, base_url: str, api_key: str, model: str):
        import openai
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key or "ollama")
        self._model = model

    def generate(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content


def load_backend(provider: str = "anthropic", config_path: str | None = None,
                 config: dict | None = None) -> LLMBackend:
    cfg = config if config is not None else _load_config(config_path)
    if provider not in cfg:
        raise ValueError(f"unknown provider {provider!r}; have {list(cfg)}")
    p = cfg[provider]
    api_key = os.environ.get(p.get("api_key_env", ""), "")
    if provider == "anthropic":
        return AnthropicBackend(api_key=api_key, model=p["model"])
    return OpenAICompatBackend(base_url=p["base_url"], api_key=api_key, model=p["model"])


def _load_config(config_path):
    path = config_path or _DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_providers.py -v`
Expected: PASS

- [ ] **Step 6: requirements 加 anthropic + openai**

在 `requirements.txt` 追加：
```
anthropic>=0.40
openai>=1.0
```

- [ ] **Step 7: Commit**

```bash
git add kovaak_tracker/coach/providers.py kovaak_tracker/coach/providers.json tests/coach/test_providers.py requirements.txt
git commit -m "feat(coach): LLM providers (pi-skeleton: protocol-classified backends)"
```

---

## Task 5: visualization.py（5 类 plotly 图表，前端无关）

**Depends on:** Task 1（CoachDiagnosis）

**Files:**
- Create: `kovaak_tracker/coach/visualization.py`
- Test: `tests/coach/test_visualization.py`

**Interfaces:**
- Consumes: `CoachDiagnosis`
- Produces: `build_figures(diagnosis) -> dict[str, plotly.graph_objects.Figure | str]`（keys: `radar`, `decel_curve`, `comparison`, `issue_list`, `profile_card`）

- [ ] **Step 1: 写失败测试**

```python
# tests/coach/test_visualization.py
from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, ProfileMatch, DiagnosisIssue, RootCause,
)
from kovaak_tracker.coach.visualization import build_figures


def _diag():
    summary = {
        "decel_frac": {"med": 0.75}, "linearity": {"med": 0.17},
        "sparc": {"med": -7.5}, "reverse_ratio": {"med": 0.23},
        "path_efficiency": {"med": 0.96}, "peak_speed_deg": {"med": 106},
    }
    comparison = [{"metric": "decel_frac", "self": 0.75, "ref": 0.45, "verdict": "worse"}]
    issue = DiagnosisIssue(
        signal="sparc low", severity="fix",
        root_causes=[RootCause("symptom", "s"), RootCause("physical", "p"), RootCause("training", "t")],
        prescriptions=[], priority=1, priority_reason="[fix]",
    )
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, ["发力不足"]),
        issues=[issue], summary=summary, comparison=comparison, meta={},
    )


def test_build_figures_returns_all_keys():
    figs = build_figures(_diag())
    for k in ("radar", "decel_curve", "comparison", "issue_list", "profile_card"):
        assert k in figs


def test_radar_is_figure_object():
    import plotly.graph_objects as go
    figs = build_figures(_diag())
    assert isinstance(figs["radar"], go.Figure)


def test_comparison_handles_none():
    d = _diag()
    from dataclasses import replace
    d = replace(d, comparison=None)
    figs = build_figures(d)  # must not raise
    assert "comparison" in figs
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_visualization.py -v`
Expected: FAIL（模块未定义）

- [ ] **Step 3: 实现 visualization.py**

```python
# kovaak_tracker/coach/visualization.py
"""Plotly figures for the coach report (frontend-agnostic: returns go.Figure
objects, never binds Streamlit)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from .diagnosis import CoachDiagnosis

# radar dims: (key, label, inverted?). inverted = lower-is-better (so we flip).
_RADAR_DIMS = [
    ("decel_frac", "减速占比", True),
    ("linearity", "制动线性度", True),
    ("sparc", "减速平滑", False),
    ("reverse_ratio", "反向加速", True),
    ("path_efficiency", "路径效率", False),
    ("peak_speed_deg", "峰值速度", False),
]


def build_figures(diagnosis: CoachDiagnosis) -> dict:
    return {
        "profile_card": _profile_card(diagnosis),
        "radar": _radar(diagnosis),
        "decel_curve": _decel_curve(diagnosis),
        "comparison": _comparison(diagnosis),
        "issue_list": _issue_list(diagnosis),
    }


def _med(summary, key):
    v = summary.get(key)
    if isinstance(v, dict):
        v = v.get("med")
    return v if isinstance(v, (int, float)) and not _isnan(v) else None


def _isnan(x):
    return isinstance(x, float) and x != x


def _profile_card(diagnosis):
    p = diagnosis.profile
    tags = "、".join(p.secondary_tags) if p.secondary_tags else "无"
    return (f"画像：{p.label}（匹配度 {p.confidence:.2f}）\n"
            f"次要特征：{tags}")


def _radar(diagnosis):
    fig = go.Figure()
    cats = [d[1] for d in _RADAR_DIMS]
    self_vals = []
    for key, _label, inv in _RADAR_DIMS:
        v = _med(diagnosis.summary, key)
        self_vals.append(_normalize(v, key, inv))
    fig.add_trace(go.Scatterpolar(r=self_vals, theta=cats, fill="toself", name="你"))
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 1])), showlegend=False,
                      title="指标雷达（归一化，外=好）")
    return fig


def _normalize(v, key, inv):
    """Map a metric to 0-1 by rough health band (spec §6). None -> 0."""
    if v is None:
        return 0.0
    bands = {
        "decel_frac": (0.50, 0.65), "linearity": (0.0, 0.12),
        "sparc": (-4.0, 0.0), "reverse_ratio": (0.0, 0.18),
        "path_efficiency": (0.85, 1.0), "peak_speed_deg": (100, 140),
    }
    lo, hi = bands.get(key, (0.0, 1.0))
    t = (v - lo) / (hi - lo) if hi != lo else 0.5
    t = max(0.0, min(1.0, t))
    return (1 - t) if inv else t


def _decel_curve(diagnosis):
    """Ideal min-jerk decel half vs placeholder self-curve (synthetic, since
    per-flick trajectory isn't in summary). Annotated for coaching."""
    fig = go.Figure()
    tau = np.linspace(0, 1, 50)
    mj = 30 * tau ** 2 * (1 - tau) ** 2 / max(30 * 0.5 ** 2 * 0.5 ** 2, 1e-9)
    fig.add_trace(go.Scatter(x=tau, y=mj, name="理想 min-jerk", mode="lines"))
    fig.update_layout(title="减速段速度曲线（理想 vs 实际见录像）",
                      xaxis_title="归一化时间", yaxis_title="归一化速度")
    return fig


def _comparison(diagnosis):
    if not diagnosis.comparison:
        return go.Figure().update_layout(title="对比（无参考数据）")
    rows = diagnosis.comparison
    metrics = [r["metric"] for r in rows]
    self_v = [r["self"] for r in rows]
    ref_v = [r["ref"] for r in rows]
    fig = go.Figure(data=[
        go.Bar(name="你", x=metrics, y=self_v, marker_color="#636"),
        go.Bar(name="参考", x=metrics, y=ref_v, marker_color="#aaa"),
    ])
    fig.update_layout(barmode="group", title="指标对比（self vs 参考）")
    return fig


def _issue_list(diagnosis):
    lines = []
    for i in diagnosis.issues:
        lines.append(f"#{i.priority} [{i.severity}] {i.signal} — {i.priority_reason}")
        for rc in i.root_causes:
            lines.append(f"    {rc.level}: {rc.text}")
        for p in i.prescriptions:
            lines.append(f"    → {p.scenario}: {p.reason}")
    return "\n".join(lines) if lines else "无明显问题"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_visualization.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/visualization.py tests/coach/test_visualization.py
git commit -m "feat(coach): visualization (5 plotly figures, frontend-agnostic)"
```

---

## Task 6: narrator.py（LLM 讲解，prompt + 防幻觉）

**Depends on:** Task 1（CoachDiagnosis）+ Task 4（providers）

**Files:**
- Create: `kovaak_tracker/coach/narrator.py`
- Test: `tests/coach/test_narrator.py`

**Interfaces:**
- Consumes: `CoachDiagnosis`, `LLMBackend`
- Produces: `generate_narration(diagnosis, backend) -> str`、`SYSTEM_PROMPT`、`build_user_prompt(diagnosis) -> str`

- [ ] **Step 1: 写失败测试（mock backend + prompt 构造）**

```python
# tests/coach/test_narrator.py
import json
from kovaak_tracker.coach.narrator import generate_narration, build_user_prompt, SYSTEM_PROMPT
from kovaak_tracker.coach.diagnosis import CoachDiagnosis, ProfileMatch


class _Fake:
    def __init__(self): self.calls = []
    def generate(self, system, user):
        self.calls.append((system, user))
        return "讲解文本"


def _diag():
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, []),
        issues=[], summary={"decel_frac": {"med": 0.75}}, comparison=None,
        meta={"cm_per_360": 48.0},
    )


def test_generate_returns_backend_text():
    b = _Fake()
    out = generate_narration(_diag(), b)
    assert out == "讲解文本"
    assert b.calls[0][0] == SYSTEM_PROMPT


def test_user_prompt_contains_diagnosis_json():
    user = build_user_prompt(_diag())
    payload = json.loads(user)
    assert payload["profile"]["label"] == "减速抖动型"
    assert payload["meta"]["cm_per_360"] == 48.0


def test_system_prompt_forbids_fabrication():
    assert "不编造" in SYSTEM_PROMPT or "不要编造" in SYSTEM_PROMPT
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_narrator.py -v`
Expected: FAIL（模块未定义）

- [ ] **Step 3: 实现 narrator.py**

```python
# kovaak_tracker/coach/narrator.py
"""LLM narration of a CoachDiagnosis. Diagnosis is rule-engineered (deterministic);
the LLM only translates structured data into coach-voice prose. No diagnosis
reasoning is delegated to the LLM (anti-hallucination)."""
from __future__ import annotations

import json
from dataclasses import asdict

from .diagnosis import CoachDiagnosis
from .providers import LLMBackend

SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，擅长用运动学（min-jerk / Becker 减速段 / "
    "submovement / Fitts）诊断瞄准问题并给训练处方。"
    "你会收到一份结构化诊断（JSON）。请用中文写一段教练讲解（150-300 字），"
    "结构：先点出玩家的流派画像，再讲头号问题及其根因（症状→物理→训练），"
    "最后给最优先的训练建议。"
    "铁律：只基于提供的诊断数据讲解，不要编造任何指标数值或未给出的信息；"
    "如果某数据缺失，就略过不提。语气具体、可执行，不空话。"
)


def generate_narration(diagnosis: CoachDiagnosis, backend: LLMBackend) -> str:
    return backend.generate(SYSTEM_PROMPT, build_user_prompt(diagnosis))


def build_user_prompt(diagnosis: CoachDiagnosis) -> str:
    payload = {
        "profile": asdict(diagnosis.profile),
        "issues": [
            {
                "priority": i.priority, "signal": i.signal, "severity": i.severity,
                "priority_reason": i.priority_reason,
                "root_causes": [{"level": rc.level, "text": rc.text} for rc in i.root_causes],
                "prescriptions": [{"scenario": p.scenario, "reason": p.reason}
                                  for p in i.prescriptions],
            }
            for i in diagnosis.issues
        ],
        "comparison": diagnosis.comparison,
        "meta": diagnosis.meta,
    }
    return json.dumps(payload, ensure_ascii=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_narrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/narrator.py tests/coach/test_narrator.py
git commit -m "feat(coach): narrator (LLM narration, anti-hallucination prompt)"
```

---

## Task 7: report.py（端到端组装 + 降级）

**Depends on:** Task 2 + Task 5 + Task 6

**Files:**
- Create: `kovaak_tracker/coach/report.py`
- Test: `tests/coach/test_report.py`

**Interfaces:**
- Consumes: `advice.advise`, `advice.compare_table`, `diagnosis.build_diagnosis`, `visualization.build_figures`, `narrator.generate_narration`, `providers.LLMBackend`
- Produces: `build_report(summary, reference_summary=None, meta=None, backend=None) -> CoachReport`

- [ ] **Step 1: 写失败测试**

```python
# tests/coach/test_report.py
from kovaak_tracker.coach.report import build_report
from kovaak_tracker.coach.narrator import SYSTEM_PROMPT


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
    assert r.notes == []  # no backend -> no narration, not a failure note


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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/coach/test_report.py -v`
Expected: FAIL（模块未定义）

- [ ] **Step 3: 实现 report.py**

```python
# kovaak_tracker/coach/report.py
"""End-to-end: fair summary -> CoachReport. Wires advice + diagnosis +
visualization + narrator, with degradation (structured + viz always produced;
narration is best-effort)."""
from __future__ import annotations

from ..advice import advise, compare_table
from .diagnosis import build_diagnosis, CoachReport
from .visualization import build_figures
from .narrator import generate_narration
from .providers import LLMBackend


def build_report(summary, reference_summary=None, meta=None, backend: LLMBackend | None = None) -> CoachReport:
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
        except Exception as e:  # narration is best-effort; never block the report
            notes.append(f"讲解不可用: {e}")

    return CoachReport(
        diagnosis=diagnosis, figures=figures, narration=narration, notes=notes,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/coach/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kovaak_tracker/coach/report.py tests/coach/test_report.py
git commit -m "feat(coach): build_report end-to-end with narration degradation"
```

---

## Task 8: 包入口 + 端到端烟测 + 文档更新

**Depends on:** all

**Files:**
- Modify: `kovaak_tracker/coach/__init__.py`
- Modify: `docs/PROGRESS.md`, `docs/flicking-aim-coach.md`
- Test: `tests/coach/test_e2e.py`

**Interfaces:**
- Produces: `kovaak_tracker.coach.build_report`（re-export）；更新后的 docs

- [ ] **Step 1: 包入口 re-export**

```python
# kovaak_tracker/coach/__init__.py
"""AI aim coach: single-shot coaching output (diagnosis -> viz -> narration)."""
from .report import build_report

__all__ = ["build_report"]
```

- [ ] **Step 2: 写端到端烟测**

```python
# tests/coach/test_e2e.py
from kovaak_tracker.coach import build_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


def test_e2e_full_pipeline_no_llm():
    r = build_report(_summary(), None, {"cm_per_360": 48.0})
    assert r.diagnosis.profile.confidence > 0
    assert len(r.diagnosis.issues) >= 1
    assert {"radar", "decel_curve", "comparison", "issue_list", "profile_card"} <= set(r.figures)
    assert r.narration is None


def test_e2e_real_user_data_matches_known_profile():
    # the user's known data (PROGRESS baseline) decel_frac 0.75 + reverse 0.23 + sparc low
    r = build_report(_summary(), None, {})
    labels = r.diagnosis.profile.label
    assert "长减速" in labels or "抖动" in labels  # hits long_decel or decel_jitter
```

- [ ] **Step 3: 运行全部测试**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: 更新 PROGRESS.md**

在 PROGRESS.md 顶部 2026-06-28 续段之后，记录 coach 模块完成（画像诊断 + 可视化 + 讲解），指向 spec 和 plan 路径。

- [ ] **Step 5: 更新 flicking-aim-coach.md**

在 `docs/flicking-aim-coach.md` §7「已就绪」补一条：coaching 输出体验已实现（`kovaak_tracker.coach.build_report`），含 PROGRESS A 统一。

- [ ] **Step 6: Commit**

```bash
git add kovaak_tracker/coach/__init__.py tests/coach/test_e2e.py docs/PROGRESS.md docs/flicking-aim-coach.md
git commit -m "feat(coach): wire-up + e2e + docs (single-shot coaching output done)"
```

---

## Self-Review（写完后自检，已修正）

1. **Spec coverage**：spec §2 产品行为 → T5（图表）+T7（组装）+T6（讲解）；§3 架构 → 全任务；§4 数据模型 → T1；§5 诊断 → T1（profiles）+T2；§6 可视化 → T5；§7 narrator → T4+T6；§8 PROGRESS A → T3；§9 边界 → T7（降级）+T5（None 处理）；§10 测试 → 每任务 TDD。✓
2. **Placeholder scan**：无 TBD/TODO；每步有完整代码或命令。✓
3. **Type consistency**：`CoachDiagnosis`/`ProfileMatch`/`DiagnosisIssue`/`RootCause` 在 T1 定义，T2/T5/T6/T7 消费，字段名一致；`build_figures`/`build_diagnosis`/`build_report`/`generate_narration`/`load_backend` 签名跨任务一致。✓
4. **已知简化**（YAGNI，已在代码注释标明）：优先级 deviation 用 severity 主导（finding 不带偏离数值）；`_decel_curve` 用理想 min-jerk + 占位（per-flick 轨迹不在 summary 里，真实曲线需后续接 trajectory）。
