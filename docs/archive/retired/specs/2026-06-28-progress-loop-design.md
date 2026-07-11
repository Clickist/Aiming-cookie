# 进步闭环（Progress Loop）设计

> 日期 2026-06-28 · 把 coach 从单次输出 → 跨次跟踪。scope B（持久化 + 趋势 + 对比），计划调整（④动态处方）留后续。autonomous brainstorming（用户授权）。
> 上游 spec：`2026-06-28-ai-aim-coach-design.md`（单次 coaching 输出）。

## 1. 目标与范围

用户多次分析（不同日期/session）→ 系统记历史 → 看趋势 + 对比 → 进步可见。

**做**：①持久化（存历史）②趋势（指标随时间）③对比（vs baseline/last/ref）。
**不做**（留后续）：④计划调整（动态处方）；多用户/云端；视频存储；自动定期复测调度。

## 2. 设计决策（autonomous）

| 维度 | 决定 |
|---|---|
| 用户模型 | 单用户本地（个人训练工具）|
| 持久化格式 | JSONL append-only，`output/history/sessions.jsonl` |
| 存什么 | timestamp / video_ref / cm_per_360 / fair summary(med,p75,p90) / profile / issues 摘要 / narration |
| 不存 | 视频（大）/ plotly figures（不可序列化）|
| 趋势指标 | linearity / sparc / decel_frac / reverse_ratio / peak_speed_deg |
| 对比基准 | baseline（首次 session）/ last（上次）/ ref（高手 summary，可选）|

## 3. 架构

新建 `kovaak_tracker/coach/progress.py` + 扩展 visualization/narrator/report。

```
build_report(summary, ..., history_path?) 
  → CoachReport 
  → (若 history_path) save_session → append sessions.jsonl

build_progress_report(history_path, current_summary, ref_summary?, backend?)
  → load_history
  → trend (5 指标 × sessions 时序) + comparison (current vs baseline/last/ref)
  → 趋势图 + 对比图 + 对比表 + 进步讲解
  → ProgressReport
```

## 4. 数据模型

```python
@dataclass(frozen=True)
class Session:  # JSONL 一行（持久化 schema）
    timestamp: str          # ISO 8601
    video_ref: str          # 录像文件名
    cm_per_360: float | None
    summary: dict           # fair summary {metric: {med,p75,p90}}
    profile: dict           # {archetype_id, label, confidence, secondary_tags}
    issues: list[dict]      # [{signal, severity, priority}]
    narration: str | None

@dataclass(frozen=True)
class ProgressReport:
    trend_figure: object           # plotly Figure（5 指标趋势）
    comparison_figure: object      # plotly Figure（current vs baseline/last/ref）
    comparison_table: list[dict]   # {metric, current, baseline, last, ref, verdict}
    progress_narration: str | None # LLM 进步讲解
    notes: list[str]
```

## 5. 持久化（progress.py）

- `save_session(report: CoachReport, meta: dict, history_path=DEFAULT_HISTORY_PATH) -> None`
  - 从 CoachReport + meta 构造 Session（summary/profile/issues/narration 提取）
  - append 一行 JSON 到 `sessions.jsonl`（文件不存在则建）
- `load_history(history_path=DEFAULT_HISTORY_PATH) -> list[Session]`
  - 读 JSONL，每行反序列化 Session；坏行跳过（容错）
- 路径常量 `DEFAULT_HISTORY_PATH = OUTPUT_DIR / "history" / "sessions.jsonl"`

## 6. 趋势（progress.py + visualization.py）

- `build_trend(history: list[Session], metrics=TREND_METRICS) -> dict`
  - 返回 `{metric: [(timestamp, med_value), ...]}`（跳过 NaN）
  - `TREND_METRICS = ("linearity", "sparc", "decel_frac", "reverse_ratio", "peak_speed_deg")`
- `build_trend_figure(trend: dict) -> plotly Figure`
  - 多指标趋势线（每指标一条，x=timestamp，y=med）；前端无关

## 7. 对比（progress.py + visualization.py）

- `build_comparison(history: list[Session], current: dict, ref_summary: dict | None) -> list[dict]`
  - 对每个指标：current / baseline（history[0]）/ last（history[-1]）/ ref（ref_summary 或 None）/ verdict
  - verdict 逻辑（复用 advice 的 lower-better/higher-better 判定方向）：current vs baseline 的 better/worse/same/info
- `build_comparison_figure(comparison: list[dict]) -> plotly Figure`
  - current vs baseline vs last vs ref 分组柱状

## 8. 进步讲解（narrator.py 扩展）

- `PROGRESS_SYSTEM_PROMPT`：教练口吻讲进步（基于 trend + comparison），不编造
- `generate_progress_narration(trend, comparison, backend) -> str`
- 防幻觉：trend/comparison JSON 作为事实约束注入

## 9. 集成（report.py 扩展）

- `build_report(..., history_path=None)`：若传 history_path，跑完调 `save_session`（向后兼容：不传则不存）
- 新 `build_progress_report(history_path, current_summary, ref_summary=None, meta=None, backend=None) -> ProgressReport`
  - load_history → build_trend + build_comparison → 趋势图 + 对比图 + 对比表 →（若 backend）进步讲解
  - 降级：backend 失败 → narration=None + note；历史空 → 趋势/对比空 + note

## 10. 边界与降级

| 情况 | 行为 |
|---|---|
| 历史空（首次）| trend/comparison 空，notes 标「首次，无历史可比」|
| 只有 1 次历史 | baseline=last=那次，对比仍渲染 |
| 无 ref_summary | 对比表 ref 列 None |
| 指标 NaN（如 throughput 无 W）| 趋势/对比跳过该指标 |
| LLM 失败 | progress_narration=None + note |

## 11. 测试

| 层 | 测试 |
|---|---|
| progress.py save/load | 合成 Session → save → load 往返一致；坏行容错 |
| progress.py trend | 合成多次 history → 趋势时序正确，NaN 跳过 |
| progress.py comparison | 合成 history + current + ref → 对比表 verdict 正确 |
| visualization 扩展 | trend_figure/comparison_figure 生成不报错 |
| narrator 扩展 | mock backend → progress prompt 含 trend/comparison JSON + 防幻觉 |
| report 集成 | build_report(history_path=) 存历史；build_progress_report 端到端 |

## 12. 后续（不在这次 spec）

④计划调整（动态处方：基于趋势自动调训练计划）；多用户/云端同步；web 前端趋势页；自动定期复测提醒。

## 附：澄清决策回顾（autonomous）

- 单用户本地 JSONL（简单、无依赖、个人训练够用）
- 不存视频/figures（大/不可序列化；summary + profile 已够跟踪）
- 趋势只看 5 核心（雷达图同维度，避免信息过载）
- 对比三基准（baseline=绝对进步，last=近期变化，ref=相对高手位置）
