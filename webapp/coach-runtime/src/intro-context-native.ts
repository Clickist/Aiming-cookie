/**
 * Native Intro Session data commands.
 *
 * The one-time Intro Session flow needs the user's local data digest and their
 * self-described profile, plus a way to persist the four answers back. Those
 * are Python-backend routes:
 *   GET /api/coach/intro-context  (summary; never errors, missing fields absent)
 *   GET /api/user-profile
 *   PUT /api/user-profile         (partial, whitelisted fields)
 *
 * Reached directly over HTTP with the desktop launch token, mirroring
 * python-analysis.ts. When the backend is not ready every command degrades to
 * a structured failure instead of throwing, so the intro skill can continue
 * gracefully (the wireframe treats missing data as expected).
 */

import { getPythonBackendConfig } from "./python-backend.ts";

type AnyDict = Record<string, any>;

const REQUEST_TIMEOUT_MS = 15_000;

const PROFILE_FIELDS = [
  "games",
  "experience",
  "self_assessment",
  "goal",
  "steam_profile_url",
] as const;

export const INTRO_NATIVE_COMMANDS = [
  "intro_context.get",
  "user_profile.get",
  "user_profile.update",
] as const;

export type IntroNativeCommandName = typeof INTRO_NATIVE_COMMANDS[number];

export function isIntroNativeCommand(commandName: string): commandName is IntroNativeCommandName {
  return (INTRO_NATIVE_COMMANDS as readonly string[]).includes(commandName);
}

export type IntroNativeResult = {
  status: "succeeded" | "failed";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

function unavailable(message: string): IntroNativeResult {
  return { status: "failed", warning_or_error: { code: "python_backend_unavailable", message } };
}

function requestSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

async function errorDetail(response: Response): Promise<string> {
  let detail = `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as AnyDict;
    if (typeof body?.detail === "string" && body.detail) detail = body.detail;
  } catch {
    // Non-JSON error body — keep the status text.
  }
  return detail;
}

/** Validate the whitelisted partial profile payload; null when acceptable. */
export function userProfileUpdateError(parameters: Record<string, unknown>): string | null {
  const unknown = Object.keys(parameters).filter(
    (key) => !(PROFILE_FIELDS as readonly string[]).includes(key),
  );
  if (unknown.length > 0) {
    return `unsupported fields: user_profile.update does not accept ${unknown.map((key) => `"${key}"`).join(", ")}; allowed fields: ${PROFILE_FIELDS.join(", ")}`;
  }
  if (Object.keys(parameters).length === 0) {
    return "unsupported fields: user_profile.update requires at least one profile field";
  }
  if (
    "games" in parameters &&
    (!Array.isArray(parameters.games) || parameters.games.some((item) => typeof item !== "string"))
  ) {
    return "unsupported fields: games must be an array of strings";
  }
  for (const field of ["experience", "self_assessment", "goal", "steam_profile_url"] as const) {
    const value = parameters[field];
    if (value !== undefined && value !== null && typeof value !== "string") {
      return `unsupported fields: ${field} must be a string or null`;
    }
  }
  return null;
}

export async function executeIntroNative(
  commandName: IntroNativeCommandName,
  parameters: Record<string, unknown>,
  ownerId: string,
  signal?: AbortSignal,
): Promise<IntroNativeResult> {
  const config = getPythonBackendConfig();
  if (!config) return unavailable("Python 后端未就绪，暂时读不到本地数据");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Aiming-Cookie-Desktop-Token": config.token,
    "X-User-Id": ownerId || "desktop-local",
  };

  try {
    if (commandName === "intro_context.get") {
      const response = await fetch(`${config.baseUrl}/api/coach/intro-context`, {
        headers,
        signal: requestSignal(signal),
      });
      if (!response.ok) {
        return { status: "failed", warning_or_error: { code: "intro_context_failed", message: await errorDetail(response) } };
      }
      return {
        status: "succeeded",
        result_ref: "intro_context:current",
        result: await response.json(),
      };
    }

    if (commandName === "user_profile.get") {
      const response = await fetch(`${config.baseUrl}/api/user-profile`, {
        headers,
        signal: requestSignal(signal),
      });
      if (!response.ok) {
        return { status: "failed", warning_or_error: { code: "user_profile_failed", message: await errorDetail(response) } };
      }
      return {
        status: "succeeded",
        result_ref: "user_profile:current",
        result: await response.json(),
      };
    }

    const parameterError = userProfileUpdateError(parameters);
    if (parameterError) {
      return { status: "failed", warning_or_error: { code: "invalid_parameters", message: parameterError } };
    }
    const response = await fetch(`${config.baseUrl}/api/user-profile`, {
      method: "PUT",
      headers,
      body: JSON.stringify(parameters),
      signal: requestSignal(signal),
    });
    if (!response.ok) {
      return { status: "failed", warning_or_error: { code: "user_profile_update_failed", message: await errorDetail(response) } };
    }
    return {
      status: "succeeded",
      result_ref: "user_profile:current",
      result: await response.json(),
    };
  } catch (error) {
    return {
      status: "failed",
      warning_or_error: {
        code: `${commandName.replace(".", "_")}_failed`,
        message: error instanceof Error ? error.message : "intro data request failed",
      },
    };
  }
}
