import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const RUNTIME_ROOT = join(fileURLToPath(new URL("..", import.meta.url)));

function collectTsFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, name.name);
    if (name.isDirectory()) {
      collectTsFiles(path, acc);
    } else if (name.isFile() && name.name.endsWith(".ts")) {
      acc.push(path);
    }
  }
  return acc;
}

const PRODUCT_PATHS = [
  ...collectTsFiles(join(RUNTIME_ROOT, "src")),
  join(RUNTIME_ROOT, "start-sidecar.ts"),
  join(RUNTIME_ROOT, "run-turn.ts"),
];

test("coach-runtime product sources import coding-agent only through pi-source", () => {
  const violations: string[] = [];
  for (const file of PRODUCT_PATHS) {
    if (file.endsWith(join("src", "pi-source.ts"))) continue;
    const text = readFileSync(file, "utf8");
    if (/(?:from\s*|import\s*\()\s*["'][^"']*coding-agent|packages[\\/"',\s]+coding-agent/.test(text)) {
      violations.push(file);
    }
  }
  assert.deepEqual(
    violations,
    [],
    `coding-agent must only be imported by src/pi-source.ts, got: ${violations.join(", ")}`,
  );
});

test("pi-source embeds pinned packages/ai, packages/agent and coding-agent tools modules", () => {
  const piSource = readFileSync(join(RUNTIME_ROOT, "src", "pi-source.ts"), "utf8");
  assert.match(piSource, /third_party\/pi\/packages\/ai\/src\/index\.ts/);
  assert.match(piSource, /third_party\/pi\/packages\/agent\/src\/index\.ts/);
  assert.match(piSource, /third_party\/pi\/packages\/ai\/src\/providers\/all\.ts/);
  assert.match(piSource, /third_party\/pi\/packages\/ai\/src\/api\/openai-completions\.ts/);
  // 2026-09-06 拍板：工具层改用 coding-agent 原版实现（fs-tools.ts 只做 Coach 护栏包装）。
  assert.match(piSource, /third_party\/pi\/packages\/coding-agent\/src\/core\/tools\/index\.ts/);
  // tools 目录之外的 coding-agent 面（TUI/modes/server 等）仍然禁止。
  const codingImports = piSource.match(/coding-agent[\/\\][^\s"']+/g) ?? [];
  for (const importPath of codingImports) {
    assert.match(
      importPath,
      /^coding-agent[\/\\]src[\/\\]core[\/\\]tools/,
      `only core/tools may be imported from coding-agent, got: ${importPath}`,
    );
  }
});
