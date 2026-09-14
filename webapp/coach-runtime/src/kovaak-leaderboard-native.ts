/**
 * Native KovaaK scenario leaderboard command.
 *
 * kovaak_leaderboard.lookup — answers "how good is this score / where do I
 * stand" with the official global rank and a percentile, instead of only the
 * player's own Viscose S2 stage (which kovaak_scores.* covers).
 *
 * This module also exports `searchScenarios` (backing the `scenario.search`
 * product command): a fuzzy search of the official scenario library through
 * the same /scenario/popular endpoint, so the Coach can recommend scenarios
 * that are not installed locally.
 *
 * The public kovaaks.com/webapp-backend endpoints (no login) are:
 *   GET /scenario/details?leaderboardId=   → scenario name + aim type
 *   GET /scenario/popular?...scenarioNameSearch= → name → leaderboardId
 *   GET /leaderboard/scores/global?leaderboardId=&page=&max= → descending
 *                                                    score board (max <= 100)
 *   GET /leaderboard/global/search/account-names?username=  → name → steamId
 *
 * A browser User-Agent header is mandatory: Cloudflare rejects the default
 * Node/undici UA. `max=100` is a hard upstream cap (max=101 → HTTP 400).
 *
 * Locating the queried player:
 *   - `score`: binary-search the descending board by page (~9 requests) and
 *     derive the rank that score would take.
 *   - `profile_ref`: kovaaks' own board search (`usernameSearch`) matches the
 *     Steam persona name, so the display name is read from the Steam profile
 *     and used to pull the player's row (with the official `rank` field).
 *     If the persona search misses, the account-names route resolves the
 *     exact steamId → KovaaKs username and we search again.
 *   - neither: return board stats only (top score, median, total), no rank.
 *
 * Failure modes follow the existing three-state convention: `failed` for bad
 * parameters, `unavailable` when the upstream cannot be reached or no longer
 * matches the expected shape.
 */
import { normalizeSteamProfileInput } from "./kovaak-scores-native.ts";

type AnyDict = Record<string, any>;

export type NativeKovaakLeaderboardResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

// ── Constants ──────────────────────────────────────────────────────────

const BACKEND_BASE = "https://kovaaks.com/webapp-backend";
const STEAM_PROFILE_BASE = "https://steamcommunity.com/profiles";
const BROWSER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const LEADERBOARD_PAGE_MAX = 100;
const HTTP_TIMEOUT_MS = 15_000;
const PERCENTILE_DECIMALS = 3;

const UNAVAILABLE: NativeKovaakLeaderboardResult = {
  status: "unavailable",
  warning_or_error: {
    code: "kovaak_leaderboard_unavailable",
    message: "KovaaK 榜单暂时不可用",
  },
};

// ── HTTP helpers ───────────────────────────────────────────────────────

