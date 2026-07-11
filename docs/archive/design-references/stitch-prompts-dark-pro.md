# Aiming Cookie — Stitch 提示词（深色专业工具方向）

> 7 个界面的完整 Stitch 提示词。喂给 Stitch 时：每次把 **System Brief** 放最前面，再接对应界面的 Screen Brief。
> Stitch 出 HTML 后交给 Claude 做统一化和调整。

---

## System Brief（每个界面都要附在最前面）

```
DESIGN SYSTEM — Aiming Cookie (use this for every screen)

Product: Aiming Cookie — AI flicking aim coach for KovaaK's players.
Brand voice: professional performance tool, like Linear or Vercel. Not
gaming-flashy. Clean, dense, trustworthy.

=== COLOR TOKENS ===
--bg:          #0d0d0c   (page background, true near-black, no brown)
--surface:     #131210   (card background)
--surface-hi:  #1c1b18   (hover/active surface, slightly lifted)
--border:      #272520   (default 1px border)
--border-hi:   #3a3632   (focused/hover border)
--text-primary:   #f3f0ea   (primary text, warm off-white — NOT pinkish cream)
--text-secondary: #807d72   (secondary text, mid-gray)
--text-muted:     #4a4845   (disabled / placeholder)
--accent:      #f54e00   (Cursor Orange — use SPARINGLY: primary CTA,
                            P1 badge, active state only)
--accent-dim:  #2a1500   (orange tint surface, for badge bg)
--green:       #22c55e   (success / "better" verdict)
--red:         #ef4444   (error / "fix" severity)
--amber:       #f59e0b   (warning / "watch" severity)
--blue:        #3b82f6   (info / neutral chart color)

=== TYPOGRAPHY ===
Fonts: Inter (all UI text), JetBrains Mono (metrics, timestamps, code, labels).
NO Outfit. NO display fonts. Inter only.

Scale:
- Hero heading: Inter 52–64px, weight 500, letter-spacing -0.035em
- Section heading: Inter 22–24px, weight 600, letter-spacing -0.02em
- Body: Inter 15–16px, weight 400, line-height 1.65
- Label / caption: JetBrains Mono 11–12px, weight 500, UPPERCASE, letter-spacing 0.08em
- Metric value: JetBrains Mono 28–36px, weight 600

=== CARD / SURFACE RULES ===
- Cards: background #131210, border 1px solid #272520, border-radius 8px
- NO shadows. Elevation only via border opacity: hover → border #3a3632
- Card padding: 24px standard, 32px for hero cards
- NO glass blur / vibrancy. Clean flat surfaces.
- Left accent bar on coaching content: 3px solid #f54e00 left border

=== LAYOUT ===
- Max content width: 1200px, centered
- Section rhythm: 64px vertical gap between major sections
- Grid: 12-column with 24px gap
- Header: 64px tall, fixed/sticky, bg #0d0d0c, 1px bottom border #272520
- NO decorative illustrations. NO hero images. NO grain overlay.
- AVOID orange overuse — only the most important interactive element per
  section gets orange.

=== COMPONENT RULES ===
Primary CTA button: bg #f54e00, text white, border-radius 6px, padding
  12px 24px, font Inter weight 600 14px
Secondary button: bg transparent, border 1px #272520, text #f3f0ea,
  hover border #3a3632
Input field: bg transparent, border-bottom 1px #3a3632, no border-radius,
  focus border-bottom #f54e00
Severity badge — fix:   bg #2d0a0a text #ef4444 border #ef4444/30
Severity badge — watch: bg #1f1200 text #f59e0b border #f59e0b/30
Severity badge — info:  bg #141210 text #807d72 border #272520
Mono label chip: JetBrains Mono 11px UPPERCASE, bg #1c1b18, border #272520,
  padding 3px 8px, radius 4px
```

---

## Screen 1 — Landing Page（营销/入口页）

