# Webapp Frontend Review — 2026-07-08

> Reviewer: Claude Code agent (read-only)
> Scope: `webapp/frontend/` 全量（Next.js 16 + React 19 + Tailwind v4，4 路由：upload / processing / report / coach）
> 依据：PRD §6 核心体验流程 + §8 13 条 UIUX 决策；PROGRESS.md 2026-07-07 前端修复段

---

## 健康度：B+（良好，可发布）

代码质量整体扎实——组件拆分清晰、注释充分、类型严格（tsconfig strict: true）、副作用清理到位、a11y 意识明显（aria-* / role / keyboard / skip link / focus-visible）。没有 Critical bug。一个 High 是 skip link 的 landing target 只存在于 loading.tsx，实际页面全部缺失 `id="main-content"`。上传文件夹记忆 bug 属于浏览器原生行为，非代码层共享 key（详见专节）。07-07 五项修复全部验证通过。

| 级别 | 数量 |
|------|------|
| Critical | 0 |
| High | 1 |
| Medium | 6 |
| Low | 6 |

---

## Critical

无。

---

## High

### H1. Skip link 目标 `#main-content` 在所有实际页面缺失

**文件**：`app/layout.tsx:57`（skip link 定义）；`app/page.tsx:169`、`app/sessions/[id]/page.tsx:239`、`app/sessions/[id]/report/ReportView.tsx:67`、`app/sessions/[id]/coach/CoachView.tsx:183`（均无 `id="main-content"`）；`app/sessions/[id]/coach/loading.tsx:17` + `app/sessions/[id]/report/loading.tsx:18`（仅这两处有）

**问题**：`layout.tsx` 定义了标准的 skip-to-main-content 链接 `<a href="#main-content">`，但只有 `loading.tsx` 的 `<main>` 带了 `id="main-content"`。页面加载完成后 RSC 渲染实际页面组件，`<main>` 没有 `id="main-content"`，skip link 点击后 URL 变成 `/#main-content` 但焦点不跳转，什么也不发生。

**影响**：键盘用户 / 屏幕阅读器用户在所有页面都无法使用 skip link 跳过 header 导航。a11y 形同虚设。

**建议**：在四个实际页面的 `<main>` 元素上加 `id="main-content"`：
- `app/page.tsx:169` → `<main id="main-content" className="flex-1 w-full ...">`
- `app/sessions/[id]/page.tsx` Shell 组件 line 239 → `<main id="main-content" className="relative z-10 ...">`
- `app/sessions/[id]/report/ReportView.tsx:67` → `<main id="main-content" className="flex-grow ...">`
- `app/sessions/[id]/coach/CoachView.tsx:183` → `<main id="main-content" className="flex-1 ...">`

---

## Medium

### M1. A-B 循环按钮看起来可交互但完全无功能

**文件**：`app/sessions/[id]/coach/CoachView.tsx:452-460`

**问题**：按钮有 `hover:text-primary transition-colors` 样式（视觉暗示可交互），但没有 `onClick` handler、没有 `disabled` 属性。`title` 和 `aria-label` 标注了 "(占位)"，但视觉上用户无法区分它和旁边的播放/倍速按钮。

**影响**：用户点击后无反应，产生困惑。A-B 循环功能实际已通过时间戳胶囊的 range 点击实现（`seekTo` + `activeSeg` 的 `onTimeUpdate` 回跳逻辑），但这个占位按钮的存在暗示有独立功能。

**建议**：加 `disabled` 属性，或直接删除（功能已由 range timestamp 胶囊覆盖）。

### M2. ChatPane 收到新消息时无条件滚到底部，打断阅读

**文件**：`app/sessions/[id]/coach/CoachView.tsx:497-500`

**问题**：
```typescript
useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
}, [history]);
```
每次 `history` 变化都强制滚到底部。如果用户正在向上滚动阅读历史消息，教练回复到达时会被强制拉回底部。

**影响**：聊天中阅读旧消息时体验被打断。

**建议**：检测用户是否在底部附近（如 `scrollHeight - scrollTop - clientHeight < threshold`），仅在底部时才自动滚动。或用 ref 讇存 "用户是否手动滚动了" 状态。

### M3. 轮询 fetch 未接 AbortSignal——组件卸载后 in-flight 请求继续

**文件**：`app/sessions/[id]/page.tsx:123-136`（`poll` 函数）

**问题**：`poll` 调用 `getSession(sessionId)` 不传 `signal`。虽然 `cancelled` flag 阻止了二次调度和 setState 后的副作用（React 19 中 setState on unmounted 是 no-op），但底层 fetch 会继续到完成，白白消耗网络请求。

对比：`CoachView.tsx` 的 `getTimeline` / `getChatHistory` 都正确使用了 `AbortController`（line 215-223、488-494）。

