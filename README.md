# Tension-Aware Aim-Analyzer

基于物理 + 运动学的 **KovaaK's flicking 瞄准 AI 教练**。从录屏提取目标运动，计算减速段质量指标，诊断瞄准问题，给个性化训练处方 + LLM 教练讲解 + 进步跟踪。

## 核心能力

**flicking AI coach**（`kovaak_tracker/coach/`）：
- **单次分析** → 流派画像 + 优先级问题（三层根因链：症状→物理→训练）+ 5 类可视化 + LLM 教练讲解
- **进步闭环** → 历史持久化 + 趋势 + 多基准对比（vs 基线 / 上次 / 高手参考）
- **双入口**：有 KovaaK stats CSV（`analyze_flicking_fair_summary`）/ 无 CSV（`analyze_flicking_reference`）

**公平指标**（无量纲、跨人可比）：
- 减速段质量：`decel_frac` / `linearity` / `sparc` / `reverse_ratio`
- 运动学：`peak_speed` / `peak_position` / `path_efficiency` / `throughput`（Fitts）
- submovement：`corrective_count` / `submovement_overlap`（两段式 vs 流体）

## 快速开始

```bash
pip install -r requirements.txt   # Python 3.9+，需 opencv-contrib-python（CSRT）
```

**单次分析**（CSV 模式，推荐）：

```python
from kovaak_tracker.pan_tracker import analyze_flicking_fair_summary
from kovaak_tracker.coach import build_report
from kovaak_tracker.coach.providers import load_backend

summary = analyze_flicking_fair_summary("your.mp4", "your Stats.csv", cm_per_360=48.0)
backend = load_backend("anthropic")   # 需设 ANTHROPIC_API_KEY；或 deepseek / local，见 coach/providers.json
report = build_report(summary, None, {"cm_per_360": 48.0}, backend=backend)

print(report.diagnosis.profile.label)   # 流派画像
print(report.narration)                 # LLM 教练讲解
```

**进步闭环**（多次分析后）：

```python
from kovaak_tracker.coach import build_report, build_progress_report
# 先多次跑 build_report(..., history_path="output/history/sessions.jsonl") 积累历史
prog = build_progress_report("output/history/sessions.jsonl", current_summary, backend=backend)
```

## 架构

`kovaak_tracker/` 包是核心逻辑，根目录脚本是薄 CLI/UI 包装：

| 模块 | 职责 |
|---|---|
| `coach/` | AI coach（diagnosis + visualization + narrator + report + progress）|
| `pan_tracker.py` | 全局平移轨迹（flick 速度 = 视角平移）+ CSV/reference 分析入口 |
| `flicking.py` | 谷切分 + 公平指标（SPARC / submovement / Fitts / throughput）|
| `advice.py` | 规则引擎（诊断信号 → 处方）|
| `analysis.py` / `tracking.py` / `vision.py` | 早期 tracking 分析（PTC 等，见下）|

## 理论底座（三层）

| 文档 | 内容 |
|---|---|
| [`docs/aim-kinematics-research.md`](docs/aim-kinematics-research.md) | 运动学指标理论（min-jerk / Becker / submovement / Fitts / SPARC）|
| [`docs/coach-theory-foundation.md`](docs/coach-theory-foundation.md) | 学术教练理论（反馈 / 习得 / 技能，peer-reviewed + 被反驳透明列）|
| [`docs/coach-community-frontier.md`](docs/coach-community-frontier.md) | 社区前沿（Voltaic S5 / static clicking 三步 / VDIM / 配置 / 场景设计）|
| [`docs/flicking-aim-coach.md`](docs/flicking-aim-coach.md) | coach 模块完整介绍（原理 → 实现 → 诊断处方）|

**铁律**：诊断规则只用学术根基（防过时）；社区实践进 narrator 文案。

## 性能

视频分析 ~160s（60s 录像；经 lock 段采样 + 目标检测降采样优化后 7x 加速，指标保持 ±6%——采样损失远小于匹配容差，详见 `docs/PROGRESS.md` 2026-06-28 续三）。

## tracking 部分（早期，待重构）

`app.py` / `Analyze.py`（CLI 仍可跑）是早期 tracking（跟枪）分析器；`dashboard.py` 已在 Phase 1B 删除（webapp 前端 `webapp/frontend/` 接替）。

**理论状态**：早期 README / CLAUDE.md 提到的 **J/E (Jitter/Error) Ratio / TBR (Tension Balance Ratio) 已确认不成立**——`analysis.py` 实际只有 PTC（公式 `mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`），J/E Ratio 在代码中没有独立实现（字面最贴近的就是 PTC 本身），TBR 没有可计算定义、其 1.8/0.6 阈值在仓库内无任何推导或人群标定来源。**"Pure Tension Coeff"命名误导**：它不是直接测肌肉张力（张力需手部摄像头/EMG 验证）。tracking coach v1 不依赖 PTC/J-E/TBR，改用 accuracy / loss_count / off_time / avg_error 等 solid 量——详见 [`docs/superpowers/specs/2026-07-05-tracking-coach-design.md`](docs/superpowers/specs/2026-07-05-tracking-coach-design.md) §2。

## 后续

- ④ 计划调整（基于趋势的动态处方）
- 多轮对话教练（B，Socratic + guidance fading）
- web 前端（Plotly 图表层已前端无关，可直接嵌）

## 致谢

**运动学理论**：Flash & Hogan (min-jerk) · Becker (减速段=成败最强信号) · Woodworth / Meyer / Novak (submovement) · Fitts (speed-accuracy) · Balasubramanian (SPARC) · Schwartze & Rouse (corrective 神经编码)
**社区实践**：Voltaic · r/FPSAimTrainer · Corporate Serf

---

Developed by Jianrui (Jerry) Zhang & Gearclickist.
