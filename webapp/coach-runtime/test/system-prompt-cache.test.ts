import assert from "node:assert/strict";
import test from "node:test";

const { loadDefaultCoachSystemPrompt, resolveSystemPromptWithEnvFacts } = await import("../src/load-system-prompt.ts");

test("default coach system prompt loads and stays process-cached (TS-5)", async () => {
  // TS-5 实测基线：修复前每调用一次都同步 readFileSync（26KB，~0.3ms/次），
  // 且发生在每回合 turn 热路径；修复后首读入缓存。缓存对外不可直接观测
  // （字符串是值类型），这里锁定加载正确性与组成结构作为重构回归闸。
  const first = loadDefaultCoachSystemPrompt();
  assert.ok(first.length > 1000, "bundled coach-system.md must load");
  assert.equal(loadDefaultCoachSystemPrompt(), first);
  // 显式传入的 request prompt 原样胜出（既合同）。
  assert.equal(await resolveSystemPromptWithEnvFacts("自定义提示词"), "自定义提示词");
  // 默认组合：提示词本体 + 本机环境实测附录。
  const composed = await resolveSystemPromptWithEnvFacts();
  assert.ok(composed.startsWith(first));
  assert.match(composed, /## 你的运行环境/);
});