**影响**：轻微网络浪费。用户快速从 processing 页跳走时（如改 URL），残留的 fetch 完成后 setState 被丢弃。

**建议**：在 polling effect 内创建 `AbortController`，传给 `poll` → `getSession`，cleanup 里 `ctrl.abort()`。

### M4. `pulse-ring` 动画无 `prefers-reduced-motion` 守卫

**文件**：`app/globals.css:239-250`

**问题**：`pulse-ring` 无限循环动画（`animation: pulse-ring 1.8s ease-out infinite`），没有 `@media (prefers-reduced-motion: reduce)` 关闭。同样 `animate-pulse`（Tailwind 内置，用于 processing 页的 `w-1.5 h-1.5 bg-primary rounded-full animate-pulse` line 266、以及 CoachView 的 `auto_awesome` 发送中图标 line 591）也没有 reduced-motion 守卫。

**影响**：前庭功能障碍用户可能被持续动画引起不适。WCAG 2.3.3 (AAA) 建议。

**建议**：在 globals.css 加：
```css
@media (prefers-reduced-motion: reduce) {
    .pulse-ring { animation: none; }
}
```
Tailwind v4 的 `animate-pulse` 可通过配置关闭或加同类 media query。

### M5. `handleVideoSelected` / `handleCsvChange` 缺少 `clearError` 依赖

**文件**：`app/page.tsx:73-85`（handleVideoSelected）、`app/page.tsx:87-114`（handleCsvChange）

**问题**：两个 `useCallback` 都调用了 `clearError`（line 75、89），但依赖数组分别是 `[validateVideo]` 和 `[validateCsv]`，没有列 `clearError`。`clearError` 是普通函数（`const clearError = () => setError(null)`，line 46），每次渲染重新创建。

由于 `clearError` 内部只调 `setError(null)` 而 `setError` 是 stable 的，行为正确——但 eslint exhaustive-deps 会报警告，且闭包捕获的是首次渲染的 `clearError`。

**影响**：无行为 bug（`setError(null)` 永远做同一件事），但违反 hooks 规范。

**建议**：把 `clearError` 包成 `useCallback(() => setError(null), [])`，或把 `clearError` 加入依赖数组。

### M6. `NumberField` 无 min/max 约束

**文件**：`app/page.tsx:417-453`

**问题**：cm/360 和 FOV 的 `<input type="number" step="0.01">` 没有 `min` 属性。用户可以输入负数或极端值（如 cm/360 = -100、FOV = 9999）。后端会做校验，但前端没有 guard。

**影响**：用户输入错误值得等到后端 422 才知道。

**建议**：加 `min` 属性——FOV `min={1}` `max={180}`；cm/360 `min={1}`。或在提交前做客户端校验。

---

## Low

### L1. DropZone 的 `onError` prop 实际是 `clearError`——命名误导

**文件**：`app/page.tsx:186`（传 `onError={clearError}`）、`app/page.tsx:309`（DropZone props 定义 `onError: () => void`）、`app/page.tsx:319`（`handleDragOver` 内调用 `onError()`）

**问题**：prop 名叫 `onError` 暗示 "错误发生时的回调"，但实际用途是 "清除错误状态"。维护者可能误以为要在这里 set error。

**建议**：重命名为 `onClearError` 或 `onInteract`（它实际表示 "用户开始交互了，清掉之前的错误"）。

### L2. CSV `FileField` 未重置 `e.target.value`

**文件**：`app/page.tsx:485-491`（FileField 的 `<input type="file">`）

**问题**：DropZone 的 `handleChange` 末尾有 `e.target.value = ""`（line 341，注释 "allow re-selecting same file"），但 FileField 的 CSV input 没有。在部分浏览器中，选择同一 CSV 文件第二次不触发 `onChange`。

**影响**：极边缘场景（用户换了一个 CSV 后又换回原来的），现代 Chrome/Firefox 通常仍触发 `onChange`。

**建议**：在 `handleCsvChange` 末尾加 `e.target.value = ""`，与 DropZone 一致。

### L3. Processing 页 pipeline `grid-cols-4` 在窄屏可能拥挤

**文件**：`app/sessions/[id]/page.tsx:286`

**问题**：4 列固定网格，每列含 48px badge + 英文 label + 中文 sub-label。在 360px 宽度下每列约 90px，中文 sub-label（"数据解析" / "轨迹追踪" / "运动学建模" / "生成执教报告"）4 字可能换行。

**影响**：视觉拥挤但功能不受影响。4 步 pipeline 在手机上横向显示是合理的设计选择。

**建议**：如需改进，在 `< sm` 断点改成 2×2 网格或水平滚动。低优先。

### L4. Timeline markers 使用 `title` 属性但元素不可聚焦

