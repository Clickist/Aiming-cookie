# IA Redesign — App 顶栏 + 现有页接入 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一的 App 顶栏组件，把现有 4 个页面（upload / processing / report / coach）的零散/缺失导航统一接入 IA spec §3.1 的词汇，消除"三套不一致导航"。

**Architecture:** 新建一个共享 client 组件 `components/AppNavbar.tsx`（`usePathname` 判 active，`full`/`processing` 两变体），各页 import 它替换现有内联 header。report/coach 当前无导航壳，补上 AppNavbar + 面包屑。同步建立前端 vitest 测试基建（项目当前无前端测试）。

**Tech Stack:** Next.js 16.2.10 · React 19.2.7 · Tailwind v4（dark，复用 globals.css 的 design tokens）· Material Symbols Outlined（layout.tsx 已引入）· vitest + @testing-library/react（本 plan 新增）

> **状态：2026-07-10 暂缓；不再是可执行 implementation plan。**
>
> **PRD 覆盖说明**：本计划以旧 IA spec 为前提，仍把 `/sessions/[id]/coach` 和 `/coach` 的关系、以及「登录后默认进入 History」当作可直接施工的 UI 合同。新 `docs/PRD.md` 已改定：首次无论权限均从上传开始；回访才默认 History；终局 Coach 是可引用 0～N 次分析的持久线程。故 Task 1–6、其中的 `/coach` stub、active-route 假设和「后续默认页逻辑」均**不得继续执行**。保留文件仅作历史设计/测试基建参考。
>
> 替代路径：先完成 Pi adoption assessment，并由其后的替代 Coach implementation plan 指定单个 Task；导航与默认路由仅按新的 persistent Coach 产品边界收敛。已经存在的 design-token 约束仍可复用，但不构成旧 IA 的继续授权。

## Global Constraints

- Next.js 16.2.10 + React 19.2.7，**不升级**版本
- 实现前先遵循 `docs/design-system.md`；复用 `webapp/frontend/app/globals.css` 的 Tailwind design tokens（`bg-background` / `text-on-surface` / `text-on-surface-variant` / `text-primary` / `border-outline-variant` / `bg-surface-container-*` / `max-w-[var(--spacing-container-max)]`），**不引入新 CSS 框架或图标库**
- 新增组件和页面不得写 raw hex、临时色板或任意视觉值来绕过 token；布局尺寸仅在现有 spacing/type/radius scale 无法表达且确有语义必要时例外，并在实现说明中记录
- 顶栏词汇严格按 IA spec §3.1：`logo · [分析 / 历史 / 教练] · [订阅状态 · 设置]`
- 三档导航规则按 IA spec §3.2：App 顶栏（upload/report/coach）/ 交易态降级（processing）
- 中文文案（zh-CN），不混入英文标题（现有 "Analyze your flicking" 等营销 hero 文案不在本 plan 改动范围）
- **不动 `next.config`**（`output: 'export'` 静态导出是桌面打包 plan 的事，不在本 plan）
- **processing 不降级**（PRD §6.1/§6.3，IA spec §3.2 已修正对齐）：processing 用完整 App 顶栏，用户可切走去 history/settings；完成通知（全局 toast + 顶栏角标）走独立 plan，不在本 plan
- path alias `@` → `webapp/frontend/`（与现有 `@/lib/api` 一致）

## Scope（本 plan 覆盖 / 不覆盖）

**覆盖**：App 顶栏组件 + upload/processing/report/coach 四页接入 + stub 占位页（/history、/settings，避免顶栏链接 404）+ 前端测试基建。

**不覆盖**（后续 plan）：
- `history` 页完整实现（趋势卡 + session 列表）— 独立 plan
- `settings` 页完整实现（aim profile 表单 + 订阅）— 独立 plan
- `login` 页（依赖 auth 后端，跨前后端大工程）— 独立 plan
- `landing` web 官网（独立静态站，不在 webapp/frontend）
- 订阅状态真实数据（v1 占位 "Early Access"）

---

## File Structure

