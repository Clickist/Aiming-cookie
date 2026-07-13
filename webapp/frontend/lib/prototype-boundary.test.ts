import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const protectedAdapters = [
  "lib/api.ts",
  "lib/types.ts",
  "lib/contracts.ts",
  "lib/csv.ts",
  "lib/desktop.ts",
];

test("protected frontend adapters do not import product UI prototypes", async () => {
  for (const relativePath of protectedAdapters) {
    const source = await readFile(path.join(frontendRoot, relativePath), "utf8");
    const imports = source
      .split("\n")
      .filter((line) => /^\s*import\b/.test(line))
      .join("\n");
    assert.doesNotMatch(imports, /(?:app|components)\//, relativePath);
    assert.doesNotMatch(
      source,
      /AppChrome|NewAnalysisClient|HistoryClient|BenchmarkPanel/,
      relativePath,
    );
  }
});
