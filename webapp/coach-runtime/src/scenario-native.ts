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
 */
import { getPythonBackendConfig } from "./python-backend.ts";

type AnyDict = Record<string, any>;

export type NativeScenarioResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

const REQUEST_TIMEOUT_MS = 15_000;
const MAX_SCENARIO_NAME_CHARS = 200;

export function isNativeScenarioCommand(commandName: string): boolean {
  return commandName === "scenario.open" || commandName === "scenario.list";
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
  return {
    status: "failed",
    warning_or_error: { code: "unknown_command", message: `${commandName} is not a scenario command` },
  };
}
