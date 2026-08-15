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

test("materialized directory mirrors the registry entry by entry", () => {
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
  assert.equal(index.entries.length, registry.entries.length);

  // No dead links and no extra files: the entries directory is exactly the
  // index's entry_file set.
  const filesOnDisk = readdirSync(join(knowledgeDir, "entries")).sort();
  assert.deepEqual(filesOnDisk, index.entries.map((item) => item.entry_file).sort());

  for (let i = 0; i < registry.entries.length; i++) {
    const entry = registry.entries[i];
    const line = index.entries[i];
    assert.equal(line.entry_ref, entryRef(entry));
    assert.equal(line.status, entry.status);
    assert.deepEqual(line.topics, entry.topics);
    assert.deepEqual(line.signals, entry.signals);
    assert.deepEqual(line.metric_refs, entry.metric_refs);
    assert.ok(typeof line.summary === "string" && line.summary.length > 0);

    const file = JSON.parse(readFileSync(join(knowledgeDir, "entries", line.entry_file), "utf-8")) as {
      schema_version: string; registry_version: string; entry: unknown;
    };
    assert.equal(file.schema_version, "coach_knowledge_entry.v1");
    assert.equal(file.registry_version, registry.registry_version);
    assert.deepEqual(file.entry, entry);
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
    rebuilt.entries.map((item) => item.entry_file).sort(),
  );
});

test("startSidecarServer materializes the knowledge directory at startup", async () => {
  rmSync(knowledgeDir, { recursive: true, force: true });
  const { startSidecarServer } = await import("../src/sidecar-server.ts");
  const server = startSidecarServer({ port: 0 });
  try {
    assert.ok(existsSync(join(knowledgeDir, "index.json")));
    assert.equal(readdirSync(join(knowledgeDir, "entries")).length, registry.entries.length);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("the v8 registry materializes all 36 entries", () => {
  materializeKnowledgeDir();
  const index = JSON.parse(readFileSync(join(knowledgeDir, "index.json"), "utf-8")) as {
    registry_version: string;
    entries: Array<{ entry_file: string }>;
  };
  assert.equal(index.registry_version, "2026-08-16.v8");
  assert.equal(index.entries.length, 36);
  assert.equal(readdirSync(join(knowledgeDir, "entries")).length, 36);
});
