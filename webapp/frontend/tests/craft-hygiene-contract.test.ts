import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

import { DARK_TOKENS, LIGHT_TOKENS } from "../ui/tokens";

// frontend-parity 批 1（工艺卫生）＋批 2（token 收敛）的行为锁定。
// 规格出处：docs/frontend-parity-research-digests.md §8/§9/§11。

const frontendRoot = resolve(import.meta.dirname, "..");
const themeCss = readFileSync(join(frontendRoot, "ui", "theme.css"), "utf8");
const primitives = readFileSync(join(frontendRoot, "ui", "primitives.tsx"), "utf8");

function rootBlock(): string {
  return themeCss.slice(themeCss.indexOf(":root {"), themeCss.indexOf(":root[data-theme"));
}

function componentCssFiles(): string[] {
  const dir = join(frontendRoot, "components");
  return readdirSync(dir, { recursive: true })
    .map((name) => String(name))
    .filter((name) => name.endsWith(".css"))
    .map((name) => join(dir, name));
}

test("left drawer anchors to inline-start instead of the copied inline-end bug", () => {
  const left = themeCss.match(/\.ac-drawer\[data-side="left"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(left, /inset-inline-start:\s*0/);
  assert.doesNotMatch(left, /inset-inline-end/);
  // 右抽屉保持原锚定，防止修正时把两侧搞反。
  const right = themeCss.match(/\.ac-drawer\[data-side="right"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(right, /inset-inline-end:\s*0/);
  assert.doesNotMatch(right, /inset-inline-start/);
});

test("drawer and dialog backdrops have exactly one shared definition", () => {
  // 收敛后只允许两条组选择器（共用底 + 共用关闭态），且每条都把 drawer/dialog 列在一起；
  // 不允许出现 dialog 独享的定义块。
  const groups = themeCss.match(/[^{}]+\{/g) ?? [];
  const dialogOnly = groups.filter((selector) => {
    const names: string[] = selector.match(/\.ac-(?:drawer|dialog)-backdrop/g) ?? [];
    return names.includes(".ac-dialog-backdrop") && !names.includes(".ac-drawer-backdrop");
  });
  assert.equal(dialogOnly.length, 0, dialogOnly.join(" | "));
  const jointDeclarations = themeCss.match(/\.ac-dialog-backdrop(?:\[data-state="closed"\])?\s*\{/g) ?? [];
  assert.equal(jointDeclarations.length, 2);
  assert.match(themeCss, /\.ac-drawer-backdrop,\s*\.ac-dialog-backdrop\s*\{[^}]*background:\s*var\(--overlay-scrim\)/s);
});

test("toast tone prop is honored by the style layer", () => {
  for (const [tone, role] of [
    ["info", "--tertiary"],
    ["success", "--event-kill"],
    ["warning", "--event-peak"],
    ["error", "--error"],
  ] as const) {
    assert.match(
      themeCss,
      new RegExp(`\\.ac-toast\\[data-tone="${tone}"\\]\\s*\\{[^}]*border-inline-start:\\s*2px solid var\\(${role}\\)`),
      tone,
    );
  }
});

test("dialog close reuses the IconButton primitive instead of a bare button", () => {
  const dialogBody = primitives.slice(primitives.indexOf("export function Dialog"));
  assert.match(dialogBody, /<IconButton label="Close" onClick=\{onClose\}/);
  assert.doesNotMatch(primitives, /ac-dialog__close/);
});

test("icon buttons keep a >=36px hit area and hover via the state-layer tokens", () => {
  // 只锚定独立的尺寸块（而非与 .ac-button 共用的布局块）。
  const base = themeCss.match(/\.ac-icon-button\s*\{[^}]*width:\s*var\(--control-height\)[^}]*\}/)?.[0] ?? "";
  assert.match(base, /height:\s*var\(--control-height\)/);
  // 工艺红线：16px 图标命中区 ≥36px；compact 不允许再压回 32px。
  const compact = themeCss.match(/\.ac-icon-button\[data-size="compact"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.ok(compact.length > 0);
  assert.doesNotMatch(compact, /--control-height-compact/);
  assert.match(compact, /width:\s*var\(--control-height\)/);
  // hover/pressed 走共享半透明叠层，禁止私定实底色名。
  assert.match(themeCss, /\.ac-icon-button:hover[^{]*\{[^}]*background:\s*var\(--state-hover\)/s);
  assert.match(themeCss, /\.ac-icon-button:active[^{]*\{[^}]*background:\s*var\(--state-pressed\)/s);
});

test("the six parity tokens live only in their token homes", () => {
  // 颜色角色：ui/tokens.ts 双主题表 + DESIGN-cursor.md 色板行（两处值一致）。
  assert.equal(LIGHT_TOKENS["ring-color"], "#c83d00");
  assert.equal(DARK_TOKENS["ring-color"], "#ff8a5c");
  const cursor = readFileSync(resolve(frontendRoot, "..", "..", "DESIGN-cursor.md"), "utf8");
  assert.match(cursor, /\| `ring-color` \| `#c83d00` \| `#ff8a5c` \|/);
  // 派生/规格型五枚：只在 theme.css :root 定义一次。
  const root = rootBlock();
  assert.match(root, /--overlay-scrim:\s*color-mix\(in srgb, var\(--inverse-surface\) 32%, transparent\)/);
  assert.match(root, /--divider-strong:\s*color-mix\(in srgb, var\(--outline-variant\) 50%, var\(--outline\)\)/);
  assert.match(root, /--state-hover:\s*color-mix\(in srgb, currentColor 8%, transparent\)/);
  assert.match(root, /--state-pressed:\s*color-mix\(in srgb, currentColor 12%, transparent\)/);
  assert.match(root, /--shadow-menu:\s*0 4px 12px color-mix\(in srgb, var\(--on-surface\) 12%, transparent\)/);
  // 组件样式层不得私定这六枚的值（只能 var() 消费）。
  for (const file of componentCssFiles()) {
    const css = readFileSync(file, "utf8");
    assert.doesNotMatch(
      css,
      /--(ring-color|overlay-scrim|divider-strong|state-hover|state-pressed|shadow-menu)\s*:/,
      file,
    );
  }
});

test("session rail's new button composes the shared primary Button primitive", () => {
  const railSource = readFileSync(join(frontendRoot, "components", "task7", "SessionRail.tsx"), "utf8");
  assert.match(railSource, /<Button className="task7-session-rail__new"[^>]*variant="primary"/);
  const railCss = readFileSync(join(frontendRoot, "components", "task7", "session-rail.css"), "utf8");
  const block = railCss.match(/\.task7-session-rail__new\s*\{[^}]*\}/)?.[0] ?? "";
  assert.ok(block.length > 0);
  // 手抄的 primary 填充与 hover 公式必须消失：本地只剩布局伸缩。
  assert.doesNotMatch(block, /background|border|font|--primary/);
  assert.doesNotMatch(railCss, /task7-session-rail__new:hover/);
});

test("sending while a run is active enqueues a visible chip instead of swallowing the keystroke", () => {
  const coachPanel = readFileSync(join(frontendRoot, "components", "task6", "CoachPanel.tsx"), "utf8");
  const sendStart = coachPanel.indexOf("const sendText = async");
  const guardEnd = coachPanel.indexOf("sendingRef.current = true");
  const guard = coachPanel.slice(sendStart, guardEnd);
  // 批 5 编排取代批 1 的 notify 止血：运行中发送进可见队列 chips
  // （Cline #12226 丢消息教训——每条可视可编辑可删）。
  // 0911：入队携带结构化分析引用（refs 随 chip 走，发送时一并挂载）。
  assert.match(guard, /enqueueQueuedItem\(content, refs\.length \? refs : undefined\)/);
  // 受理即清空输入＝移入而非复制；需要改写时走 chip 的回填编辑。
  assert.match(guard, /enqueueQueuedItem\(content, refs\.length \? refs : undefined\);\s*setDraft\(""\);/);
});
