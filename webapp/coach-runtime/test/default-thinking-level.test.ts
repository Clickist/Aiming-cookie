import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { defaultThinkingLevel } from "../src/turn.ts";

describe("defaultThinkingLevel（推理模型默认次顶级思考档）", () => {
  it("内置 deepseek 推理模型开 high", () => {
    assert.equal(
      defaultThinkingLevel({ provider: "deepseek", reasoning: true, baseUrl: "https://api.deepseek.com" }),
      "high",
    );
  });

  it("自定义 deepseek.com 兼容端点的推理模型同样开 high", () => {
    assert.equal(
      defaultThinkingLevel({ provider: "my-proxy", reasoning: true, baseUrl: "https://api.deepseek.com/v1" }),
      "high",
    );
  });

  it("opencode-go 等其它推理模型也统一开 high（元数据与 deepseek 同款）", () => {
    assert.equal(defaultThinkingLevel({ provider: "opencode-go", reasoning: true }), "high");
    assert.equal(
      defaultThinkingLevel({ provider: "openai", reasoning: true, baseUrl: "https://api.openai.com/v1" }),
      "high",
    );
  });

  it("非推理模型不开，维持默认 off", () => {
    assert.equal(defaultThinkingLevel({ provider: "deepseek", reasoning: false }), undefined);
    assert.equal(defaultThinkingLevel({ provider: "openai", reasoning: false }), undefined);
    assert.equal(defaultThinkingLevel(null), undefined);
    assert.equal(defaultThinkingLevel("deepseek"), undefined);
    assert.equal(defaultThinkingLevel({ provider: "deepseek" }), undefined);
  });
});
