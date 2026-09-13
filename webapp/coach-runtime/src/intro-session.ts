/**
 * One-time Intro Session flag + Coach session bootstrap.
 *
 * The Intro Session is a once-per-install Coach conversation created on first
 * launch. The durable fact is a tiny flag file in the sidecar config dir
 * (`config/intro-session.json`): { created, session_id, created_at }. The
 * session itself lives in the normal Pi JSONL session store (session-repo.ts);
 * the flag only records that it happened so the endpoint is idempotent.
 *
 * Deliberately dependency-light (fs + app-data + session-repo) because
 * turn.ts reads the flag on every turn to scope the intro-session skill; pulling
 * in agent-runs/sidecar-coach-data here would create an import cycle.
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { ensureAppDataDirs, getConfigDir } from "./app-data.ts";
import { isRecord } from "./contracts.ts";
import {
  ensureSession,
  nextSessionIdSync,
  writeConversationMeta,
} from "./session-repo.ts";

export const INTRO_SESSION_TITLE = "开场分析";
const INTRO_SESSION_FLAG_FILE = "intro-session.json";

export type IntroSessionFlag = {
  created: boolean;
  session_id: number | null;
  created_at: string | null;
};

const EMPTY_FLAG: IntroSessionFlag = { created: false, session_id: null, created_at: null };

function flagPath(): string {
  return join(getConfigDir(), INTRO_SESSION_FLAG_FILE);
}

export function readIntroSessionFlag(): IntroSessionFlag {
  const path = flagPath();
  if (!existsSync(path)) return { ...EMPTY_FLAG };
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (
      isRecord(raw) &&
      raw.created === true &&
      typeof raw.session_id === "number" &&
      Number.isInteger(raw.session_id) &&
      raw.session_id > 0
    ) {
      return {
        created: true,
        session_id: raw.session_id,
        created_at: typeof raw.created_at === "string" ? raw.created_at : null,
      };
    }
  } catch {
    // Corrupt/partial flag: treat as not created so the session can be recovered.
  }
  return { ...EMPTY_FLAG };
}

function writeIntroSessionFlag(flag: { session_id: number; created_at: string }): void {
  ensureAppDataDirs();
  writeFileSync(
    flagPath(),
    JSON.stringify({ created: true, session_id: flag.session_id, created_at: flag.created_at }, null, 2),
    "utf8",
  );
}

/** Intro session id when the one-time session exists; null otherwise. */
export function readIntroSessionId(): number | null {
  const flag = readIntroSessionFlag();
  return flag.created ? flag.session_id : null;
}

export function isIntroSession(sessionId: number | null | undefined): boolean {
  if (sessionId === null || sessionId === undefined || !Number.isInteger(sessionId)) return false;
  return readIntroSessionId() === sessionId;
}

let ensureInFlight: Promise<{ created: boolean; session_id: number }> | null = null;

/**
 * Idempotent creation: the first caller creates the Coach session titled
 * 「开场分析」 and persists the flag; later callers get the existing id back.
 * Concurrent callers share one in-flight creation (local single-user app, but
 * a double-click on first launch must not create two sessions).
 */
export async function ensureIntroSession(): Promise<{ created: boolean; session_id: number }> {
  const existing = readIntroSessionFlag();
  if (existing.created && existing.session_id !== null) {
    return { created: false, session_id: existing.session_id };
  }
  if (ensureInFlight) return ensureInFlight;
  ensureInFlight = (async () => {
    const id = nextSessionIdSync();
    await ensureSession(id);
    const now = new Date().toISOString();
    // title_source="user" pins the title: auto-naming must never overwrite
    // 「开场分析」with the first sentence.
    writeConversationMeta(id, {
      id,
      title: INTRO_SESSION_TITLE,
      title_source: "user",
      status: "active",
      created_at: now,
      updated_at: now,
    });
    writeIntroSessionFlag({ session_id: id, created_at: now });
    return { created: true, session_id: id };
  })();
  try {
    return await ensureInFlight;
  } finally {
    ensureInFlight = null;
  }
}
