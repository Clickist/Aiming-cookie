import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import { createEventMapper } from "../src/event-mapper.ts";
import { loadPiAgent } from "../src/pi-source.ts";
import { createPythonAnalysisClient } from "../src/python-analysis-client.ts";
import { createProxyStreamFn } from "../src/proxy-stream.ts";

function makeModel() {
  return {
    id: "fixture-model",
    name: "fixture model",
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    baseUrl: "http://127.0.0.1/fixture",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: 2048,
  };
}

test("real Pi Agent completes proxy tool turn and final text turn", async () => {
  let requests = 0;
  const server = createServer((_request, response) => {
    requests += 1;
    response.writeHead(200, { "content-type": "application/x-ndjson" });
    if (requests === 1) {
      response.end('{"type":"start"}\n{"type":"tool_call","id":"proxy-tool-1","name":"get_analysis_summary","arguments":{"analysis_id":"analysis-fixture-1"}}\n{"type":"done","stop_reason":"toolUse","usage":{"input":10,"output":4,"total_tokens":14}}\n');
      return;
    }
    response.end('{"type":"start"}\n{"type":"text_delta","delta":"proxy coach answer"}\n{"type":"done","stop_reason":"stop","usage":{"input":10,"output":4,"total_tokens":14}}\n');
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");

  const { Agent } = await loadPiAgent() as { Agent: new (options: unknown) => { subscribe(listener: (event: unknown) => void): () => void; prompt(text: string): Promise<void> } };
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  const agent = new Agent({
    streamFn: createProxyStreamFn({ endpoint: `http://127.0.0.1:${address.port}/fixture`, runId: "proxy-agent-run" }),
    initialState: { model: makeModel(), tools: [createAnalysisSummaryTool({ client: createPythonAnalysisClient() })] },
  });
  const unsubscribe = agent.subscribe(createEventMapper({
    runId: "proxy-agent-run",
    clock: () => "2026-07-11T00:00:00.000Z",
    emit: (event) => events.push(event),
  }));
  try {
    await agent.prompt("Use the proxy fixture.");
  } finally {
    unsubscribe();
    await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }

  assert.equal(requests, 2);
  assert.ok(events.some((event) => event.type === "tool.started"));
  assert.ok(events.some((event) => event.type === "tool.progress" && (event.payload.details as { stage?: unknown } | null)?.stage === "loading_fixture"));
  assert.ok(events.some((event) => event.type === "tool.completed" && event.payload.ok === true));
  assert.ok(events.some((event) => event.type === "assistant.delta" && event.payload.text === "proxy coach answer"));
});
