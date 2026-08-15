import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";

import { loadDefaultCoachSystemPrompt } from "../src/load-system-prompt.ts";
import { piSourceRoot } from "../src/pi-source.ts";
import {
  activeScenarioProfileRefs,
  loadKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

const repoRoot = join(import.meta.dirname, "..", "..", "..");

test("release resource root overrides source prompt, Pi metadata, and knowledge paths", () => {
  const promptRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-release-resources-"));
  const previous = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  try {
    writeFileSync(join(promptRoot, "coach-system.md"), "packaged prompt\n");
    process.env.AIMING_COOKIE_RESOURCE_ROOT = promptRoot;

    assert.equal(loadDefaultCoachSystemPrompt(), "packaged prompt");

    process.env.AIMING_COOKIE_RESOURCE_ROOT = repoRoot;
    assert.equal(piSourceRoot(), join(repoRoot, "pi"));
    assert.equal(loadKnowledgeRegistry().registry_version, "2026-08-15.v7");
    assert.ok(activeScenarioProfileRefs().has("scenario:static.1wall_6targets_small@1"));
  } finally {
    if (previous === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previous;
    rmSync(promptRoot, { recursive: true, force: true });
  }
});

test("development path remains available when release root is absent", () => {
  const previousResourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  const previousPiRoot = process.env.PI_SOURCE_DIR;
  try {
    delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    process.env.PI_SOURCE_DIR = join(repoRoot, "third_party", "pi");
    assert.match(loadDefaultCoachSystemPrompt(), /Aiming Cookie/);
    assert.match(readFileSync(join(piSourceRoot(), "packages", "agent", "package.json"), "utf8"), /0\.80\.6/);
  } finally {
    if (previousResourceRoot === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previousResourceRoot;
    if (previousPiRoot === undefined) delete process.env.PI_SOURCE_DIR;
    else process.env.PI_SOURCE_DIR = previousPiRoot;
  }
});