| 文件 | 责任 | 本 plan |
|---|---|---|
| `components/AppNavbar.tsx` | 共享 App 顶栏（full/processing 变体，active 态） | 新建 |
| `components/AppNavbar.test.tsx` | AppNavbar 单测 | 新建 |
| `vitest.config.ts` + `vitest.setup.ts` | 前端测试基建 | 新建 |
| `app/page.tsx` | upload 页：删内联 header，接入 AppNavbar | 改 |
| `app/sessions/[id]/page.tsx` | processing 页：自定义 TopAppBar → AppNavbar(processing) | 改 |
| `app/sessions/[id]/report/page.tsx` | report 页：加 AppNavbar + 面包屑 | 改 |
| `app/sessions/[id]/coach/page.tsx` | coach 页：加 AppNavbar | 改 |
| `app/history/page.tsx` | history stub 占位 | 新建 |
| `app/settings/page.tsx` | settings stub 占位 | 新建 |
| `package.json` | 加 vitest 等 devDeps + test script | 改 |

---

### Task 1: 前端测试基建 + AppNavbar 组件

**Files:**
- Create: `webapp/frontend/vitest.config.ts`
- Create: `webapp/frontend/vitest.setup.ts`
- Create: `webapp/frontend/components/AppNavbar.tsx`
- Create: `webapp/frontend/components/AppNavbar.test.tsx`
- Modify: `webapp/frontend/package.json`（devDeps + scripts）

**Interfaces:**
- Produces: `default export AppNavbar()` — 顶栏组件，显示完整导航（分析/历史/教练 + Early Access + 设置）。processing 页也用完整顶栏（不降级，见 Global Constraints / IA spec §3.2）

- [ ] **Step 1: 装测试依赖**

Run（在 `webapp/frontend/`）:
```bash
npm install -D vitest @vitejs/plugin-react @testing-library/react @testing-library/jest-dom @testing-library/dom jsdom
```
Expected: 依赖写入 `package.json` devDependencies。

- [ ] **Step 2: 写 `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./") },
  },
});
```

- [ ] **Step 3: 写 `vitest.setup.ts`**

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 4: 加 test scripts 到 `package.json`**

在 `scripts` 加：
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 5: 写失败测试 `components/AppNavbar.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AppNavbar from "./AppNavbar";
import { usePathname } from "next/navigation";

// Mock next/navigation 的 usePathname
vi.mock("next/navigation", () => ({ usePathname: vi.fn() }));

const mockPathname = (p: string) =>
  (usePathname as unknown as ReturnType<typeof vi.fn>).mockReturnValue(p);

describe("AppNavbar", () => {
  it("full 变体渲染三个导航项 + 设置", () => {
    mockPathname("/");
    render(<AppNavbar />);
    expect(screen.getByText("分析")).toBeInTheDocument();
    expect(screen.getByText("历史")).toBeInTheDocument();
    expect(screen.getByText("教练")).toBeInTheDocument();
    expect(screen.getByLabelText("设置")).toBeInTheDocument();
  });

  it("当前路径对应的导航项带 active 样式", () => {
    mockPathname("/history");
    const { container } = render(<AppNavbar />);
    const historyLink = screen.getByText("历史");
    // active 用 text-primary font-bold（见 AppNavbar 实现）
    expect(historyLink.className).toContain("font-bold");
  });

  it("根路径只对 '/' 精确 active，不误命中其他项", () => {
    mockPathname("/");
    render(<AppNavbar />);
    expect(screen.getByText("分析").className).toContain("font-bold");
    expect(screen.getByText("历史").className).not.toContain("font-bold");
  });
});
```

- [ ] **Step 6: 跑测试确认失败**

Run: `npx vitest run components/AppNavbar.test.tsx`
Expected: FAIL — `Cannot find module './AppNavbar'`（组件还没建）。

- [ ] **Step 7: 实现 `components/AppNavbar.tsx`**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { label: "分析", href: "/" },
  { label: "历史", href: "/history" },
  { label: "教练", href: "/coach" },
] as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

