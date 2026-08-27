import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";
import { test } from "node:test";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("api-types.generated.ts stays in sync with backend OpenAPI schema", () => {
  const result = spawnSync(
    process.execPath,
    [join(frontendDir, "scripts", "sync-api-types.mjs"), "--check"],
    { encoding: "utf8", timeout: 120_000 },
  );
  assert.equal(
    result.status,
    0,
    `lib/api-types.generated.ts drifted from the backend OpenAPI schema (exit ${result.status}). Run \`npm run sync-types\` and commit the regenerated file.\n${result.stderr}`,
  );
});
