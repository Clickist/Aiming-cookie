/**
 * Active knowledge resolution for the TS/sidecar side (kb-sdk plan C4).
 *
 * DATA_ROOT/config/knowledge.json selects the active knowledge base, mirroring
 * the Python-side knowledge_active module. A missing config is the default
 * official state (not a fallback); a broken config or an `active` pointer that
 * cannot be resolved degrades to official with a recorded fallback reason, and
 * a pack registry that fails to load degrades the same way with a
 * console.error so the sidecar startup log shows why the official base came
 * back. TS never reads or evaluates mapping documents — registry only.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { getDataRoot } from "./app-data.ts";
import { isRecord } from "./contracts.ts";
import {
  loadKnowledgeRegistry,
  loadKnowledgeRegistryFromPath,
  type KnowledgeRegistry,
} from "./knowledge-registry.ts";

const CONFIG_SCHEMA_VERSION = "knowledge_config.v1";
const ACTIVE_OFFICIAL = "official";

export type ActiveKnowledge =
  | { mode: "official" }
  | { mode: "pack"; packId: string; registryPath: string };

let lastFallbackReason: string | null = null;

function recordFallback(reason: string): void {
  lastFallbackReason = reason;
}

/** Fallback reason of the most recent resolve/load call (null = no fallback). */
export function lastActiveKnowledgeFallbackReason(): string | null {
  return lastFallbackReason;
}

function readConfig(): Record<string, unknown> | null {
  const path = join(getDataRoot(), "config", "knowledge.json");
  let raw: string;
  try {
    raw = readFileSync(path, "utf-8");
  } catch {
    return null; // Missing config is the default official state, not a fallback.
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    recordFallback("knowledge config is not valid JSON");
    return null;
  }
  if (!isRecord(parsed)) {
    recordFallback("knowledge config is not a JSON object");
    return null;
  }
  if (parsed.schema_version !== CONFIG_SCHEMA_VERSION) {
    recordFallback("knowledge config schema_version is unsupported");
    return null;
  }
  return parsed;
}

function installedPackIds(config: Record<string, unknown>): Set<string> {
  const installed = config.installed;
  if (!Array.isArray(installed)) return new Set();
  return new Set(
    installed
      .filter(isRecord)
      .map((item) => item.pack_id)
      .filter((packId): packId is string => typeof packId === "string"),
  );
}

export function resolveActiveKnowledge(): ActiveKnowledge {
  lastFallbackReason = null;
  const config = readConfig();
  if (!config) return { mode: "official" };
  const active = config.active;
  if (typeof active !== "string" || active.length === 0) {
    recordFallback("knowledge config active field is invalid");
    return { mode: "official" };
  }
  if (active === ACTIVE_OFFICIAL) return { mode: "official" };
  const packDir = join(getDataRoot(), "knowledge-packs", active);
  if (!installedPackIds(config).has(active) || !existsSync(packDir)) {
    recordFallback(`active knowledge pack is not installed: ${active}`);
    return { mode: "official" };
  }
  const registryPath = join(packDir, "knowledge", "registry.json");
  if (!existsSync(registryPath)) {
    recordFallback(`active knowledge pack registry is missing: ${active}`);
    return { mode: "official" };
  }
  return { mode: "pack", packId: active, registryPath };
}

export function loadActiveKnowledgeRegistry(): KnowledgeRegistry {
  lastFallbackReason = null;
  const active = resolveActiveKnowledge();
  if (active.mode === "official") return loadKnowledgeRegistry();
  try {
    return loadKnowledgeRegistryFromPath(active.registryPath);
  } catch (error) {
    recordFallback(`active knowledge pack registry is invalid: ${active.packId}`);
    console.error(
      `[coach] active knowledge pack registry is invalid (${active.packId}), `
      + `falling back to official: ${error instanceof Error ? error.message : String(error)}`,
    );
    return loadKnowledgeRegistry();
  }
}

/** display_name of an installed pack from the active config (Coach source attribution). */
export function activePackDisplayName(packId: string): string | undefined {
  const config = readConfig();
  if (!config || !Array.isArray(config.installed)) return undefined;
  const entry = config.installed.find((item) => isRecord(item) && item.pack_id === packId);
  if (!entry || !isRecord(entry)) return undefined;
  const displayName = entry.display_name;
  return typeof displayName === "string" && displayName.length > 0 ? displayName : undefined;
}