```
[SYSTEM BRIEF ABOVE]

Design a landing page for Aiming Cookie. This is the marketing/entry page
for KovaaK's players who haven't used the product. Goal: explain what it
does in 5 seconds, then drive them to "Start analyzing" (upload page).

=== HEADER ===
Fixed, 64px tall, bg #0d0d0c, 1px bottom border.
Left: wordmark "Aiming Cookie" in Inter weight 600, #f3f0ea.
Right: nav links in JetBrains Mono 12px UPPERCASE — "DOCS" "GITHUB" — then
a primary CTA button "开始分析 →" (accent orange).

=== HERO SECTION ===
Centered, max-width 720px, padding-top 140px.

Eyebrow label (JetBrains Mono 11px UPPERCASE letter-spacing 0.1em, color
#f54e00): "FLICKING TENSION ANALYZER"

H1 (Inter 64px weight 500 tracking -0.035em, color #f3f0ea, two lines):
  "Upload your clip.
  Read your aim."

Subtext (Inter 17px weight 400 color #807d72, max-width 480px, centered,
line-height 1.65): "上传 KovaaK's 录像，AI 教练解析减速段张力、目标获取速度
与微校正模式，给出个性化诊断。"

CTA row (margin-top 40px, centered, flex gap 12px):
- Primary: "开始分析 →" orange button (large: padding 14px 32px)
- Secondary: "查看示例报告" outline button

=== FEATURE STRIP ===
Horizontal row of 3 cards below hero (margin-top 96px), full-width, 12-col
grid with 24px gap. Each card: bg #131210, border 1px #272520, radius 8px,
padding 28px 24px.

Card 1:
  Icon: simple line SVG of a velocity curve (no fills, #f54e00 stroke).
  Label chip: "DECEL ANALYSIS"
  Heading (Inter 18px weight 600): "减速段公平指标"
  Body (Inter 15px #807d72): "SPARC 平滑度 / Fitts Throughput / decel_frac —
  跨距离公平，不刷高速。"

Card 2:
  Icon: simple line target/crosshair SVG, #f54e00 stroke.
  Label chip: "AI DIAGNOSIS"
  Heading: "三层根因诊断"
  Body: "症状 → 物理原因 → 训练原因。不是说你哪里不好，是告诉你为什么。"

Card 3:
  Icon: simple chat bubble with a small spark, #f54e00 stroke.
  Label chip: "COACH CHAT"
  Heading: "和教练对话"
  Body: "点击视频里的时间戳，教练直接跳到那一帧讲解。"

=== HOW IT WORKS ===
Section heading (Inter 22px weight 600, centered, margin-top 96px):
"三步开始"

Below: horizontal 3-step flow, connected by a hairline (#272520).
Each step: number badge (JetBrains Mono 11px in a 28px circle, border #272520)
+ label.
  1 — 上传录像 + CSV
  2 — 等待分析 (~2 min)
  3 — 读取诊断 + 和教练对话

=== FOOTER ===
Border-top 1px #272520, padding 32px 0.
Left: "Aiming Cookie © 2026" in JetBrains Mono 11px #4a4845.
Right: "Privacy · Terms" same style.
Center: "Powered by DeepSeek" mono label chip.

Critical design notes:
- NO hero illustration or gamer stock photo
- NO purple/blue gradients
- The orange accent only appears on: eyebrow label, CTA button, icon strokes
- Everything else is off-white / mid-gray on near-black
- Desktop-first layout but responsive (stack to single column below 768px)
```

---

## Screen 2 — Upload Page（应用入口）

