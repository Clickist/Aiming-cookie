import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-skills-loading-"));
process.env.DATA_ROOT = dataRoot;

import { loadPiAgent, loadPiNodeEnv } from "../src/pi-source.ts";

const skillsDir = resolve(dirname(fileURLToPath(import.meta.url)), "../prompts/skills");

test("Coach skills load from the bundled skills directory on Windows paths", async () => {
  // The Pi loader derives FileInfo.name from the path with split("/"), which
  // breaks on Windows backslash paths — no SKILL.md ever matched. This test
  // pins the fix by loading the real skills directory and requiring every
  // bundled skill to be present.
  const { loadSkills } = (await loadPiAgent()) as {
    loadSkills: (env: unknown, dirs: string) => Promise<{ skills: unknown[]; diagnostics: unknown[] }>;
  };
  const { NodeExecutionEnv } = (await loadPiNodeEnv()) as {
    NodeExecutionEnv: new (opts: { cwd: string }) => unknown;
  };
  const env = new NodeExecutionEnv({ cwd: dataRoot });

  const { skills, diagnostics } = await loadSkills(env, skillsDir);
  const names = skills.map((skill) => (skill as { name?: unknown }).name);

  for (const expected of ["teaching", "kovaak-data-reference", "peripheral-reference"]) {
    assert.ok(
      names.includes(expected),
      `skill "${expected}" should load; got ${JSON.stringify(names)}; diagnostics: ${JSON.stringify(diagnostics)}`,
    );
  }
});
