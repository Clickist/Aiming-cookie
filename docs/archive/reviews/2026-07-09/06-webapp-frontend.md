# Webapp Frontend Review — 2026-07-09

> Reviewer: Claude Code agent (read-only, deep dive)
> Scope: `webapp/frontend/` 全量（Next.js 16 + React 19 + Tailwind v4，4 路由：upload / processing / report / coach）
> 依据：PRD §6 核心体验流程 + §8 13 条 UIUX 决策；昨天的 05-webapp-frontend.md review 补位

---

## 健康度：B+（良好，深挖发现新问题）

代码质量整体扎实——组件拆分清晰、类型严格、a11y 意识强。昨天 review 已验证 07-07 五项修复全部通过。本次深挖补充了 **Critical 决策冲突**（processing 强制跳转 vs PRD）+ **High 功能缺口**（history 页缺失）+ 若干 React 正确性问题。无 Security Critical。

| 级别 | 数量 |
|------|------|
| Critical | 1 |
| High | 1 |
| Medium | 6 |
| Low | 4 |

---

## Critical

### C1. Processing 完成强制跳转违反 PRD §6.3"不强制跳转"

**文件**：`app/sessions/[id]/page.tsx:164-168`

**代码**：
```typescript
useEffect(() => {
  if (state.kind === "ok" && state.data.status === "done") {
    router.push(`/sessions/${sessionId}/report`);
  }
}, [state, sessionId, router]);
```

**问题**：当前实现是分析完成后自动 `router.push` 到报告页。**这与 PRD §6.3 明确冲突**：
> "完成通知：全局 toast + 顶栏角标（任意页可见，**不强制跳转**）"

PRD §6.1 核心体验流程也写明：
> "完成时：全局 toast + 顶栏角标（任意页可见，不强制跳转）"

**影响**：
1. 用户如果在 processing 页切走（如切换标签页），会被强制拉回 report 页，打断其他工作
2. 与"processing 可后台"的产品定位矛盾（PRD §8 #3）
3. 违反"不强制跳转"的明确产品决策

**建议**：
1. 删除 `router.push` 的强制跳转逻辑
2. 改为显示 toast/角标通知（设计：toast 内容"分析完成，查看报告"带按钮；角标：header 加红点/badge）
3. 保留 processing 页的"查看报告"按钮（当前没有，需新增）作为用户主动出口

**状态**：**需立即修复**（违反产品决策）

---

## High

### H1. History 页缺失（最大功能缺口）

**文件**：整个 `webapp/frontend/app/`

**问题**：PRD §6.2 回访旅程的核心：
```
启动（已登录）→ 检测有 history → **history（默认页）**
  ├ 趋势卡 + 诊断列表 + 对话历史
  └ 大"新建分析"按钮
```

PRD §8 #1 决策：
> "默认页动态：无 history → upload，有 → history"

**现状**：`app/page.tsx` 是 upload 首页（固定），没有 history 路由/组件。以下功能都依赖它：
- 默认页动态切换（PRD §8 #1）
- 导出/导入功能（PRD §8 #6）
- 角标显示（PRD §11 完成通知）
- 趋势卡（progress.py 趋势数据）

**影响**：
1. 回访用户每次都进 upload 页，无法看到历史
2. 默认页动态分支无法实现
3. 完成通知的角标无处显示
4. PRD 核心体验流程缺失

**建议**：
1. 新建 `app/history/page.tsx`（或 `app/page.tsx` 改为动态分支，检测 history 有无决定渲染）
2. history 页布局：
   - 趋势卡（趋势图 + ④ 训练计划按钮）
   - 诊断列表（每次的 archetype label + 时间 + 查看报告/继续教练）
   - 大"新建分析"按钮 → `router.push('/upload')`（新建 upload 路由或保持当前 `/`）
3. 后端 API：`GET /api/sessions?user_id=X` 返回历史列表（当前后端无此 endpoint，需新增）

**状态**：**高优先级缺口**（阻塞核心体验）

---

## Medium

### M1. useEffect 依赖数组不完整（exhaustive-deps 违规）

**文件**：`app/page.tsx:73-85`（handleVideoSelected）、`app/page.tsx:87-114`（handleCsvChange）

