/**
 * Materializes the in-memory knowledge REGISTRY into the app-data directory
 * so the Coach can browse it with the plain file tools.
 *
 * Layout (owned exclusively by this module — never hand-edited):
 *   knowledge/index.json          — one line of discovery metadata per entry
 *   knowledge/entries/{ref}.json  — one full entry per file
 *
 * The directory is bound to registry_version: a version change rebuilds it
 * from scratch (no stale files from the previous version survive). Writes
 * are idempotent — an already-current directory is left untouched.
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { getDataRoot } from "./app-data.ts";
import {
  entryRef,
  loadKnowledgeRegistry,
  type KnowledgeEntry,
  type KnowledgeEntryV2,
  type KnowledgeRegistry,
} from "./knowledge-registry.ts";

const INDEX_SCHEMA_VERSION = "coach_knowledge_index.v1";
const ENTRY_SCHEMA_VERSION = "coach_knowledge_entry.v1";
const SUMMARY_MAX_CHARS = 160;

type KnowledgeIndex = {
  schema_version: typeof INDEX_SCHEMA_VERSION;
  registry_version: string;
  entries: Array<{
    entry_ref: string;
    entry_file: string;
    status: string;
    summary: string;
    topics: string[];
    signals: string[];
    metric_refs: string[];
  }>;
};

function knowledgeDir(): string {
  return join(getDataRoot(), "knowledge");
}

/** Title-style summary: the definition text (v2+) or the entry text (v1). */
function entrySummary(entry: KnowledgeEntry): string {
  const raw = "family_scope" in entry
    ? (entry as KnowledgeEntryV2).definition.text
    : entry.text;
  return raw.length > SUMMARY_MAX_CHARS ? `${raw.slice(0, SUMMARY_MAX_CHARS - 1)}…` : raw;
}

function entryFileName(entry: KnowledgeEntry): string {
  const ref = `${entry.entry_id}@${entry.entry_version}`;
  return `${ref.replace(/[^A-Za-z0-9._@-]/g, "_")}.json`;
}

function buildIndex(registry: KnowledgeRegistry): KnowledgeIndex {
  return {
    schema_version: INDEX_SCHEMA_VERSION,
    registry_version: registry.registry_version,
    entries: registry.entries.map((entry) => ({
      entry_ref: entryRef(entry),
      entry_file: entryFileName(entry),
      status: entry.status,
      summary: entrySummary(entry),
      topics: entry.topics,
      signals: entry.signals,
      metric_refs: entry.metric_refs,
    })),
  };
}

/** True when the on-disk directory already reflects exactly this registry. */
function isCurrentKnowledgeDir(root: string, index: KnowledgeIndex): boolean {
  const indexPath = join(root, "index.json");
  if (!existsSync(indexPath)) return false;
  let existing: unknown;
  try {
    existing = JSON.parse(readFileSync(indexPath, "utf-8"));
  } catch {
    return false;
  }
  if (!existing || typeof existing !== "object") return false;
  const current = existing as KnowledgeIndex;
  if (current.schema_version !== INDEX_SCHEMA_VERSION || current.registry_version !== index.registry_version) return false;
  if (!Array.isArray(current.entries) || current.entries.length !== index.entries.length) return false;
  // Compare the full ordered ref list, not just the count: same-length
  // indexes from different registries must trigger a rebuild.
  for (let i = 0; i < index.entries.length; i++) {
    if (current.entries[i]?.entry_ref !== index.entries[i].entry_ref) return false;
    if (!existsSync(join(root, "entries", index.entries[i].entry_file))) return false;
  }
  return true;
}

export function materializeKnowledgeDir(registryVersion?: string): void {
  const registry = loadKnowledgeRegistry(registryVersion);
  const index = buildIndex(registry);
  const root = knowledgeDir();
  if (isCurrentKnowledgeDir(root, index)) return;

  // Rebuild the whole directory so no file from a previous version survives.
  rmSync(root, { recursive: true, force: true });
  const entriesDir = join(root, "entries");
  mkdirSync(entriesDir, { recursive: true });
  for (const entry of registry.entries) {
    writeFileSync(
      join(entriesDir, entryFileName(entry)),
      JSON.stringify({ schema_version: ENTRY_SCHEMA_VERSION, registry_version: registry.registry_version, entry }, null, 2),
      "utf-8",
    );
  }
  writeFileSync(join(root, "index.json"), JSON.stringify(index, null, 2), "utf-8");
}
