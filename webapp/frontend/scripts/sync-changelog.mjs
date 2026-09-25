#!/usr/bin/env node
/**
 * 把仓库级单一事实源 design/opendesign-landing/changelog.json 同步为
 * webapp/frontend/lib/changelog.generated.json。
 *
 * 背景：应用内更新公告卡直接 import 仓库级 changelog.json 时，Turbopack
 * 不解析 frontend 项目根外的模块（tsc 能过、next build 模块解析失败），
 * 故按 lib/api-types.generated.ts 同款「生成物入库」模式落地一份拷贝；
 * predev / prebuild 钩子自动刷新，也可 `npm run sync-changelog` 手动同步。
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.resolve(frontendRoot, "../../design/opendesign-landing/changelog.json");
const target = path.join(frontendRoot, "lib", "changelog.generated.json");

const parsed = JSON.parse(readFileSync(source, "utf8"));
if (!Array.isArray(parsed.versions)) {
  throw new Error(`[sync-changelog] changelog.json 缺少 versions 数组：${source}`);
}
for (const entry of parsed.versions) {
  if (typeof entry.version !== "string" || !Array.isArray(entry.items)) {
    throw new Error(`[sync-changelog] changelog.json 版本块结构非法（缺 version 或 items）：${source}`);
  }
}
writeFileSync(target, `${JSON.stringify(parsed, null, 2)}\n`);
console.log(`[sync-changelog] 已同步 ${parsed.versions.length} 个版本块 → lib/changelog.generated.json`);
