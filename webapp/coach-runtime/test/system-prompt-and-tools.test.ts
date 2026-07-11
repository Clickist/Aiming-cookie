import assert from "node:assert/strict";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import { CODING_AGENT_DEFAULT_PROMPT_MARKER, FORBIDDEN_TOOL_NAMES } from "../src/contracts.ts";
import { loadDefaultCoachSystemPrompt } from "../src/load-system-prompt.ts";
import { loadPiAgent } from "../src/pi-source.ts";
import { createFakeStreamFn } from "../src/fake-stream.ts";
import { buildCoachModel } from "../src/stream-openai-compatible.ts";

test("default coach system prompt is product-owned and excludes coding-agent default", () => {
  const prompt = loadDefaultCoachSystemPrompt();
  assert.ok(prompt.includes("Aiming Cookie"));
  assert.ok(!prompt.includes(CODING_AGENT_DEFAULT_PROMPT_MARKER));
});

test("registered tools are read-only whitelist without bash/read/write/edit", async () => {
  const tool = createAnalysisSummaryTool("fixture summary");
  assert.equal(tool.name, "get_analysis_summary");
  assert.ok(!FORBIDDEN_TOOL_NAMES.has(tool.name));

  const { Agent } = (await loadPiAgent()) as {
    Agent: new (opts: Record<string, unknown>) => { state: { tools: Array<{ name: string }> } };
  };
  const agent = new Agent({
    streamFn: createFakeStreamFn(),
    initialState: {
      systemPrompt: loadDefaultCoachSystemPrompt(),
      model: buildCoachModel({
        base_url: "https://api.deepseek.com",
        api_key_env: "DEEPSEEK_API_KEY",
        model_id: "deepseek-chat",
      }),
      tools: [tool],
      messages: [],
    },
  });

  const names = agent.state.tools.map((entry) => entry.name);
  for (const forbidden of FORBIDDEN_TOOL_NAMES) {
    assert.ok(!names.includes(forbidden));
  }
  assert.deepEqual(names, ["get_analysis_summary"]);
});