export default function AppNavbar() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-50 w-full bg-background/80 backdrop-blur-md border-b border-outline-variant">
      <div className="max-w-[var(--spacing-container-max)] mx-auto px-md lg:px-lg h-16 flex items-center justify-between">
        <Link
          href="/"
          className="font-display text-headline-sm font-bold text-primary"
        >
          Aiming Cookie
        </Link>

        <nav className="flex items-center gap-md">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={
                "text-label-md transition-colors " +
                (isActive(pathname, item.href)
                  ? "text-primary font-bold"
                  : "text-on-surface-variant hover:text-primary")
              }
            >
              {item.label}
            </Link>
          ))}
          <div className="h-4 w-px bg-outline mx-xs" />
          {/* 订阅状态 — v1 占位（auth/计费接通后替换为真实余量/badge） */}
          <span
            className="text-label-sm text-on-surface-variant/60"
            title="v1 开放注册"
          >
            Early Access
          </span>
          <Link
            href="/settings"
            aria-label="设置"
            className="material-symbols-outlined text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            settings
          </Link>
        </nav>
      </div>
    </header>
  );
}
```

- [ ] **Step 8: 跑测试确认通过**

Run: `npx vitest run components/AppNavbar.test.tsx`
Expected: PASS（4 tests）。

- [ ] **Step 9: type-check + lint**

Run: `npx tsc --noEmit && npx next lint`
Expected: 无错误。

- [ ] **Step 10: Commit**

```bash
git add webapp/frontend/vitest.config.ts webapp/frontend/vitest.setup.ts \
  webapp/frontend/components/AppNavbar.tsx webapp/frontend/components/AppNavbar.test.tsx \
  webapp/frontend/package.json webapp/frontend/package-lock.json
git commit -m "feat(webapp): add AppNavbar shared component + vitest harness"
```

---

### Task 2: upload 页接入 AppNavbar

**Files:**
- Modify: `webapp/frontend/app/page.tsx:147-167`（删内联 `<header>`，替换为 `<AppNavbar />`）

**Interfaces:**
- Consumes: `AppNavbar` from Task 1

- [ ] **Step 1: 改 `app/page.tsx`**

在文件顶部 import 区加：
```tsx
import AppNavbar from "@/components/AppNavbar";
```

把现有的内联 `<header>...</header>`（第 147-167 行，含 logo + "Flicking Tension Analyzer" + GitHub 链接）**整段删除**，替换为：
```tsx
      <AppNavbar />
```

> 理由：IA spec §3.3 upload「去掉 DOCS/GITHUB，进 App 顶栏」。GitHub 链接移除（产品本体不暴露外部仓库入口）。

- [ ] **Step 2: type-check + lint + build**

Run: `npx tsc --noEmit && npx next lint && npx next build`
Expected: build 成功（upload 页改用 AppNavbar，无类型错误）。

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/app/page.tsx
git commit -m "feat(webapp): upload page uses shared AppNavbar, drop inline header"
```

---

### Task 3: report 页加 AppNavbar + #session 面包屑

**Files:**
- Modify: `webapp/frontend/app/sessions/[id]/report/page.tsx`

**Interfaces:**
- Consumes: `AppNavbar` from Task 1

> 现状：report page 是 Server Component（async），`return <ReportView .../>` 直接渲染，**无导航壳**；错误分支是裸 `<main>`。IA spec §3.3 要求「App 顶栏 + `#session` 面包屑」。

- [ ] **Step 1: 改 `report/page.tsx`**

顶部 import 加：
```tsx
import AppNavbar from "@/components/AppNavbar";
```

把成功分支的 `return <ReportView report={status.result} sessionId={sessionId} />;` 改为带顶栏 + 面包屑的壳：
```tsx
  return (
    <div className="min-h-dvh flex flex-col">
      <AppNavbar />
      <nav
        aria-label="面包屑"
        className="max-w-[var(--spacing-container-max)] w-full mx-auto px-md py-sm text-label-md text-on-surface-variant"
      >
        <span>分析</span>
        <span className="mx-xs">/</span>
        <span className="text-on-surface">Session #{sessionId}</span>
      </nav>
      <ReportView report={status.result} sessionId={sessionId} />
    </div>
  );
```

