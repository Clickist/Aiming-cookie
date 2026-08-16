/**
 * Native KovaaK benchmark score commands.
 *
 * Ports the Python kovaak_benchmark_provider + kovaak_benchmark_service flow:
 * makes HTTP requests to KovaaK's Viscose S2 benchmark endpoint, normalizes
 * the response using the shared course catalog, and returns a bounded score
 * summary. Uses Node's built-in fetch().
 *
 * kovaak_scores.refresh_connected — reads the owner's Steam ID from
 * kovaak_connections, fetches scores, optionally writes records to
 * benchmark_records, and returns a bounded summary.
 *
 * kovaak_scores.lookup — accepts a literal 17-digit Steam ID or a
 * steamcommunity.com profile URL pasted by the user (preferred path), or a
 * temporary steam_profile:N reference resolved via the optional
 * temporary_profile_refs map, fetches scores, and returns a bounded summary.
 *
 * If the HTTP call or normalization fails, returns an `unavailable` result
 * with a bounded score summary, matching the Python fallback behaviour.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getConfigDir } from "./app-data.ts";

// ── Types ──────────────────────────────────────────────────────────────

type AnyDict = Record<string, any>;

export type NativeScoreResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

// ── Paths ──────────────────────────────────────────────────────────────

const _dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(_dirname, "..", "..", "..");
const CATALOG_PATH = resolve(REPO_ROOT, "knowledge", "benchmarks", "viscose-s2.v1.json");

// ── Constants (mirror Python kovaak_benchmark_provider) ────────────────

const BENCHMARK_URL =
  "https://kovaaks.com/webapp-backend/benchmarks/player-progress-rank-benchmark";
const DIFFICULTIES = ["easier", "medium"] as const;
const RANK_MAX = 9;

// ── Catalog ────────────────────────────────────────────────────────────

type Catalog = {
  schema_version: string;
  catalog_ref: string;
  catalog_version: string;
  benchmark_ids: Record<string, number>;
  rank_names: string[];
  pairs: Array<{
    pair_id: string;
    category: string;
    subcategory: string;
    easier: { scenario_id: string; scenario_name: string };
    medium: { scenario_id: string; scenario_name: string };
  }>;
};

let _catalogCache: Catalog | null = null;

function loadCatalog(): Catalog | null {
  if (_catalogCache !== null) return _catalogCache;
  if (!existsSync(CATALOG_PATH)) return null;
  try {
    const raw = readFileSync(CATALOG_PATH, "utf-8");
    _catalogCache = JSON.parse(raw) as Catalog;
    return _catalogCache;
  } catch {
    return null;
  }
}

// ── HTTP fetch ─────────────────────────────────────────────────────────

async function fetchBenchmark(
  benchmarkId: number,
  steamId: string,
): Promise<unknown> {
  const url = new URL(BENCHMARK_URL);
  url.searchParams.set("benchmarkId", String(benchmarkId));
  url.searchParams.set("steamId", steamId);
  url.searchParams.set("page", "0");
  url.searchParams.set("max", "100");
  const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// ── Normalization (port of Python _normalize_difficulty + _score + _rank) ─

function normalizeRank(value: unknown, field: string): number {
  if (typeof value === "boolean" || typeof value !== "number" || !Number.isInteger(value) || value < 0 || value > RANK_MAX) {
    throw new Error(`invalid ${field}`);
  }
  return value;
}

function normalizeScore(value: unknown): number {
  if (typeof value === "boolean" || typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error("invalid score");
  }
  return value / 100;
}

type NormalizedDifficulty = {
  overall_rank: number;
  scenarios: Array<{
    pair_id: string;
    scenario_id: string;
    scenario_name: string;
    score: number;
    scenario_rank: number;
  }>;
};

function normalizeDifficulty(
  payload: unknown,
  difficulty: string,
  catalog: Catalog,
): NormalizedDifficulty {
  if (typeof payload !== "object" || payload === null) throw new Error("invalid benchmark payload");
  const p = payload as AnyDict;
  const categories = p.categories;
  if (typeof categories !== "object" || categories === null) throw new Error("benchmark categories are unavailable");

  // Flatten all scenarios across categories.
  const rawScenarios: Map<string, AnyDict> = new Map();
  for (const category of Object.values(categories)) {
    if (typeof category !== "object" || category === null) throw new Error("invalid benchmark category");
    const scenarios = (category as AnyDict).scenarios;
    if (typeof scenarios !== "object" || scenarios === null) throw new Error("benchmark scenarios are unavailable");
    for (const [name, entry] of Object.entries(scenarios)) {
      if (rawScenarios.has(name)) throw new Error("duplicate benchmark scenario");
      rawScenarios.set(name, entry as AnyDict);
    }
  }

  // Build expected scenario-name → pair mapping for this difficulty.
  const expected = new Map<string, typeof catalog.pairs[number]>();
  for (const pair of catalog.pairs) {
    expected.set(pair[difficulty].scenario_name, pair);
  }

  // Validate scenario names match the catalog exactly.
  const rawNames = new Set(rawScenarios.keys());
  const expectedNames = new Set(expected.keys());
  if (rawNames.size !== expectedNames.size || ![...rawNames].every((n) => expectedNames.has(n))) {
    throw new Error("benchmark scenarios do not match Viscose S2");
  }

  const scenarios: NormalizedDifficulty["scenarios"] = [];
  for (const pair of catalog.pairs) {
    const scenario = pair[difficulty];
    const raw = rawScenarios.get(scenario.scenario_name);
    if (typeof raw !== "object" || raw === null) throw new Error("invalid benchmark scenario");
    scenarios.push({
      pair_id: pair.pair_id,
      scenario_id: scenario.scenario_id,
      scenario_name: scenario.scenario_name,
      score: normalizeScore(raw.score),
      scenario_rank: normalizeRank(raw.scenario_rank, "scenario rank"),
    });
  }

  return {
    overall_rank: normalizeRank(p.overall_rank, "overall rank"),
    scenarios,
  };
}

// ── Bounded score summary (port of Python _bounded_kovaak_score_summary) ─

function observedAt(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function buildScoreSummary(
  catalog: Catalog,
  difficulties: Record<string, NormalizedDifficulty>,
): AnyDict {
  const now = observedAt();

  // Build stages from completion + overall_rank.
  const stages: AnyDict[] = [];
  for (const stage of DIFFICULTIES) {
    const diff = difficulties[stage];
    stages.push({
      stage,
      completed: diff.scenarios.filter((s) => s.score > 0).length,
      required: diff.scenarios.length,
      rank: diff.overall_rank,
      rank_name: catalog.rank_names[diff.overall_rank],
    });
  }

  // Build items from scenarios.
  const items: AnyDict[] = [];
  for (const stage of DIFFICULTIES) {
    const diff = difficulties[stage];
    for (const scenario of diff.scenarios) {
      const pair = catalog.pairs.find((p) => p.pair_id === scenario.pair_id);
      items.push({
        stage,
        name: scenario.scenario_name,
        category: pair?.category ?? "",
        subcategory: pair?.subcategory ?? "",
        score: scenario.score,
        item_rank: scenario.scenario_rank,
        item_rank_name: catalog.rank_names[scenario.scenario_rank],
        completed: scenario.score > 0,
      });
    }
  }

  // Sort items and take top 8 (matching Python _bounded_kovaak_score_summary).
  const sortedItems = items
    .sort((a, b) => a.item_rank - b.item_rank || a.score - b.score || a.stage.localeCompare(b.stage) || a.name.localeCompare(b.name))
    .slice(0, 8);

  return {
    schema_version: "kovaak_scores.v1",
    availability: "available",
    observed_at: now,
    stages,
    items: sortedItems,
  };
}

// ── Snapshot fetch + project ───────────────────────────────────────────

async function fetchAndNormalize(
  steamId: string,
  catalog: Catalog,
): Promise<Record<string, NormalizedDifficulty> | null> {
  try {
    const payloads = await Promise.all(
      DIFFICULTIES.map((d) => fetchBenchmark(catalog.benchmark_ids[d], steamId)),
    );
    const difficulties: Record<string, NormalizedDifficulty> = {};
    for (let i = 0; i < DIFFICULTIES.length; i++) {
      difficulties[DIFFICULTIES[i]] = normalizeDifficulty(payloads[i], DIFFICULTIES[i], catalog);
    }
    return difficulties;
  } catch {
    return null;
  }
}

// ── Steam ID helpers ───────────────────────────────────────────────────

const STEAM_ID_RE = /^\d{17}$/;
const STEAM_PROFILE_URL_RE = /^https:\/\/steamcommunity\.com\/profiles\/(\d{17})\/?$/;

function normalizeSteamProfileInput(value: string): string | null {
  if (STEAM_ID_RE.test(value)) return value;
  const match = STEAM_PROFILE_URL_RE.exec(value);
  return match ? match[1] : null;
}

// ── Public API ─────────────────────────────────────────────────────────

export function isNativeKovaakScoreCommand(commandName: string): boolean {
  return commandName === "kovaak_scores.lookup" || commandName === "kovaak_scores.refresh_connected";
}

export async function executeNativeKovaakScore(
  commandName: string,
  params: AnyDict,
  ownerId: string,
  temporaryProfileRefs?: Map<string, string>,
): Promise<NativeScoreResult> {
  if (commandName === "kovaak_scores.lookup") {
    return executeLookup(params, temporaryProfileRefs);
  }
  if (commandName === "kovaak_scores.refresh_connected") {
    return executeRefreshConnected(ownerId);
  }
  return { status: "failed", warning_or_error: { code: "unknown_command", message: `${commandName} is not a kovaak_scores command` } };
}

async function executeLookup(
  params: AnyDict,
  temporaryProfileRefs?: Map<string, string>,
): Promise<NativeScoreResult> {
  const profileRef = params.profile_ref;
  if (typeof profileRef !== "string") {
    return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "kovaak_scores.lookup accepts only profile_ref" } };
  }

  // Preferred path: a literal Steam ID or profile URL provided by the user.
  let normalizedId = normalizeSteamProfileInput(profileRef);

  // Legacy path: an opaque steam_profile:N ref resolved through the optional
  // turn-scoped map (kept for callers that still supply one).
  if (!normalizedId && /^steam_profile:[1-9]\d*$/.test(profileRef)) {
    const steamId = temporaryProfileRefs?.get(profileRef);
    if (!steamId) {
      return {
        status: "unavailable",
        warning_or_error: {
          code: "temporary_profile_unavailable",
          message: "this temporary profile is not available in the current Coach turn",
        },
      };
    }
    normalizedId = normalizeSteamProfileInput(steamId);
  }

  if (!normalizedId) {
    return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "profile_ref must be a 17-digit Steam ID, a steamcommunity profile URL, or a steam_profile ref" } };
  }

  const catalog = loadCatalog();
  if (!catalog) {
    return {
      status: "unavailable",
      warning_or_error: { code: "kovaak_scores_unavailable", message: "KovaaK scores are temporarily unavailable" },
    };
  }

  const difficulties = await fetchAndNormalize(normalizedId, catalog);
  if (!difficulties) {
    return {
      status: "unavailable",
      warning_or_error: { code: "kovaak_scores_unavailable", message: "KovaaK scores are temporarily unavailable" },
    };
  }

  const summary = buildScoreSummary(catalog, difficulties);
  return { status: "succeeded", result_ref: "kovaak_scores:lookup", result: summary };
}

async function executeRefreshConnected(
  ownerId: string,
): Promise<NativeScoreResult> {
  // Read the owner's connected Steam ID from config file.
  const connPath = join(getConfigDir(), "kovaak-connection.json");
  let steamId: string | null = null;
  if (existsSync(connPath)) {
    try {
      const conn = JSON.parse(readFileSync(connPath, "utf-8"));
      steamId = typeof conn.steam_id === "string" ? conn.steam_id : null;
    } catch { /* ignore */ }
  }
  if (!steamId) {
    return {
      status: "unavailable",
      warning_or_error: { code: "connected_account_unavailable", message: "connect your KovaaK profile first" },
    };
  }

  const catalog = loadCatalog();
  if (!catalog) {
    return {
      status: "unavailable",
      warning_or_error: { code: "kovaak_scores_unavailable", message: "KovaaK scores are temporarily unavailable" },
    };
  }

  const difficulties = await fetchAndNormalize(steamId, catalog);
  if (!difficulties) {
    return {
      status: "unavailable",
      warning_or_error: { code: "kovaak_scores_unavailable", message: "KovaaK scores are temporarily unavailable" },
    };
  }

  const summary = buildScoreSummary(catalog, difficulties);
  return { status: "succeeded", result_ref: "kovaak_scores:connected", result: summary };
}