```
[SYSTEM BRIEF ABOVE]

Design the upload page for Aiming Cookie. This is where users start an
analysis: upload a KovaaK's recording + Stats CSV + enter calibration params.

=== HEADER ===
Same as landing page header. CTA button replaced with "历史记录" link.

=== MAIN LAYOUT ===
Two-column grid (desktop), max-width 1200px, padding 96px 24px:
Left column (7/12): Hero text + video dropzone
Right column (5/12): Config card

LEFT COLUMN:
  Eyebrow: "FLICKING ANALYSIS" (mono label, orange)
  H1 (Inter 52px weight 500 tracking -0.035em): "分析你的 flicking 张力"
  Subtext (Inter 16px #807d72): "上传录像和 KovaaK Stats CSV，系统提取 flick
  减速段，生成个性化诊断。"

  Video dropzone (margin-top 40px):
    bg #131210, border 1.5px dashed #272520, border-radius 8px,
    min-height 280px, centered content.
    Hover state: border-color #f54e00, bg #131210 (border only changes).
    Content (centered, flex-col, gap 12px):
      Upload icon (line SVG, 40px, color #4a4845)
      Primary text (Inter 16px weight 500 #f3f0ea): "拖入 MP4 录像，或点击选择"
      Secondary text (Inter 13px #807d72): "上限 100MB · 推荐 1080p / 60fps"
    Filled state (file selected):
      Show filename in JetBrains Mono 14px #f3f0ea, filesize in #807d72,
      a "×" remove button top-right.

RIGHT COLUMN:
  Config card (bg #131210, border 1px #272520, radius 8px, padding 32px):
    Heading (Inter 16px weight 600 UPPERCASE letter-spacing 0.04em): "分析配置"
    Hairline divider below heading.

    Field 1: "KOVAAK STATS CSV（必填）"
      Mono label 11px above field.
      Custom file input: full-width, border 1px #272520, radius 4px,
      padding 10px 12px, flex row: filename text + "选择文件" small button.

    Field 2: "CM / 360"
      Mono label above. Underline-only input (no box), JetBrains Mono font,
      default value "48", right-aligned. Helper text below in 11px #4a4845:
      "后端从 CSV 自动算，可手填覆盖"

    Field 3: "FOV"
      Same style as cm/360. Default "103".
      If CSV auto-fills this: show "✓ CSV 已读" in green 11px mono.

    Primary CTA: "开始分析 →" full-width orange button, margin-top 24px.
    Disabled state: opacity 40%, cursor not-allowed.
    Loading state: show "提交中…" with a small spinner (no color change).

    Error state: a red text line below CTA, Inter 13px #ef4444.

=== FOOTER ===
Same minimal footer as landing page.
```

---

## Screen 3 — Processing / Waiting Page

```
[SYSTEM BRIEF ABOVE]

Design the waiting/processing page shown while analysis runs (~1-3 minutes).
The user just submitted; backend is processing. Make the wait feel deliberate
and trustworthy, not anxious.

=== HEADER ===
Same sticky header. Center text (desktop only): "Processing analysis…" in
JetBrains Mono 11px UPPERCASE #4a4845.

=== MAIN CONTENT (centered, max-width 720px) ===

Status pill (centered, top):
  Small pulsing dot (4px circle, #f54e00, CSS pulse animation) + text in
  JetBrains Mono 11px UPPERCASE #807d72: "RUNNING · SESSION #42"
  Wrapped in: bg #131210, border 1px #272520, radius 99px, padding 6px 14px.

H1 (Inter 52px weight 500 tracking -0.035em, centered, margin-top 32px):
  "正在构建你的教练报告"

Subtext (Inter 16px #807d72, centered, max-width 480px):
  "AI 正在解析录像与 CSV，提取 flick 减速段，并生成个性化诊断。"

=== PIPELINE (margin-top 64px) ===
Horizontal 4-step pipeline, full-width, connected by hairline track.
Track: 1px line #272520, horizontal, at vertical center of badges.

Each step (4 columns):
  Badge (40px circle):
    done state: border 1px #f54e00, bg #0d0d0c, checkmark icon #f54e00
    active state: border 2px #f54e00, bg #0d0d0c, spark/bolt icon #f54e00,
                  subtle box-shadow: 0 0 12px rgba(245,78,0,0.25)
    pending state: border 1px #272520, bg #0d0d0c, small dot #4a4845

  Label below badge (Inter 13px weight 500 #f3f0ea for active, #4a4845 for pending)
  Sublabel (JetBrains Mono 11px UPPERCASE #807d72)

  Steps:
    1. PARSING — 数据解析
    2. TRAJECTORY — 轨迹追踪
    3. KINEMATICS — 运动学建模
    4. NARRATION — 生成报告

=== COACH TIP CARD (margin-top 64px, max-width 520px, centered) ===
  bg #131210, border-left 3px solid #f54e00, border (other sides) 1px #272520,
  radius 0 8px 8px 0, padding 20px 24px.
  Row layout: left = small bulb icon (#f54e00, 16px), right = content.
  Tag chip above body: JetBrains Mono 11px UPPERCASE, bg #1c1b18 border
  #272520 — e.g. "BECKER 2020"
  Body text (Inter 15px #807d72 line-height 1.65):
    "flick 减速段是命中成败最强的预测信号——爆发靠本能，命中靠刹车。"
  (Tips rotate; show static in design)

=== PROGRESS BAR (margin-top 48px, centered) ===
  Width 240px, height 2px, bg #272520, radius 99px.
  Fill: bg #f54e00, width varies by state (10% queued, 60% running, 100% done).
  Status text below: JetBrains Mono 11px UPPERCASE #4a4845 — "RUNNING…"

=== CANCEL LINK (bottom) ===
  "取消分析" — plain text link, Inter 13px #4a4845, no button style,
  centered below progress bar.

No countdown timers. No spinning loaders. No fabricated ETAs.
```

