import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-turn-context-prefix-test-"));
process.env.DATA_ROOT = dataRoot;

const { wrapCoachSession } = await import("../src/turn.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

/**
 * In-memory stand-in for pi Session, mirroring just what wrapCoachSession
 * touches: append-only entries, getBranch(), buildContext() with pi's entry →
 * message mapping for the entry kinds used here (message / compaction).
 */
function createMemorySession() {
  type Entry =
    | { type: "message"; id: string; message: Record<string, unknown> }
    | { type: "compaction"; id: string; summary: string };
  const entries: Entry[] = [];
  let nextId = 0;
  const entryToMessage = (entry: Entry): Record<string, unknown> => {
    if (entry.type === "message") return entry.message;
    // pi sessionEntryToContextMessages: compaction → summary message (+ retainedTail).
    return { role: "compactionSummary", summary: entry.summary, tokensBefore: 0, timestamp: Date.now() };
  };
  return {
    async appendMessage(message: Record<string, unknown>): Promise<string> {
      const id = `m${++nextId}`;
      entries.push({ type: "message", id, message });
      return id;
    },
    async getBranch(): Promise<Entry[]> {
      return entries.slice();
    },
    async buildContext(): Promise<{ messages: Record<string, unknown>[] }> {
      return { messages: entries.map(entryToMessage) };
    },
    /** Test seam: seed a compaction entry the way pi's appendCompaction would. */
    seedCompaction(summary: string): void {
      entries.push({ type: "compaction", id: `m${++nextId}`, summary });
    },
  };
}

const textMessage = (role: "user" | "assistant", text: string) => ({
  role,
  content: [{ type: "text", text }],
  timestamp: Date.now(),
});

const firstText = (message: unknown): string => {
  const content = (message as { content?: Array<{ type?: string; text?: string }> }).content;
  return content?.[0]?.text ?? "";
};

test("buildContext stays prefix-stable across turns past the old 40-message window", async () => {
  // 24 turns = 48 messages > the removed 40-message cap: under the old sliding
  // window each new turn shifted the first context message, invalidating the
  // whole DeepSeek prefix cache. Prefix stability is the code-level precondition
  // for cache hits: every later context must be a pure extension of the previous.
  const raw = createMemorySession();
  for (let i = 0; i < 24; i++) {
    await raw.appendMessage(textMessage("user", `u${i}`));
    await raw.appendMessage(textMessage("assistant", `a${i}`));
  }
  const wrapped = wrapCoachSession(raw, []);

  // Simulate consecutive turns: agent-runs persists the current user message
  // before the turn, buildContext feeds the provider, the reply lands after.
  const contexts: unknown[][] = [];
  for (let turn = 24; turn <= 26; turn++) {
    await raw.appendMessage(textMessage("user", `u${turn}`));
    const ctx = (await wrapped.buildContext()) as { messages: unknown[] };
    contexts.push(ctx.messages);
    await raw.appendMessage(textMessage("assistant", `a${turn}`));
  }

  // Every context still starts at the very first message of the session.
  assert.equal(firstText(contexts[0][0]), "u0");
  // Strict prefix growth: later contexts never rewrite earlier messages.
  for (let i = 1; i < contexts.length; i++) {
    const previous = contexts[i - 1];
    assert.ok(contexts[i].length > previous.length, `context ${i} must grow`);
    assert.deepEqual(
      contexts[i].slice(0, previous.length),
      previous,
      `context ${i} must be a prefix-extension of context ${i - 1}`,
    );
  }
});

test("buildContext keeps the compaction summary at the head after pi compaction", async () => {
  // pi compaction rewrites the context head into a compactionSummary message
  // (convertToLlm maps it to a user message). The boundary alignment must
  // accept it as a valid start instead of trimming it away — with the old
  // count window gone, nothing else keeps a leading summary out of reach of
  // the alignment loop.
  const raw = createMemorySession();
  raw.seedCompaction("历史摘要");
  await raw.appendMessage(textMessage("user", "u1"));
  await raw.appendMessage(textMessage("assistant", "a1"));
  const wrapped = wrapCoachSession(raw, []);

  const ctx = (await wrapped.buildContext()) as { messages: unknown[] };
  assert.equal(ctx.messages.length, 3);
  assert.equal((ctx.messages[0] as { role?: string }).role, "compactionSummary");
  assert.equal(firstText(ctx.messages[1]), "u1");
});

test("buildContext still trims a leading orphan assistant message defensively", async () => {
  const raw = createMemorySession();
  await raw.appendMessage(textMessage("assistant", "孤儿回复"));
  await raw.appendMessage(textMessage("user", "u0"));
  await raw.appendMessage(textMessage("assistant", "a0"));
  const wrapped = wrapCoachSession(raw, []);

  const ctx = (await wrapped.buildContext()) as { messages: unknown[] };
  assert.equal(ctx.messages.length, 2);
  assert.equal(firstText(ctx.messages[0]), "u0");
});
