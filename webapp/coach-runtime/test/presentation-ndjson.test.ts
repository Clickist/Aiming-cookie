/**
 * Regression: an openui-only partial (text/thinking both empty, presentation
 * carries the rich snapshot) is a valid NDJSON partial and must not fail-close
 * the turn.
 */
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-presentation-ndjson-"));
process.env.DATA_ROOT = dataRoot;

const { createSidecarServer } = await import("../src/sidecar-server.ts");
const { splitPresentation } = await import("../src/presentation/index.ts");
const { DESKTOP_TEST_TOKEN } = await import("./desktop-token-env.ts");

test("NDJSON accepts a presentation-only partial and frames it", async () => {
  const chartOnly = "```openui-lang\nroot = Card([m1])\nm1 = MetricIndicatorInline(\"23%\")\n```";
  const presentation = splitPresentation(chartOnly, "streaming", {
    revision: 1,
    messageId: "agent_run:presentation-only",
    runId: "agent_run:presentation-only",
  });
  assert.ok(presentation.segments.some((segment) => segment.kind === "openui"));

  const server = createSidecarServer({
    turnRunner: async (_request, options) => {
      await options?.onPartial?.({
        revision: 1,
        text: null,
        presentation,
        thinking_text: null,
        elapsed_ms: 8,
        provider_rounds: 1,
      });
      return {
        schema_version: "coach_runtime_turn.v1",
        run_id: "agent_run:presentation-only",
        ok: true,
        reply: "",
        partial_reply: null,
        error: null,
        notes: [],
        tool_events: [],
      };
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const frames = await new Promise<Array<Record<string, unknown>>>((resolve, reject) => {
      const req = http.request({
        host: "127.0.0.1",
        port: address.port,
        method: "POST",
        path: "/v1/turn",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/x-ndjson",
          "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
        },
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          try {
            resolve(Buffer.concat(chunks).toString("utf8").trim().split("\n")
              .map((line) => JSON.parse(line) as Record<string, unknown>));
          } catch (error) {
            reject(error);
          }
        });
      });
      req.on("error", reject);
      req.end("{}");
    });

    assert.deepEqual(frames.map((frame) => frame.type), ["partial", "final"]);
    assert.equal(frames[0].text, null);
    assert.equal(frames[0].thinking_text, null);
    const streamed = frames[0].presentation as { generation_status: string; segments: Array<{ kind: string }> };
    assert.equal(streamed.generation_status, "streaming");
    assert.ok(streamed.segments.some((segment) => segment.kind === "openui"));
    assert.equal((frames[1].response as { ok: boolean }).ok, true);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});
