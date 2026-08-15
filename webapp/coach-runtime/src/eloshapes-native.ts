/**
 * Native EloShapes mouse catalog query.
 *
 * Ports Python eloshapes_query.query_mice — reads the same artifact JSON files
 * and applies the same filters and projection. No DB access required; this
 * is a pure file-read + filter operation that eliminates the HTTP bridge.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ── Types ──────────────────────────────────────────────────────────────

type AnyDict = Record<string, any>;

export type NativeCommandResult = {
  status: "succeeded" | "failed";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

// ── Paths ──────────────────────────────────────────────────────────────

const _dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(_dirname, "..", "..", "..");
export const CATALOG_PATH = resolve(
  REPO_ROOT,
  "artifacts", "eloshapes", "snapshots",
  "eloshapes_mouse_catalog_2026-07-31T211736Z.json",
);
const MAPPING_PATH = resolve(
  REPO_ROOT,
  "artifacts", "eloshapes", "marketplace-mapping",
  "marketplace-mapping.json",
);

const SNAPSHOT_SOURCE = "eloshapes_mouse_catalog_2026-07-31T211736Z";

// ── Cached data ────────────────────────────────────────────────────────

let _catalogCache: AnyDict[] | null = null;
let _mappingCache: Map<number, AnyDict> | null = null;

function loadCatalog(): AnyDict[] {
  if (_catalogCache !== null) return _catalogCache;
  if (!existsSync(CATALOG_PATH)) {
    // Confirmed absent on this call — do not cache: caching the empty array
    // would keep every later query catalog_unavailable even after the
    // snapshot appears (Bug 9 of the 2026-08-16 deep test).
    return [];
  }
  const raw = readFileSync(CATALOG_PATH, "utf-8");
  _catalogCache = JSON.parse(raw) as AnyDict[];
  return _catalogCache;
}

function loadMapping(): Map<number, AnyDict> {
  if (_mappingCache !== null) return _mappingCache;
  _mappingCache = new Map();
  if (!existsSync(MAPPING_PATH)) return _mappingCache;
  const raw = readFileSync(MAPPING_PATH, "utf-8");
  const data = JSON.parse(raw) as AnyDict;
  const entries = Array.isArray(data) ? data : (data.entries ?? []);
  for (const entry of entries) {
    if (
      entry && typeof entry === "object" &&
      entry.mapping_status === "identity_verified_candidate" &&
      typeof entry.eloshapes_id === "number"
    ) {
      _mappingCache.set(entry.eloshapes_id, entry);
    }
  }
  return _mappingCache;
}

// ── Projection ─────────────────────────────────────────────────────────

const PROJECTION_LABELS = [
  "shape", "weight", "length", "width", "height",
  "size_category", "front_flare", "side_curvature", "hump_placement",
  "thumb_rest", "ring_finger_rest", "hand_compatibility",
  "is_wired", "is_wireless_2_4_ghz", "is_bluetooth",
] as const;

function projectMouse(mouse: AnyDict, eloshapesId: number, mapping: Map<number, AnyDict>): AnyDict {
  const result: AnyDict = {
    eloshapes_id: eloshapesId,
    brand: ((mouse.general__brand_names as string[]) ?? []).join(" "),
    model: mouse.general__model ?? null,
    variant: mouse.general__variant ?? null,
  };
  for (const label of PROJECTION_LABELS) {
    result[label] = mouse[`mouse__${label}`] ?? null;
  }
  const jdEntry = mapping.get(eloshapesId);
  if (jdEntry) {
    result.jd_product_id = jdEntry.product_id ?? null;
    result.jd_canonical_url = jdEntry.canonical_url ?? null;
    result.jd_match_confidence = jdEntry.match_confidence ?? null;
  } else {
    result.jd_product_id = null;
  }
  return result;
}

// ── Query ──────────────────────────────────────────────────────────────

export function isNativeEloshapesCommand(commandName: string): boolean {
  return commandName === "eloshapes.query";
}

export function executeNativeEloshapes(
  commandName: string,
  params: AnyDict,
): NativeCommandResult {
  if (commandName !== "eloshapes.query") {
    return { status: "failed", warning_or_error: { code: "unknown_command", message: `${commandName} is not an eloshapes command` } };
  }

  const allowed = new Set([
    "weight_max", "size_category", "shape", "front_flare",
    "side_curvature", "hump_placement", "hand_compatibility",
    "brand_search", "model_search", "limit",
  ]);
  // Unknown filters must be rejected, not silently dropped: a dropped filter
  // answers a different question than the one asked (Bug 6).
  const unknownKeys = Object.keys(params).filter((key) => !allowed.has(key));
  if (unknownKeys.length > 0) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: `unsupported filters: ${unknownKeys.map((key) => `"${key}"`).join(", ")}; allowed: ${[...allowed].join(", ")}`,
      },
    };
  }
  const filtered: AnyDict = {};
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) {
      filtered[key] = value;
    }
  }

  try {
    const catalog = loadCatalog();
    const mapping = loadMapping();

    if (catalog.length === 0) {
      return { status: "failed", warning_or_error: { code: "catalog_unavailable", message: "EloShapes catalog is not available" } };
    }

    const {
      weight_max, size_category, shape, front_flare,
      side_curvature, hump_placement, hand_compatibility,
      brand_search, model_search,
    } = filtered;
    const limit = typeof filtered.limit === "number" && filtered.limit > 0
      ? Math.min(Math.floor(filtered.limit), 100)
      : 20;

    const sizeSet: Set<string> | null = size_category
      ? new Set(Array.isArray(size_category) ? size_category : [size_category])
      : null;
    const brandLower = typeof brand_search === "string" ? brand_search.toLowerCase().trim() : null;
    const modelLower = typeof model_search === "string" ? model_search.toLowerCase().trim() : null;

    const candidates: AnyDict[] = [];
    for (const mouse of catalog) {
      if (mouse.general__category !== "mouse") continue;

      const weight = mouse.mouse__weight;
      if (weight_max !== undefined && weight_max !== null && (weight === null || weight === undefined || weight > weight_max)) continue;
      if (sizeSet && !sizeSet.has(mouse.mouse__size_category)) continue;
      if (shape && mouse.mouse__shape !== shape) continue;
      if (front_flare && mouse.mouse__front_flare !== front_flare) continue;
      if (side_curvature && mouse.mouse__side_curvature !== side_curvature) continue;
      if (hump_placement && mouse.mouse__hump_placement !== hump_placement) continue;
      if (hand_compatibility && mouse.mouse__hand_compatibility !== hand_compatibility) continue;

      const brand = ((mouse.general__brand_names as string[]) ?? []).join(" ");
      const model = mouse.general__model ?? "";
      if (brandLower && !brand.toLowerCase().includes(brandLower)) continue;
      if (modelLower && !`${brand} ${model}`.toLowerCase().includes(modelLower)) continue;

      const eloId = mouse.general__id;
      candidates.push(projectMouse(mouse, eloId, mapping));
    }

    candidates.sort((a, b) => {
      const wa = a.weight ?? 999;
      const wb = b.weight ?? 999;
      if (wa !== wb) return wa - wb;
      return String(a.brand ?? "").localeCompare(String(b.brand ?? ""));
    });

    return {
      status: "succeeded",
      result: {
        schema_version: "eloshapes_query.v1",
        total_matches: candidates.length,
        returned: Math.min(candidates.length, limit),
        snapshot_source: SNAPSHOT_SOURCE,
        mice: candidates.slice(0, limit),
      },
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "eloshapes query failed";
    return { status: "failed", warning_or_error: { code: "native_error", message } };
  }
}
