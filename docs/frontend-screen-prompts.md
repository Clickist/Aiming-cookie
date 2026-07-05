# 前端界面 Stitch 提示词

> 用途：每个界面的 Stitch 设计提示词，点点喂给 [Stitch](https://stitch.withgoogle.com/) 出 UI mockup。
> 设计基调 + 3 个核心界面（上传 / 处理中 / 教练报告）+ 1 个后续界面（历史趋势）。

---

## 通用设计基调（每个界面都要遵守）

- **产品名**：Aiming Cookie，AI 瞄准教练
- **主色**：Cursor Orange（`#FF7E47`）作 accent，**不要满屏铺**——只用于优先级最高的元素（CTA 按钮 / 当前步骤 / 头号问题徽标）
- **背景**：暖奶油色 `#FAF7F2`（不是冷白），中性温暖
- **文字**：深炭灰 `#1F1B16`，不用纯黑
- **字体**：Inter，中英混排（英文术语 + 中文讲解）
- **气质**：专业但不冰冷——像一个有干货的运动教练的办公室。干净排版 + 大量数据可视化 + 教练口吻文案
- **避免**：花哨动画、装饰性插画、营销话术、generic 紫蓝渐变

---

## Screen 1 — 上传界面

**目的**：用户上传录像（+ 可选 CSV + 配置），点开始分析。

**Stitch 提示词**（直接复制粘贴）：

```
Design an upload screen for an AI aim coaching web app called "Aiming Cookie".
The screen should feel professional and welcoming—like a coach's clean office,
not a generic SaaS landing page.

Background: warm cream (#FAF7F2). Accent: Cursor Orange (#FF7E47). Font: Inter.
Text color: dark charcoal (#1F1B16).

Layout: centered hero section. Brand "Aiming Cookie" in heavy weight, with a
one-line bilingual tagline: "AI 瞄准教练 — upload your KovaaK's clip, get a
personalized diagnosis". Below the brand, a smaller line: "Tension-Aware Aim Analyzer".

Main upload area: a large dashed-border drop zone (60% width on desktop, full
width mobile) for the video file (.mp4). Cloud-upload icon in cursor orange,
supporting copy "拖入录像，或点击选择文件". Below the icon: file format hint
"MP4 · 60fps 推荐 · 任意分辨率".

Right column (40% desktop, below dropzone on mobile): optional configuration
fields stacked, each with clean labels:
- "KovaaK Stats CSV（可选）" — small file input
- "灵敏度 cm/360" — number input, default 48
- "FOV" — number input, default 103
- "高手参考视频（可选对比）" — small file input

Bottom: primary CTA button "开始分析" full-width on mobile, right-aligned
desktop. Cursor Orange fill, white text, generous padding.

Avoid: marketing hero illustrations, stock photos of gamers, feature list
with checkmarks, "trusted by" logos. Keep it focused on the upload flow.
```

---

## Screen 2 — 处理中界面

**目的**：分析 1-3 分钟期间，告诉用户在等什么 + 让等待有价值。

**Stitch 提示词**：

```
Design a processing/waiting screen for an AI aim coaching app. The user just
uploaded a video and analysis is running (1-3 minutes). The screen should make
the wait feel valuable, not anxious.

Background: warm cream (#FAF7F2). Accent: Cursor Orange (#FF7E47).

Center: a horizontal 4-step pipeline visualization (NOT a generic spinner):
  1. 解析录像 / Parsing video
  2. 提取目标轨迹 / Extracting trajectory
  3. 计算运动学指标 / Computing kinematics
  4. 生成教练讲解 / Generating narration

Each step: small line icon + Chinese label + smaller English subtitle.
Completed steps: orange checkmark + filled circle. Current step: orange pulsing
ring. Future steps: grayed out. Connect steps with a thin horizontal line that
fills with orange as steps complete.

Below the pipeline: a "教练小贴士" card (soft cream with orange left border)
showing a rotating coaching tip while the user waits. Example content:
"Becker 2020：flick 减速段是命中成败最强信号。你接下来会看到自己减速段的
SPARC 平滑度评分。" The tip text rotates every ~15 seconds.

Bottom-center: a subtle text link "取消分析" (no button styling—make it
recessively visible, not prominent).

Critical: avoid countdown timers (we don't know exact duration) and avoid
anxious fast-spinning loaders. The pipeline should feel methodical and
trustworthy.
```

---

## Screen 3 — 教练报告界面（核心，最重要）

**目的**：展示 CoachReport 全部内容——画像 + LLM 讲解 + 问题列表 + 可视化。这是产品价值交付的核心时刻。

**Stitch 提示词**：

```
Design the main result screen for an AI aim coaching app—the "Coach Report"
page. This is the core value-delivery moment: user uploaded video, waited,
now gets a full personalized coaching report. Lots of content—design for
scanability and hierarchy.

Background: warm cream (#FAF7F2). Accent: Cursor Orange (#FF7E47) used
sparingly—only for the highest-priority element in each section. Font: Inter.

=== TOP HERO — Profile Card ===
Full-width card at top, soft shadow, orange-tinted left border (4px).
Content:
- Large label: archetype name in Chinese + English, e.g., "减速抖动型 / Decel
  Jitter"
- Match confidence as a small pill: "匹配度 0.82"
- Secondary tags as small gray chips below: "长减速", "张力释放不平滑"
- Top-right corner: meta info in small gray text — "48 cm/360 · 60fps · 1w6ts"

=== BELOW HERO — Coach Narration (primary content) ===
The LLM-generated coaching text (150-300 Chinese chars). Format this like a
coach's voice note, not a paragraph of body copy:
- Prominent typography (slightly larger than body, generous line-height 1.7)
- Left orange accent bar (3px) running the full height of the text block
- Soft cream background with slightly more warmth than page bg
- Maybe an "AI 教练" label or coach-avatar icon top-left

This is the "what to focus on"—user reads this first.

=== THEN — Prioritized Issue List ===
Section heading "诊断细节 / Issues by Priority". Then a vertical stack of issue
cards. Each card:
- Top-left: severity badge (fix=red filled, watch=orange outlined, info=gray)
- Top-right: priority number "P1", "P2"... in small gray
- Headline: signal name in plain Chinese, e.g., "减速段占比过高"
- 3-layer root cause chain as a nested indented list with subtle vertical
  connector lines:
    症状：减速段占 75%
    → 物理原因：制动释放不果断
    → 训练原因：减速一次到位意识弱
- Bottom: prescription chips — each is a small card with scenario name (bold)
  + reason text. e.g., "pasu — 练完整的加速→减速"

Priority 1 card visually largest (more padding, slightly larger headline).
Lower priority cards progressively more compact.

=== BOTTOM — Visualizations ===
Section heading "数据可视化 / Visual Evidence". Responsive grid (2 columns
desktop, 1 column mobile):
1. Radar chart — player vs reference across 6 dimensions (decel_frac,
   linearity, sparc, reverse_ratio, path_eff, peak_speed)
2. Deceleration curve — one typical flick's speed profile vs ideal min-jerk
   bell curve, with peak marked
3. (If reference given) Comparison table — per-metric self vs ref with
   better/worse badges
4. Meta info bar — small strip showing cm/360, fps, scenario, timestamp,
   "powered by DeepSeek"

=== STICKY BOTTOM BAR (desktop only) ===
A slim sticky bar at bottom of viewport with two actions:
- Secondary: "导出 PDF"
- Primary: "复测 / Re-test" (Cursor Orange)

Critical: design for 5-second glance-value. User should see profile + top
issue + headline prescription without scrolling. Everything below is depth.
Use orange sparingly—overuse kills the urgency signal.
```

---

## Screen 4 — 历史趋势界面（后续 Phase，先备着）

**目的**：跨 session 进步追踪 + ④ 计划调整展示。

**Stitch 提示词**：

```
Design a history/progress screen for an AI aim coaching app. The user has done
multiple sessions and wants to see progress over time + the AI coach's plan
for next week.

Background: warm cream (#FAF7F2). Accent: Cursor Orange (#FF7E47).

Top: page heading "进步轨迹 / Progress Over Time" + scenario filter dropdown
("1w6ts" default).

Main content area, vertical stack:

1. Trend chart card — line chart with 5 metrics over time (sparc / linearity /
   decel_frac / reverse_ratio / peak_speed). Each metric a different line with
   muted color, except the user's "focus metric" (highlighted in orange).
   X-axis: session dates. Y-axis: normalized score. Show most-recent value
   as a labeled dot.

2. "上次 vs 这次" comparison card — two-column layout, each metric row:
   metric name | last value | current value | verdict badge (better=green,
   worse=red, same=gray, info=blue)

3. "下周训练计划" card (orange-tinted border) — coach's plan for next week:
   - Focus metric: "减速段平滑度 (SPARC)"
   - 3-4 scenario chips with reasons (e.g., "pasu ×10min — 完整加减速",
     "Multiclick ×5min — 落点精度")
   - Schedule note: "交错练，不刷单一场景"
   - Optional: progress bar showing if user is on track or needs rest day

Sidebar (desktop only, hidden on mobile): vertical list of past sessions,
each entry: date + scenario + one-line summary metric + colored dot for
trend (improving/plateau/regressing). Click to expand past report.

Tone: calm, longitudinal, not pushy. The user is here to reflect, not to be
sold more features.
```

---

## 给 Stitch 用的小贴士

- **每个 prompt 单独喂**，让 Stitch 出独立 mockup；之后再合并到一个产品 mockup 里看整体
- **如果出的设计不对劲**，加这一句约束："warm cream background, no purple/blue gradients, no decorative illustrations"
- **report 界面（Screen 3）内容多**，如果 Stitch 出得太挤，分两次喂：先 hero + narration（上半部分），再 issue list + 图表（下半部分）
- **图标用 line style**，不用 filled emoji-style
- **数据可视化用 plotly**，前端可以直接嵌（前端无关设计已经在 `coach/visualization.py` 里）
