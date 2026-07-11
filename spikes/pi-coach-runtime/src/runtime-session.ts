import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { isRecord, makeSpikeError, type SpikeErrorV1 } from "./contracts.ts";

type RuntimeSession = {
  appendCustomEntry(customType: string, data?: unknown): Promise<string>;
  appendMessage(message: unknown): Promise<string>;
  getEntries(): Promise<Array<Record<string, unknown>>>;
  buildContext(): Promise<{ messages: unknown[] }>;
};

export type RuntimeSessionHandle = {
  session: RuntimeSession;
};

function sourceModule(...parts: string[]): string {
  const root = process.env.PI_SOURCE_DIR;
  if (!root) throw new Error("PI_SOURCE_DIR is required and must point to the pinned Pi source checkout");
  return pathToFileURL(join(root, ...parts)).href;
}

async function loadPiSession() {
  return await import(sourceModule("packages", "agent", "src", "index.ts")) as {
    JsonlSessionStorage: {
      open(env: unknown, filePath: string): Promise<unknown>;
      create(env: unknown, filePath: string, options: { cwd: string; sessionId: string; metadata: Record<string, unknown> }): Promise<unknown>;
    };
    Session: new (storage: unknown) => RuntimeSession;
  };
}

function storageFailure(): SpikeErrorV1 {
  return makeSpikeError({
    category: "storage_disk",
    code: "runtime_session_storage_failed",
    message: "Runtime session storage failed",
    retryable: false,
    trace_id: null,
    details: null,
  });
}

export async function openOrCreateRuntimeSession(options: {
  env: unknown;
  filePath: string;
  sessionId: string;
}): Promise<RuntimeSessionHandle> {
  try {
    if (!isRecord(options.env) || typeof options.env.cwd !== "string") throw new Error("Invalid execution environment");
    const { JsonlSessionStorage, Session } = await loadPiSession();
    const storage = existsSync(options.filePath)
      ? await JsonlSessionStorage.open(options.env, options.filePath)
      : await JsonlSessionStorage.create(options.env, options.filePath, {
          cwd: options.env.cwd,
          sessionId: options.sessionId,
          metadata: { purpose: "pi-coach-runtime-spike" },
        });
    return { session: new Session(storage) };
  } catch {
    throw storageFailure();
  }
}

function runningMarker(entry: Record<string, unknown>): string | null {
  if (entry.type !== "custom" || entry.customType !== "aiming_cookie_run.v0" || !isRecord(entry.data)) return null;
  if (entry.data.status !== "running" || typeof entry.data.run_id !== "string") return null;
  return entry.data.run_id;
}

export async function recoverInterruptedRun(handle: RuntimeSessionHandle): Promise<{ previousRunId: string } | null> {
  const entries = await handle.session.getEntries();
  const lastMarker = [...entries].reverse().find((entry) => entry.type === "custom" && entry.customType === "aiming_cookie_run.v0");
  if (!lastMarker) return null;
  const previousRunId = runningMarker(lastMarker);
  if (!previousRunId) return null;
  await handle.session.appendCustomEntry("aiming_cookie_run.v0", { run_id: previousRunId, status: "interrupted" });
  return { previousRunId };
}

export function createRuntimeSessionTranscriptSubscriber(handle: RuntimeSessionHandle) {
  return async (event: unknown) => {
    if (!isRecord(event) || event.type !== "message_end" || !isRecord(event.message)) return;
    const role = event.message.role;
    if (role !== "user" && role !== "assistant" && role !== "toolResult") return;
    await handle.session.appendMessage(event.message);
  };
}
