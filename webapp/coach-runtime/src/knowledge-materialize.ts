/**
 * Materializes the in-memory knowledge REGISTRY into the app-data directory
 * so the Coach can browse it with the plain file tools.
 *
 * Layout (owned exclusively by this module — never hand-edited):
 *   knowledge/index.json         — discovery metadata for explanation/diagnosis entries
 *   knowledge/prescriptions.json — compact index of prescription.* training entries
 *   knowledge/entries/{ref}.json — one full entry per file (all entries)
 *
 * prescription.* entries are excluded from the main index: v12's 60 corpus
 * prescriptions pushed index.json past the Coach read tool's 50 KB single-read
 * limit. They get their own small index so `read knowledge/prescriptions.json`
 * fits in one read; full entry files stay at the same path for follow-up reads.
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
const PRESCRIPTION_INDEX_SCHEMA_VERSION = "coach_prescription_index.v1";
const ENTRY_SCHEMA_VERSION = "coach_knowledge_entry.v1";
const SUMMARY_MAX_CHARS = 160;
const PRESCRIPTION_PREFIX = "prescription.";

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

type KnowledgePrescriptionIndex = {
  schema_version: typeof PRESCRIPTION_INDEX_SCHEMA_VERSION;
  registry_version: string;
  entries: Array<{
    entry_ref: string;
    entry_file: string;
    status: string;
    topics: string[];
    signals: string[];
    metric_refs: string[];
    recommendation: string;
    scenario_availability: string;
  }>;
};

function knowledgeDir(): string {
  return join(getDataRoot(), "knowledge");
}

function isPrescription(entry: KnowledgeEntry): boolean {
  return entry.entry_id.startsWith(PRESCRIPTION_PREFIX);
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
    entries: registry.entries.filter((entry) => !isPrescription(entry)).map((entry) => ({
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

/** The recommended action line: the cue when present, else the definition. */
function prescriptionRecommendation(entry: KnowledgeEntryV2): string {
  const cue = entry.cue;
  const raw = cue !== undefined && cue !== "not_applicable" ? cue.text : entry.definition.text;
  return raw.replace(/^推荐做法（[^）]*）：/, "");
}

/** Scenario availability codes extracted from the scope text ("场景可用性：..."). */
function prescriptionScenarioAvailability(entry: KnowledgeEntryV2): string {
  const match = /场景可用性：(.+)/.exec(entry.scope.text);
  if (!match) return "method";
  const text = match[1];
  const codes: string[] = [];
  if (text.includes("本机可开") || text.includes("本机已装")) codes.push("local");
  if (text.includes("官方库")) codes.push("official");
  if (text.includes("未解析") || text.includes("存疑")) codes.push("unresolved");
  return codes.length > 0 ? codes.join("+") : "method";
}

function buildPrescriptionIndex(registry: KnowledgeRegistry): KnowledgePrescriptionIndex {
  return {
    schema_version: PRESCRIPTION_INDEX_SCHEMA_VERSION,
    registry_version: registry.registry_version,
    entries: registry.entries.filter(isPrescription).map((entry) => {
      // prescription.* entries always carry the v2/v3 capability fields.
      const v2 = entry as KnowledgeEntryV2;
      return {
        entry_ref: entryRef(entry),
        entry_file: entryFileName(entry),
        status: entry.status,
        topics: entry.topics,
        signals: entry.signals,
        metric_refs: entry.metric_refs,
        recommendation: prescriptionRecommendation(v2),
        scenario_availability: prescriptionScenarioAvailability(v2),
      };
    }),
  };
}

type RefIndex = { schema_version: string; registry_version: string; entries: Array<{ entry_ref: string }> };

function refsMatch(existing: unknown, index: RefIndex): boolean {
  if (!existing || typeof existing !== "object") return false;
  const current = existing as { schema_version?: unknown; registry_version?: unknown; entries?: unknown };
  if (current.schema_version !== index.schema_version || current.registry_version !== index.registry_version) return false;
  if (!Array.isArray(current.entries) || current.entries.length !== index.entries.length) return false;
  // Compare the full ordered ref list, not just the count: same-length
  // indexes from different registries must trigger a rebuild.
  return index.entries.every((entry, i) => (
    (current.entries as Array<{ entry_ref?: unknown }>)[i]?.entry_ref === entry.entry_ref
  ));
}

/** True when the on-disk directory already reflects exactly this registry. */
function isCurrentKnowledgeDir(
  root: string,
  index: KnowledgeIndex,
  prescriptions: KnowledgePrescriptionIndex,
): boolean {
  const indexPath = join(root, "index.json");
  const prescriptionsPath = join(root, "prescriptions.json");
  if (!existsSync(indexPath) || !existsSync(prescriptionsPath)) return false;
  let existingIndex: unknown;
  let existingPrescriptions: unknown;
  try {
    existingIndex = JSON.parse(readFileSync(indexPath, "utf-8"));
    existingPrescriptions = JSON.parse(readFileSync(prescriptionsPath, "utf-8"));
  } catch {
    return false;
  }
  if (!refsMatch(existingIndex, index) || !refsMatch(existingPrescriptions, prescriptions)) return false;
  // Every entry still needs its full entry file on disk.
  return [...index.entries, ...prescriptions.entries]
    .every((entry) => existsSync(join(root, "entries", entry.entry_file)));
}

export function materializeKnowledgeDir(registryVersion?: string): void {
  const registry = loadKnowledgeRegistry(registryVersion);
  const index = buildIndex(registry);
  const prescriptions = buildPrescriptionIndex(registry);
  const root = knowledgeDir();
  if (isCurrentKnowledgeDir(root, index, prescriptions)) return;

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
  writeFileSync(join(root, "prescriptions.json"), JSON.stringify(prescriptions, null, 2), "utf-8");
}
