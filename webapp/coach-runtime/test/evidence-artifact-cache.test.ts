import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-evidence-cache-"));
process.env.DATA_ROOT = dataRoot;

import { cachedProcessedTableCatalog, loadArtifact } from "../src/evidence-native.ts";

const ARTIFACT_PATH = join(dataRoot, "analyses", "77", "evidence.json");

function writeArtifact(metricValue: number): void {
  mkdirSync(join(dataRoot, "analyses", "77"), { recursive: true });
  const artifact = {
    schema_version: "analysis_evidence_artifact.v1",
    analysis_ref: "analysis:77",
    canonical_run_facts: { scenario_profile_ref: "scenario:test@1" },
    canonical_time_window: { start_ms: 0, end_ms: 1000, timebase_version: "tb.v1" },
    event_bundles: [{
      schema_version: "event_bundle.v1",
      analysis_ref: "analysis:77",
      events: [{
        event_id: "flick:1", event_kind: "static_flick",
        start_ms: 100, end_ms: 400, confidence: 1, limitations: [],
        attributes: { path_efficiency: metricValue },
      }],
    }],
    evidence_segments: [],
    metric_records: [],
    sample_sets: [],
    signal_bundles: [],
    normalized_outcome_records: [],
  };
  writeFileSync(ARTIFACT_PATH, JSON.stringify(artifact, null, metricValue > 0 ? 2 : 0), "utf8");
}

writeArtifact(0.5);

test("loadArtifact caches by stat: same object identity until the file changes (TS-2)", () => {
  const first = loadArtifact("analysis:77", "owner");
  const second = loadArtifact("analysis:77", "owner");
  assert.ok(first && second);
  // 引用相等 = 缓存命中（同回合成串命令零重复读+解析）。
  assert.equal(first.artifact, second.artifact);

  // 文件重写 → 强制 mtime 前移（防同毫秒 stat 粒度假命中）→ 重新解析。
  writeArtifact(0.9);
  const future = Date.now() / 1000 + 5;
  utimesSync(ARTIFACT_PATH, future, future);
  const third = loadArtifact("analysis:77", "owner");
  assert.ok(third);
  assert.notEqual(third.artifact, first.artifact);
  assert.equal(
    (third.artifact.event_bundles[0]!.events[0]!.attributes as Record<string, number>).path_efficiency,
    0.9,
    "cache must not serve stale content after the artifact file is rewritten",
  );
});

test("table catalog builds once per artifact object and is shared across commands (TS-2)", () => {
  const loaded = loadArtifact("analysis:77", "owner");
  assert.ok(loaded);
  const first = cachedProcessedTableCatalog(loaded.artifact);
  const second = cachedProcessedTableCatalog(loaded.artifact);
  assert.equal(first, second, "catalog must be cached per artifact identity");
});

test("missing artifact still resolves to null", () => {
  assert.equal(loadArtifact("analysis:404", "owner"), null);
});
