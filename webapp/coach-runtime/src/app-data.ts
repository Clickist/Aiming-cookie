/**
 * App-data directory management for the Coach sidecar.
 *
 * The DATA_ROOT env var is set by Tauri at launch. The desktop shell resolves the
 * effective data root itself (its storage-location pointer may move the root to a
 * custom drive) and always passes that resolved path down as DATA_ROOT, so this
 * module only needs to honor it. In development it falls back to a local
 * `app-data/` directory so the sidecar can start standalone.
 */

import { mkdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * Resolve the effective data root from the injected value.
 *
 * A blank/non-string DATA_ROOT (unset, empty, whitespace) means "not injected":
 * fall back to `<cwd>/app-data` so the sidecar stays runnable standalone. An
 * injected value is resolved as given — the shell already validated it.
 */
export function resolveDataRoot(injected: string | undefined, cwd: string): string {
  const raw = typeof injected === "string" ? injected.trim() : "";
  return raw ? resolve(raw) : resolve(cwd, "app-data");
}

let cachedDataRoot: string | null = null;

export function getDataRoot(): string {
  if (cachedDataRoot) return cachedDataRoot;
  cachedDataRoot = resolveDataRoot(process.env.DATA_ROOT, process.cwd());
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

/** Backend-owned session records (sessions/{id}.json); not created eagerly. */
export function getSessionsDir(): string {
  return join(getDataRoot(), "sessions");
}
