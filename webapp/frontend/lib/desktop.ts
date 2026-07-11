import { convertFileSrc, invoke, isTauri } from "@tauri-apps/api/core";
import { appDataDir, join } from "@tauri-apps/api/path";
import { open } from "@tauri-apps/plugin-dialog";

export interface DesktopRuntimeConnection {
  baseUrl: string;
  token: string;
}

let connectionPromise: Promise<DesktopRuntimeConnection> | null = null;

export function isDesktopRuntime(): boolean {
  return typeof window !== "undefined" && isTauri();
}

export async function getDesktopRuntimeConnection(): Promise<DesktopRuntimeConnection> {
  if (!isDesktopRuntime()) {
    throw new Error("Desktop runtime is unavailable in this browser session");
  }
  connectionPromise ??= invoke<DesktopRuntimeConnection>("desktop_runtime_connection");
  return connectionPromise;
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
  const videoPath = await join(
    await appDataDir(),
    "sessions",
    String(sessionId),
    "video.mp4",
  );
  return convertFileSrc(videoPath);
}