**文件**：`app/sessions/[id]/coach/CoachView.tsx:389-411`

**问题**：marker `<div>` 有 `title={ev.label}` 但 `pointer-events-none` 且无 `tabIndex`，键盘用户无法 hover 看 label。`title` 属性在触摸设备上也不可靠。

**影响**：marker 信息对键盘/触摸用户不可达。但 marker 是视觉辅助，实际事件数据在 chat 消息中。

**建议**：marker 保持纯装饰即可，或加 `aria-label` + `role="img"`。低优先。

### L5. 无前端测试

**文件**：整个 `webapp/frontend/`

**问题**：没有任何 `.test.tsx` / `.test.ts` 文件。后端有 webapp/tests/（47 passed），但前端零覆盖。

**影响**：`parseTimestamps`（时间戳解析）、`parseKovaaKConfig`（CSV config 提取）、`fmtSec`（时间格式化）等纯函数适合 unit test。轮询 / 副作用逻辑适合 integration test。

**建议**：至少为 `lib/csv.ts` 的 `parseKovaaKConfig` 加 unit test（边缘 case：空文件、无 config block、多 config block、Unicode）。v1 可选。

### L6. 无 Error Boundary 组件

**文件**：整个 `webapp/frontend/`

**问题**：React 客户端组件渲染错误（如后端返回意外 JSON 结构导致 `profile.label` 为 undefined）会白屏。没有 `error.tsx` 或 Error Boundary。

**影响**：运行时错误时用户看到白屏而非友好的错误提示。

**建议**：加 `app/error.tsx`（Next.js App Router 级别的 error boundary），或在关键组件外包 Error Boundary。

---

## Top 3 建议优先修复

1. **H1 — skip link 目标缺失**：一行修复（加 `id="main-content"`），影响全部页面的 a11y。最简单最值得做。
2. **M1 — A-B 循环按钮加 disabled 或删除**：一分钟修复，消除用户困惑。
3. **M2 — ChatPane 智能滚动**：大幅提升 coach 对话页的核心体验（时间戳联动是核心创新，但用户回看旧消息被打断很烦）。

---

## 07-07 修复验证（全部通过）

| # | 修复项 | 验证 | 状态 |
|---|--------|------|------|
| 1 | **no-scrollbar / pulse-ring CSS 补全** | `globals.css:228-234`（`.no-scrollbar` 含 webkit + Firefox + IE 三条规则）、`globals.css:239-250`（`@keyframes pulse-ring` + `.pulse-ring` 类）。使用点：`CoachView.tsx:575`（chat thread）、`CoachView.tsx:606`（chip row）、`sessions/[id]/page.tsx:346`（StepBadge active）。CSS 定义在使用前出现，Tailwind v4 `@theme inline` 不干预自定义类。 | PASS |
| 2 | **videoRef 提升 + seekTo props** | `CoachView.tsx:150`：`const videoRef = useRef<HTMLVideoElement>(null)` 在 CoachView 顶层创建。`seekTo` callback（line 153-159）操作 `videoRef.current.currentTime` + `setActiveSeg`。`videoRef` 作为 prop 传入 VideoPane 和 ChatPane。全文件无 `window.dispatchEvent` / `CustomEvent` / `document.querySelector`。 | PASS |
| 3 | **轮询闭包 cancelled flag** | `sessions/[id]/page.tsx:142-160`：effect 内 `let cancelled = false`，`loop()` 内两次检查（await 前后），cleanup 设 `cancelled = true` + `clearTimeout(timer)`。StrictMode 双挂载时首个 effect 的 cleanup 设 cancelled=true，旧 loop 在 await 返回后退出；新 effect 起新 loop。无双循环。 | PASS |
| 4 | **timeline / 倍速 a11y** | track div（line 349-362）：`role="slider"` + `tabIndex={0}` + `aria-label="视频时间轴"` + `aria-valuemin/max/now` + `aria-valuetext={fmtSec(current)}` + `onKeyDown`（ArrowLeft/Right ±5s）。倍速按钮（line 437-450）：`aria-pressed={rate === r}`。播放按钮（line 338-347）：`aria-label={playing ? "暂停" : "播放"}`。 | PASS |
| 5 | **响应式断点** | CoachView：`md:h-dvh` / `md:overflow-hidden` / `md:flex-row` / `md:w-[65%]` + `md:w-[35%]` / `h-[60vh] md:h-auto`（移动端垂直堆叠 + 聊天固定 60vh）。Report：`md:col-span-{8,4,7,5}` + `md:flex-row` + `hidden md:flex`（bento 网格在窄屏塌缩为单列）。Upload：`lg:grid-cols-12` + `lg:col-span-{8,4}`（窄屏单列）。Processing：`md:flex-row` + `md:items-center`（footer）、`md:inline`（顶栏副标题）。断点策略一致，移动端可用。 | PASS |

