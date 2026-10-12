// D15 一次性 e2e：runCoachTurn 生产路径（无 streamFn → wrap 分支）全链验证。
// 场景 A：正常路径——自定义 provider 指向本地 mock OpenAI 端点，对话成功且
//         DATA_ROOT/coach-error.log 零 netprobe 行（首选成功零介入）。
// 场景 B：故障注入——base_url 指向必死端口，期待 network_transient + retryable
//         + coach-error.log 出现 [netprobe] rescue_fail 行（涛形态复现+修复行为）。
// 运行：DATA_ROOT=<tmp> PI_SOURCE_DIR=... tsx/loader node 本文件。
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { appendFileSync, existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.DATA_ROOT = mkdtempSync(join(tmpdir(), "ac-d15-"));
const { runCoachTurn } = await import("../src/turn.ts");
const { COACH_RUNTIME_TURN_SCHEMA_V1 } = await import("../src/contracts.ts");

function sse(res: import("node:http").ServerResponse, chunks: string[]): void {
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
  for (const c of chunks) res.write(`data: ${JSON.stringify(c)}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
}

let mockHits = 0;
const mock = createServer((req, res) => {
  if ((req.url ?? "").includes("/chat/completions")) {
    mockHits++;
    sse(res, [
      { id: "cmpl-1", object: "chat.completion.chunk", created: 1, model: "mock-model", choices: [{ index: 0, delta: { role: "assistant", content: "" } }] },
      { id: "cmpl-1", object: "chat.completion.chunk", created: 1, model: "mock-model", choices: [{ index: 0, delta: { content: "OK，链路通。" } }] },
      { id: "cmpl-1", object: "chat.completion.chunk", created: 1, model: "mock-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] },
    ]);
    return;
  }
  res.writeHead(404).end();
});
await new Promise<void>((r) => mock.listen(0, "127.0.0.1", r));
const mockPort = (mock.address() as { port: number }).port;

// ── 场景 A：正常路径（生产 wrap 分支 + 首选成功）────────────────────────────
const okTurn = await runCoachTurn({
  schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
  run_id: "d15-normal",
  user_id: "d15",
  messages: [{ role: "user", content: "连通性验证，收到请说 OK" }],
  analysis_summary: null,
  model: {
    kind: "custom_openai_compatible",
    provider_id: "d15-mock",
    provider_name: "D15 Mock",
    base_url: `http://127.0.0.1:${mockPort}/v1`,
    model_id: "mock-model",
    credential: { type: "api_key", key: "d15-key" },
  },
} as Parameters<typeof runCoachTurn>[0]);

assert.equal(okTurn.ok, true, `normal turn should succeed: ${JSON.stringify(okTurn.error)}`);
assert.match(okTurn.reply ?? "", /OK/);
const logPath = join(process.env.DATA_ROOT!, "coach-error.log");
const logA = existsSync(logPath) ? readFileSync(logPath, "utf8") : "";
assert.ok(!logA.includes("[netprobe]"), "normal path must not emit netprobe lines");
console.log(`A PASS: reply=${JSON.stringify(okTurn.reply)} mockHits=${mockHits} netprobeLines=0`);

// ── 场景 B：必死端口（涛形态）───────────────────────────────────────────────
const deadTurn = await runCoachTurn({
  schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
  run_id: "d15-dead",
  user_id: "d15",
  messages: [{ role: "user", content: "x" }],
  analysis_summary: null,
  model: {
    kind: "custom_openai_compatible",
    provider_id: "d15-dead",
    provider_name: "D15 Dead",
    base_url: "http://127.0.0.1:1/v1",
    model_id: "mock-model",
    credential: { type: "api_key", key: "d15-key" },
  },
} as Parameters<typeof runCoachTurn>[0]);

assert.equal(deadTurn.ok, false, "dead endpoint must fail");
assert.equal(deadTurn.error?.code, "network_transient", `got: ${deadTurn.error?.code}`);
assert.equal(deadTurn.error?.retryable, true);
const logB = readFileSync(logPath, "utf8");
const probeLine = logB.split("\n").find((l) => l.includes("[netprobe]") && l.includes("rescue_fail"));
assert.ok(probeLine, "dead endpoint must emit netprobe rescue_fail line");
const record = JSON.parse(probeLine.split("] ")[1]);
assert.equal(record.host, "127.0.0.1");
assert.equal(record.tcp4, "refused");
console.log(`B PASS: code=${deadTurn.error?.code} retryable=${deadTurn.error?.retryable} netprobe phase=${record.phase} tcp4=${record.tcp4}`);

mock.close();
appendFileSync(join(process.env.DATA_ROOT!, "RESULT"), "D15 ALL PASS");
console.log("== D15 ALL PASS ==");
