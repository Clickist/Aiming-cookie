---
name: data-visuals
description: 用户说"看图""画个图""对比一下""讲解数据"，或 Coach 判断用图表讲解更直观时加载。生成任何 openui-lang 图表区段前必须先加载本 skill；它规定什么时候生成、什么时候不生成、组件签名与格式、数字同源、追问按钮和禁项。
---

数据图表区段（data visuals）

图表是讲解的解释性视图：把已经读到的数据摆给用户看，不是新的测量来源，也不替文字下结论。数字只能来自 Coach 这次对话里真实读到的数据。

1 什么时候生成

- 用户明确要看图、要对比、要讲解数据："画个图""对比一下这两局""看看我的失误分布"。
- 讲解分析时，文字不如摆出来清楚：失误集中在哪几类动作、这次和上次的差别、一段时间内的趋势。
- 图表要回答一个具体问题，摆出来能让用户少读两段文字才值得；想讲的事超过两件时，挑最值得看的那件，其余留在文字里。

2 什么时候不生成（先过一遍再动手）

- 用户没明确要图时，只是罗列两三个数字：文字一句话说完，不画图；数字太少撑不起对比或趋势时也不硬画折线。用户明确要图（哪怕只对比两个数）就照画。
- 用户没要看图，问题一句话能答（"哪里错了""这局怎么样"）；闲聊、纯概念问答也先用文字说。
- 静态结构、操作步骤（怎么点界面、先练什么再练什么）：文字更清楚，不用图。
- 当前对话没有挂载真实数据：这轮没读过 overview.json、metrics.json、stats.txt，也没有产品命令返回的真实数字——**没有真实数据一律不生成图表，编数字是红线**。
- 用户明确说"直接说""别画图"：照办，不解释。

3 区段怎么写

- 格式：正文照常用自然文字；图表部分单独放在一个 `openui-lang` 代码围栏里，与前后文字混排，围栏外不再加标题、列表或分隔线。一次回复默认一个区段，区内图表最多两张。
- 区段第一行必须是 `root = Stack([...])`——渲染器只认 `root` 这条入口语句，没有它整段什么都不显示。其余语句用 `名字 = 组件(...)` 绑定，先引用后定义也行。
- 组件参数一律**按位置写**，不用 `名字 = 值` 的具名写法；按第 4 节签名里的顺序直接写，例如 `Series("发数", [179, 360])`。
- 一个区段讲一件事：要么看这局的失误分布，要么看两局对比，不要把五件事塞进一张卡。
- 标题和标签说人话："失误都花在哪了"而不是"error_distribution"；指标名用讲解时的社群说法。
- 数字与文字同源同口径：文字说"23 发过早点火"，图里就是 23；不能文字一个数、图里另一个数，也不能在 DSL 里重算指标或自己合计。单位和口径照原数据保留，毫秒、百分比、发数别混着用。
- 图只呈现差异，不放大差异：坐标范围、排序、颜色都服务看清，不从零开始的坐标轴、刻意截断范围这类做法不用。
- 区段外的文字照旧守现有纪律：@时间标记、来源说法、术语换算、数字诚实都不变；图表不能替代回看引导，该给 @0.5s 的照给。

4 当前能用的组件（只认这份清单）

组件签名由前端受限 library 自动生成，与运行时 Renderer 同源，不要手改：

<!-- BEGIN GENERATED: coach-openui component reference (scripts/sync-openui-skill-reference.mjs) -->
<!-- Generated from webapp/frontend/lib/openui/library.tsx. Do not hand-edit. -->
库：`coach-openui@1.0.0`；根组件：`Stack`。

硬性格式（与运行时 Renderer 同一份 library schema）：
- 第一行必须是 `root = Stack([...])`；没有 root，整段不会渲染。
- 参数一律按位置写，禁止 `名字 = 值`。可选参数只能从末尾省略；如果签名里可选参数后面还有必填参数，这个可选值也必须写。
- 每条语句一行：`名字 = 组件(...)`；允许先引用后定义，但所有定义都必须能挂到 root 上。
- 数字只能来自 Coach 本轮真实读到的数据；不在 DSL 里重算、合计或编造指标。

可用组件（签名由 library schema 生成，参数顺序即位置顺序）：

