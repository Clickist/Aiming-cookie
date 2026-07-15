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

test("coach-runtime product sources must not import coding-agent", () => {
  const violations: string[] = [];
  for (const file of PRODUCT_PATHS) {
    const text = readFileSync(file, "utf8");
    if (/(?:from\s*|import\s*\()\s*["'][^"']*coding-agent|packages[\\/"',\s]+coding-agent/.test(text)) {
      violations.push(file);
    }
  }
  assert.deepEqual(
    violations,
    [],
    `coding-agent must not be imported by product paths: ${violations.join(", ")}`,
  );
});

test("pi-source only resolves pinned packages/ai and packages/agent source modules", () => {
  const piSource = readFileSync(join(RUNTIME_ROOT, "src", "pi-source.ts"), "utf8");
  assert.match(piSource, /packages", "ai", "src", "index\.ts/);
  assert.match(piSource, /packages", "agent", "src", "index\.ts/);
  assert.match(piSource, /packages", "ai", "src", "providers", "all\.ts/);
  assert.match(piSource, /packages", "ai", "src", "api", "openai-completions\.ts/);
  assert.ok(!piSource.includes("coding-agent"));
  assert.ok(!piSource.includes("packages/coding-agent"));
  assert.ok(!piSource.includes('packages", "coding-agent"'));
});
