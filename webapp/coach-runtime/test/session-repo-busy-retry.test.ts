import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-busy-retry-test-"));
process.env.DATA_ROOT = dataRoot;

const { isStorageBusyFileError, retryOnBusyFile } = await import("../src/session-repo.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

// 用例共享同一 DATA_ROOT：每个用例前清掉 coach-error.log，防前序 recovered 行污染断言。
test.beforeEach(() => {
  rmSync(join(dataRoot, "coach-error.log"), { force: true });
});

const busyError = () => ({ ok: false as const, error: { message: "EBUSY: resource busy or locked, open 'D:\\集锦\\kvk\\session.jsonl'" } });
const okResult = () => ({ ok: true as const, value: undefined });

function coachErrorLog(): string {
  try {
    return readFileSync(join(dataRoot, "coach-error.log"), "utf8");
  } catch {
    return "";
  }
}

test("retryOnBusyFile recovers after transient EBUSY and logs the recovery", async () => {
  let calls = 0;
  const result = await retryOnBusyFile(() => {
    calls += 1;
    return calls < 3 ? Promise.resolve(busyError()) : Promise.resolve(okResult());
  }, { delays: [1, 1, 1] });
  assert.equal(result.ok, true);
  assert.equal(calls, 3);
  assert.match(coachErrorLog(), /storage-busy recovered retries=2/);
});

test("retryOnBusyFile gives up after exhausting delays and surfaces the error unchanged", async () => {
  let calls = 0;
  const result = await retryOnBusyFile(() => {
    calls += 1;
    return Promise.resolve(busyError());
  }, { delays: [1, 1, 1] });
  assert.equal(result.ok, false);
  assert.match(String(result.error?.message), /EBUSY/);
  assert.equal(calls, 4);
});

test("retryOnBusyFile does not retry non-busy errors", async () => {
  let calls = 0;
  const denied = () => ({ ok: false as const, error: { code: "permission_denied", message: "EACCES: permission denied, open 'x'" } });
  const result = await retryOnBusyFile(() => {
    calls += 1;
    return Promise.resolve(denied());
  }, { delays: [1, 1, 1] });
  assert.equal(result.ok, false);
  assert.equal(calls, 1);
  assert.doesNotMatch(coachErrorLog(), /storage-busy recovered/);
});

test("retryOnBusyFile succeeds immediately without logging on first-try success", async () => {
  let calls = 0;
  const result = await retryOnBusyFile(() => {
    calls += 1;
    return Promise.resolve(okResult());
  }, { delays: [1, 1, 1] });
  assert.equal(result.ok, true);
  assert.equal(calls, 1);
  assert.doesNotMatch(coachErrorLog(), /storage-busy recovered/);
});

test("isStorageBusyFileError matches message text and cause.code", () => {
  assert.equal(isStorageBusyFileError({ message: "EBUSY: resource busy or locked, open 'x'" }), true);
  assert.equal(isStorageBusyFileError({ message: "something else", cause: { code: "EBUSY" } }), true);
  assert.equal(isStorageBusyFileError({ message: "EACCES: permission denied" }), false);
  assert.equal(isStorageBusyFileError(new Error("fetch failed")), false);
  assert.equal(isStorageBusyFileError(null), false);
  assert.equal(isStorageBusyFileError(undefined), false);
});
