import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-debug-log-"));
process.env.DATA_ROOT = dataRoot;

const { wrapCoachSession } = await import("../src/turn.ts");

const DEBUG_LOG = join(dataRoot, "coach-debug.log");

function fakeSession(messages: unknown[]): unknown {
  return {
    appendMessage: async () => "id",
    buildContext: async () => ({ messages }),
    getBranch: async () => [],
  };
}

async function waitFor(condition: () => boolean, timeoutMs = 2_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) return true;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return condition();
}

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("buildContext writes no debug log by default (TS-3)", async () => {
  delete process.env.COACH_DEBUG_CONTEXT_LOG;
  const wrapped = wrapCoachSession(fakeSession([
    { role: "user", content: [{ type: "text", text: "问" }] },
    { role: "assistant", content: [{ type: "text", text: "答" }] },
  ]), []);
  await (wrapped as { buildContext(): Promise<unknown> }).buildContext();
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(existsSync(DEBUG_LOG), false, "debug log must stay off by default (hot-path sync write removed)");
});

test("COACH_DEBUG_CONTEXT_LOG=1 enables async append with 1MB rotation (TS-3)", async () => {
  process.env.COACH_DEBUG_CONTEXT_LOG = "1";
  // 预置超限旧日志，验证轮转：旧内容进 .1，新写入落在新文件。
  writeFileSync(DEBUG_LOG, "x".repeat(1_000_001), "utf8");
  const wrapped = wrapCoachSession(fakeSession([
    { role: "user", content: [{ type: "text", text: "问" }] },
    { role: "assistant", content: [{ type: "text", text: "答" }] },
  ]), []);
  await (wrapped as { buildContext(): Promise<unknown> }).buildContext();

  const rotated = await waitFor(() => existsSync(`${DEBUG_LOG}.1`));
  assert.ok(rotated, "oversized debug log must rotate to .1");
  const fresh = await waitFor(() => {
    try {
      return readFileSync(DEBUG_LOG, "utf8").includes("[buildContext] user → assistant");
    } catch {
      return false;
    }
  });
  assert.ok(fresh, "new async append must land in the fresh log file");
  assert.ok(readFileSync(DEBUG_LOG, "utf8").length < 10_000, "fresh log must be the rotated (small) file");
  delete process.env.COACH_DEBUG_CONTEXT_LOG;
});
