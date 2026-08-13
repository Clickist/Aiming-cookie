/**
 * Shared Coach conversation persistence over Pi's JsonlSessionRepo.
 *
 * Coach threads map 1:1 to Pi sessions: `id = String(threadId)`, all living
 * under a single cwd namespace (`coach`) inside conversations/. Pi owns the
 * message content (JSONL session files with header + typed entries); mutable
 * display metadata (title / status) stays in a small per-thread meta file so
 * the header metadata does not need to be rewritten.
 *
 * Legacy conversations written by the pre-Pi format (plain `{role, content}`
 * JSONL files in conversations/) are still readable and are migrated into a Pi
 * session on first access.
 */

import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { ensureAppDataDirs, getConversationsDir, getDataRoot } from "./app-data.ts";
import { isRecord } from "./contracts.ts";
import { loadPiAgent, loadPiNodeEnv } from "./pi-source.ts";

export const SESSION_CWD = "coach";

export type ConversationMeta = {
  id: number;
  title: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  /** Analysis ids this session engaged with via Coach file reads (frontend
   *  uses them to resolve `@3.4s` time links to video seeks). */
  analysis_session_ids?: number[];
};

export type SessionMessage = {
  role: string;
  content: string;
  timestamp: string;
};

// ── Pi repo access ───────────────────────────────────────────────────────

type SessionEntryLike = {
  type: string;
  id: string;
  timestamp?: string;
  message?: unknown;
};

type SessionMetadataLike = {
  id: string;
  createdAt: string;
  path: string;
  cwd: string;
  metadata?: Record<string, unknown>;
};

export type SessionLike = {
  getMetadata(): Promise<SessionMetadataLike>;
  getBranch(): Promise<SessionEntryLike[]>;
  getEntries(): Promise<SessionEntryLike[]>;
  appendMessage(message: unknown): Promise<string>;
  buildContext(options?: unknown): Promise<{ messages: unknown[] }>;
  getStorage(): unknown;
};

type JsonlRepoLike = {
  create(options: { cwd: string; id?: string; metadata?: Record<string, unknown> }): Promise<SessionLike>;
  open(metadata: SessionMetadataLike): Promise<SessionLike>;
  list(options: { cwd?: string }): Promise<SessionMetadataLike[]>;
  delete(metadata: SessionMetadataLike): Promise<void>;
};

let repoPromise: Promise<JsonlRepoLike> | null = null;

export async function getSessionRepo(): Promise<JsonlRepoLike> {
  if (!repoPromise) {
    repoPromise = (async () => {
      const { JsonlSessionRepo } = (await loadPiAgent()) as Record<string, unknown>;
      const { NodeExecutionEnv } = (await loadPiNodeEnv()) as Record<string, unknown>;
      ensureAppDataDirs();
      const env = new (NodeExecutionEnv as new (opts: { cwd: string }) => unknown)({ cwd: getDataRoot() });
      return new (JsonlSessionRepo as new (opts: { fs: unknown; sessionsRoot: string }) => JsonlRepoLike)({
        fs: env,
        sessionsRoot: getConversationsDir(),
      });
    })();
  }
  return repoPromise;
}

// ── Thread → session mapping ─────────────────────────────────────────────

async function findSessionMetadata(threadId: number): Promise<SessionMetadataLike | null> {
  const repo = await getSessionRepo();
  const sessions = await repo.list({ cwd: SESSION_CWD });
  return sessions.find((session) => session.id === String(threadId)) ?? null;
}

export async function openSession(threadId: number): Promise<SessionLike | null> {
  const metadata = await findSessionMetadata(threadId);
  if (!metadata) return null;
  return (await getSessionRepo()).open(metadata);
}

export async function ensureSession(threadId: number): Promise<SessionLike> {
  const existing = await openSession(threadId);
  if (existing) return existing;
  const migrated = await migrateLegacyConversation(threadId);
  if (migrated) return migrated;
  const repo = await getSessionRepo();
  return repo.create({ cwd: SESSION_CWD, id: String(threadId) });
}

export async function sessionExists(threadId: number): Promise<boolean> {
  if (await openSession(threadId)) return true;
  return existsSync(join(getConversationsDir(), `${threadId}.jsonl`));
}

export async function deleteSessionFile(threadId: number): Promise<void> {
  const metadata = await findSessionMetadata(threadId);
  if (!metadata) return;
  await (await getSessionRepo()).delete(metadata);
}

export async function listSessionIds(): Promise<number[]> {
  const repo = await getSessionRepo();
  const sessions = await repo.list({ cwd: SESSION_CWD });
  const ids = new Set<number>();
  for (const session of sessions) {
    const parsed = Number(session.id);
    if (Number.isInteger(parsed) && parsed > 0) ids.add(parsed);
  }
  // Legacy root conversation files remain visible until migrated on access.
  const dir = getConversationsDir();
  if (existsSync(dir)) {
    for (const file of readdirSync(dir)) {
      const match = file.match(/^(\d+)\.jsonl$/);
      if (match) ids.add(parseInt(match[1], 10));
    }
  }
  return [...ids].sort((a, b) => b - a);
}