async function requestJson(url: string): Promise<AnyDict | AnyDict[]> {
  const response = await fetch(url, {
    headers: { "User-Agent": BROWSER_UA, Accept: "application/json" },
    signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = (await response.json()) as AnyDict | AnyDict[];
  // The backend answers 200 + {"error": "..."} for some rejections; treat any
  // error envelope as a failed request rather than a malformed board.
  if (payload && !Array.isArray(payload) && typeof payload.error === "string") {
    throw new Error(`upstream error: ${payload.error.slice(0, 120)}`);
  }
  return payload;
}

type BoardEntry = { steamId: string; score: number; rank: number };

function normalizeBoardEntry(value: unknown): BoardEntry | null {
  if (typeof value !== "object" || value === null) return null;
  const entry = value as AnyDict;
  const rank = entry.rank;
  const score = entry.score;
  if (typeof rank !== "number" || !Number.isInteger(rank) || rank < 1) return null;
  if (typeof score !== "number" || !Number.isFinite(score) || score < 0) return null;
  const steamId = typeof entry.steamId === "string" ? entry.steamId : "";
  return { steamId, score, rank };
}

type BoardPage = { total: number; entries: BoardEntry[] };

async function fetchBoardPage(
  leaderboardId: number,
  page: number,
  usernameSearch?: string,
): Promise<BoardPage> {
  const url = new URL(`${BACKEND_BASE}/leaderboard/scores/global`);
  url.searchParams.set("leaderboardId", String(leaderboardId));
  url.searchParams.set("page", String(page));
  url.searchParams.set("max", String(LEADERBOARD_PAGE_MAX));
  if (usernameSearch) url.searchParams.set("usernameSearch", usernameSearch);
  const payload = (await requestJson(url.toString())) as AnyDict;
  const total = payload.total;
  if (typeof total !== "number" || !Number.isInteger(total) || total < 0) {
    throw new Error("invalid leaderboard total");
  }
  const rawData = Array.isArray(payload.data) ? payload.data : [];
  const entries = rawData
    .map(normalizeBoardEntry)
    .filter((entry): entry is BoardEntry => entry !== null);
  return { total, entries };
}

async function fetchScenarioDetails(
  leaderboardId: number,
): Promise<{ scenarioName: string; aimType: string | null } | null> {
  const url = new URL(`${BACKEND_BASE}/scenario/details`);
  url.searchParams.set("leaderboardId", String(leaderboardId));
  const payload = (await requestJson(url.toString())) as AnyDict;
  const scenarioName = typeof payload.scenarioName === "string" ? payload.scenarioName : "";
  if (!scenarioName) return null;
  const aimType = typeof payload.aimType === "string" && payload.aimType ? payload.aimType : null;
  return { scenarioName, aimType };
}

type ScenarioCatalogEntry = {
  leaderboardId: number;
  scenarioName: string;
  aimType: string | null;
  plays: number | null;
  entries: number | null;
  topScore: number | null;
};

function normalizeScenarioEntry(value: unknown): ScenarioCatalogEntry | null {
  if (typeof value !== "object" || value === null) return null;
  const entry = value as AnyDict;
  const leaderboardId = entry.leaderboardId;
  const name = entry.scenarioName;
  if (typeof leaderboardId !== "number" || !Number.isInteger(leaderboardId) || leaderboardId <= 0) {
    return null;
  }
  if (typeof name !== "string" || !name) return null;
  const scenario = typeof entry.scenario === "object" && entry.scenario !== null ? (entry.scenario as AnyDict) : {};
  const aimType = typeof scenario.aimType === "string" && scenario.aimType ? scenario.aimType : null;
  const counts = typeof entry.counts === "object" && entry.counts !== null ? (entry.counts as AnyDict) : {};
  const topScore = typeof entry.topScore === "object" && entry.topScore !== null ? (entry.topScore as AnyDict) : {};
  const plays =
    typeof counts.plays === "number" && Number.isFinite(counts.plays) && counts.plays >= 0 ? counts.plays : null;
  const entries =
    typeof counts.entries === "number" && Number.isFinite(counts.entries) && counts.entries >= 0
      ? counts.entries
      : null;
  const top =
    typeof topScore.score === "number" && Number.isFinite(topScore.score) ? topScore.score : null;
  return { leaderboardId, scenarioName: name, aimType, plays, entries, topScore: top };
}

/**
 * scenario/popular is the same lookup kovaaks.com itself uses for the "find a
 * scenario" box. The response carries the scenario name, its leaderboardId,
 * aim type, play/entry counts and top score.
 */
async function fetchScenarioCatalog(scenarioNameSearch: string, max: number): Promise<ScenarioCatalogEntry[]> {
  const url = new URL(`${BACKEND_BASE}/scenario/popular`);
  url.searchParams.set("page", "0");
  url.searchParams.set("max", String(max));
  url.searchParams.set("scenarioNameSearch", scenarioNameSearch);
  const payload = (await requestJson(url.toString())) as AnyDict;
  const data = Array.isArray(payload.data) ? payload.data : [];
  return data
    .map(normalizeScenarioEntry)
    .filter((item): item is ScenarioCatalogEntry => item !== null);
}

type ResolvedScenario = { leaderboardId: number; scenarioName: string; aimType: string | null };

/**
 * Single-name resolution for kovaak_leaderboard.lookup. Prefer an exact
 * (case-insensitive) name match, then the first result that carries a
 * leaderboardId: a scenario name has several board variants (e.g.
 * "Smoothsphere Viscose" vs "... Easier"), so "first" is only a fallback.
 */
async function resolveScenarioByName(scenarioName: string): Promise<ResolvedScenario | null> {
  const candidates = await fetchScenarioCatalog(scenarioName, 20);
  const wanted = scenarioName.trim().toLowerCase();
  const match =
    candidates.find((item) => item.scenarioName.trim().toLowerCase() === wanted) ?? candidates[0] ?? null;
  if (!match) return null;
  return { leaderboardId: match.leaderboardId, scenarioName: match.scenarioName, aimType: match.aimType };
}

/** Product-facing projection of the official scenario library search. */
export type NativeScenarioSearchEntry = {
  scenario_name: string;
  leaderboard_id: number;
  aim_type: string | null;
  plays: number | null;
  entries: number | null;
  top_score: number | null;
};

export const SCENARIO_SEARCH_DEFAULT_LIMIT = 10;
export const SCENARIO_SEARCH_MAX_LIMIT = 20;

/**
 * scenario.search backing call: fuzzy search of the official KovaaKs scenario
 * library through the same /scenario/popular endpoint (scenarioNameSearch is a
 * substring match). Throws on transport/upstream failure so the command layer
 * can surface a readable `unavailable` instead of silently returning nothing.
 */
export async function searchScenarios(query: string, limit: number): Promise<NativeScenarioSearchEntry[]> {
  const entries = await fetchScenarioCatalog(query, limit);
  return entries.map((entry) => ({
    scenario_name: entry.scenarioName,
    leaderboard_id: entry.leaderboardId,
    aim_type: entry.aimType,
    plays: entry.plays,
    entries: entry.entries,
    top_score: entry.topScore,
  }));
}

// ── Steam display name (for the profile_ref lookup path) ───────────────

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'");
}

/** Read the Steam persona name; kovaaks' board search matches on it. */
async function fetchSteamPersonaName(steamId: string): Promise<string | null> {
  const response = await fetch(`${STEAM_PROFILE_BASE}/${steamId}`, {
    headers: { "User-Agent": BROWSER_UA },
    signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
  });
  if (!response.ok) return null;
  const html = await response.text();
  const match = /class="actual_persona_name">([^<]*)</.exec(html);
  if (!match) return null;
  const name = decodeHtmlEntities(match[1]).trim();
  return name || null;
}

// ── Rank locators ──────────────────────────────────────────────────────

type ScoreLocation = { rank: number | null; boardScore: number | null; percentileTop: number | null };

function percentileFromRank(rank: number, total: number): number {
  const factor = 10 ** PERCENTILE_DECIMALS;
  return Math.round((rank / total) * 100 * factor) / factor;
}

/**
 * Find the rank a given score would take on the descending board. Ranks are
 * 1-based, so rank = (#entries with score strictly above) + 1, clamped to the
 * board size. Binary-searches page summaries, then scans one page.
 */
async function locateByScore(
  leaderboardId: number,
  total: number,
  score: number,
): Promise<number> {
  if (total <= 0) return 1;
  const lastPage = Math.floor((total - 1) / LEADERBOARD_PAGE_MAX);
  const cache = new Map<number, BoardEntry[]>();
  const page = async (index: number): Promise<BoardEntry[]> => {
    const hit = cache.get(index);
    if (hit) return hit;
    const fresh = (await fetchBoardPage(leaderboardId, index)).entries;
    cache.set(index, fresh);
    return fresh;
  };

  let lo = 0;
  let hi = lastPage;
  let boundary = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const entries = await page(mid);
    const base = mid * LEADERBOARD_PAGE_MAX;
    if (entries.length === 0) {
      hi = mid - 1;
      continue;
    }
    const first = entries[0].score;
    const last = entries[entries.length - 1].score;
    if (first <= score) {
      // This page starts at or below the target, so the boundary is here or earlier.
      hi = mid - 1;
      boundary = base;
    } else if (last > score) {
      // Every entry in this page is still above the target.
      lo = mid + 1;
    } else {
      const index = entries.findIndex((entry) => entry.score <= score);
      boundary = index >= 0 ? base + index : base + entries.length;
      break;
    }
  }
  if (boundary < 0) boundary = lo * LEADERBOARD_PAGE_MAX;
  const clamped = Math.max(0, Math.min(boundary, total));
  return Math.min(Math.max(1, clamped + 1), total);
}

