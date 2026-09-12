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
    assert.equal(loadKnowledgeRegistry().registry_version, "2026-09-12.v12");
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
    assert.match(readFileSync(join(piSourceRoot(), "packages", "agent", "package.json"), "utf8"), /0\.83\.0/);
  } finally {
    if (previousResourceRoot === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previousResourceRoot;
    if (previousPiRoot === undefined) delete process.env.PI_SOURCE_DIR;
    else process.env.PI_SOURCE_DIR = previousPiRoot;
  }
});

test("default coach prompt carries the sensitivity scenario-attribution rule", () => {
  // Prompt 硬规矩合同：灵敏度类建议只能引用当前讨论场景自己的数据；
  // 引用其它场景必须点名场景且不得当作当前场景的推荐值。防止把
  // smoothsphere 的灵敏度错套到 1wall 6targets small 这类跨场景混用。
  const previous = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  try {
    delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    const prompt = loadDefaultCoachSystemPrompt();
    assert.match(prompt, /灵敏度类建议[\s\S]*只能引用当前讨论场景自己的局内数据/);
    assert.match(prompt, /必须明说是哪个场景在哪一局的值/);
    assert.match(prompt, /不得把它直接当作当前场景的推荐值/);
  } finally {
    if (previous === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previous;
  }
});