**代码**：
```typescript
const handleVideoSelected = useCallback(
  (file: File | null | undefined) => {
    clearError();  // 调用 clearError
    // ...
  },
  [validateVideo],  // ← 依赖数组没有 clearError
);

const handleCsvChange = useCallback(
  async (e: ChangeEvent<HTMLInputElement>) => {
    clearError();  // 调用 clearError
    // ...
  },
  [validateCsv],  // ← 依赖数组没有 clearError
);

const clearError = () => setError(null);  // ← 每次渲染重新创建
```

**问题**：`clearError` 是普通函数，每次渲染重新创建，但 `useCallback` 依赖数组没有包含它。虽然 `setError` 是 stable 的（行为正确），但违反 ESLint `react-hooks/exhaustive-deps` 规则。

**影响**：
- 无运行时 bug（`setError(null)` 永远做同一件事）
- ESLint 报警
- 闭包理论风险（未来如果 `clearError` 变复杂会出问题）

**建议**：把 `clearError` 包成 `useCallback`：
```typescript
const clearError = useCallback(() => setError(null), []);
```
或者加入依赖数组（不推荐，会导致每次渲染重新创建 callback）。

---

### M2. 轮询 fetch 未接 AbortSignal

**文件**：`app/sessions/[id]/page.tsx:123-136`（`poll` 函数）

**代码**：
```typescript
const poll = useCallback(async (): Promise<SessionStatus | null> => {
  if (!Number.isFinite(sessionId)) return null;
  try {
    const data = await getSession(sessionId);  // ← 不传 signal
    setState({ kind: "ok", data });
    return data;
  } catch (e) {
    // ...
  }
}, [sessionId]);
```

**问题**：虽然 effect 有 `cancelled` flag 阻止二次调度，但底层 fetch 会继续到完成。用户快速从 processing 页跳走时（如改 URL），残留的 fetch 完成后 setState 被 React 19 忽略（no-op），但白白消耗网络请求。

**对比**：`CoachView.tsx` 的 `getTimeline` / `getChatHistory` 都正确使用了 `AbortController`（line 215-223、488-494）。

**影响**：轻微网络浪费。不影响行为。

**建议**：
```typescript
useEffect(() => {
  const ctrl = new AbortController();
  const loop = async () => {
    if (cancelled) return;
    const data = await poll();  // poll 内部传 ctrl.signal
    // ...
  };
  loop();
  return () => {
    cancelled = true;
    ctrl.abort();  // ← 取消进行中的 fetch
    clearTimeout(timer);
  };
}, [poll]);
```
`poll` 函数需接收 `signal` 参数并传给 `getSession`。

---

### M3. ChatPane 收到新消息时无条件滚到底部

**文件**：`app/sessions/[id]/coach/CoachView.tsx:497-500`

**代码**：
```typescript
useEffect(() => {
  const el = scrollRef.current;
  if (el) el.scrollTop = el.scrollHeight;
}, [history]);
```

**问题**：每次 `history` 变化都强制滚到底部。如果用户正在向上滚动阅读历史消息，教练回复到达时会被强制拉回底部。

**影响**：聊天中阅读旧消息时体验被打断（时间戳联动是核心创新，但回看被打断很烦）。

**建议**：检测用户是否在底部附近：
```typescript
useEffect(() => {
  const el = scrollRef.current;
  if (!el) return;
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
  if (isNearBottom) {
    el.scrollTop = el.scrollHeight;
  }
}, [history]);
```
或用 ref 记存 "用户是否手动滚动了" 状态。

---

### M4. `pulse-ring` 动画无 `prefers-reduced-motion` 守卫

**文件**：`app/globals.css:239-250`

**代码**：
```css
@keyframes pulse-ring {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 78, 0, 0.35); }
  50% { box-shadow: 0 0 0 6px rgba(245, 78, 0, 0); }
}
.pulse-ring {
  animation: pulse-ring 1.8s ease-out infinite;
}
```

**问题**：无限循环动画没有 `@media (prefers-reduced-motion: reduce)` 关闭。同样 Tailwind 的 `animate-pulse`（processing 页 line 266、CoachView line 591）也没有 reduced-motion 守卫。

