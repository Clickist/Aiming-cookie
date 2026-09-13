import { convertFileSrc, invoke, isTauri } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";

import type { DesktopCaptureCoordinatorStatus, ScenarioOpenResultV1 } from "./types";

export interface DesktopRuntimeConnection {
  baseUrl: string;
  token: string;
  sidecarUrl: string;
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

export async function exportDesktopCaptureDiagnostics(): Promise<string | null> {
  if (!isDesktopRuntime()) {
    throw new Error("Capture diagnostics are only available in the desktop app");
  }
  const path = await save({
    title: "导出运行日志",
    defaultPath: "aiming-cookie-capture-diagnostics.json",
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (typeof path !== "string" || !path) return null;
  return invoke<string>("desktop_export_capture_diagnostics", { path });
}

// 诊断包直传（ac-logs：洛杉矶机 + CF Tunnel，logs.aimingcookie.com）。
// token 只是防滥用的轻门禁，不是机密；上传失败由调用方降级到本地导出。
const DIAGNOSTICS_UPLOAD_URL = "https://logs.aimingcookie.com/upload";
const DIAGNOSTICS_UPLOAD_TOKEN = "1a7432abc2f8e2bbe03121e953e0b3be2deedde581a5f65d";

export async function uploadDesktopCaptureDiagnostics(): Promise<string> {
  if (!isDesktopRuntime()) {
    throw new Error("Capture diagnostics are only available in the desktop app");
  }
  const bundle = await invoke<string>("desktop_collect_capture_diagnostics");
  const response = await fetch(DIAGNOSTICS_UPLOAD_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-AC-Token": DIAGNOSTICS_UPLOAD_TOKEN,
    },
    body: bundle,
  });
  if (response.status === 429) {
    throw new Error("UPLOAD_RATE_LIMITED");
  }
  if (response.status === 503) {
    throw new Error("UPLOAD_QUOTA_EXCEEDED");
  }
  if (!response.ok) {
    throw new Error(`diagnostics upload failed with HTTP ${response.status}`);
  }
  const data = (await response.json()) as { id?: string };
  if (!data.id) throw new Error("diagnostics upload response missing id");
  return data.id;
}

export async function openKovaakScenario(
  scenarioName: string,
): Promise<ScenarioOpenResultV1> {
  const trimmed = scenarioName.trim();
  if (!trimmed || trimmed.length > 200 || /[\u0000-\u001f\u007f]/.test(trimmed)) {
    return {
      status: "scenario_unmapped",
      scenario_name: null,
      display_name: null,
      message: "本机 KovaaK 没有这个场景，需要先订阅/下载。",
    };
  }
  if (!isDesktopRuntime()) {
    return {
      status: "desktop_unavailable",
      scenario_name: trimmed,
      display_name: null,
      message: "当前网页预览不能启动 KovaaK，请在桌面版中操作",
    };
  }
  return invoke<ScenarioOpenResultV1>("scenario_open", {
    scenarioName: trimmed,
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

/** Native folder selection for local KovaaK Stats and Performance locations. */
export async function pickDesktopDirectory(title: string): Promise<string | null> {
  if (!isDesktopRuntime()) {
    throw new Error("Native folder selection is only available in the desktop app");
  }
  const selected = await open({
    title,
    multiple: false,
    directory: true,
    fileAccessMode: "scoped",
  });
  return typeof selected === "string" ? selected : null;
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


/** 受控外链统一出口：桌面端经 opener 插件唤起默认浏览器（WebView2 里
    target=_blank 导航被拦，点链接无反应，1.0.0 内测实测）；纯浏览器
    预览环境保持原生新标签。 */
export async function openExternalUrl(url: string): Promise<void> {
  if (isDesktopRuntime()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
