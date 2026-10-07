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

import { appendFileSync, existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { ensureAppDataDirs, getConversationsDir, getDataRoot } from "./app-data.ts";
import { isRecord } from "./contracts.ts";
import { isIntroKickoffMessage } from "./intro-kickoff.ts";
import { loadPiAgent, loadPiNodeEnv } from "./pi-source.ts";

export const SESSION_CWD = "coach";

export type ConversationMeta = {
  id: number;
  title: string | null;
  /** 谁定的标题（0910 路线 B）："auto"=模型自动命名，"user"=手动改名；
   *  缺省＝两者都没发生，标题走首句截断降级。手动命名永不被自动覆盖。 */
  title_source?: "user" | "auto" | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  /** Analysis ids this session engaged with via Coach file reads (frontend
   *  uses them to resolve `@3.4s` time links to video seeks). */
  analysis_session_ids?: number[];
  /** 挂载顺序台账（1002 串视频修复）：analysis_session_ids 各 id 的挂载时刻，
   *  按 attached_at 升序、时间取挂载 run 的 started_at（≤ 该回合全部回复的
   *  created_at，前端据此做「时间就近」归属）。升级前的旧 meta 无此字段——
   *  detail 侧从并集列表合成空时间戳 refs，前端退回序数近似。 */
  analysis_refs?: Array<{ id: number; attached_at: string }>;
  /** Non-subject deep-read analysis ids (`analysis:{id}` without a subject):
   *  history/comparison references the AI read this session. @time-link
   *  fallback only — these are viewable, but NOT 本次讨论 subjects. */
  deep_read_analysis_session_ids?: number[];
};

export type SessionMessage = {
  role: string;
  content: string;
  timestamp: string;
  /** 被用户停止的半截回复（stopReason=aborted）：前端据实在消息上挂「已停止」。 */
  stopped?: boolean;
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
  /** pi Session 内建的会话级 token/费用统计（审计#20）；pi 0.83 全量实现。 */
  getSessionStats?(): Promise<unknown>;
};

type JsonlRepoLike = {
  create(options: { cwd: string; id?: string; metadata?: Record<string, unknown> }): Promise<SessionLike>;
  open(metadata: SessionMetadataLike): Promise<SessionLike>;
  list(options: { cwd?: string }): Promise<SessionMetadataLike[]>;
  delete(metadata: SessionMetadataLike): Promise<void>;
};

let repoPromise: Promise<JsonlRepoLike> | null = null;

// ── EBUSY 感知重试（1008 报障包）─────────────────────────────────────────
// 用户数据目录在同步盘/杀软视野内时（野外样本：D:\集锦\kvk），会话 jsonl 的
// appendFile/writeFile 会撞上外部进程的排他锁（EBUSY: resource busy or
// locked）。pi 的 NodeFileSystem 单次尝试零重试，一次瞬态锁就炸掉整轮
// Coach turn。这里在注入 pi 的 fs 边界上包退避重试；「跳过持久化继续回
// 复」不可行——pi 会话是 parent 指针树，缺条目=下次 open 直接 invalid_session。

/** pi 的 FileError("unknown") 原样透传 node 文本（toFileError 无 EBUSY 分支），
 * 双判据：消息文本 + cause.code，不 import pi 的类型（pi 是动态加载的）。 */
export function isStorageBusyFileError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const message = (error as { message?: unknown }).message;
  if (typeof message === "string" && /EBUSY|resource busy or locked/i.test(message)) return true;
  const cause = (error as { cause?: unknown }).cause;
  return Boolean(cause && (cause as { code?: unknown }).code === "EBUSY");
}

export type StorageResult = { ok: true; value?: unknown } | { ok: false; error: unknown };

/** EBUSY 退避重试：至多重试 delays.length 次（默认 150/300/600ms，总尝试 ≤4）；
 * 非 EBUSY 原样返回零重试。重试成功后向 coach-error.log 记一行 recovered，
 * 供野外观测锁家族频率（best-effort，日志失败绝不掩盖原结果）。 */