**影响**：前庭功能障碍用户可能被持续动画引起不适。WCAG 2.3.3 (AAA) 建议。

**建议**：
```css
@media (prefers-reduced-motion: reduce) {
  .pulse-ring, .animate-pulse {
    animation: none !important;
  }
}
```

---

### M5. NumberField 无 min/max 约束

**文件**：`app/page.tsx:440-449`

**代码**：
```typescript
<input
  id={id}
  type="number"
  inputMode="decimal"
  step="0.01"
  value={value}
  // ← 没有 min/max
/>
```

**问题**：用户可以输入负数或极端值（如 cm/360 = -100、FOV = 9999）。后端会做校验，但前端没有 guard。

**影响**：用户输入错误值得等到后端 422 才知道。

**建议**：加 `min` 属性——FOV `min={1}` `max={180}`；cm/360 `min={0.1}` `max={200}`。或在提交前做客户端校验。

---

### M6. Timeline markers 不可访问

**文件**：`app/sessions/[id]/coach/CoachView.tsx:389-411`

**代码**：
```typescript
<div
  key={`${ev.type}-${i}`}
  title={ev.label}  // ← 有 title 但无法 hover
  className="absolute pointer-events-none"  // ← pointer-events-none
  style={{ ... }}
/>
```

**问题**：marker `<div>` 有 `title={ev.label}` 但 `pointer-events-none` 且无 `tabIndex`，键盘用户无法 hover 看 label。`title` 属性在触摸设备上也不可靠。

**影响**：marker 信息对键盘/触摸用户不可达。但 marker 是视觉辅助，实际事件数据在 chat 消息中。

**建议**：marker 保持纯装饰（`aria-hidden="true"`），或加 `role="img"` + `aria-label`。低优先。

---

## Low

### L1. DropZone 的 `onError` prop 实际是 `clearError`（命名误导）

**文件**：`app/page.tsx:186`（传 `onError={clearError}`）

**问题**：prop 名叫 `onError` 暗示 "错误发生时的回调"，但实际用途是 "清除错误状态"（用户开始交互时清掉之前的错误）。

**建议**：重命名为 `onClearError` 或 `onInteract`。

---

### L2. CSV `FileField` 未重置 `e.target.value`

**文件**：`app/page.tsx:485-491`

**问题**：DropZone 的 `handleChange` 末尾有 `e.target.value = ""`（line 341），但 FileField 的 CSV input 没有。在部分浏览器中，选择同一 CSV 文件第二次不触发 `onChange`。

**建议**：在 `handleCsvChange` 末尾加 `e.target.value = ""`。

---

### L3. Processing 页 pipeline `grid-cols-4` 在窄屏可能拥挤

**文件**：`app/sessions/[id]/page.tsx:286`

**问题**：4 列固定网格，每列含 48px badge + 英文 label + 中文 sub-label。在 360px 宽度下每列约 90px，中文 sub-label 可能换行。

**建议**：在 `< sm` 断点改成 2×2 网格或水平滚动。低优先。

---

### L4. 无 Error Boundary 组件

**文件**：整个 `webapp/frontend/`

**问题**：React 客户端组件渲染错误（如后端返回意外 JSON 结构）会白屏。没有 `app/error.tsx` 或 Error Boundary。

**建议**：加 `app/error.tsx`（Next.js App Router 级别的 error boundary）。

---

## 正向发现（值得保持）

### 1. 时间戳联动实现优雅
**文件**：`app/sessions/[id]/coach/CoachView.tsx`

**优点**：
- `parseTimestamps` 正则实现正确（区间贪婪 + 单点非重叠）
- `videoRef` 提升到父组件避免 `window.dispatchEvent`（昨天已验证修复）
- A-B 循环通过 `activeSeg` + `onTimeUpdate` 实现（line 243-246），无需额外状态机

### 2. AbortController 使用正确
**文件**：`CoachView.tsx:215-223`、`CoachView.tsx:488-494`

**优点**：`getTimeline` 和 `getChatHistory` 都正确使用了 `AbortSignal`，cleanup 里 `ctrl.abort()`。

### 3. 类型定义严格
**文件**：`lib/types.ts`