---

## 上传文件夹记忆 Bug 定位（PRD §8 #13）

### 结论：非代码层 bug——浏览器原生行为，无可修的 "共用 key"

**搜索结果**：整个 `webapp/frontend/` 无任何以下内容：
- `localStorage` / `sessionStorage` 调用（grep 零命中）
- `showOpenFilePicker` / File System Access API
- `webkitdirectory` 属性
- 自定义路径记忆 / directory handle 持久化

**实际机制**：两个 `<input type="file">` 均为裸 HTML 元素：
- 视频：`app/page.tsx:369-375`，`accept="video/mp4,video/*"`，有 `ref={inputRef}`
- CSV：`app/page.tsx:485-491`，`accept=".csv,text/csv"`，无 ref

Chromium 内核浏览器（Chrome / Edge / Brave）的文件选择器在 OS 层面记住 "上次使用的目录"，这个记忆是**跨所有同 origin 页面的 file input 共享的**（与 `accept` 无关，与 input 的 `id` / `ref` 无关）。用户先选视频（如 `D:\录像\`），再点 CSV 上传时，选择器打开在 `D:\录像\` 而非 CSV 所在目录——这就是 PRD 描述的 "共用" 现象。

**这不是代码 bug，是浏览器默认行为**。不存在 "localStorage key 共用" 或 "input 默认路径共用" 的代码层问题。

### 修复方案（需要新增功能，非简单 patch）

**方案 A（推荐，渐进增强）**：File System Access API + IndexedDB
1. 检测 `window.showOpenFilePicker` 是否可用（Chrome/Edge 支持；Firefox/Safari 不支持）
2. 用 `showOpenFilePicker({ id: 'video-dir', startIn: ... })` 替代 `<input type="file">`，其中 `id` 参数让浏览器**按 input 用途分别记住目录**
3. Firefox/Safari fallback 到普通 `<input type="file">`

这实际上是 Chromium 124+ 已内置的功能——`showOpenFilePicker` 的 `id` 参数让浏览器按 picker identity 分别记忆目录，无需 IndexedDB。

**方案 B（轻量但不可靠）**：依赖 `accept` 属性
部分 Chromium 旧版本按 `accept` filter 分别记忆目录。当前两个 input 的 `accept` 已不同（`video/*` vs `.csv`），在某些浏览器上可能已经分开记忆。但不可靠，不能作为修复。

**方案 C（零代码）**：文档说明
在 upload 页面加一行提示："建议先选 CSV，再选视频"——因为 CSV 目录更可能固定（KovaaK's 导出目录），视频目录更多变。

**建议**：方案 A 是正解，但属于 PRD v1 功能开发范畴（需要新的上传组件 + 能力检测 + fallback），不适合作为 "bug 修复" 直接 patch。应在 IA redesign → writing-plans 的 upload 组件重构中一并处理。

### 关键文件路径
- 上传页：`webapp/frontend/app/page.tsx`（DropZone 组件 line 302-415，FileField 组件 line 455-494）
- CSV 解析：`webapp/frontend/lib/csv.ts`
- API 调用：`webapp/frontend/lib/api.ts`（`uploadVideo` line 50-82）
- 样式：`webapp/frontend/app/globals.css`（no-scrollbar / pulse-ring 定义）

---

## 附：组件架构总览

```
app/
├── layout.tsx              # Root layout（字体加载 + skip link + Material Symbols CDN）
├── page.tsx                # Upload 页（DropZone + NumberField + FileField + 表单）
├── not-found.tsx           # 404 页
└── sessions/[id]/
    ├── page.tsx            # Processing 页（轮询 + pipeline + coach tips + 进度条）
    ├── report/
    │   ├── page.tsx        # RSC server component（status gate + fetch）
    │   ├── ReportView.tsx  # Client view（hero + bento grid + IssueCard + MetaLine）
    │   └── loading.tsx     # Suspense skeleton
    └── coach/
        ├── page.tsx        # RSC server component（status gate + fetch + archetype label）
        ├── CoachView.tsx   # Client view（videoRef + VideoPane + ChatPane + MessageBubble）
        └── loading.tsx     # Suspense skeleton

components/
└── PlotlyChart.tsx         # react-plotly.js wrapper（dynamic import, ssr: false）

lib/
├── api.ts                  # fetch-based API client（uploadVideo / getSession / chat / timeline / video URL）
├── csv.ts                  # 客户端 KovaaK CSV config block 解析（parseKovaaKConfig）
└── types.ts                # TypeScript 镜像后端 dataclass（snake_case 直传）
```

**4 路由**（upload / processing / report / coach），无 history / login（IA redesign spec → writing-plans 待做）。文件总量约 2947 行 TS/TSX。