export async function retryOnBusyFile(
  op: () => Promise<StorageResult>,
  options: { delays?: number[] } = {},
): Promise<StorageResult> {
  const delays = options.delays ?? [150, 300, 600];
  let result = await op();
  let retries = 0;
  while (!result.ok && isStorageBusyFileError(result.error) && retries < delays.length) {
    await new Promise((resolve) => setTimeout(resolve, delays[retries]!));
    retries += 1;
    result = await op();
  }
  if (retries > 0 && result.ok) {
    try {
      appendFileSync(
        join(getDataRoot(), "coach-error.log"),
        `${new Date().toISOString()} [session-repo] storage-busy recovered retries=${retries}\n`,
        "utf8",
      );
    } catch {
      // best-effort
    }
  }
  return result;
}

/** 注入 pi 前需要包 EBUSY 重试的 fs 方法：写侧 appendFile（条目追加/叶子）+
 * writeFile（create 首写 header）；读侧 readTextFile/readTextLines——open 会话
 * 与 repo.list 都要读文件 header，验收实机实测锁窗口内读同样一次失败炸整轮
 * turn（1008 修复发版走查用例 A 抓到），比写侧暴露面更广（锁任意一个会话
 * 文件，所有新对话的会话列表扫描都会撞）。 */
export const BUSY_RETRY_FS_METHODS = [
  "appendFile",
  "writeFile",
  "readTextFile",
  "readTextLines",
] as const;

function wrapWithBusyRetry(
  env: Record<string, unknown>,
  method: (typeof BUSY_RETRY_FS_METHODS)[number],
): void {
  const orig = (env[method] as (...args: unknown[]) => Promise<StorageResult>).bind(env);
  env[method] = (...args: unknown[]) => retryOnBusyFile(() => orig(...args));
}

