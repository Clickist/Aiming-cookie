import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import { createFixtureStreamFn } from "../src/fake-stream.ts";
import { loadPiAgent } from "../src/pi-source.ts";
import { createPythonAnalysisClient } from "../src/python-analysis-client.ts";
import {
  createRuntimeSessionTranscriptSubscriber,
  openOrCreateRuntimeSession,
} from "../src/runtime-session.ts";

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

async function createEnv(cwd: string) {
  const sourceRoot = process.env.PI_SOURCE_DIR;
  assert.ok(sourceRoot, "PI_SOURCE_DIR is required");
  const module = await import(pathToFileURL(join(sourceRoot, "packages", "agent", "src", "node.ts")).href) as {
    NodeExecutionEnv: new (options: { cwd: string }) => unknown;
  };
  return new module.NodeExecutionEnv({ cwd });
}

async function withTempDirectory(run: (directory: string) => Promise<void>) {
  const directory = await mkdtemp(join(tmpdir(), "aiming-cookie-pi-session-"));
  try {
    await run(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("Pi JSONL session reopens completed user assistant and tool-result transcript", async () => {
  await withTempDirectory(async (directory) => {
    const filePath = join(directory, "runtime.jsonl");
    const handle = await openOrCreateRuntimeSession({
      env: await createEnv(directory),
      filePath,
      sessionId: "session-runtime-1",
    });
    const { Agent } = await loadPiAgent() as {
      Agent: new (options: unknown) => { subscribe(listener: (event: unknown) => Promise<void>): () => void; prompt(input: string): Promise<void> };
    };
    const agent = new Agent({
      streamFn: createFixtureStreamFn(),
      initialState: { model: makeModel(), tools: [createAnalysisSummaryTool({ client: createPythonAnalysisClient() })] },
    });
    const unsubscribe = agent.subscribe(createRuntimeSessionTranscriptSubscriber(handle));
    try {
      await agent.prompt("Use the analysis fixture.");
    } finally {
      unsubscribe();
    }

    const reopened = await openOrCreateRuntimeSession({
      env: await createEnv(directory),
      filePath,
      sessionId: "session-runtime-1",
    });
    const context = await reopened.session.buildContext();
    assert.deepEqual(context.messages.map((message: { role: string }) => message.role), ["user", "assistant", "toolResult", "assistant"]);
  });
});

test("storage failure maps to runtime_session_storage_failed", async () => {
  await withTempDirectory(async (directory) => {
    const nonDirectory = join(directory, "not-a-directory");
    await writeFile(nonDirectory, "fixture");
    await assert.rejects(
      openOrCreateRuntimeSession({
        env: await createEnv(directory),
        filePath: join(nonDirectory, "runtime.jsonl"),
        sessionId: "session-storage-failure",
      }),
      (error) => {
        assert.equal((error as { schema_version?: unknown }).schema_version, "error.v1");
        assert.equal((error as { category?: unknown }).category, "storage_disk");
        assert.equal((error as { code?: unknown }).code, "runtime_session_storage_failed");
        assert.equal((error as { details?: unknown }).details, null);
        return true;
      },
    );
  });
});
