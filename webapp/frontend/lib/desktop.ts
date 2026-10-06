import { convertFileSrc, invoke, isTauri } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";

import { t, type MessageKey } from "./i18n/core";

import type {
  DesktopCaptureCoordinatorStatus,
  ScenarioOpenResultV1,
  ScenarioOpenStatus,
} from "./types";

// Rust invoke 错误码 → 字典键：src-tauri 命令的 Err(String) 只回稳定码
// （src-tauri/src/lib.rs 的 diag.* / frontend_log.*），文案单一事实源在
// 前端字典。未知码原样输出——裸码可捕获，与后端过渡期兜底双保险。
const DESKTOP_ERROR_KEYS: Record<string, MessageKey> = {
  "diag.path_not_absolute": "desktop.diag.pathNotAbsolute",
  "diag.serialize_failed": "desktop.diag.serializeFailed",
  "diag.write_failed": "desktop.diag.writeFailed",
  "frontend_log.dir_create_failed": "desktop.frontendLog.dirCreateFailed",
  "frontend_log.rotate_failed": "desktop.frontendLog.rotateFailed",
  "frontend_log.open_failed": "desktop.frontendLog.openFailed",
  "frontend_log.write_failed": "desktop.frontendLog.writeFailed",
};

/** 把 invoke 错误码译成当前语言文案；未知错误转为字符串（原样可见）。 */
export function desktopErrorMessage(reason: unknown): string {
  const key = typeof reason === "string" ? DESKTOP_ERROR_KEYS[reason] : undefined;
  return key ? t(key) : String(reason);
}

function localizeInvokeError(reason: unknown): never {
  throw new Error(desktopErrorMessage(reason));
}

// KovaaK 场景启动状态 → 字典键：scenario_open 只回 status 枚举（Rust 不持
// 自然语言），桌面回包与下方浏览器兜底共用这一张表——五处重复文案归一。
// unmapped / webPreviewBlocked 复用 shared 冻结键（文本与 Rust 原句一致）。
const SCENARIO_STATUS_KEYS: Record<ScenarioOpenStatus, MessageKey> = {
  scenario_dispatched: "desktop.kovaak.scenarioDispatched",
  scenario_unmapped: "desktop.kovaak.scenarioUnmapped",
  desktop_unavailable: "desktop.kovaak.webPreviewBlocked",
  deep_link_dispatch_failed: "desktop.kovaak.scenarioDispatchFailed",
};

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

// Tauri 侧 runtime 状态稳定码（src-tauri/src/runtime.rs 的 connection()）：
// starting = 后台拉起中（首次启动或崩溃重启），failed = 重启预算耗尽。
export const RUNTIME_STARTING_CODE = "runtime.starting";
export const RUNTIME_FAILED_CODE = "runtime.failed";

/** 等本地 runtime 就绪：starting 每秒重查，failed 原样抛给调用方呈现终态。 */
export async function awaitDesktopRuntimeConnection(): Promise<DesktopRuntimeConnection> {
  for (;;) {
    try {
      return await getDesktopRuntimeConnection();
    } catch (error) {
      if (error === RUNTIME_FAILED_CODE) throw error;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
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

// 窗口自动录像状态（src-tauri window_capture.rs 的 WindowCaptureStatus 投影，
// serde camelCase）：性能栏只读展示当前编码路径。这里只声明前端消费的字段，
// 运行时回包字段更多（TS 结构类型按子集读取）；encoderPath 的取值映射见
// lib/encoder-path-label.ts（HardwareEncoderPath 枚举的 serde 序列化值）。
export interface DesktopWindowCaptureStatus {
  recording: boolean;
  encoderPath: string | null;
}

export async function getDesktopWindowCaptureStatus(): Promise<DesktopWindowCaptureStatus> {
  if (!isDesktopRuntime()) {
    throw new Error("Window capture status is only available in the desktop app");
  }
  return invoke<DesktopWindowCaptureStatus>("desktop_window_capture_status");
}

export async function exportDesktopCaptureDiagnostics(): Promise<string | null> {
  if (!isDesktopRuntime()) {
    throw new Error("Capture diagnostics are only available in the desktop app");
  }
  const path = await save({
    title: t("desktop.dialog.exportLogs"),
    defaultPath: "aiming-cookie-capture-diagnostics.json",
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (typeof path !== "string" || !path) return null;
  return invoke<string>("desktop_export_capture_diagnostics", { path }).catch(
    localizeInvokeError,
  );
}

// 诊断包直传（ac-logs，logs.aimingcookie.com）。
// token 只是防滥用的轻门禁，不是机密；上传失败由调用方降级到本地导出。
// token 不入库：由 Next 构建期从 .env.production.local（gitignore）注入
// NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN；未注入的构建跳过上传、走本地导出。
// 该错误码区别于网络/服务失败：它说明当前构建缺配置，重试无用。
export const DIAGNOSTICS_UPLOAD_NOT_CONFIGURED = "DIAGNOSTICS_UPLOAD_NOT_CONFIGURED";
const DIAGNOSTICS_UPLOAD_URL = "https://logs.aimingcookie.com/upload";
const DIAGNOSTICS_UPLOAD_TOKEN = process.env.NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN ?? "";

export async function uploadDesktopCaptureDiagnostics(): Promise<string> {
  if (!isDesktopRuntime()) {
    throw new Error("Capture diagnostics are only available in the desktop app");
  }
  if (!DIAGNOSTICS_UPLOAD_TOKEN) {
    throw new Error(DIAGNOSTICS_UPLOAD_NOT_CONFIGURED);
  }
  const bundle = await invoke<string>("desktop_collect_capture_diagnostics").catch(
    localizeInvokeError,
  );
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
      message: t(SCENARIO_STATUS_KEYS.scenario_unmapped),
    };
  }
  if (!isDesktopRuntime()) {
    return {
      status: "desktop_unavailable",
      scenario_name: trimmed,
      display_name: null,
      message: t(SCENARIO_STATUS_KEYS.desktop_unavailable),
    };
  }
  // Rust 侧只回 status 枚举码（wire 无 message 字段），展示文案在此查字典补齐，
  // 对外类型 ScenarioOpenResultV1 保持不变（消费方无需感知）。
  const result = await invoke<Omit<ScenarioOpenResultV1, "message">>("scenario_open", {
    scenarioName: trimmed,
  });
  return { ...result, message: t(SCENARIO_STATUS_KEYS[result.status]) };
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
  return pickSinglePath(t("desktop.dialog.pickVideo"), "mp4");
}

export function pickDesktopCsvPath(): Promise<string | null> {
  return pickSinglePath(t("desktop.dialog.pickCsv"), "csv");
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
