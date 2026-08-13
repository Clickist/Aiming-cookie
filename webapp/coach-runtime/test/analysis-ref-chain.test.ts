import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-analysis-ref-"));
process.env.DATA_ROOT = dataRoot;

const { ensureAppDataDirs } = await import("../src/app-data.ts");
const { createReadTool, createLsTool, subscribeAnalysisReads } = await import("../src/fs-tools.ts");
const { readConversationMeta, updateConversationAnalysisIds } = await import("../src/session-repo.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("read tool reports analysis ids under analyses/{id}/", async () => {
  ensureAppDataDirs();
  const dir = join(dataRoot, "analyses", "1");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), "{}", "utf8");

  const seen: number[] = [];
  const unsubscribe = subscribeAnalysisReads((id) => seen.push(id));
  try {
    const read = createReadTool(dataRoot);
    await read.execute("call", { path: "analyses/1/overview.json" });
    await read.execute("call", { path: join(dataRoot, "analyses", "1", "overview.json") });
    assert.deepEqual(seen, [1, 1]);
  } finally {
    unsubscribe();
  }
});

test("read tool does not report paths outside analyses/", async () => {
  ensureAppDataDirs();
  mkdirSync(join(dataRoot, "conversations"), { recursive: true });
  writeFileSync(join(dataRoot, "conversations", "x.txt"), "hi", "utf8");

  const seen: number[] = [];
  const unsubscribe = subscribeAnalysisReads((id) => seen.push(id));
  try {
    const read = createReadTool(dataRoot);
    await read.execute("call", { path: "conversations/x.txt" });
    assert.deepEqual(seen, []);
  } finally {
    unsubscribe();
  }
});

test("ls tool reports an analysis directory", async () => {
  ensureAppDataDirs();
  mkdirSync(join(dataRoot, "analyses", "2"), { recursive: true });

  const seen: number[] = [];
  const unsubscribe = subscribeAnalysisReads((id) => seen.push(id));
  try {
    const ls = createLsTool(dataRoot);
    await ls.execute("call", { path: "analyses/2" });
    assert.deepEqual(seen, [2]);
  } finally {
    unsubscribe();
  }
});

test("session meta unions engaged analysis ids", () => {
  ensureAppDataDirs();
  updateConversationAnalysisIds(1, [2]);
  updateConversationAnalysisIds(1, [1, 2]);
  const meta = readConversationMeta(1);
  assert.deepEqual(meta.analysis_session_ids, [1, 2]);
});
