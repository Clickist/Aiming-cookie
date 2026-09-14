/**
 * Native scenario commands.
 *
 * scenario.list — read-only list of locally installed KovaaK scenario names.
 *                The Python backend owns KovaaK install discovery and exposes
 *                it at GET /api/kovaak-scenarios; this command just forwards.
 * scenario.open — resolve-and-open is owned by the Tauri `scenario_open`
 *                command (local Scenarios/*.sce scan → Steam deep link). The
 *                Coach only emits a coach_ui_event; the frontend performs the
 *                actual open so install/availability messaging stays in one
 *                place.
 * scenario.search — fuzzy search of the official KovaaKs scenario library
 *                (name → leaderboardId / playcount), backed by
 *                kovaak-leaderboard-native's searchScenarios. Lets the Coach
 *                recommend official scenarios that are not installed locally.
 */
import { getPythonBackendConfig } from "./python-backend.ts";
import {
  SCENARIO_SEARCH_DEFAULT_LIMIT,
  SCENARIO_SEARCH_MAX_LIMIT,
  searchScenarios,
} from "./kovaak-leaderboard-native.ts";

type AnyDict = Record<string, any>;

export type NativeScenarioResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

const REQUEST_TIMEOUT_MS = 15_000;
const MAX_SCENARIO_NAME_CHARS = 200;
const MAX_SCENARIO_QUERY_CHARS = 200;

export function isNativeScenarioCommand(commandName: string): boolean {
  return commandName === "scenario.open" || commandName === "scenario.list" || commandName === "scenario.search";
}

async function listLocalScenarios(): Promise<NativeScenarioResult> {
  const config = getPythonBackendConfig();
  if (!config) {
    return {
      status: "unavailable",
      warning_or_error: { code: "kovaak_list_unavailable", message: "本机 KovaaK 场景清单暂时不可用" },
    };
  }
  let payload: AnyDict;
  try {
    const response = await fetch(`${config.baseUrl}/api/kovaak-scenarios`, {
      headers: { "X-Aiming-Cookie-Desktop-Token": config.token },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = (await response.json()) as AnyDict;
  } catch (error) {
    return {
      status: "unavailable",
      warning_or_error: {
        code: "kovaak_list_unavailable",
        message: `本机 KovaaK 场景清单暂时不可用（${String(error).slice(0, 80)}）`,
      },
    };
  }
  const scenarios = Array.isArray(payload.scenarios)
    ? payload.scenarios.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
  return {
    status: "succeeded",
    result_ref: "kovaak_scenarios:local",
    result: {
      schema_version: "kovaak_scenarios.v1",
      availability: payload.availability === "available" ? "available" : "unavailable",
      scenarios,
    },
  };
}

function openScenario(params: AnyDict): NativeScenarioResult {
  const unknown = Object.keys(params).filter((key) => key !== "scenario_name");
  if (unknown.length) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: `scenario.open does not accept ${unknown.map((key) => `"${key}"`).join(", ")}; allowed fields: scenario_name`,
      },
    };
  }
  const scenarioName = params.scenario_name;
  if (typeof scenarioName !== "string" || scenarioName.trim().length === 0) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "scenario.open requires scenario_name" },
    };
  }
  const trimmed = scenarioName.trim();
  if (trimmed.length > MAX_SCENARIO_NAME_CHARS) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "scenario_name is too long" },
    };
  }
  const event: AnyDict = {
    schema_version: "coach_ui_event.v1",
    kind: "scenario",
    scenario_name: trimmed,
  };
  return { status: "succeeded", result_ref: `scenario:${trimmed}`, result: event };
}

async function searchOfficialScenarios(params: AnyDict): Promise<NativeScenarioResult> {
  const unknown = Object.keys(params).filter((key) => key !== "query" && key !== "limit");
  if (unknown.length) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: `scenario.search does not accept ${unknown.map((key) => `"${key}"`).join(", ")}; allowed fields: query, limit`,
      },
    };
  }
  const query = params.query;
  if (typeof query !== "string" || query.trim().length === 0) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "scenario.search requires a non-empty query" },
    };
  }
  const trimmed = query.trim();
  if (trimmed.length > MAX_SCENARIO_QUERY_CHARS) {
    return {
      status: "failed",
      warning_or_error: { code: "invalid_parameters", message: "query is too long" },
    };
  }
  const limitParam = params.limit;
  let limit = SCENARIO_SEARCH_DEFAULT_LIMIT;
  if (limitParam !== undefined && limitParam !== null) {
    if (typeof limitParam !== "number" || !Number.isInteger(limitParam) || limitParam <= 0) {
      return {
        status: "failed",
        warning_or_error: { code: "invalid_parameters", message: "limit must be a positive integer" },
      };
    }
    limit = Math.min(limitParam, SCENARIO_SEARCH_MAX_LIMIT);
  }

  let scenarios;
  try {
    scenarios = await searchScenarios(trimmed, limit);
  } catch (error) {
    return {
      status: "unavailable",
      warning_or_error: {
        code: "scenario_search_unavailable",
        message: `官方场景库暂时不可用（${String(error).slice(0, 80)}）`,
      },
    };
  }
  return {
    status: "succeeded",
    result_ref: "kovaak_scenarios:official",
    result: {
      schema_version: "kovaak_scenario_search.v1",
      query: trimmed,
      limit,
      count: scenarios.length,
      scenarios,
    },
  };
}

export async function executeNativeScenario(
  commandName: string,
  params: AnyDict,
): Promise<NativeScenarioResult> {
  if (commandName === "scenario.list") {
    return listLocalScenarios();
  }
  if (commandName === "scenario.open") {
    return openScenario(params);
  }
  if (commandName === "scenario.search") {
    return searchOfficialScenarios(params);
  }
  return {
    status: "failed",
    warning_or_error: { code: "unknown_command", message: `${commandName} is not a scenario command` },
  };
}
