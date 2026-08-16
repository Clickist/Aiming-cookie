import assert from "node:assert/strict";
import { test } from "node:test";
import { runCoachTurn } from "../src/turn.ts";
import { COACH_RUNTIME_TURN_SCHEMA_V1 } from "../src/contracts.ts";

const API_KEY = process.env.OPENCODE_API_KEY ?? "";
const SKIP = !API_KEY;

const peripheralRequest = () => ({
  schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
  run_id: "peripheral-skill-test",
  session_id: "coach-thread:99",
  user_id: "test-user",
  messages: [
    { role: "user" as const, content: "我想换个鼠标，现在的用着不太舒服，能帮我推荐一下吗？" },
  ],
  analysis_summary: null,
  model: {
    kind: "builtin" as const,
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key" as const, key: API_KEY },
  },
});

test("coach recognizes peripheral intent and starts intake flow", { skip: SKIP }, async () => {
  const response = await runCoachTurn(peripheralRequest(), {});

  assert.equal(response.ok, true, `Coach turn should succeed, error: ${JSON.stringify(response.error)}`);
  assert.ok(response.reply, "Coach should produce a reply");

  const reply = response.reply!;
  console.log("\n--- Coach reply ---");
  console.log(reply);
  console.log("--- end reply ---\n");

  assert.ok(
    reply.includes("鼠标") || reply.includes("握") || reply.includes("手"),
    "Reply should be about mouse/grip/hand topics",
  );
});

test("coach calls get_peripheral_reference when user provides grip info", { skip: SKIP }, async () => {
  const toolEvents: Array<{ type: string; tool_name?: string }> = [];

  const response = await runCoachTurn({
    schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
    run_id: "peripheral-skill-test-2",
    session_id: "coach-thread:99",
    user_id: "test-user",
    messages: [
      { role: "user", content: "我想换个鼠标，现在的用着不太舒服" },
      { role: "assistant", content: "先说说你现在的鼠标型号、手长，还有你是怎么握鼠标的？" },
      { role: "user", content: "我用的是罗技GPX2，手长18cm，指握握姿，手腕悬空。想换个更轻更小的。" },
    ],
    analysis_summary: null,
    model: {
      kind: "builtin" as const,
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      credential: { type: "api_key" as const, key: API_KEY },
    },
  }, {
    onActivity: (activity) => {
      const a = activity as Record<string, unknown>;
      // Coach activities are kind:"tool" with state started/completed/failed.
      if (a.kind === "tool" && (a.state === "started" || a.state === "completed")) {
        toolEvents.push({ type: `tool_${a.state}`, tool_name: a.tool_name as string | undefined });
      }
    },
  });

  assert.equal(response.ok, true, `Turn should succeed, error: ${JSON.stringify(response.error)}`);

  const calledTools = toolEvents
    .filter((e) => e.type === "tool_start")
    .map((e) => e.tool_name)
    .filter((n): n is string => typeof n === "string");

  console.log("\n--- Tool calls ---:", calledTools);
  console.log("--- Coach reply ---");
  console.log(response.reply?.slice(0, 800));
  console.log("--- end ---\n");

  const allTools = [...calledTools, ...(response.tool_events?.map((e: any) => e.tool_name) ?? [])];
  console.log("All tool events:", allTools);
});
