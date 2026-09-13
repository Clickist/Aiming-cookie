import { invoke } from "@tauri-apps/api/core";

import { isDesktopRuntime } from "./desktop";

/**
 * 前端错误环形日志：打包版 WebView 的 console 不可见也不持久，
 * 这里留一份内存环（dev 浏览器也能查），桌面运行时再 fire-and-forget
 * 落盘到 {DATA_ROOT}/logs/frontend.log，供诊断包读取。
 */
export interface FrontendLogEntry {
  ts: number;
  source: string;
  message: string;
}

const MAX_ENTRIES = 100;
const MAX_MESSAGE_CHARS = 2000;
const GLOBAL_HANDLERS_MARK = "__aimingCookieFrontendLogInstalled";

const entries: FrontendLogEntry[] = [];

function truncateMessage(message: string): string {
  return message.length > MAX_MESSAGE_CHARS ? message.slice(0, MAX_MESSAGE_CHARS) : message;
}

/** 落盘行格式：`{epoch_ms} [{source}] {message}`，换行/制表替换成空格，一行一条。 */
function formatLine(entry: FrontendLogEntry): string {
  return `${entry.ts} [${entry.source}] ${entry.message.replace(/[\r\n\t]/g, " ")}`;
}

function describeError(reason: unknown): string {
  if (reason instanceof Error) return reason.stack || `${reason.name}: ${reason.message}`;
  return typeof reason === "string" ? reason : String(reason);
}

/** 记录一条前端错误：入内存环；桌面运行时异步落盘，失败静默不拖垮业务。 */
export function logFrontendError(source: string, message: string): void {
  const entry: FrontendLogEntry = {
    ts: Date.now(),
    source,
    message: truncateMessage(message),
  };
  entries.push(entry);
  if (entries.length > MAX_ENTRIES) entries.splice(0, entries.length - MAX_ENTRIES);
  if (!isDesktopRuntime()) return;
  void invoke("desktop_append_frontend_log", { entry: formatLine(entry) }).catch((error) => {
    console.error("[frontend-log] 前端错误落盘失败", error);
  });
}

/** 内存环快照（写入顺序），供页面内调试与测试读取。 */
export function readFrontendLog(): readonly FrontendLogEntry[] {
  return entries.slice();
}

/** 清空内存环（测试隔离用）。 */
export function resetFrontendLog(): void {
  entries.length = 0;
}

// 全局兜底：模块加载时挂一次；HMR 下重复加载用 window 标记位防重。
function installGlobalHandlers(): void {
  if (typeof window === "undefined") return;
  const marked = window as unknown as Record<string, unknown>;
  if (marked[GLOBAL_HANDLERS_MARK]) return;
  marked[GLOBAL_HANDLERS_MARK] = true;
  window.addEventListener("error", (event) => {
    const errorEvent = event as ErrorEvent;
    logFrontendError(
      "window.onerror",
      errorEvent.error ? describeError(errorEvent.error) : String(errorEvent.message || "unknown error"),
    );
  });
  window.addEventListener("unhandledrejection", (event) => {
    logFrontendError("unhandledrejection", describeError((event as PromiseRejectionEvent).reason));
  });
}

installGlobalHandlers();
