#!/usr/bin/env node
/**
 * 从受限 OpenUI library 生成 data-visuals skill 的组件参考块。
 *
 * 用法（在 webapp/frontend 下）：
 *   node --import ../../third_party/pi/node_modules/tsx/dist/loader.mjs scripts/sync-openui-skill-reference.mjs
 *   node --import ../../third_party/pi/node_modules/tsx/dist/loader.mjs scripts/sync-openui-skill-reference.mjs --check
 *
 * 生成块写回 webapp/coach-runtime/prompts/skills/data-visuals/SKILL.md：
 * 组件签名直接来自 library.tsx 的 `coachOpenUILibrary.toSpec()`，与运行时
 * Renderer 同源；--check 只校验入库文件是否漂移（退出码 1）。
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(frontendDir, "..", "..");
const skillPath = join(
  repoRoot,
  "webapp",
  "coach-runtime",
  "prompts",
  "skills",
  "data-visuals",
  "SKILL.md",
);

const { coachOpenUILibrary } = await import("../lib/openui/library.tsx");
const { renderOpenUISkillReference, replaceOpenUISkillReference } = await import(
  "../lib/openui/skill-reference.ts"
);

const current = readFileSync(skillPath, "utf8");
const generated = renderOpenUISkillReference(coachOpenUILibrary);
const next = replaceOpenUISkillReference(current, generated);

if (process.argv.includes("--check")) {
  if (current !== next) {
    console.error(
      "[sync-openui-skill] data-visuals/SKILL.md 的组件参考与受限 library 不一致；请重新运行生成脚本。",
    );
    process.exit(1);
  }
  console.log("[sync-openui-skill] 组件参考已与受限 library 同步。");
} else {
  writeFileSync(skillPath, next, "utf8");
  console.log(`[sync-openui-skill] 已写入 ${skillPath}`);
}
