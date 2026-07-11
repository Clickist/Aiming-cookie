import assert from "node:assert/strict";
import test from "node:test";

import { COACH_RUNTIME_TURN_SCHEMA } from "../src/contracts.ts";
import { runCoachTurnWithFakeStream } from "../src/turn.ts";

function baseRequest() {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA,
    run_id: "run-test-1",
    user_id: "dev",
    messages: [{ role: "user", content: "帮我看看该怎么练" }],
    analysis_summary: null,
    model: {
      base_url: "https://api.deepseek.com",
      api_key_env: "DEEPSEEK_API_KEY",
      model_id: "deepseek-chat",
    },
  };
}

test("fake streamFn drives one Pi turn and returns non-empty reply", async () => {
  const response = await runCoachTurnWithFakeStream(baseRequest(), "测试教练回复");
  assert.equal(response.ok, true);
  assert.equal(response.reply, "测试教练回复");
  assert.equal(response.error, null);
});