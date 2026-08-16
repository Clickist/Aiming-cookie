/**
 * App-data directory management for the Coach sidecar.
 *
 * The DATA_ROOT env var is set by Tauri at launch. In development it falls
 * back to a local `app-data/` directory so the sidecar can start standalone.
 */

import { mkdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

let cachedDataRoot: string | null = null;

export function getDataRoot(): string {
  if (cachedDataRoot) return cachedDataRoot;
  const raw = process.env.DATA_ROOT?.trim();
  cachedDataRoot = raw ? resolve(raw) : resolve(process.cwd(), "app-data");
  return cachedDataRoot;
}

const APP_DATA_SUBDIRS = ["analyses", "conversations", "training", "teaching", "config"] as const;

export function ensureAppDataDirs(): void {
  const root = getDataRoot();
  for (const sub of APP_DATA_SUBDIRS) {
    const dir = join(root, sub);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
  }
}

export function getConversationsDir(): string {
  return join(getDataRoot(), "conversations");
}

export function getAnalysesDir(): string {
  return join(getDataRoot(), "analyses");
}

export function getTrainingDir(): string {
  return join(getDataRoot(), "training");
}

export function getTeachingDir(): string {
  return join(getDataRoot(), "teaching");
}

export function getConfigDir(): string {
  return join(getDataRoot(), "config");
}