把 `ReportError` 的裸 `<main>` 也包一层（加顶栏，保持错误可见 + 可导航）：
```tsx
function ReportError({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="min-h-dvh flex flex-col">
      <AppNavbar />
      <main className="flex-1 flex items-center justify-center px-md">
        <div className="bg-surface-container-low border border-outline rounded-lg p-lg max-w-[640px] w-full">
          <h1 className="text-headline-sm text-on-surface mb-sm">{title}</h1>
          <p className="text-body-md text-on-surface-variant break-words">
            {detail}
          </p>
        </div>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: type-check + lint + build**

Run: `npx tsc --noEmit && npx next lint && npx next build`
Expected: 成功。

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/app/sessions/[id]/report/page.tsx
git commit -m "feat(webapp): report page adds AppNavbar + session breadcrumb"
```

---

### Task 4: coach 页加 AppNavbar

**Files:**
- Modify: `webapp/frontend/app/sessions/[id]/coach/page.tsx`

**Interfaces:**
- Consumes: `AppNavbar` from Task 1

> 现状同 report：coach page 无导航壳，`<CoachView>` 直接渲染，错误是裸 `<main>`。IA spec §3.3 要求「App 顶栏（教练 active）」。注意：教练 active 对应的是独立 `/coach`（coach_dialogue），当前页是 `/sessions/[id]/coach`，属于某 session 的对话，顶栏「教练」项不会 active（路径不匹配 `/coach`）——这是预期行为（本 session 对话 ≠ 独立教练入口），不做特殊处理。

- [ ] **Step 1: 改 `coach/page.tsx`**

顶部 import 加：
```tsx
import AppNavbar from "@/components/AppNavbar";
```

成功分支改为：
```tsx
  return (
    <div className="min-h-dvh flex flex-col">
      <AppNavbar />
      <CoachView sessionId={sessionId} archetypeLabel={archetypeLabel} />
    </div>
  );
```

`CoachError` 同 Task 3 的 `ReportError` 一样包顶栏（把裸 `<main>` 替换为 `<div className="min-h-dvh flex flex-col"><AppNavbar /><main className="flex-1 flex items-center justify-center px-md">...</main></div>`）。

- [ ] **Step 2: type-check + lint + build**

Run: `npx tsc --noEmit && npx next lint && npx next build`
Expected: 成功。

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/app/sessions/[id]/coach/page.tsx
git commit -m "feat(webapp): coach page adds AppNavbar shell"
```

---

### Task 5: processing 页接入 AppNavbar

**Files:**
- Modify: `webapp/frontend/app/sessions/[id]/page.tsx`（删自定义 `TopAppBar` 函数 + `Shell` 里的引用）

**Interfaces:**
- Consumes: `AppNavbar` from Task 1

> 现状：processing 页有自定义 `TopAppBar` 函数（第 247-260 行），与统一顶栏不一致。**processing 不降级**（IA spec §3.2 已修正，对齐 PRD §6.1「本地 CV ~160s，可后台/可切走」）——用完整 App 顶栏替换，用户分析时可切走去 history/settings 逛，完成通知走独立 plan（全局 toast + 顶栏角标，PRD §6.3）。页内"分析中…"状态文字保留在 `Header` 组件主体，不进顶栏。

- [ ] **Step 1: 改 `sessions/[id]/page.tsx`**

顶部 import 加：
```tsx
import AppNavbar from "@/components/AppNavbar";
```

把 `Shell` 里的 `<TopAppBar />`（第 238 行）替换为 `<AppNavbar />`。

删除整个 `TopAppBar` 函数定义（第 247-260 行）——它被 AppNavbar 取代，不再有调用方。

- [ ] **Step 2: type-check + lint + build**

Run: `npx tsc --noEmit && npx next lint && npx next build`
Expected: 成功（确认删 TopAppBar 后无悬空引用）。

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/app/sessions/[id]/page.tsx
git commit -m "refactor(webapp): processing page uses AppNavbar processing variant"
```

---

### Task 6: history / settings stub 占位页

**Files:**
- Create: `webapp/frontend/app/history/page.tsx`
- Create: `webapp/frontend/app/settings/page.tsx`

**Interfaces:**
- Consumes: `AppNavbar` from Task 1

> AppNavbar 的「历史」「设置」链接指向 `/history`、`/settings`，若不存在会命中 `not-found.tsx`。建最简占位页，避免顶栏链接 404，并为后续 plan（history 完整实现 / settings aim profile）占位。

- [ ] **Step 1: 写 `app/history/page.tsx`**

