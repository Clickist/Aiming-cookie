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
    assert.equal(loadKnowledgeRegistry().registry_version, "2026-10-04.v14");
    assert.ok(activeScenarioProfileRefs().has("scenario:static.1wall_6targets_small@1"));

    // Packaged mapping resources: the Python coach engine resolves
    // knowledge/mapping/*.v1.json from the resource root, so the release
    // layout must ship both files (build-windows-runtime.ps1 copies them
    // explicitly alongside the wholesale knowledge tree).
    const mappingRoot = join(repoRoot, "knowledge", "mapping");
    const officialMapping = JSON.parse(
      readFileSync(join(mappingRoot, "official.v1.json"), "utf8"),
    ) as { schema_version?: unknown };
    const vocabulary = JSON.parse(
      readFileSync(join(mappingRoot, "vocabulary.v1.json"), "utf8"),
    ) as { schema_version?: unknown };
    assert.equal(officialMapping.schema_version, "coach_mapping.v1");
    assert.equal(vocabulary.schema_version, "coach_mapping_vocabulary.v1");
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

test("default coach prompt carries the honest empty-diagnosis rule", () => {
  // Prompt 硬规矩合同：问题列表为空时必须明说没有标出问题，严禁把空
  // 字段编成有内容或发明评级词。生产案例：空 issues 被讲成「分析标出
  // 典型问题，属于基线档」。同时「没标」≠「没问题」——Coach 必须基于
  // 数据自己诊断（2026-10-07 二次生产案例：Coach 把「自动诊断空」讲成
  // 「这局没典型问题」，放弃了自己的诊断职责）。
  const previous = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  try {
    delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    const prompt = loadDefaultCoachSystemPrompt();
    assert.match(prompt, /问题列表（diagnosis\.issues）为空时，说明「自动诊断没有标出典型问题」即可/);
    assert.match(prompt, /严禁说成「标出了问题」/);
    assert.match(prompt, /严禁发明评级词/);
    assert.match(prompt, /绝不等于「这局没有问题」/);
    assert.match(prompt, /自己诊断/);
    // 内部工程标注不外讲（2026-10-07）：limitations/投影估算/校准缺失这类
    // 管道状态不对用户转述，不可用指标直接跳过。生产案例：Coach 把
    // 「遥测投影估算、没有图像校准」念给用户听。
    assert.match(prompt, /内部工程标注不外讲/);
    assert.match(prompt, /一律不对用户转述或解释/);
    assert.match(prompt, /某项指标不可用时直接跳过不提/);
  } finally {
    if (previous === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previous;
  }
});

test("default coach prompt carries the real-names-only scenario recommendation rule", () => {
  // Prompt 硬规矩合同：具名场景推荐必须来自检索结果原文；检索结果里
  // 没有的具名场景（尤其带人名前缀、Easy/Very Easy 档位后缀）严禁说
  // 出口。生产案例：凭记忆说出知识库里不存在的「WHJ SmoothStrafeSphere
  // 的 Very Easy 和 Easy 两档」。
  const previous = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  try {
    delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    const prompt = loadDefaultCoachSystemPrompt();
    assert.match(prompt, /场景名与档位必须来自实际检索结果原文/);
    assert.match(prompt, /严禁说出口，改用泛化描述/);
    assert.match(prompt, /不得凭记忆编造场景名或档位/);
  } finally {
    if (previous === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previous;
  }
});

test("default coach prompt carries the internal-stats expression rule", () => {
  // Prompt 硬规矩合同：limitations/coverage/置信度/数据来源说明都是内部
  // 工程标注，不对用户转述或解释，不可用指标直接跳过。生产案例：coverage
  // 0.539 被讲成「证据覆盖率只有五成多」；limitations 被念成「遥测投影
  // 估算、没有图像校准」。
  const previous = process.env.AIMING_COOKIE_RESOURCE_ROOT;
  try {
    delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    const prompt = loadDefaultCoachSystemPrompt();
    assert.match(prompt, /内部工程标注不外讲/);
    assert.match(prompt, /一律不对用户转述或解释/);
    assert.match(prompt, /某项指标不可用时直接跳过不提/);
    assert.match(prompt, /不得展开工程细节/);
  } finally {
    if (previous === undefined) delete process.env.AIMING_COOKIE_RESOURCE_ROOT;
    else process.env.AIMING_COOKIE_RESOURCE_ROOT = previous;
  }
});
