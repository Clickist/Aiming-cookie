import assert from "node:assert/strict";
import test from "node:test";

// 0911 点点纠偏回归：应用分发到任意用户机器，bash/python/node/jq 的有无
// 必须运行时探测并注入提示词，不得写死开发机能力。

const { describeCoachEnvFacts } = await import("../src/coach-env-facts.ts");
const { resolveSystemPromptWithEnvFacts, loadDefaultCoachSystemPrompt } = await import(
  "../src/load-system-prompt.ts"
);

test("env facts render explicit unavailable guidance for bare machines", () => {
  const text = describeCoachEnvFacts({ bashPath: null, pythonCommand: null, nodeCommand: null, jqAvailable: false });
  assert.match(text, /你的运行环境/);
  assert.match(text, /bash 工具在这台机器上不可用[\s\S]*不要调用 bash[\s\S]*不要重试/);
  assert.match(text, /Python 不可用[\s\S]*不要尝试 python\/python3/);
  assert.match(text, /Node\.js 不可用/);
  assert.match(text, /jq 不可用/);
});

test("env facts render available commands when probed present", () => {
  const text = describeCoachEnvFacts({
    bashPath: "C:\\Program Files\\Git\\bin\\bash.exe",
    pythonCommand: "python",
    nodeCommand: "node",
    jqAvailable: true,
  });
  assert.match(text, /bash 工具可用/);
  assert.match(text, /命令是 `python`/);
  assert.match(text, /命令是 `node`/);
  assert.match(text, /jq 可用/);
  assert.doesNotMatch(text, /不可用/);
});

test("default system prompt ships without machine-specific claims; env facts are appended at resolve time", async () => {
  const base = loadDefaultCoachSystemPrompt();
  // 正文不得写死任何机器能力（开发机残留会让教练在用户机器上瞎猜）。
  assert.doesNotMatch(base, /Windows Git Bash/);
  assert.doesNotMatch(base, /node 可用/);
  assert.match(base, /以提示词末尾「你的运行环境」实测结果为准/);

  const resolved = await resolveSystemPromptWithEnvFacts();
  assert.ok(resolved.startsWith(base));
  assert.match(resolved, /你的运行环境（本机实测/);
});

test("custom request prompt passes through without env facts", async () => {
  const custom = "自定义系统提示词";
  assert.equal(await resolveSystemPromptWithEnvFacts(custom), custom);
});