---

## Screen 4 — Coach Report Page（核心价值交付）

```
[SYSTEM BRIEF ABOVE]

Design the Coach Report page — the core value delivery of the product.
User sees: archetype profile → LLM coaching narration → prioritized issue
cards with root-cause chains → data visualizations. Design for 5-second
glance value: top content should answer "what's my main problems" without
scrolling.

=== HEADER ===
Sticky, 64px. Left: "Aiming Cookie" wordmark + separator + "Coach Report
· #42" in mono. Right: "← 返回" link text.

=== HERO CARD (full-width, margin-top 32px) ===
bg #131210, border 1px #272520, radius 8px, padding 32px.
Left-border accent: 4px solid #f54e00.

Left side (60%):
  Eyebrow chip (JetBrains Mono 11px UPPERCASE, bg #1c1b18 border #272520):
    "PLAYER ARCHETYPE"
  H1 (Inter 52px weight 500 tracking -0.035em, color #f3f0ea, margin-top 12px):
    archetype label, e.g. "减速抖动型 / Decel Jitter"
  Secondary tags (flex-wrap, gap 8px, margin-top 16px):
    Each tag: JetBrains Mono 11px, border 1px #272520, #807d72, padding 3px 10px
    radius 99px. e.g. "长减速" "张力释放不平滑"
  Meta line (JetBrains Mono 11px #4a4845, margin-top 16px, flex gap 16px):
    "48 CM/360 · 60 FPS · 1W6TS · 2026-07-05"

Right side (40%):
  Top-right aligned:
    Small label "匹配度" JetBrains Mono 11px UPPERCASE #807d72
    Large number (JetBrains Mono 48px weight 600 #f54e00): "82%"

=== BENTO GRID (margin-top 24px, 12-col 24px gap) ===

Section A — COACHING NARRATION (col-span 8):
  Card: bg #131210, border 1px #272520, border-left 3px #f54e00, radius 0 8px 8px 0.
  Padding 32px.
  Header row: icon spark/AI (#f54e00 16px) + label chip "AI 教练" + section
  heading "教练讲解" Inter 18px weight 600.
  Body: Inter 17px #f3f0ea line-height 1.75, the LLM narration text.
  Fallback (no narration): gray bordered info box "讲解生成失败，请参考下方诊断。"

Section B — RADAR CHART (col-span 4):
  Card: bg #131210, border 1px #272520, radius 8px, padding 24px.
  Header: "SKILL DISTRIBUTION" JetBrains Mono 11px UPPERCASE #807d72.
  Plotly radar chart embedded below (dark bg, orange trace).

Section C — PRIORITIZED ISSUES (col-span 7, below A+B):
  Section heading row: "诊断细节 / Issues by Priority" Inter 18px weight 600
  + "(3 issues)" mono chip.
  Stack of issue cards, each (bg #131210, border 1px #272520, radius 8px,
  padding 24px, margin-bottom 12px):

    Top row: severity badge (left) + "P1" priority (right, mono 11px #4a4845)
    Signal name (Inter 18px weight 600 #f3f0ea, margin-top 8px)
    Priority reason (Inter 14px #807d72, margin-top 4px)

    Root cause chain (margin-top 16px, border-left 2px #272520, padding-left 16px):
      Each layer row:
        Level label (JetBrains Mono 11px UPPERCASE #4a4845, fixed width 72px): "症状"
        Text (Inter 14px #807d72)
      Layers: 症状 → 物理原因 → 训练原因

    Prescription chips (margin-top 16px):
      Label "训练处方" JetBrains Mono 11px UPPERCASE #4a4845.
      Each chip: Inter 13px #f3f0ea, border 1px #272520, padding 4px 12px,
      radius 99px, hover border-color #f54e00 hover text #f54e00. title attr = reason.

    P1 card: slightly more padding, signal name 20px. P2+ cards: normal size.

Section D — DECEL CURVE (col-span 5, beside C):
  Card: bg #131210, border 1px #272520, radius 8px, padding 24px, min-height 400px.
  Header: "VELOCITY PROFILE · DECEL CURVE" mono label.
  Plotly chart embedded.

=== STICKY BOTTOM ACTION BAR ===
Fixed bottom-0, full-width. bg #0d0d0c/90 backdrop-blur, border-top 1px #272520.
Inner content: max-width 1200px centered, padding 16px 24px, flex justify-between.

Left (desktop only):
  Label "NEXT STEP" mono 11px #4a4845
  Text "和教练对话 · 训练计划 · 复测" Inter 14px #807d72

Right row (flex gap 12px):
  "导出 PDF" — secondary outline button (disabled / opacity 40%)
  "复测" — secondary outline button (disabled / opacity 40%)
  "和教练对话 →" — primary orange CTA button
```

