/**
 * 自定义数据存储位置（桌面壳 storage_location 模块的前端客户端）。
 *
 * 权威在 Rust 壳：它解析「生效数据根」（指针文件合法且自定义根可用时用自定义根，
 * 否则回落默认根），壳自身写入与子进程 DATA_ROOT 一律用生效根。前端只做三件事：
 * 读状态、写位置、把稳定码译成当前语言文案（壳不持自然语言）。
 *
 * 生效根切换要重启才生效（迁移在重启时执行），所以这里没有「立即生效」的假象：
 * `restartRequired` 为真时 UI 只提示重启，迁移进度由 `migration` 现场呈现。
 */

import { invoke } from "@tauri-apps/api/core";

import { isDesktopRuntime } from "./desktop";
import { t, type MessageKey } from "./i18n/core";

export type StorageMigrationPhase = "planned" | "running" | "done" | "failed";

export interface StorageMigrationStatusV1 {
  sourceRoot: string;
  targetRoot: string;
  phase: StorageMigrationPhase;
  movedEntries: string[];
  pendingEntries: string[];
  totalBytes: number;
  copiedBytes: number;
  error: string | null;
  updatedAt: string;
}

export interface StorageLocationStatusV1 {
  /** 生效数据根（壳写入与子进程 DATA_ROOT 用的那个）。 */
  effectiveRoot: string;
  /** 默认数据根（指针文件所在处，自定义根为空时的生效根）。 */
  defaultRoot: string;
  /** 用户请求的自定义根；null = 使用默认位置。 */
  customRoot: string | null;
  /** 请求位置 ≠ 生效根：重启后才切换（届时自动迁移数据）。 */
  restartRequired: boolean;
  migration: StorageMigrationStatusV1 | null;
}

/** 壳返回的稳定错误码 → 字典键（未知码走兜底键，不再回显裸码）。 */
const STORAGE_LOCATION_ERROR_KEYS: Record<string, MessageKey> = {
  "storage_location.not_absolute": "settings.storage.location.errorNotAbsolute",
  "storage_location.not_a_directory": "settings.storage.location.errorNotDirectory",
  "storage_location.create_failed": "settings.storage.location.errorCreateFailed",
  "storage_location.not_writable": "settings.storage.location.errorNotWritable",
  "storage_location.nested": "settings.storage.location.errorNested",
  "storage_location.write_failed": "settings.storage.location.errorWriteFailed",
  "storage_location.migration_in_progress": "settings.storage.location.errorMigrationInProgress",
  "storage_location.insufficient_space": "settings.storage.location.errorInsufficientSpace",
};

export const STORAGE_LOCATION_UNKNOWN_ERROR: MessageKey = "settings.storage.location.errorUnknown";

/** 壳的错误回包形状：稳定码 + 可选数字（空间不足时带需求/可用字节）。 */
export interface StorageLocationErrorV1 {
  code: string;
  requiredBytes?: number;
  availableBytes?: number;
}

function parseStorageLocationError(reason: unknown): StorageLocationErrorV1 | null {
  if (typeof reason === "string") return { code: reason };
  if (reason && typeof reason === "object" && typeof (reason as { code?: unknown }).code === "string") {
    const value = reason as { code: string; requiredBytes?: unknown; availableBytes?: unknown };
    return {
      code: value.code,
      requiredBytes: typeof value.requiredBytes === "number" ? value.requiredBytes : undefined,
      availableBytes: typeof value.availableBytes === "number" ? value.availableBytes : undefined,
    };
  }
  return null;
}

/** 稳定码 → 当前语言文案；未知码给兜底句（不把裸码或英文诊断抛给用户）。 */
export function storageLocationErrorMessage(reason: unknown): string {
  const error = parseStorageLocationError(reason);
  if (!error) return t(STORAGE_LOCATION_UNKNOWN_ERROR);
  if (error.code === "storage_location.insufficient_space") {
    // 缺数字时不渲染带占位符的句子（插值缺参会把 {required} 原样上屏）。
    if (error.requiredBytes == null || error.availableBytes == null) {
      return t("settings.storage.location.errorInsufficientSpaceUnknown");
    }
    return t("settings.storage.location.errorInsufficientSpace", {
      required: formatStorageBytes(error.requiredBytes),
      available: formatStorageBytes(error.availableBytes),
    });
  }
  return t(STORAGE_LOCATION_ERROR_KEYS[error.code] ?? STORAGE_LOCATION_UNKNOWN_ERROR);
}

/** 字节数 → 人话单位（B/KB/MB/GB，一位小数）；与设置页占用显示同一口径。 */
export function formatStorageBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function isDesktopStorageLocationAvailable(): boolean {
  return isDesktopRuntime();
}

export async function getStorageLocationStatus(): Promise<StorageLocationStatusV1> {
  if (!isDesktopRuntime()) {
    throw new Error("Storage location is only available in the desktop app");
  }
  return invoke<StorageLocationStatusV1>("desktop_storage_location_status");
}

/** 设置存储位置：`null` = 恢复默认位置。壳校验失败时抛出已本地化的消息。 */
export async function setStorageLocation(path: string | null): Promise<StorageLocationStatusV1> {
  if (!isDesktopRuntime()) {
    throw new Error("Storage location is only available in the desktop app");
  }
  return invoke<StorageLocationStatusV1>("desktop_set_storage_location", { path }).catch(
    (reason: unknown) => {
      throw new Error(storageLocationErrorMessage(reason));
    },
  );
}

/** 复制到剪贴板；WebView2 之外的降级路径（execCommand）失败时返回 false。 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的降级路径。
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(textarea);
    return copied;
  } catch {
    return false;
  }
}

/** 迁移进度百分比（0-100）；数据量未知或已结束时给 null（不硬造进度）。 */
export function migrationPercent(migration: StorageMigrationStatusV1): number | null {
  if (migration.phase !== "running" || migration.totalBytes <= 0) return null;
  return Math.min(100, Math.round((migration.copiedBytes / migration.totalBytes) * 100));
}

/** 迁移失败原因 → 字典键：只翻译壳的已知前缀，其余归「未知原因」。 */
export function migrationFailureKey(migration: StorageMigrationStatusV1): MessageKey {
  const error = migration.error ?? "";
  if (error.startsWith("insufficient_space")) return "settings.storage.location.failureSpace";
  if (error.startsWith("source_missing")) return "settings.storage.location.failureSourceMissing";
  if (error.startsWith("target_unavailable")) return "settings.storage.location.failureTarget";
  return "settings.storage.location.failureUnknown";
}