/** Locate a player's official rank via the board's name search. */
async function locateByProfile(
  leaderboardId: number,
  boardTotal: number,
  steamId: string,
  personaName: string,
): Promise<ScoreLocation | null> {
  const fromPersona = await searchBoardForSteamId(leaderboardId, boardTotal, personaName, steamId);
  if (fromPersona) return fromPersona;

  // Persona search can miss when the Steam name is not the KovaaKs username.
  // Resolve the exact steamId → KovaaKs username, then search again.
  const username = await resolveKovaaksUsername(personaName, steamId);
  if (username) {
    return await searchBoardForSteamId(leaderboardId, boardTotal, username, steamId);
  }
  return null;
}

async function searchBoardForSteamId(
  leaderboardId: number,
  boardTotal: number,
  query: string,
  steamId: string,
): Promise<ScoreLocation | null> {
  const page = await fetchBoardPage(leaderboardId, 0, query);
  const match = page.entries.find((entry) => entry.steamId === steamId);
  if (!match) return null;
  return {
    rank: match.rank,
    boardScore: match.score,
    // NOTE: page.total here counts search matches, not the board size, so the
    // percentile must use the unfiltered board total.
    percentileTop: boardTotal > 0 ? percentileFromRank(match.rank, boardTotal) : null,
  };
}

