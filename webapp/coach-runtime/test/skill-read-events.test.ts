import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-skill-read-events-"));
process.env.DATA_ROOT = dataRoot;

const { COACH_RUNTIME_TURN_SCHEMA_V1 } = await import("../src/contracts.ts");
const { runCoachTurn } = await import("../src/turn.ts");
const { ensureAppDataDirs } = await import("../src/app-data.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

function turnRequest(runId: string, sessionId: string, content: string) {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
    run_id: runId,
    session_id: sessionId,
    user_id: "test-user",
    messages: [{ role: "user", content }],
    analysis_summary: null,
    model: {
      kind: "builtin" as const,
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      credential: { type: "api_key" as const, key: "skill-read-test-key" },
    },
  };
}

/** Fake stream rounds: tool-call rounds first, then a stop text reply. */
function streamFnWithToolCalls(rounds: Array<Array<Record<string, unknown>>>): StreamFn {
  let call = 0;
  return async () => {
    const roundIndex = call;
    call += 1;
    if (roundIndex < rounds.length) {
      return streamAssistant(rounds[roundIndex], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "讲完了。" }], "stop");
  };
}

test("reading a SKILL.md emits exactly one deduped skill tool event per run", async () => {
  ensureAppDataDirs();
  const skillDir = join(dataRoot, "skills", "teaching");
  mkdirSync(skillDir, { recursive: true });
  writeFileSync(join(skillDir, "SKILL.md"), "---\nname: teaching\ndescription: test\n---\nteaching body\n", "utf8");
  const analysisDir = join(dataRoot, "analyses", "3");
  mkdirSync(analysisDir, { recursive: true });
  writeFileSync(join(analysisDir, "overview.json"), "{}", "utf8");

  const response = await runCoachTurn(
    turnRequest("skill-read-turn-1", "coach-thread:301", "带我系统训练一下"),
    {
      streamFn: streamFnWithToolCalls([
        [
          { type: "toolCall", id: "read-skill-1", name: "read", arguments: { path: join(skillDir, "SKILL.md") } },
          { type: "toolCall", id: "read-ov", name: "read", arguments: { path: "analyses/3/overview.json" } },
        ],
        [
          // Second read of the same skill（正斜杠写法，验证分隔符兼容）不再重复计事件。
          { type: "toolCall", id: "read-skill-2", name: "read", arguments: { path: join(skillDir, "SKILL.md").split("\\").join("/") } },
        ],
      ]),
    },
  );

  assert.equal(response.ok, true, `turn should succeed, error: ${JSON.stringify(response.error)}`);
  const events = response.tool_events as Array<{ type: string; skill_name?: string }>;
  const skillEvents = events.filter((event) => event.type === "skill");
  assert.equal(skillEvents.length, 1, `expected exactly one skill event, got ${JSON.stringify(events)}`);
  assert.equal(skillEvents[0]!.skill_name, "teaching");
});
