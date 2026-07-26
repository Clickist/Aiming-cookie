import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");
async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("history page is a light list and does not render full result payloads or benchmark UI", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /getHistorySessions/);
  assert.match(value, /getHistoryAnalysisDetail/);
  assert.match(value, /historyEvidenceState/);
  assert.match(value, /detail\.history/);
  assert.match(value, /visual_replay/);
  assert.doesNotMatch(value, /Benchmark|Plotly|result\.deterministic|video_url/);
});

test("history preserves stale rows when refresh fails", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /refreshing|unavailable|旧内容|保留/);
});

test("history and run inspector do not expose path or raw trace fields", async () => {
  const value = await source("components/task4/RunInspector.tsx");
  assert.doesNotMatch(value, /stats_source_ref|performance_source_ref|trace_artifact_ref|trace_error/);
  assert.match(value, /data-operation=\"manage_storage\"/);
  assert.match(value, /disabled variant=\"secondary\"/);
});