async function resolveKovaaksUsername(
  personaName: string,
  steamId: string,
): Promise<string | null> {
  const url = new URL(`${BACKEND_BASE}/leaderboard/global/search/account-names`);
  url.searchParams.set("username", personaName);
  let payload: AnyDict | AnyDict[];
  try {
    payload = await requestJson(url.toString());
  } catch {
    return null;
  }
  if (!Array.isArray(payload)) return null;
  for (const item of payload) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as AnyDict;
    if (entry.steamId === steamId && typeof entry.username === "string" && entry.username) {
      return entry.username;
    }
  }
  return null;
}

// ── Summary ────────────────────────────────────────────────────────────

function observedAt(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

async function medianScore(leaderboardId: number, total: number): Promise<number | null> {
  if (total <= 0) return null;
  const page = Math.floor(total / 2 / LEADERBOARD_PAGE_MAX);
  const entries = (await fetchBoardPage(leaderboardId, page)).entries;
  if (entries.length === 0) return null;
  const localIndex = Math.min(Math.floor(total / 2) % LEADERBOARD_PAGE_MAX, entries.length - 1);
  return entries[localIndex].score;
}

type LookupInput = {
  leaderboardId: number;
  scenarioName: string;
  aimType: string | null;
  profileRef?: string;
  score?: number;
};

function buildSummary(
  input: LookupInput,
  stats: { total: number; topScore: number | null; median: number | null },
  location: ScoreLocation | null,
  queried: AnyDict,
): AnyDict {
  return {
    schema_version: "kovaak_leaderboard.v1",
    availability: "available",
    observed_at: observedAt(),
    leaderboard_id: input.leaderboardId,
    scenario_name: input.scenarioName,
    aim_type: input.aimType,
    total_entries: stats.total,
    queried,
    on_board: location !== null && location.rank !== null,
    rank: location?.rank ?? null,
    percentile_top: location?.percentileTop ?? null,
    median_score: stats.median,
    top_score: stats.topScore,
  };
}

// ── Public API ─────────────────────────────────────────────────────────

export function isNativeKovaakLeaderboardCommand(commandName: string): boolean {
  return commandName === "kovaak_leaderboard.lookup";
}

export async function executeNativeKovaakLeaderboard(
  commandName: string,
  params: AnyDict,
): Promise<NativeKovaakLeaderboardResult> {
  if (commandName !== "kovaak_leaderboard.lookup") {
    return {
      status: "failed",
      warning_or_error: {
        code: "unknown_command",
        message: `${commandName} is not a kovaak_leaderboard command`,
      },
    };
  }
  return executeLookup(params);
}

async function executeLookup(params: AnyDict): Promise<NativeKovaakLeaderboardResult> {
  const leaderboardIdParam = params.leaderboard_id;
  const scenarioNameParam = params.scenario_name;
  const hasId = typeof leaderboardIdParam === "number";
  const hasName = typeof scenarioNameParam === "string" && scenarioNameParam.trim().length > 0;
  if (hasId === hasName) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: "kovaak_leaderboard.lookup requires exactly one of leaderboard_id or scenario_name",
      },
    };
  }
  if (hasId && (!Number.isInteger(leaderboardIdParam) || leaderboardIdParam <= 0)) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "leaderboard_id must be a positive integer" },
    };
  }

  const profileRef = params.profile_ref;
  const scoreParam = params.score;
  const hasProfile = profileRef !== undefined && profileRef !== null;
  const hasScore = scoreParam !== undefined && scoreParam !== null;
  if (hasProfile && hasScore) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: "kovaak_leaderboard.lookup accepts only one of profile_ref or score",
      },
    };
  }
  if (hasProfile && normalizeSteamProfileInput(String(profileRef)) === null) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: "profile_ref must be a 17-digit Steam ID or a steamcommunity profile URL",
      },
    };
  }
  if (hasScore && (typeof scoreParam !== "number" || !Number.isFinite(scoreParam) || scoreParam < 0)) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "score must be a non-negative number" },
    };
  }

  let resolved: ResolvedScenario;
  try {
    if (hasName) {
      const found = await resolveScenarioByName(scenarioNameParam as string);
      if (!found) {
        return {
          status: "failed",
          warning_or_error: {
            code: "scenario_not_found",
            message: `找不到场景「${(scenarioNameParam as string).slice(0, 80)}」对应的榜单`,
          },
        };
      }
      resolved = found;
    } else {
      const details = await fetchScenarioDetails(leaderboardIdParam);
      if (!details) {
        return {
          status: "failed",
          warning_or_error: {
            code: "scenario_not_found",
            message: `找不到 leaderboardId=${leaderboardIdParam} 对应的场景`,
          },
        };
      }
      resolved = { leaderboardId: leaderboardIdParam, ...details };
    }
  } catch {
    return UNAVAILABLE;
  }

  const leaderboardId = resolved.leaderboardId;

  // Board stats: total, top score, and one median page.
  let total: number;
  let topScore: number | null;
  let median: number | null;
  try {
    const first = await fetchBoardPage(leaderboardId, 0);
    total = first.total;
    topScore = first.entries.length > 0 ? first.entries[0].score : null;
    median = await medianScore(leaderboardId, total);
  } catch {
    return UNAVAILABLE;
  }

  const base: LookupInput = {
    leaderboardId,
    scenarioName: resolved.scenarioName,
    aimType: resolved.aimType,
    profileRef: hasProfile ? String(profileRef) : undefined,
    score: hasScore ? (scoreParam as number) : undefined,
  };
  const stats = { total, topScore, median };

  // No locator requested: board stats only.
  if (!hasProfile && !hasScore) {
    return {
      status: "succeeded",
      result_ref: "kovaak_leaderboard:lookup",
      result: buildSummary(base, stats, null, { kind: "none", value: null }),
    };
  }

  if (hasProfile) {
    const steamId = normalizeSteamProfileInput(String(profileRef));
    if (!steamId) {
      return {
        status: "failed",
        warning_or_error: { code: "invalid_parameters", message: "profile_ref is invalid" },
      };
    }
    let location: ScoreLocation | null;
    try {
      const personaName = await fetchSteamPersonaName(steamId);
      if (!personaName) {
        return {
          status: "unavailable",
          warning_or_error: {
            code: "profile_name_unavailable",
            message: "无法读取该 Steam 主页的显示名，暂时无法在榜单中定位",
          },
        };
      }
      location = await locateByProfile(leaderboardId, total, steamId, personaName);
    } catch {
      return UNAVAILABLE;
    }
    return {
      status: "succeeded",
      result_ref: "kovaak_leaderboard:lookup",
      result: buildSummary(base, stats, location, {
        kind: "steam_id_suffix",
        value: steamId.slice(-4),
      }),
    };
  }

  // Explicit score locator. score=0 (or an empty board) means "no entry" for
  // a descending board, so there is no rank to report.
  const score = scoreParam as number;
  if (score <= 0 || total <= 0) {
    return {
      status: "succeeded",
      result_ref: "kovaak_leaderboard:lookup",
      result: buildSummary(base, stats, null, { kind: "score", value: score }),
    };
  }
  let rank: number;
  try {
    rank = await locateByScore(leaderboardId, total, score);
  } catch {
    return UNAVAILABLE;
  }
  const location: ScoreLocation = {
    rank,
    boardScore: null,
    percentileTop: total > 0 ? percentileFromRank(rank, total) : null,
  };
  return {
    status: "succeeded",
    result_ref: "kovaak_leaderboard:lookup",
    result: buildSummary(base, stats, location, { kind: "score", value: score }),
  };
}