```tsx
import AppNavbar from "@/components/AppNavbar";

export default function HistoryPage() {
  return (
    <div className="min-h-dvh flex flex-col">
      <AppNavbar />
      <main className="flex-1 w-full max-w-[var(--spacing-container-max)] mx-auto px-md py-xl">
        <h1 className="font-display text-display-md text-on-surface mb-sm">
          历史
        </h1>
        <p className="text-body-md text-on-surface-variant">
          趋势概览与 session 列表即将上线。
        </p>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: 写 `app/settings/page.tsx`**

```tsx
import AppNavbar from "@/components/AppNavbar";

export default function SettingsPage() {
  return (
    <div className="min-h-dvh flex flex-col">
      <AppNavbar />
      <main className="flex-1 w-full max-w-[var(--spacing-container-max)] mx-auto px-md py-xl">
        <h1 className="font-display text-display-md text-on-surface mb-sm">
          设置
        </h1>
        <p className="text-body-md text-on-surface-variant">
          Aim profile（DPI / sens / cm/360 / FOV）与订阅管理即将上线。
        </p>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: type-check + lint + build**

Run: `npx tsc --noEmit && npx next lint && npx next build`
Expected: 成功。

- [ ] **Step 4: Commit**

```bash
git add webapp/frontend/app/history/page.tsx webapp/frontend/app/settings/page.tsx
git commit -m "feat(webapp): stub history + settings pages (placeholder for later plans)"
```

---

## Self-Review

**1. Spec coverage**（IA spec §3.3 各页导航映射）：
- upload「去 DOCS/GITHUB，进 App 顶栏」→ Task 2 ✅
- processing「App 顶栏，可切走 + 完成通知（独立 plan）」→ Task 5（完整 AppNavbar，不再降级）✅
- coach_report「App 顶栏 + #session 面包屑」→ Task 3 ✅
- coach_dialogue「App 顶栏（教练 active），去 DOCS/GITHUB + 开始分析按钮」→ Task 4（顶栏✅；当前 CoachView 内的 DOCS/GITHUB/按钮若有，在 CoachView 内部处理，本 plan 不深入——已在 Task 4 注明）
- history「App 顶栏（历史 active），替换旧词汇 + 趋势卡」→ Task 6 stub（完整实现留后续 plan）
- settings「新增 aim profile + 订阅状态」→ Task 6 stub（完整实现留后续 plan）
- login / landing → 明确不在本 plan（scope 节已声明）

**2. 占位符扫描**：无 TBD/TODO；所有代码块完整；改造指令给了精确行号与替换内容。

**3. 类型一致性**：`AppNavbar({ variant?: "full" | "processing" })` 在 Task 1 定义，Task 2-6 调用签名一致（Task 5 用 `variant="processing"`，其余默认 `full`）。`isActive(pathname, href)` 内部使用，未在跨 task 暴露。

**已知留白（非占位符，是实现期决策）**：
- coach_dialogue 的「教练」顶栏 active：当前 `/sessions/[id]/coach` 不匹配 `/coach`，故不 active——预期行为，Task 4 已注明。
- 订阅状态「Early Access」是 v1 占位字符串，计费 plan 接通后替换。

---

## 后续 plan 路线图（本 plan 之后）

1. **history 页完整实现**：趋势卡（聚合 `build_progress_report`）+ session 列表 + 默认页逻辑（登录后默认进 history）
2. **settings 页完整实现**：aim profile 表单（DPI/sens/cm360/FOV）+ 订阅状态
3. **login 页**：视觉方向 C（shader aura + 玻璃卡片，IA spec §4.3）+ 密码+OTP 流程；**依赖 auth 后端**（OTP 发送/密码哈希/session），是跨前后端独立工程
4. **landing web 官网**：独立静态站（Cloudflare Pages），CTA「下载 Aiming Cookie」
5. **完成通知机制**（PRD §6.3，processing 可切走的闭环，**优先级紧跟本 plan**）：全局 running-session 状态 provider（layout 层轮询用户的 running sessions）+ 顶栏角标（AppNavbar 有 running 时显指示，完成时变可点击跳 report）+ 全局 toast。这是"processing 可切走"能成立的配套，没它用户切走就丢失完成感知。
