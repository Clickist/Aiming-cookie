import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// 打包环境分歧铁律 #1（2026-09-13 实战，见 docs/DEVELOPMENT.md 打包三条铁律）：
// sidecar 编译成单 exe 后，import.meta 相对路径落在 bun 虚拟文件系统里，
// native 命令读磁盘数据文件（knowledge/、artifacts/）必须经
// AIMING_COOKIE_RESOURCE_ROOT 解析，仓库相对路径只作开发兜底。
// 此钉桩保证今后任何按 REPO_ROOT 惯例读仓库数据的文件都同时接上 env 正门，
// 否则打包版读不到数据、工具静默 unavailable（Node 单测测不出，只有真机炸）。
test("native data file reads declare the packaged resource-root path", () => {
  const srcDir = join(import.meta.dirname, "..", "src");
  const offenders = readdirSync(srcDir)
    .filter((name) => name.endsWith(".ts"))
    .map((name) => ({ name, text: readFileSync(join(srcDir, name), "utf8") }))
    .filter(({ text }) => /REPO_ROOT/.test(text))
    .filter(({ text }) => !/AIMING_COOKIE_RESOURCE_ROOT/.test(text))
    .map(({ name }) => name);
  assert.deepEqual(offenders, []);
});