**优点**：与后端 `schemas.py` 字段一致（snake_case 直传），无映射层错误。`SessionStatusEnum` 类型安全。

### 4. a11y 意识强
**优点**：
- `role="slider"` + `aria-*` 属性齐全（timeline track line 352-357）
- `aria-label` 在按钮/交互元素上都有
- `focus-visible:ring-2` 样式（globals.css line 169-172）
- `sr-only` skip link（layout.tsx line 56-61）

### 5. StrictMode 兼容
**文件**：`app/sessions/[id]/page.tsx:142-160`（轮询 effect）

**优点**：effect-local `cancelled` flag 防止 StrictMode 双挂载时的双循环（昨天已验证修复）。

---

## 类型一致性检查（lib/types.ts vs backend/schemas.py）

| 字段 | types.ts | schemas.py | 状态 |
|------|----------|------------|------|
| `SessionStatus.id` | `number` | `int` | ✓ |
| `SessionStatus.status` | `SessionStatusEnum` | `str` | ✓（前端更严格） |
| `SessionStatus.result` | `CoachReport \| null` | `Optional[dict]` | ✓ |
| `Timeline.fps` | `number` | `int` | ✓（JS number 涵盖 int） |
| `TimelineEvent.type` | `"kill" \| "miss" \| "peak" \| "corrective" \| string` | `str` | ✓（前端更严格） |
| `ChatMessage.role` | `"user" \| "assistant"` | `str` | ✓（前端更严格） |

**结论**：类型定义一致，前端更严格（字面量类型），这是好实践。

---

## 决策项现状总结

### 1. Processing 完成强制跳转
**现状**：`app/sessions/[id]/page.tsx:164-168` 实现 `router.push` 强制跳转
**PRD 要求**：不强制跳转（PRD §6.3、§6.1）
**状态**：**冲突，需修复**

### 2. Upload 文件夹分别记忆
**现状**：两个 `<input type="file">` 共享 Chromium 同origin 目录记忆（浏览器原生行为，非代码 bug）
**PRD 要求**：分别记忆（PRD §8 #13）
**状态**：**PRD 描述需修正**（不是 bug，是浏览器默认行为；修复需 File System Access API，属功能开发）

---

## Top 3 建议优先修复

1. **C1 — Processing 强制跳转**：违反 PRD，影响核心体验流程。删除 `router.push`，改为 toast/角标。
2. **H1 — History 页缺失**：阻塞默认页动态/导出导入/角标显示。需新建路由 + 后端 API。
3. **M1 — useEffect 依赖数组**：一行修复（`clearError` 包成 `useCallback`），消除 ESLint 警告。

---

## 07-08 已修复项验证（继承昨天的结论）

| # | 修复项 | 状态 |
|---|--------|------|
| 1 | no-scrollbar / pulse-ring CSS 补全 | PASS |
| 2 | videoRef 提升 + seekTo props | PASS |
| 3 | 轮询闭包 cancelled flag | PASS |
| 4 | timeline / 倍速 a11y | PASS |
| 5 | 响应式断点 | PASS |

---

## 昨天未覆盖的新发现

| # | 发现 | 昨天覆盖? |
|---|------|----------|
| 1 | Processing 强制跳转 vs PRD（Critical） | ✗ |
| 2 | History 页缺失（High） | ✗ |
| 3 | useEffect 依赖数组不完整 | ✗ |
| 4 | ChatPane 无条件滚动 | ✓（昨天 M2） |
| 5 | Timeline markers 不可访问 | ✗ |
| 6 | 类型一致性检查 | ✗ |

---

## 结论

前端代码质量 B+，React/Next.js 正确性整体良好。本次深挖的主要问题是 **PRD 对齐缺口**（history 页缺失、processing 强制跳转冲突）和 **React 细节**（依赖数组/滚动行为/动画守卫）。无 Security Critical，无类型错误。

**关键路径**：
1. 修复 C1（processing 跳转）—— 昨天提到"toast/角标落地时改"，今天明确为 Critical
2. 补齐 H1（history 页）—— 功能缺口最大，阻塞 PRD 核心体验
3. 处理 M1-M4（React 正确性）—— 低成本高价值
