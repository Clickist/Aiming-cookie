import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const frontendRoot = resolve(import.meta.dirname, "..");

function cssFiles(): string[] {
  const dir = join(frontendRoot, "components");
  const files = readdirSync(dir, { recursive: true })
    .map((name) => String(name))
    .filter((name) => name.endsWith(".css"))
    .map((name) => join(dir, name));
  return [...files, join(frontendRoot, "ui", "theme.css")];
}

// design-system.md：可执行字号 --text-micro..display、圆角 --radius-sm..lg＋xl
// （批3 新增第四档 --radius-xl:16px，user 气泡 pill 向，digests §8）、
// 间距 --space-1..6；旧 board 别名层已废除。
const LEGACY_ALIASE = [
  "--fg", "--fg2", "--s-low", "--s-cont", "--s-high", "--s-highest",
  "--p-cont", "--on-p-cont", "--t-cont", "--on-t-cont", "--e-cont",
  "--on-e-cont", "--outline-v", "--ok", "--scrim", "--bg",
];

test("all stylesheet font sizes use the text scale", () => {
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const match of css.matchAll(/(?:font|font-size)\s*:\s*([^;{}]+)/g)) {
      const value = match[1];
      if (/inherit|var\(--text-(micro|caption|ui|body|title|display)\)/.test(value) && !/\dpx/.test(value)) continue;
      assert.fail(
        `${file}: 字号必须使用 --text-* token，禁止裸 px（"${value.trim()}"）`,
      );
    }
  }
});

test("all stylesheet radii use the radius scale or micro shapes", () => {
  const allowed = new Set(["0", "1px", "2px", "3px", "50%", "999px", "inherit"]);
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const match of css.matchAll(/border-radius\s*:\s*([^;{}]+)/g)) {
      for (const part of match[1].trim().split(/\s+/)) {
        const ok =
          allowed.has(part) || /^var\(--radius-(sm|md|lg|xl)\)$/.test(part);
        assert.ok(
          ok,
          `${file}: 圆角必须使用 --radius-* token（或 1-3px 微形状/50%/999px），禁止 "${part}"`,
        );
      }
    }
  }
});

test("the retired board alias layer stays dead", () => {
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const alias of LEGACY_ALIASE) {
      assert.ok(
        !css.includes(`var(${alias})`) && !new RegExp(`^\\s*${alias.slice(2)}:`, "m").test(css),
        `${file}: 旧别名 ${alias} 已废除，直接使用 app token`,
      );
    }
  }
});

test("transitions use motion tokens, not raw durations or easings", () => {
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const match of css.matchAll(/transition\s*:\s*([^;{}]+?)(?=;)/g)) {
      const value = match[1];
      if (/\bnone\b/.test(value) && !/var\(/.test(value)) continue;
      for (const part of value.split(",")) {
        const clause = part.trim();
        if (!clause) continue;
        // var() 的 fallback 值不算裸值；0ms/0.001ms 是可访问性即时过渡。
        const bare = clause.replace(/var\([^)]*\)/g, "");
        assert.ok(
          !/(^|[\s(])\d+(\.\d+)?m?s\b/.test(bare) || /^(0|0\.001)ms/.test(bare.trim()),
          `${file}: transition 时长必须用 var(--duration-*)，禁止裸 ms（"${clause}"）`,
        );
        assert.ok(
          !/(^|\s)(ease|ease-in|ease-out|ease-in-out|linear|cubic-bezier)\(/.test(clause) && !/(^|\s)(ease|ease-in|ease-out|ease-in-out|linear)\b/.test(clause) || /var\(--ease/.test(clause),
          `${file}: transition 缓动必须用 var(--ease-*)（"${clause}"）`,
        );
      }
    }
  }
});

test("font weights stay on the four-step scale", () => {
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const match of css.matchAll(/font(-weight)?\s*:\s*([^;{}]+)/g)) {
      const weight = match[2].trim().split(/\s+/)[0];
      assert.ok(
        weight === "inherit" || ["400", "500", "600", "700"].includes(weight),
        `${file}: 字重只允许 400/500/600/700（"${weight}"）`,
      );
    }
  }
});

test("elevation and focus shadows use the shared tokens", () => {
  for (const file of cssFiles()) {
    const css = readFileSync(file, "utf8");
    for (const match of css.matchAll(/box-shadow\s*:\s*([^;{}]+)/g)) {
      const value = match[1].trim();
      const ok =
        value === "none" ||
        value.startsWith("var(--") ||
        value.startsWith("inset") ||
        /^0 0 0 1px var\(--outline(-variant)?\)$/.test(value);
      assert.ok(
        ok,
        `${file}: 阴影必须用 var(--shadow-overlay)/var(--ring)/inset 形状或 1px hairline ring（"${value}"）`,
      );
    }
  }
});
