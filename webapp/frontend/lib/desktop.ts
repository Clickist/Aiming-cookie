import { convertFileSrc, invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

import type { DesktopCaptureCoordinatorStatus, ScenarioOpenResultV1 } from "./types";

export interface DesktopRuntimeConnection {
  baseUrl: string;
  token: string;
}

let connectionPromise: Promise<DesktopRuntimeConnection> | null = null;

export function resetDesktopRuntimeConnection(): void {
  connectionPromise = null;
}

export function isDesktopRuntime(): boolean {
  return typeof window !== "undefined" && isTauri();
}

export async function getDesktopRuntimeConnection(): Promise<DesktopRuntimeConnection> {
  if (!isDesktopRuntime()) {
    throw new Error("Desktop runtime is unavailable in this browser session");
  }
  if (!connectionPromise) {
    const nextConnection = invoke<DesktopRuntimeConnection>("desktop_runtime_connection");
    connectionPromise = nextConnection;
    void nextConnection.catch(() => {
      if (connectionPromise === nextConnection) resetDesktopRuntimeConnection();
    });
  }
  return connectionPromise;
}

export async function getDesktopCaptureCoordinatorStatus(): Promise<DesktopCaptureCoordinatorStatus> {
  if (!isDesktopRuntime()) {
    throw new Error("Automatic capture is only available in the desktop app");
  }
  return invoke<DesktopCaptureCoordinatorStatus>("desktop_capture_coordinator_status");
}

export async function setDesktopCaptureEnabled(
  enabled: boolean,
): Promise<DesktopCaptureCoordinatorStatus> {
  if (!isDesktopRuntime()) {
    throw new Error("Automatic capture is only available in the desktop app");
  }
  return invoke<DesktopCaptureCoordinatorStatus>(
    "desktop_capture_coordinator_set_enabled",
    { enabled },
  );
}

export async function openKovaakScenario(
  scenarioProfileRef: string,
): Promise<ScenarioOpenResultV1> {
  if (!/^scenario:[a-z0-9._-]+@[1-9][0-9]*$/.test(scenarioProfileRef)) {
    return {
      status: "scenario_unmapped",
      scenario_profile_ref: null,
      display_name: null,
      message: "该训练项目没有可验证的 KovaaK 场景",
    };
  }
  if (!isDesktopRuntime()) {
    return {
      status: "desktop_unavailable",
      scenario_profile_ref: scenarioProfileRef,
      display_name: null,
      message: "当前网页预览不能启动 KovaaK，请在桌面版中操作",
    };
  }
  return invoke<ScenarioOpenResultV1>("scenario_open", {
    scenarioProfileRef,
  });
}

async function pickSinglePath(
  title: string,
  extension: "mp4" | "csv",
): Promise<string | null> {
  if (!isDesktopRuntime()) {
    throw new Error("Native file selection is only available in the desktop app");
  }
  const selected = await open({
    title,
    multiple: false,
    directory: false,
    fileAccessMode: "scoped",
    filters: [{ name: extension.toUpperCase(), extensions: [extension] }],
  });
  return typeof selected === "string" ? selected : null;
}

export function pickDesktopVideoPath(): Promise<string | null> {
  return pickSinglePath("选择 MP4 录像", "mp4");
}

export function pickDesktopCsvPath(): Promise<string | null> {
  return pickSinglePath("选择 KovaaK Stats CSV", "csv");
}

export async function getManagedVideoUrl(sessionId: number): Promise<string | null> {
  if (!isDesktopRuntime()) return null;
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error("Analysis id is invalid");
  }
  // Tauri encodes the entire file-path argument, so append the virtual route separately.
  const protocolBase = convertFileSrc("", "aiming-cookie-media");
  return new URL(`/analysis/${sessionId}`, protocolBase).toString();
}
