import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { createStreamingParser } from "@openuidev/react-lang";

import { coachOpenUILibrary, OPENUI_WHITELIST } from "../lib/openui/library";
import {
  OPENUI_SKILL_REFERENCE_BEGIN,
  OPENUI_SKILL_REFERENCE_END,
  OPENUI_UNSUPPORTED_PROMPT_TOKENS,
  renderOpenUISkillReference,
  restrictOpenUIPromptSignature,
} from "../lib/openui/skill-reference";

const skillPath = new URL(
  "../../coach-runtime/prompts/skills/data-visuals/SKILL.md",
  import.meta.url,
);

async function readSkill(): Promise<string> {
  return readFile(skillPath, "utf8");
}

function generatedBlock(skill: string): string {
  const start = skill.indexOf(OPENUI_SKILL_REFERENCE_BEGIN);
  const end = skill.indexOf(OPENUI_SKILL_REFERENCE_END);
  assert.ok(start >= 0, "data-visuals/SKILL.md 缺少生成块开始标记");
  assert.ok(end > start, "data-visuals/SKILL.md 缺少生成块结束标记");
  return skill.slice(start, end + OPENUI_SKILL_REFERENCE_END.length);
}

test("data-visuals SKILL.md 的组件参考块与受限 library 生成结果逐字一致", async () => {
  const skill = await readSkill();
  assert.equal(generatedBlock(skill), renderOpenUISkillReference(coachOpenUILibrary));
});

test("参考块覆盖白名单全部组件，签名直接来自 library schema", async () => {
  const skill = await readSkill();
  const block = generatedBlock(skill);
  const spec = coachOpenUILibrary.toSpec();

  assert.equal(
    OPENUI_WHITELIST.length,
    Object.keys(spec.components).length,
    "白名单与 library schema 的组件数量必须一致",
  );
  for (const name of OPENUI_WHITELIST) {
    const component = spec.components[name];
    assert.ok(component, `library schema 缺少白名单组件 ${name}`);
    const signature = restrictOpenUIPromptSignature(component.signature);
    assert.ok(
      block.includes(`\`${signature}\``),
      `${name} 的生成签名没有出现在 SKILL.md：${signature}`,
    );
  }
});

test("模型指南不包含未开放的通用动作或工具", async () => {
  const skill = await readSkill();
  for (const token of OPENUI_UNSUPPORTED_PROMPT_TOKENS) {
    assert.ok(
      !skill.includes(token),
      `data-visuals/SKILL.md 不应出现未开放的动作/工具标记：${token}`,
    );
  }
});

test("参考块示例是带 root 的完整程序，并按运行时 schema 解析通过", async () => {
  const skill = await readSkill();
  const match = generatedBlock(skill).match(/```openui-lang\n([\s\S]*?)\n```/);
  assert.ok(match, "参考块必须包含一个 openui-lang 示例");
  const source = match[1]!;
  assert.equal(source.split("\n")[0], `root = ${coachOpenUILibrary.root}([card, actions])`);

  const parsed = createStreamingParser(
    coachOpenUILibrary.toJSONSchema(),
    coachOpenUILibrary.root,
  ).set(source, true);
  assert.ok(parsed?.root, "示例必须解析出 root");
  assert.deepEqual(parsed?.meta.errors ?? [], [], "示例不应产生解析错误");
});