---

## Screen 5 — Coach Dialogue Page（视频 + 聊天分屏）

```
[SYSTEM BRIEF ABOVE]

Design the Coach Dialogue page. This is a split-pane layout:
LEFT 65%: video player + custom timeline with flick markers.
RIGHT 35%: AI coach chat interface.
Full-viewport-height, no scroll.

=== TOP HEADER (64px, same as other pages) ===
Left: "Aiming Cookie" wordmark + "Coach Dialogue · #42" breadcrumb.
Right: "← 返回报告" text link.

=== LEFT PANE (65% width, bg #0d0d0c, border-right 1px #272520) ===
Padding 24px. Flex-col.

  Video player area (flex-grow):
    16:9 aspect ratio container, bg #000000, radius 8px, overflow hidden.
    Video element fills container (object-fit: contain).
    Error overlay if video fails: centered text #807d72 on black bg.

  Custom timeline (margin-top 16px, bg #131210, border 1px #272520,
  radius 8px, padding 12px 16px):
    Control row:
      Play/Pause icon button (material-symbols or SVG, #f3f0ea hover #f54e00)
      Timestamp display: JetBrains Mono 13px "0:12 / 1:45" (#f3f0ea / #807d72)
      Speed options: "0.5x 1x 2x" — small mono chips, active state bg
        #1c1b18 text #f54e00, inactive text #807d72.
      "A-B" loop button: mono 11px #807d72, hover #f3f0ea.

    Track area (margin-top 8px, height 32px, relative, cursor pointer):
      Base bar: full width, height 2px, bg #272520, centered vertically.
      Progress fill: height 2px, bg #f54e00.
      Playhead: 2px wide vertical line, full height of track area, bg #f54e00,
        top: 4px circle handle.
      Active segment highlight (A-B range): bg #f54e00/15 full height, positioned.
      Flick markers: small vertical ticks at computed positions:
        - "peak" type: 6px tall, 1px wide, bg #f54e00
        - "corrective" type: 8px tall, 1px wide, bg #3b82f6
        - "valley" type: 4px tall, 1px wide, bg #272520

=== RIGHT PANE (35% width, bg #0d0d0c, flex-col) ===

  Chat header (shrink-0, border-bottom 1px #272520, padding 12px 16px):
    Left: avatar circle (32px, bg #2a1500, "AI" mono text or spark icon #f54e00) +
    "AI 教练 · 减速抖动型 专项" Inter 13px weight 600 #f3f0ea.
    Right: close "×" icon link (returns to report).

  Message thread (flex-grow, overflow-y auto, padding 16px, gap 16px):
    Empty state: centered text "和你的 AI 教练对话" Inter 14px #807d72.

    Starter chips (when no messages yet, flex-wrap gap 8px):
      Each chip: Inter 12px #807d72, border 1px #272520, padding 4px 12px,
      radius 99px, hover border #f54e00 text #f54e00.
      E.g. "减速段分析" "握持建议" "我的反向修正太多?"

    User message bubble:
      Right-aligned, max-width 85%.
      bg #f54e00, text white, radius 12px 12px 2px 12px, padding 12px 16px.
      Timestamp below: JetBrains Mono 10px UPPERCASE #4a4845, right-aligned.

    AI coach message:
      Left-aligned, with avatar circle (same as header, 28px).
      Message bg #131210, border 1px #272520, radius 12px 12px 12px 2px,
      padding 12px 16px, Inter 14px #f3f0ea line-height 1.65.
      Timestamp below: JetBrains Mono 10px UPPERCASE #4a4845.
      Timestamp capsules (inline): bg #2a1500 text #f54e00, border #f54e00/20,
      radius 99px, JetBrains Mono 11px, cursor pointer, hover bg #3a1800.

  Pinned-frame bar (above input, if active):
    Full-width strip, border-top 1px #272520, padding 6px 16px.
    Flex row: pin icon (#f54e00 12px) + "已锁定 0:23 (点击取消)" Inter 11px #807d72.
    Click → clear pin.

  Input area (shrink-0, border-top 1px #272520, padding 12px 16px):
    Container: bg #131210, border 1px #272520, radius 8px,
    focus-within border #f54e00. Flex-col.
    Textarea row: textarea (bg transparent, no border, Inter 14px #f3f0ea
    placeholder #4a4845, resize none, rows 1 auto-grow) + pin-frame button
    (24px, #807d72 hover #f54e00) + send button (36px circle bg #f54e00,
    white arrow icon, disabled opacity 40%).
```