export async function getSessionRepo(): Promise<JsonlRepoLike> {
  if (!repoPromise) {
    repoPromise = (async () => {
      const { JsonlSessionRepo } = (await loadPiAgent()) as Record<string, unknown>;
      const { NodeExecutionEnv } = (await loadPiNodeEnv()) as Record<string, unknown>;
      ensureAppDataDirs();
      const env = new (NodeExecutionEnv as new (opts: { cwd: string }) => unknown)({ cwd: getDataRoot() });
      const envRecord = env as Record<string, unknown>;
      // 仅覆写本函数独占新建的实例（闭包绑 orig 防自引用递归）；turn.ts 的
      // env 是另一个实例，不经此路径。createDir/listDir/remove 是目录级操作，
      // 不在会话文件锁病灶上，不包。
      for (const method of BUSY_RETRY_FS_METHODS) {
        wrapWithBusyRetry(envRecord, method);
      }
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

/**
 * pi Session 内建的会话级 token/费用统计（审计#20）：messageCount、
 * cachedTokens、uncachedTokens、totalTokens、costTotal。会话不存在或底层
 * 未实现时返回 null（调用方自行降级），统计失败不抛——这是展示型数据。
 */
export async function readSessionStats(threadId: number): Promise<unknown | null> {
  const session = await openSession(threadId);
  if (!session?.getSessionStats) return null;
  try {
    return await session.getSessionStats();
  } catch {
    return null;
  }
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
      // 删除流只删 JSONL、保留归档 meta：归档 {id}.meta.json 也占号，否则
      // 删除最新会话后新 id 撞归档 meta，writeConversationMeta 把它覆盖回
      // active（0911 审计 §12.4 id 撞号复活）。
      const jsonl = file.match(/^(\d+)\.jsonl$/);
      if (jsonl) {
        maxId = Math.max(maxId, parseInt(jsonl[1], 10));
        continue;
      }
      const meta = file.match(/^(\d+)\.meta\.json$/);
      if (meta) maxId = Math.max(maxId, parseInt(meta[1], 10));
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

/**
 * UI 读取变体：在 readSessionMessages 之上为「被打断的回合」补一条空的
 * stopped 标记消息。run 在工具阶段被停止时引擎不产出任何带正文的
 * assistant 消息，回合在会话里完全隐形（1.0.0 前实测）；标记让前端
 * 渲染「回答已停止」。仅喂 UI——agent-runs 的 priorMessages 走
 * readSessionMessages 原始路径，标记不得进入 Provider 上下文。
 *
 * `suppressTrailingInterruptMarker`：调用方已知该会话有活跃 run 时置位。
 * 此时末尾「有工具/思考活动但尚无正文」的回合是正常的进行中中间态，
 * 不是被停止；只跳过这一条尾标记，更早回合的真停止标记照常保留。
 */
export async function readSessionMessagesForUi(
  threadId: number,
  opts: { suppressTrailingInterruptMarker?: boolean } = {},
): Promise<SessionMessage[]> {
  const session = await openSession(threadId);
  if (session) {
    return messagesFromSession(session, {
      withInterruptMarkers: true,
      ...(opts.suppressTrailingInterruptMarker ? { suppressTrailingInterruptMarker: true } : {}),
    });
  }
  return readLegacyMessages(threadId);
}

/**
 * 可见消息的唯一枚举口径：UI 读取（readSessionMessagesForUi）与编辑重发截断
 * （truncateSessionFromMessage）都从这里取，保证「第 N 条 UI 消息」逐条对应。
 *
 * 每条可见项带 `keepThroughIndex`：把它设为会话 leaf，重新读取后这一项恰好
 * 是最后一条可见项。普通消息即其 branch index；合成标记落在「空正文但有
 * toolCall/thinking 活动」的 assistant 条目上，截断点落在标记上时 leaf 必须
 * 是产生标记的底层条目本身——保留它才会让标记继续可见（标记随回合边界合成，
 * 不能只数不存在的真实消息）。
 */
type VisibleMessage = { message: SessionMessage; keepThroughIndex: number };

function collectVisibleMessages(
  entries: SessionEntryLike[],
  opts: { withInterruptMarkers?: boolean; suppressTrailingInterruptMarker?: boolean } = {},
): VisibleMessage[] {
  const visible: VisibleMessage[] = [];
  // 自上一条可见消息以来，本轮是否出现过 assistant 工具/思考活动而始终
  // 没有产出正文（停止/中断的签名）。见到 assistant 正文或 user 边界时结算。
  let pendingInterruptStart: number | null = null;
  let lastTimestamp = "";
  // 每条可见项的 keepThroughIndex 覆盖到「下一可见项起点」的前一条：普通
  // assistant 文本后的 toolResult 等非可见条目属于它的回合，截断时一并保留
  // （否则会留下有 toolCall 无 toolResult 的悬空调用）。合成标记同理覆盖到
  // 下一边界前，保证标记原料不被剪掉。
  const closeGroupBefore = (nextStart: number) => {
    const previous = visible[visible.length - 1];
    if (previous && previous.keepThroughIndex < 0) previous.keepThroughIndex = nextStart - 1;
  };
  const flushInterruptMarker = (nextStart: number | null) => {
    // 尾部未完结回合（nextStart===null）在有活跃 run 时是进行中的中间态，
    // 跳过合成；边界结算（nextStart 为下一条起点）的真停止标记不受影响。
    const suppressTrailing = nextStart === null && opts.suppressTrailingInterruptMarker === true;
    if (opts.withInterruptMarkers && pendingInterruptStart !== null && !suppressTrailing) {
      closeGroupBefore(pendingInterruptStart);
      visible.push({
        message: {
          role: "assistant",
          content: "",
          timestamp: lastTimestamp || new Date().toISOString(),
          stopped: true,
        },
        keepThroughIndex: -1,
      });
    }
    pendingInterruptStart = null;
  };
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index]!;
    if (entry.type !== "message" || !isRecord(entry.message)) continue;
    const message = entry.message;
    if (message.role !== "user" && message.role !== "assistant") continue;
    lastTimestamp = typeof entry.timestamp === "string" ? entry.timestamp : lastTimestamp;
    const content = extractUserFacingText(message.content);
    // Intro Session kickoff: the sidecar synthesizes an internal user turn to
    // start the flow, but the user must never see a fake user message
    // (wireframe 状态①). It stays in the branch for the provider context.
    if (message.role === "user" && isIntroKickoffMessage(content)) continue;
    if (message.role === "assistant" && !content.trim()) {
      const hasUnshownActivity = Array.isArray(message.content)
        && message.content.some(
          (c) => isRecord(c) && (c.type === "toolCall" || c.type === "thinking"),
        );
      if (hasUnshownActivity && pendingInterruptStart === null) pendingInterruptStart = index;
      continue;
    }
    if (message.role === "user") flushInterruptMarker(index);
    pendingInterruptStart = null;
    closeGroupBefore(index);
    visible.push({
      message: {
        role: message.role,
        content,
        timestamp: typeof entry.timestamp === "string" ? entry.timestamp : new Date().toISOString(),
        ...(message.role === "assistant" && message.stopReason === "aborted" ? { stopped: true } : {}),
      },
      keepThroughIndex: -1,
    });
  }
  flushInterruptMarker(null);
  // 最后一条可见项覆盖到分支末尾（truncate 只取 keepMessages-1 < 末项，此值
  // 不会被截断路径使用，显式填好避免留 -1 误导调用方）。
  const last = visible[visible.length - 1];
  if (last && last.keepThroughIndex < 0) last.keepThroughIndex = entries.length - 1;
  return visible;
}

async function messagesFromSession(
  session: SessionLike,
  opts: { withInterruptMarkers?: boolean; suppressTrailingInterruptMarker?: boolean } = {},
): Promise<SessionMessage[]> {
  const entries = await session.getBranch();
  return collectVisibleMessages(entries, opts).map((item) => item.message);
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

/**
 * 面向用户展示的文本提取：按块 trim 后用单换行连接、丢弃纯空白块。
 * 模型在工具调用间隙输出的 narration 块自带 \n\n 头尾，直接拼接会让
 * 对话气泡出现连续空行；provider 历史上下文仍用 extractMessageText
 * 的原始拼接，二者不要混用。
 */
export function extractUserFacingText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((block): block is { type: string; text?: unknown } => isRecord(block) && block.type === "text")
      .map((block) => (typeof block.text === "string" ? block.text.trim() : ""))
      .filter((text) => text.length > 0)
      .join("\n");
  }
  return "";
}

// ── Edit-resend truncation（digests §11 item 7：截断派）──────────────────

/**
 * 编辑重发的截断实现：丢弃第 `keepMessages` 条可见消息及其后的整段历史。
 *
 * 通过 pi Session 的公开 storage 合同把 leaf 重指到最后一条保留的分支条目
 * （keepMessages===0 时置 null），后续 buildContext/getBranch/appendMessage
 * 都沿新 leaf 走——单线 JsonlSession 兼容、不引入 branch_id fork 树；
 * 被剪掉的行留在文件里成为孤儿分支，可追溯但不再参与任何上下文。
 * 调用方负责保证该会话当前没有活跃 agent run（见 hasActiveAgentRunForSession）。
 */
export async function truncateSessionFromMessage(threadId: number, keepMessages: number): Promise<void> {
  if (!Number.isInteger(keepMessages) || keepMessages < 0) {
    throw new Error(`invalid keep_messages: ${keepMessages}`);
  }
  const session = (await openSession(threadId)) ?? (await migrateLegacyConversation(threadId));
  if (!session) return; // 会话不存在：幂等 no-op

  const branch = await session.getBranch();
  // 与 readSessionMessagesForUi 同一枚举（含合成标记），keepMessages 指的是
  // UI 列表里的位次：保留前 keepMessages 条即可，标记也参与计数。
  const visible = collectVisibleMessages(branch, { withInterruptMarkers: true });
  if (keepMessages >= visible.length) return; // 已短于截断点：幂等 no-op

  const targetId = keepMessages === 0
    ? null
    : branch[visible[keepMessages - 1]!.keepThroughIndex]!.id;
  const storage = session.getStorage() as { setLeafId?: (id: string | null) => Promise<void> };
  if (typeof storage?.setLeafId !== "function") {
    throw new Error("session storage does not support setLeafId");
  }
  await storage.setLeafId(targetId);
}

export function deriveConversationTitle(messages: SessionMessage[], meta: ConversationMeta): string {
  // 优先级（0910 路线 B 修正）：已命名（auto/user）的 meta.title > 首句截断
  // 降级 > "新对话"。此前首句截断恒优先，会把 LLM 命名盖回去。
  if (meta.title_source && meta.title?.trim()) return meta.title.trim();
  const firstUser = messages.find((message) => message.role === "user");
  if (firstUser && firstUser.content.trim()) return firstUser.content.trim().slice(0, 120);
  return meta.title ?? "新对话";
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
          title_source: raw.title_source === "user" || raw.title_source === "auto" ? raw.title_source : null,
          status: raw.status === "archived" ? "archived" : "active",
          created_at: typeof raw.created_at === "string" ? raw.created_at : new Date().toISOString(),
          updated_at: typeof raw.updated_at === "string" ? raw.updated_at : new Date().toISOString(),
          analysis_session_ids: Array.isArray(raw.analysis_session_ids)
            ? raw.analysis_session_ids.filter((value): value is number => Number.isInteger(value) && value > 0)
            : undefined,
          analysis_refs: Array.isArray(raw.analysis_refs)
            ? raw.analysis_refs
                .map((item) =>
                  isRecord(item)
                    && typeof item.id === "number" && Number.isInteger(item.id) && item.id > 0
                    && typeof item.attached_at === "string"
                    ? { id: item.id, attached_at: item.attached_at }
                    : null,
                )
                .filter((item): item is { id: number; attached_at: string } => item !== null)
            : undefined,
          deep_read_analysis_session_ids: Array.isArray(raw.deep_read_analysis_session_ids)
            ? raw.deep_read_analysis_session_ids.filter((value): value is number => Number.isInteger(value) && value > 0)
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
    title_source: null,
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

export function writeConversationMeta(threadId: number, meta: ConversationMeta): void {
  ensureAppDataDirs();
  writeFileSync(metaFilePath(threadId), JSON.stringify(meta, null, 2), "utf8");
}

/**
 * Union new analysis ids into a thread's engaged-analysis list, and keep the
 * mount-order ledger (analysis_refs) in sync (1002 串视频修复).
 *
 * `attachedAt` 是挂载时刻，调用方（agent-runs）传挂载 run 的 started_at——它
 * 不晚于本回合任何 assistant 消息的 created_at，前端「attached_at ≤ 消息时间
 * 的最近一次挂载」才能把本回合回复归到本回合挂载的分析上。缺省落库此刻。
 *
 * - 新 id → 追加到台账末尾；
 * - 已知 id（重挂旧分析）→ 时间戳刷新并移到末尾，之后的追问归它；
 * - 升级前的旧 meta（只有并集列表）→ 台账先以空时间戳（时间未知）播种，
 *   前端对含未知时间的场景退回序数近似。
 * `analysis_session_ids` 并集语义不变（其他消费方不感知台账）。
 */
export function updateConversationAnalysisIds(
  threadId: number,
  ids: number[],
  attachedAt: string = new Date().toISOString(),
): void {
  const meta = readConversationMeta(threadId);
  const merged = new Set(meta.analysis_session_ids ?? []);
  const ledger = new Map<number, string>();
  for (const ref of meta.analysis_refs ?? []) ledger.set(ref.id, ref.attached_at);
  for (const id of merged) {
    if (!ledger.has(id)) ledger.set(id, "");
  }
  for (const id of ids) {
    if (!Number.isInteger(id) || id <= 0) continue;
    merged.add(id);
    // delete+set：重挂把该 id 移到台账末尾（挂载顺序 = 插入顺序）。
    ledger.delete(id);
    ledger.set(id, attachedAt);
  }
  meta.analysis_session_ids = [...merged].sort((a, b) => a - b);
  meta.analysis_refs = [...ledger]
    .map(([id, at]) => ({ id, attached_at: at }))
    // attached_at 升序兜底（空串自然排最前 = 挂载时间未知的旧挂载）。
    .sort((a, b) => (a.attached_at < b.attached_at ? -1 : a.attached_at > b.attached_at ? 1 : 0));
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(threadId, meta);
}

/**
 * Union non-subject deep-read analysis ids into the session's @time-link
 * fallback list — kept separate from analysis_session_ids so 深读旧分析 never
 * claims to be part of 本次讨论.
 */
export function updateConversationDeepReadAnalysisIds(threadId: number, ids: number[]): void {
  const meta = readConversationMeta(threadId);
  const merged = new Set(meta.deep_read_analysis_session_ids ?? []);
  for (const id of ids) {
    if (Number.isInteger(id) && id > 0) merged.add(id);
  }
  meta.deep_read_analysis_session_ids = [...merged].sort((a, b) => a - b);
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(threadId, meta);
}
