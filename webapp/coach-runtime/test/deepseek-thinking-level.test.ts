import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { deepSeekThinkingLevel } from "../src/turn.ts";

describe("deepSeekThinkingLevel", () => {
  it("内置 deepseek 推理模型开 high 思考档", () => {
    assert.equal(
      deepSeekThinkingLevel({ provider: "deepseek", reasoning: true, baseUrl: "https://api.deepseek.com" }),
      "high",
    );
  });

  it("自定义 deepseek.com 兼容端点的推理模型同样开 high", () => {
    assert.equal(
      deepSeekThinkingLevel({ provider: "my-proxy", reasoning: true, baseUrl: "https://api.deepseek.com/v1" }),
      "high",
    );
  });

  it("deepseek 非推理模型不开（Pi 会保持 thinking:disabled 干净行为）", () => {
    assert.equal(deepSeekThinkingLevel({ provider: "deepseek", reasoning: false }), undefined);
  });

  it("其他 provider 一律不动，维持既有请求形态", () => {
    assert.equal(deepSeekThinkingLevel({ provider: "opencode-go", reasoning: true }), undefined);
    assert.equal(
      deepSeekThinkingLevel({ provider: "openai", reasoning: true, baseUrl: "https://api.openai.com/v1" }),
      undefined,
    );
    assert.equal(deepSeekThinkingLevel({ provider: "openai", reasoning: false }), undefined);
    assert.equal(deepSeekThinkingLevel(null), undefined);
    assert.equal(deepSeekThinkingLevel("deepseek"), undefined);
  });
});