---

## Screen 6 — History Page（历史记录列表）

```
[SYSTEM BRIEF ABOVE]

Design the History page. User sees all past analysis sessions, listed most
recent first. Each row links to the report. No trend charts (Phase 2).

=== HEADER ===
Same as other pages. Page breadcrumb: "历史记录".

=== MAIN CONTENT (max-width 1200px, padding 64px 24px) ===

Page heading row (flex, justify-between, align-center):
  Left: H1 "历史记录" Inter 32px weight 600 #f3f0ea
  Right: "新建分析 →" orange CTA button

Subheading: "共 N 次分析" JetBrains Mono 11px UPPERCASE #4a4845,
margin-top 8px.

Hairline divider (1px #272520, margin-top 24px).

=== SESSION LIST ===
Vertical stack of rows (no cards — use full-width hairline-divided rows for
density). Each row:
  border-bottom 1px #272520, padding 20px 0, flex align-center gap.
  Hover: bg #131210 (subtle surface lift on the row).

  Left:
    Date (JetBrains Mono 13px #807d72): "2026-07-04"
    Time (JetBrains Mono 11px #4a4845): "14:32"

  Middle (flex-grow, padding 0 32px):
    Scenario chip (JetBrains Mono 11px UPPERCASE, bg #1c1b18 border #272520
    #807d72): "1W6TS"
    Archetype label (Inter 16px weight 500 #f3f0ea, margin-left 12px):
    "减速抖动型 / Decel Jitter"
    Meta (JetBrains Mono 11px #4a4845, margin-top 4px): "48 cm/360 · 60fps"

  Right:
    P1 severity badge (fix/watch/info style)
    P1 signal (Inter 13px #807d72): "decel_frac 偏高"
    Arrow icon "→" (#4a4845, hover #f3f0ea)

Empty state (no sessions yet):
  Centered card, bg #131210 border #272520, padding 48px, text-center.
  Icon: simple upload line icon, #4a4845, 32px.
  Text "还没有分析记录" Inter 16px #807d72.
  CTA: "上传第一个录像 →" orange button.
```

---

## Screen 7 — Login / OTP Page

