/**
 * Python backend address resolution for the Coach sidecar.
 *
 * The Python backend binds a dynamic loopback port and the launch token is
 * generated after the sidecar starts, so Tauri publishes the pair in a config
 * file (`desktop-runtime.json`) once the Python backend is ready. The sidecar
 * reads it lazily on each use so it picks up fresh values after a runtime
 * restart.
 */

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { getDataRoot } from "./app-data.ts";

export type PythonBackendConfig = {
  baseUrl: string;
  token: string;
};

export function getPythonBackendConfig(): PythonBackendConfig | null {
  const configured = process.env.AIMING_COOKIE_DESKTOP_RUNTIME_CONFIG?.trim();
  const path = configured
    ? join(configured)
    : join(getDataRoot(), "desktop-runtime.json");
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf-8")) as Record<string, unknown>;
    const baseUrl = parsed.python_base_url;
    const token = parsed.python_token;
    if (
      typeof baseUrl === "string" && baseUrl.length > 0 &&
      typeof token === "string" && token.length > 0
    ) {
      return { baseUrl, token };
    }
  } catch {
    // Corrupt or partial config — treat as not ready.
  }
  return null;
}
