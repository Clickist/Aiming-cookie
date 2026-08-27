import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const frontendRoot = resolve(import.meta.dirname, "..");

function read(relativePath: string): string {
  return readFileSync(join(frontendRoot, relativePath), "utf8");
}

function componentCssFiles(): string[] {
  const dir = join(frontendRoot, "components");
  return readdirSync(dir, { recursive: true })
    .map((name) => String(name))
    .filter((name) => name.endsWith(".css"))
    .map((name) => join(dir, name));
}

// frontend-parity 批 7（受控富渲染，digests §10）：中文排印十条入验收。
// 聊天列 max ~42em 一条经点点决定不采纳（批 3 已定稿 720px，未决不改），
// 故本套不含该断言；720px 合同继续由 coach-panel-layout.test 锁定。

test("typography rule 1: chat body line-height stays within 1.75 ±0.10", () => {
  const css = read("components/task6/task6.css");
  const block = css.match(/\.task7-rich\s*\{[^}]*\}/)?.[0] ?? "";
  assert.ok(block.length > 0, ".task7-rich block must exist");
  const match = block.match(/line-height:\s*([\d.]+)/);
  assert.ok(match, "line-height declared");
  const value = Number(match?.[1]);
  assert.ok(value >= 1.65 && value <= 1.85, `line-height ${value} outside 1.75±0.10`);
});

test("typography rules 2/9: native text-autospace carries CJK-Latin gaps, no JS spacing", () => {
  // 原生特性声明必须存在（不支持的环境静默忽略＝渐进增强）
  const css = read("components/task6/task6.css");
  assert.match(css, /\.task7-rich\s*\{[^}]*text-autospace:\s*normal/s);
  // 红线：渲染链路禁做任何 JS 文本变换插空格（盘古之白手工方案已被取代）
  const richSource = read("lib/rich-text.ts");
  const renderer = read("components/task7/CoachMessageText.tsx");
  for (const [name, source] of [["rich-text", richSource], ["CoachMessageText", renderer]] as const) {
    assert.doesNotMatch(source, /\bnbsp\b|\u00a0|\u2009|盘古/, name);
    assert.doesNotMatch(
      source,
      /[\u4e00-\u9fff]["']\s*\+\s*["']\s+["']/,
      `${name}: CJK 边界禁止 JS 插空格`,
    );
  }
});

test("typography rule 3: curly quotes pass through untouched by the renderer", () => {
  // 渲染层与解析层都不改写任何引号字形；弯引号的来源是模型输出端与系统
  // 字体栈字形——展示端唯一职责是保证字符原样透传、不被归一化吃掉。
  const richSource = read("lib/rich-text.ts");
  const renderer = read("components/task7/CoachMessageText.tsx");
  for (const [name, source] of [["rich-text", richSource], ["CoachMessageText", renderer]] as const) {
    assert.doesNotMatch(
      source,
      /replace\([^)]*[“”‘’「」『』]/,
      `${name}: 禁止改写弯引号`,
    );
  }
});

test("typography rules 4/10: Source Han Sans SC fallback sits before YaHei, stack stays system-first", () => {
  const themeCss = read("ui/theme.css");
  for (const token of ["--font-ui", "--font-display"]) {
    const decl = themeCss.match(new RegExp(`${token}:[^;]+;`))?.[0] ?? "";
    assert.ok(decl.length > 0, `${token} declared`);
    const han = decl.indexOf("Source Han Sans SC");
    const yahei = decl.indexOf("Microsoft YaHei");
    assert.ok(han > -1 && yahei > han, `${token} needs Source Han Sans SC before YaHei`);
    assert.match(decl, /sans-serif;\s*$/, `${token} keeps generic system fallback`);
  }
  // 维持系统字体栈：不许引入 webfont 资源
  assert.doesNotMatch(themeCss, /@font-face|url\(/);
});

test("typography rule 5: numeric table columns carry tabular-nums", () => {
  const css = read("components/task6/task6.css");
  const rule = css.match(/\.task7-rich td\[data-num="true"\][^{]*\{[^}]*\}/)?.[0] ?? "";
  assert.match(rule, /font-variant-numeric:\s*tabular-nums/);
});

test("typography rule 6: CJK wrapping bans break-all everywhere, rich body uses overflow-wrap:anywhere", () => {
  for (const file of [...componentCssFiles(), join(frontendRoot, "ui", "theme.css")]) {
    assert.doesNotMatch(readFileSync(file, "utf8"), /word-break:\s*break-all/, file);
  }
  const css = read("components/task6/task6.css");
  assert.match(css, /\.task7-rich\s*\{[^}]*overflow-wrap:\s*anywhere/s);
});

test("typography rule 8: no fake italics in the rich rendering path", () => {
  // 受控子集没有强调斜体这一档；源文件里不得出现合成斜体声明。
  for (const relativePath of ["components/task7/CoachMessageText.tsx", "lib/rich-text.ts"]) {
    assert.doesNotMatch(read(relativePath), /font-style|<i>|italic|oblique/, relativePath);
  }
  const css = read("components/task6/task6.css");
  const richSection = css.slice(css.indexOf("受控富渲染"), css.indexOf("Time links in Coach messages"));
  assert.doesNotMatch(richSection, /font-style\s*:\s*(?:italic|oblique)/);
});

test("rich subset renders tables inside a horizontal scroll container under five columns", () => {
  const css = read("components/task6/task6.css");
  const scroll = css.match(/\.task7-rich-table-scroll\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(scroll, /overflow-x:\s*auto/);
  // ≤5 列钳制住在解析器常量里，样式层只负责滚动容器
  const parser = read("lib/rich-text.ts");
  assert.match(parser, /MAX_TABLE_COLUMNS = 5/);
});
