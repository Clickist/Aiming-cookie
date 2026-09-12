import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { entryRef, loadKnowledgeRegistry } from "../src/knowledge-registry.ts";
import { materializeKnowledgeDir } from "../src/knowledge-materialize.ts";

// Materialization owns DATA_ROOT/knowledge — point DATA_ROOT at a throwaway
// directory before the first call (getDataRoot caches on first use).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-knowledge-dir-"));
process.env.DATA_ROOT = dataRoot;

const registry = loadKnowledgeRegistry();
const knowledgeDir = join(dataRoot, "knowledge");
const prescriptionEntries = registry.entries.filter((entry) => entry.entry_id.startsWith("prescription."));
const mainEntries = registry.entries.filter((entry) => !entry.entry_id.startsWith("prescription."));

test("materialized directory mirrors the non-prescription registry entries", () => {
  materializeKnowledgeDir();

  const index = JSON.parse(readFileSync(join(knowledgeDir, "index.json"), "utf-8")) as {
    schema_version: string;
    registry_version: string;
    entries: Array<{
      entry_ref: string; entry_file: string; status: string; summary: string;
      topics: string[]; signals: string[]; metric_refs: string[];
    }>;
  };
  assert.equal(index.schema_version, "coach_knowledge_index.v1");
  assert.equal(index.registry_version, registry.registry_version);
  assert.equal(index.entries.length, mainEntries.length);
  // prescription.* has its own index; it must not appear in the main index.
  assert.ok(!index.entries.some((item) => item.entry_ref.includes("prescription.")));

  const prescriptions = JSON.parse(readFileSync(join(knowledgeDir, "prescriptions.json"), "utf-8")) as {
    schema_version: string;
    registry_version: string;
    entries: Array<{ entry_ref: string; entry_file: string; recommendation: string; scenario_availability: string }>;
  };
  assert.equal(prescriptions.schema_version, "coach_prescription_index.v1");
  assert.equal(prescriptions.registry_version, registry.registry_version);
  assert.equal(prescriptions.entries.length, prescriptionEntries.length);

  // No dead links and no extra files: the entries directory is exactly the
  // union of both indexes' entry_file sets (all full entries still materialize).
  const filesOnDisk = readdirSync(join(knowledgeDir, "entries")).sort();
  assert.deepEqual(
    filesOnDisk,
    [...index.entries, ...prescriptions.entries].map((item) => item.entry_file).sort(),
  );
  assert.equal(filesOnDisk.length, registry.entries.length);

  for (let i = 0; i < mainEntries.length; i++) {
    const entry = mainEntries[i];
    const line = index.entries[i];
    assert.equal(line.entry_ref, entryRef(entry));
    assert.equal(line.status, entry.status);
    assert.deepEqual(line.topics, entry.topics);
    assert.deepEqual(line.signals, entry.signals);
    assert.deepEqual(line.metric_refs, entry.metric_refs);
    assert.ok(typeof line.summary === "string" && line.summary.length > 0);
  }

  for (const line of [...index.entries, ...prescriptions.entries]) {
    const file = JSON.parse(readFileSync(join(knowledgeDir, "entries", line.entry_file), "utf-8")) as {
      schema_version: string; registry_version: string; entry: unknown;
    };
    assert.equal(file.schema_version, "coach_knowledge_entry.v1");
    assert.equal(file.registry_version, registry.registry_version);
  }
});

test("materialization is idempotent — a current directory is not rewritten", () => {
  const indexPath = join(knowledgeDir, "index.json");
  const before = statSync(indexPath).mtimeMs;
  materializeKnowledgeDir();
  assert.equal(statSync(indexPath).mtimeMs, before);
});

test("a registry version change rebuilds the directory without stale files", () => {
  // Start from a genuine materialization of the older registry, then plant a
  // leftover file — the state a version bump must clean up entirely.
  materializeKnowledgeDir("2026-08-06.v6");
  const v6Index = JSON.parse(readFileSync(join(knowledgeDir, "index.json"), "utf-8")) as { registry_version: string };
  assert.equal(v6Index.registry_version, "2026-08-06.v6");
  writeFileSync(join(knowledgeDir, "entries", "stale.entry@9.json"), "{}");

  // Back on the default version: the version change forces a full rebuild.
  materializeKnowledgeDir();
  const rebuilt = JSON.parse(readFileSync(join(knowledgeDir, "index.json"), "utf-8")) as {
    registry_version: string; entries: Array<{ entry_file: string }>;
  };
  assert.equal(rebuilt.registry_version, registry.registry_version);
  assert.ok(!existsSync(join(knowledgeDir, "entries", "stale.entry@9.json")));
  assert.deepEqual(
    readdirSync(join(knowledgeDir, "entries")).sort(),
    registry.entries.map((entry) => `${entry.entry_id}@${entry.entry_version}.json`).sort(),
  );
});

test("startSidecarServer materializes the knowledge directory at startup", async () => {
  rmSync(knowledgeDir, { recursive: true, force: true });
  const { startSidecarServer } = await import("../src/sidecar-server.ts");
  const server = startSidecarServer({ port: 0 });
  try {
    assert.ok(existsSync(join(knowledgeDir, "index.json")));
    assert.ok(existsSync(join(knowledgeDir, "prescriptions.json")));
    assert.equal(readdirSync(join(knowledgeDir, "entries")).length, registry.entries.length);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("the v12 registry splits prescriptions into a sub-50KB index", () => {
  materializeKnowledgeDir();
  const index = JSON.parse(readFileSync(join(knowledgeDir, "index.json"), "utf-8")) as {
    registry_version: string;
    entries: Array<{ entry_file: string; topics: string[]; signals: string[]; metric_refs: string[] }>;
  };
  assert.equal(index.registry_version, "2026-09-12.v12");
  assert.ok(!index.entries.some((entry) => entry.entry_file.startsWith("prescription.")));
  assert.equal(index.entries.length, 51);
  assert.equal(readdirSync(join(knowledgeDir, "entries")).length, 111);

  // index.json now fits the Coach read tool's 50KB single-read limit.
  const indexBytes = statSync(join(knowledgeDir, "index.json")).size;
  assert.ok(indexBytes < 50 * 1024, `index.json is ${indexBytes} bytes`);

  // The prescription index is itself readable in one go.
  const prescriptionPath = join(knowledgeDir, "prescriptions.json");
  const prescriptionBytes = statSync(prescriptionPath).size;
  assert.ok(prescriptionBytes < 50 * 1024, `prescriptions.json is ${prescriptionBytes} bytes`);

  // The corpus prescription entries reach the Coach by weakness so it can
  // match topics/signals before reading the full entry.
  const prescriptions = JSON.parse(readFileSync(prescriptionPath, "utf-8")) as {
    entries: Array<{ entry_ref: string; topics: string[]; signals: string[]; metric_refs: string[]; recommendation: string; scenario_availability: string }>;
  };
  assert.equal(prescriptions.entries.length, 60);
  const overflick = prescriptions.entries.find((entry) =>
    entry.entry_ref.includes("prescription.p030."));
  if (!overflick) throw new Error("missing P030 prescription entry in index");
  assert.ok(overflick.topics.includes("static-clicking-terminal-control"));
  assert.ok(overflick.signals.includes("reverse_ratio high"));
  assert.ok(overflick.metric_refs.includes("metric:reverse_ratio"));
  assert.match(overflick.scenario_availability, /local/);
  assert.ok(overflick.recommendation.length > 0);
});
