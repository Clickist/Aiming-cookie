import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

import { createAnalysisSummaryTool } from "./analysis-summary-tool.ts";
import { type SpikeRuntimeEvent } from "./contracts.ts";
import { createEventMapper } from "./event-mapper.ts";
import { loadPiAgent } from "./pi-source.ts";
import { createProxyStreamFn } from "./proxy-stream.ts";
import { createPythonAnalysisClient } from "./python-analysis-client.ts";
import {
  createRuntimeSessionTranscriptSubscriber,
  openOrCreateRuntimeSession,
  recoverInterruptedRun,
} from "./runtime-session.ts";

function makeModel(endpoint: string) {
  return {
    id: "fixture-model",
    name: "fixture model",
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    baseUrl: endpoint,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: 2048,
  };
}

async function createNodeExecutionEnv(cwd: string): Promise<unknown> {
  const root = process.env.PI_SOURCE_DIR;
  if (!root) throw new Error("PI_SOURCE_DIR is required and must point to the pinned Pi source checkout");
  const module = await import(pathToFileURL(join(root, "packages", "agent", "src", "node.ts")).href) as {
    NodeExecutionEnv: new (options: { cwd: string }) => unknown;
  };
  return new module.NodeExecutionEnv({ cwd });
}

export async function runSpike(options: {
  endpoint: string;
  filePath: string;
  sessionId: string;
  runId: string;
  prompt: string;
}): Promise<{ events: SpikeRuntimeEvent[] }> {
  const emittedAt = "2026-07-11T00:00:00.000Z";
  const events: SpikeRuntimeEvent[] = [];
  let sequence = 0;
  const emit = (event: SpikeRuntimeEvent) => {
    events.push({ ...event, sequence: ++sequence });
  };
  const handle = await openOrCreateRuntimeSession({
    env: await createNodeExecutionEnv(dirname(options.filePath)),
    filePath: options.filePath,
    sessionId: options.sessionId,
  });
  const interrupted = await recoverInterruptedRun(handle);
  if (interrupted) {
    emit({
      schema_version: "coach_runtime_event.v0",
      run_id: options.runId,
      sequence: 0,
      emitted_at: emittedAt,
      type: "run.interrupted",
      payload: { previous_run_id: interrupted.previousRunId },
    });
  }
  await handle.session.appendCustomEntry("aiming_cookie_run.v0", { run_id: options.runId, status: "running" });

  const { Agent } = await loadPiAgent() as {
    Agent: new (options: unknown) => {
      subscribe(listener: (event: unknown) => Promise<void> | void): () => void;
      prompt(input: string): Promise<void>;
    };
  };
  const agent = new Agent({
    streamFn: createProxyStreamFn({ endpoint: options.endpoint, runId: options.runId }),
    initialState: {
      model: makeModel(options.endpoint),
      tools: [createAnalysisSummaryTool({ client: createPythonAnalysisClient() })],
    },
  });
  let hasError = false;
  const unsubscribeSession = agent.subscribe(createRuntimeSessionTranscriptSubscriber(handle));
  const unsubscribeLifecycle = agent.subscribe(async (event) => {
    if (!event || typeof event !== "object") return;
    const typed = event as { type?: unknown; message?: { errorMessage?: unknown } };
    if (typed.type === "message_end" && typeof typed.message?.errorMessage === "string") hasError = true;
    if (typed.type === "agent_end" && !hasError) {
      await handle.session.appendCustomEntry("aiming_cookie_run.v0", { run_id: options.runId, status: "completed" });
    }
  });
  const unsubscribeMapper = agent.subscribe(createEventMapper({
    runId: options.runId,
    clock: () => emittedAt,
    emit,
  }));
  try {
    await agent.prompt(options.prompt);
  } finally {
    unsubscribeMapper();
    unsubscribeLifecycle();
    unsubscribeSession();
  }
  return { events };
}