```
[SYSTEM BRIEF ABOVE]

Design the email OTP login page. Two states shown side by side in design:
State A = email input; State B = OTP code input (after email sent).

=== HEADER ===
Same minimal header. No nav links in auth state.

=== MAIN CONTENT (centered, max-width 400px, full-height centered) ===

State A — Email input:
  Wordmark "Aiming Cookie" (Inter 20px weight 600 #f3f0ea, centered, mb 32px).
  H2 (Inter 24px weight 600 tracking -0.02em): "登录 / 注册"
  Subtext (Inter 14px #807d72, margin-top 8px): "输入邮箱，我们发送 6 位验证码"

  Card (bg #131210, border 1px #272520, radius 8px, padding 32px, margin-top 32px):
    Field label: "邮箱地址" JetBrains Mono 11px UPPERCASE #807d72
    Input (full-width, bg transparent, border-bottom 1px #3a3632, padding 12px 0,
    Inter 16px #f3f0ea, placeholder "you@example.com", focus border-bottom #f54e00)
    Primary CTA: "发送验证码 →" full-width orange button, margin-top 24px.
    Below CTA, small text (Inter 12px #4a4845, centered, margin-top 16px):
    "登录即同意《服务条款》和《隐私政策》"

State B — OTP input (after email sent):
  Same wordmark + H2.
  Subtext: "验证码已发送至 player@example.com"
  Below: a "更换邮箱" plain text link, Inter 12px #807d72, hover #f54e00.

  Card:
    Field label: "验证码" JetBrains Mono 11px UPPERCASE #807d72
    6-digit OTP input: 6 separate boxes (48px × 56px each), bg transparent,
    border 1px #272520, border-radius 6px, centered single digit in
    JetBrains Mono 24px #f3f0ea. Active box: border #f54e00.
    Primary CTA: "登录 →" full-width orange button, margin-top 24px.
    Below CTA (Inter 12px #4a4845, centered, margin-top 16px):
    "59s 后可重新发送" with a "重新发送" link (#807d72 → #f54e00 on hover,
    disabled while counting down).

Error states:
  Invalid email: red text below input "邮箱格式不正确" Inter 12px #ef4444.
  Wrong OTP: red text below boxes "验证码错误" Inter 12px #ef4444,
    boxes border #ef4444.

Loading state: CTA shows "发送中…" / "验证中…" with small spinner.
```

---

## 喂给 Stitch 的操作建议

- **每次喂一个 screen**（含 System Brief），不要一次塞多个，否则 Stitch 容易混乱。
- **如果 Stitch 出的设计跑偏**（出现紫色/蓝色渐变、装饰插图、glass blur），加一句约束：
  `warm near-black background #0d0d0c, NO purple/blue gradients, NO decorative illustrations, NO glass blur/vibrancy, flat surfaces only.`
- **Report 页（Screen 4）内容多**，如果 Stitch 出得太挤，分两次喂：
  先 hero + narration + radar（上半），再 issue cards + decel curve + sticky bar（下半）。
- **Coach 页（Screen 5）是分屏**，建议喂的时候强调"split-pane, no scroll, full viewport height"。
- **字体**：Stitch 可能默认用 system fonts，明确要求 `Inter + JetBrains Mono from Google Fonts`。

## 给我（Claude）的统一化任务（Stitch 出 HTML 后）

Stitch 经常前后不一致，需要我统一的地方：

1. **颜色 token**：所有 HTML 的颜色必须严格匹配 System Brief 的 token，不能有近似值。
2. **字体加载**：每个 HTML 都要有 Google Fonts 的 Inter + JetBrains Mono link。
3. **border-radius 一致性**：卡片 8px、按钮 6px、chip 4px 或 99px（pill）——不能混用。
4. **spacing 节律**：section 间距 64px，card padding 24px/32px，不能任意值。
5. **橙色使用克制**：每个 screen 检查橙色只出现在 CTA / P1 badge / active state / eyebrow label。
6. **mono label 格式**：UPPERCASE + letter-spacing 0.08em 统一。
7. **无装饰**：去掉 Stitch 可能加的渐变背景、装饰 SVG、emoji 图标。