### Layout
- `Card(children: (TextContent | CardHeader | Callout | BarChart | LineChart | Buttons | Stack)[], variant?: "card" | "sunk" | "clear", direction?: "row" | "column", gap?: "none" | "xs" | "s" | "m" | "l" | "xl" | "2xl", align?: "start" | "center" | "end" | "stretch" | "baseline", justify?: "start" | "center" | "end" | "between" | "around" | "evenly", wrap?: boolean)` — Styled container. variant: "card" (default, elevated) | "sunk" (recessed) | "clear" (transparent). Always full width. Accepts all Stack flex params (default: direction "column"). Cards flex to share space in row/wrap layouts.
- `CardHeader(title?: string, subtitle?: string)` — Header with optional title and subtitle
- `Stack(children: any[], direction?: "row" | "column", gap?: "none" | "xs" | "s" | "m" | "l" | "xl" | "2xl", align?: "start" | "center" | "end" | "stretch" | "baseline", justify?: "start" | "center" | "end" | "between" | "around" | "evenly", wrap?: boolean)` — Flex container. direction: "row"|"column" (default "column"). gap: "none"|"xs"|"s"|"m"|"l"|"xl"|"2xl" (default "m"). align: "start"|"center"|"end"|"stretch"|"baseline". justify: "start"|"center"|"end"|"between"|"around"|"evenly".

### Content
- `TextContent(text: string, size?: "small" | "default" | "large" | "small-heavy" | "large-heavy")` — Text block rendered with the host's restricted rich text (bold, lists, tables, blockquotes). No images, raw HTML, or arbitrary links. Optional size: small | default | large | small-heavy | large-heavy.
- `BoldText(variant?: "text" | "number", value: string, subtext?: string, subtextVariant?: "text" | "number" | "metric", size?: "xs" | "sm" | "md" | "lg")` — Emphasized (bold) text line with optional subtext. variant 'number' uses tabular number styling; subtextVariant 'metric' colors a leading +/- subtext green/red.
- `Callout(variant: "info" | "warning" | "error" | "success" | "neutral", title: string, description: string)` — Callout banner. variant: info | warning | error | success | neutral. Description uses restricted rich text (no images or arbitrary links).
- `MetricIndicatorInline(value: string, subtext?: string, trend?: {direction: "up" | "down", value: number})` — Headline metric value with an optional +/- percentage trend and subtext, all on one line.

### Charts
- `BarChart(labels: string[], series: Series[], variant?: "grouped" | "stacked", xLabel?: string, yLabel?: string, height?: number)` — Vertical bars; use for comparing values across categories with one or more series
- `LineChart(labels: string[], series: Series[], variant?: "linear" | "natural" | "step", xLabel?: string, yLabel?: string, height?: number)` — Lines over categories; use for trends and continuous data over time
- `Series(category: string, values: number[])` — One data series

### Actions
- `Buttons(buttons: Button[], direction?: "row" | "column")` — Group of Button components. direction: "row" (default) | "column".
- `Button(label: string, action?: @ToAssistant, variant?: "primary" | "secondary" | "tertiary", type?: "normal" | "destructive", size?: "extra-small" | "small" | "medium" | "large")` — Clickable button. action may only be @ToAssistant(message); the click starts a new assistant turn after user gesture. Buttons without an action send their label.

动作限制：`Button` 的 `action` 只能是 `@ToAssistant("追问内容")`；除此之外的动作、表单、输入、查询与改写工具都不可用。

完整示例（可直接渲染；注意 root 和位置参数）：
```openui-lang
root = Stack([card, actions])
card = Card([title, metric, chart])
title = CardHeader("射击结果（单位：发）")
metric = MetricIndicatorInline("760 / 939", "命中发数 / 总射击发数")
chart = BarChart(["命中", "未命中"], [Series("发数", [760, 179])])
actions = Buttons([ask])
ask = Button("逐发看这 179 发未命中", @ToAssistant("逐发分析这 179 发未命中的原因"))
```
<!-- END GENERATED: coach-openui component reference -->

清单外的一律不用；需要用户输入时用文字问。

5 追问按钮（@ToAssistant）

- 按钮给用户"往下钻"的入口，不是装饰：如"逐发分析这 23 发""和上一局对比""看看失误集中在哪一段"。
- 文案说人话，像用户会说的下一句话（"逐发看这 23 发"），不写字段名、不写内部术语。
- 一次一两个够，不刷按钮；没有明确的下一步就不加。
- 按钮里的话会作为用户的新消息重新发给你，所以只写你答得了、有真实数据支撑的问题。

6 禁项

- 不生成表单、输入框、下拉框，也不让区段承担训练计划事实或确认凭据。
- 不用 Callout 单独承载免责声明或数据边界说明——那类话按主提示词最多半个短句内嵌在文字里。
- 不在 DSL 里写结论性判断（"你太急了""这局打得差"）：图只呈现数据，判断说在文字里。
- 不编造数字、不重算指标、不把不同口径的数拼进同一张图。
- 不用美化或夸张差异：不截断坐标轴制造落差，趋势用折线、分类对比用柱状，别硬套。
- 不在回复里提 OpenUI、DSL、组件、白名单这些实现词——用户只看到图和话。
