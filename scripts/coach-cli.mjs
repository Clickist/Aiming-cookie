#!/usr/bin/env node
/**
 * Coach CLI — drive the Coach agent from the terminal, no browser needed.
 *
 * Usage:
 *   node scripts/coach-cli.mjs "帮我看看这一局"            # 新 session（自动分配）
 *   node scripts/coach-cli.mjs "继续刚才的" --session 12   # 续用 session 12
 *
 * Prints the reply and a compact list of the tools Coach actually invoked.
 * Env: COACH_SIDECAR_URL (default http://127.0.0.1:8765).
 */
const BASE = process.env.COACH_SIDECAR_URL ?? "http://127.0.0.1:8765";

function parseArgs(argv) {
  let sessionId;
  const message = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--session" && argv[i + 1]) {
      sessionId = Number(argv[i + 1]);
      i += 1;
    } else if (argv[i] === "--new") {
      sessionId = undefined;
    } else {
      message.push(argv[i]);
    }
  }
  return { sessionId, content: message.join(" ").trim() };
}

async function main() {
  const { sessionId, content } = parseArgs(process.argv.slice(2));
  if (!content) {
    console.error('usage: node scripts/coach-cli.mjs "消息" [--session N | --new]');
    process.exit(1);
  }

  const body = { content };
  if (sessionId !== undefined) body.session_id = sessionId;

  const created = await fetch(`${BASE}/v1/agent-runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const run = await created.json();
  if (!run.run_ref) {
    console.error("create failed:", JSON.stringify(run));
    process.exit(1);
  }
  console.log(`session=${run.session_id} run=${run.run_ref}\n`);

  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1000));
    const res = await fetch(`${BASE}/v1/agent-runs/${encodeURIComponent(run.run_ref)}`);
    const state = await res.json();
    if (!["succeeded", "failed", "stopped"].includes(state.status)) continue;

    console.log(`=== status: ${state.status} ===\n`);
    if (state.partial_text) console.log(state.partial_text);
    else console.log("(无回复文本)");
    if (state.error) console.log(`\n[error] ${JSON.stringify(state.error)}`);

    const tools = (state.events ?? [])
      .filter((e) => e.type === "tool" && e.payload)
      .map((e) => {
        const p = e.payload ?? {};
        return `- ${p.command_name ?? p.tool_name ?? e.code}  [${p.state ?? e.code}]`;
      });
    if (tools.length) console.log(`\n=== 工具调用 (${tools.length}) ===\n${tools.join("\n")}`);
    else console.log("\n(本轮没有调用任何工具)");

    process.exit(0);
  }
  console.error("timeout: 两分钟内未结束");
  process.exit(1);
}

main().catch((error) => {
  console.error("coach-cli failed:", error.message);
  process.exit(1);
});