/** Next numeric thread id without opening sessions (sync; safe for run creation). */
export function nextSessionIdSync(): number {
  ensureAppDataDirs();
  const dir = getConversationsDir();
  let maxId = 0;
  const coachDir = join(dir, `--${SESSION_CWD}--`);
  if (existsSync(coachDir)) {
    for (const file of readdirSync(coachDir)) {
      const match = file.match(/_(\d+)\.jsonl$/);
      if (match) maxId = Math.max(maxId, parseInt(match[1], 10));
    }
  }
  if (existsSync(dir)) {
    for (const file of readdirSync(dir)) {
      const match = file.match(/^(\d+)\.jsonl$/);
      if (match) maxId = Math.max(maxId, parseInt(match[1], 10));
    }
  }
  return maxId + 1;
}

// ── Message reads ────────────────────────────────────────────────────────

export async function readSessionMessages(threadId: number): Promise<SessionMessage[]> {
  const session = await openSession(threadId);
  if (session) return messagesFromSession(session);
  return readLegacyMessages(threadId);
}

async function messagesFromSession(session: SessionLike): Promise<SessionMessage[]> {
  const entries = await session.getBranch();
  const messages: SessionMessage[] = [];
  for (const entry of entries) {
    if (entry.type !== "message" || !isRecord(entry.message)) continue;
    const message = entry.message;
    if (message.role !== "user" && message.role !== "assistant") continue;
    const content = extractMessageText(message.content);
    if (message.role === "assistant" && !content.trim()) continue;
    messages.push({
      role: message.role,
      content,
      timestamp: typeof entry.timestamp === "string" ? entry.timestamp : new Date().toISOString(),
    });
  }
  return messages;
}

function readLegacyMessages(threadId: number): SessionMessage[] {
  const path = join(getConversationsDir(), `${threadId}.jsonl`);
  if (!existsSync(path)) return [];
  const lines = readFileSync(path, "utf8").split("\n").filter((line) => line.trim());
  const messages: SessionMessage[] = [];
  for (const line of lines) {
    try {
      const entry = JSON.parse(line) as unknown;
      if (!isRecord(entry) || typeof entry.role !== "string" || typeof entry.content !== "string") continue;
      if (entry.role !== "user" && entry.role !== "assistant") continue;
      messages.push({
        role: entry.role,
        content: entry.content,
        timestamp:
          typeof entry.timestamp === "number"
            ? new Date(entry.timestamp).toISOString()
            : new Date().toISOString(),
      });
    } catch {
      // Skip malformed lines.
    }
  }
  return messages;
}

async function migrateLegacyConversation(threadId: number): Promise<SessionLike | null> {
  const legacyPath = join(getConversationsDir(), `${threadId}.jsonl`);
  if (!existsSync(legacyPath)) return null;
  const repo = await getSessionRepo();
  const session = await repo.create({ cwd: SESSION_CWD, id: String(threadId) });
  const lines = readFileSync(legacyPath, "utf8").split("\n").filter((line) => line.trim());
  for (const line of lines) {
    try {
      const entry = JSON.parse(line) as unknown;
      if (!isRecord(entry) || typeof entry.role !== "string" || typeof entry.content !== "string") continue;
      if (entry.role !== "user" && entry.role !== "assistant") continue;
      await session.appendMessage({
        role: entry.role,
        content: [{ type: "text", text: entry.content }],
        timestamp: typeof entry.timestamp === "number" ? entry.timestamp : Date.now(),
      });
    } catch {
      // Skip malformed lines.
    }
  }
  return session;
}

// ── Text helpers ─────────────────────────────────────────────────────────

export function extractMessageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((block): block is { type: string; text?: unknown } => isRecord(block) && block.type === "text")
      .map((block) => (typeof block.text === "string" ? block.text : ""))
      .join("");
  }
  return "";
}

export function deriveConversationTitle(messages: SessionMessage[], fallback: string | null): string {
  const firstUser = messages.find((message) => message.role === "user");
  if (firstUser && firstUser.content.trim()) return firstUser.content.trim().slice(0, 120);
  return fallback ?? "新对话";
}

// ── Mutable conversation metadata ────────────────────────────────────────

function metaFilePath(threadId: number): string {
  return join(getConversationsDir(), `${threadId}.meta.json`);
}

export function readConversationMeta(threadId: number): ConversationMeta {
  const path = metaFilePath(threadId);
  if (existsSync(path)) {
    try {
      const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
      if (isRecord(raw) && typeof raw.id === "number") {
        return {
          id: raw.id,
          title: typeof raw.title === "string" ? raw.title : null,
          status: raw.status === "archived" ? "archived" : "active",
          created_at: typeof raw.created_at === "string" ? raw.created_at : new Date().toISOString(),
          updated_at: typeof raw.updated_at === "string" ? raw.updated_at : new Date().toISOString(),
          analysis_session_ids: Array.isArray(raw.analysis_session_ids)
            ? raw.analysis_session_ids.filter((value): value is number => Number.isInteger(value) && value > 0)
            : undefined,
        };
      }
    } catch {
      // Fall through to default.
    }
  }
  return {
    id: threadId,
    title: "新对话",
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

export function writeConversationMeta(threadId: number, meta: ConversationMeta): void {
  ensureAppDataDirs();
  writeFileSync(metaFilePath(threadId), JSON.stringify(meta, null, 2), "utf8");
}

/** Union new analysis ids into a thread's engaged-analysis list. */
export function updateConversationAnalysisIds(threadId: number, ids: number[]): void {
  const meta = readConversationMeta(threadId);
  const merged = new Set(meta.analysis_session_ids ?? []);
  for (const id of ids) {
    if (Number.isInteger(id) && id > 0) merged.add(id);
  }
  meta.analysis_session_ids = [...merged].sort((a, b) => a - b);
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(threadId, meta);
}
