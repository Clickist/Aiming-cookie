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

import { appendFileSync, existsSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from "node:fs";
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
  /** 应用重启/进程死亡打断的合成标记；用户主动停止只有 stopped。 */
  interrupted?: boolean;
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

// ── 会话查找缓存（性能审计 TS-4）─────────────────────────────────────────
// findSessionMetadata 此前每次 openSession 都全量 repo.list（每会话一次 header
// 文件读）：回合路径一次扫两遍、/v1/sessions 每次轮询对每会话再全量解析——
// O(N²) 文件读，与已修 usage 12.5s→364ms 案同根。id→metadata 进程内缓存：
// 首次全扫后全命中，create/迁移后回填，删除时清除。
const metadataByThread = new Map<number, SessionMetadataLike>();

// UI 消息列表 stat 缓存：listCoachSessions 每次 3s/10s 轮询对每会话全量解析
// JSONL。文件 (mtimeMs, size) 未变即复用上次解析结果；追加/截断/删除都会改
// 变 stat，自然失效，不需要显式失效钩子。
type UiMessagesCacheEntry = { mtimeMs: number; size: number; messages: SessionMessage[] };
const uiMessagesCache = new Map<number, UiMessagesCacheEntry>();

async function findSessionMetadata(threadId: number): Promise<SessionMetadataLike | null> {
  const cached = metadataByThread.get(threadId);
  if (cached) return cached;
  const repo = await getSessionRepo();
  const sessions = await repo.list({ cwd: SESSION_CWD });
  for (const session of sessions) {
    const parsed = Number(session.id);
    if (Number.isInteger(parsed) && parsed > 0 && !metadataByThread.has(parsed)) {
      metadataByThread.set(parsed, session);
    }
  }
  return metadataByThread.get(threadId) ?? null;
}

// ── 会话 JSONL 撕裂自愈（鲁棒性审计 P0-1）────────────────────────────────
// pi appendEntry 是单次 appendFile（无 fsync、无原子写）：进程死在写入中途
// （unhandledRejection / taskkill sweep / 断电）会留下尾部半行 JSON，下次
// repo.open 抛 invalid_entry——该会话所有后续 turn 判不可重试失败、detail
// 500，整段对话事实砖化。自愈策略：
//   1) 按「最长全行合法前缀」截断重建（尾部孤儿行本就是未完成写入，丢弃是
//      唯一正解）；被丢弃的内容写入 *.corrupt-<时间戳> 隔离文件留痕，绝不
//      静默蒸发；重写走 tmp+rename 原子替换。
//   2) header 损坏（无法定位合法起点）时整文件改名隔离，openSession 返回
//      null，ensureSession 以同一 id 重建空会话（meta 标题保留）。
//   3) 自愈全程带 cause 码落 coach-error.log；自愈本身失败时原样抛出原始
//      错误——分类层落 local_storage_corrupt（可重试），绝不静默降级。
// 写路径原子性口径：整文件写（meta、重建重写）走 tmp+rename；JSONL 追加
// 保持单次 appendFile（逐条重写全文件是 O(N²)），其崩溃面由本自愈兜底——
// 这正是 LDJSON 存储的标准崩溃恢复形态。

function logCoachCause(line: string): void {
  try {
    appendFileSync(join(getDataRoot(), "coach-error.log"), `${line}\n`, "utf8");
  } catch {
    // best-effort：日志失败不掩盖自愈结果
  }
}

/** pi SessionError 的鸭子类型识别（pi 是动态加载的，不 import 其类型）。 */
function sessionCorruptionCause(error: unknown): "invalid_entry" | "invalid_session" | null {
  if (!error || typeof error !== "object") return null;
  const code = (error as { code?: unknown }).code;
  if (code === "invalid_entry" || code === "invalid_session") return code;
  const name = (error as { name?: unknown }).name;
  const message = (error as { message?: unknown }).message;
  if (name === "SessionError" && typeof message === "string" && /Invalid JSONL session file/.test(message)) {
    return /invalid_entry/.test(message) ? "invalid_entry" : "invalid_session";
  }
  return null;
}

/** pi parseHeaderLine 的结构镜像：只为定位撕裂尾，重写后由 pi 权威校验，
 * 若判宽了会在重开时再次抛错并原样上报，绝不静默。 */
function parsesAsSessionHeader(line: string): boolean {
  try {
    const parsed = JSON.parse(line) as unknown;
    if (!isRecord(parsed) || Array.isArray(parsed)) return false;
    return parsed.type === "session"
      && parsed.version === 3
      && typeof parsed.id === "string" && parsed.id.length > 0
      && typeof parsed.timestamp === "string" && parsed.timestamp.length > 0
      && typeof parsed.cwd === "string" && parsed.cwd.length > 0;
  } catch {
    return false;
  }
}

/** pi parseEntryLine 的结构镜像（同上，宁严勿宽）。 */
function parsesAsSessionEntry(line: string): boolean {
  try {
    const parsed = JSON.parse(line) as unknown;
    if (!isRecord(parsed) || Array.isArray(parsed)) return false;
    if (typeof parsed.type !== "string" || typeof parsed.id !== "string" || parsed.id.length === 0) return false;
    if (parsed.parentId !== null && typeof parsed.parentId !== "string") return false;
    if (typeof parsed.timestamp !== "string" || parsed.timestamp.length === 0) return false;
    if (parsed.type === "leaf" && parsed.targetId !== null && typeof parsed.targetId !== "string") return false;
    return true;
  } catch {
    return false;
  }
}

type CorruptionRepairOutcome =
  | { status: "rebuilt"; keptLines: number; droppedLines: number; firstBadLine: number }
  | { status: "quarantined" }
  | { status: "failed" };

function quarantinePathFor(filePath: string): string {
  // 后缀不能以 .jsonl 结尾：repo.list 按后缀发现会话，隔离件不得再次入选。
  return `${filePath}.corrupt-${new Date().toISOString().replace(/[:.]/g, "-")}`;
}

/**
 * 按「最长全行合法前缀」截断重建会话 JSONL。同步 fs 直读直写（不经 env 的
 * busy-retry 包装）：自愈是一次原子动作，失败就原样上报，不重试不递归。
 */
function repairCorruptSessionFile(filePath: string): CorruptionRepairOutcome {
  let content: string;
  try {
    content = readFileSync(filePath, "utf8");
  } catch {
    return { status: "failed" };
  }
  const lines = content.split("\n");
  let keepCount = 0;
  let firstBadLine = 0;
  if (lines[0] !== undefined && lines[0].trim() && parsesAsSessionHeader(lines[0])) {
    keepCount = 1;
    for (let index = 1; index < lines.length; index++) {
      const line = lines[index]!;
      if (!line.trim()) continue; // pi 解析前本就过滤空行，不算损坏
      if (parsesAsSessionEntry(line)) {
        keepCount = index + 1;
        continue;
      }
      firstBadLine = index + 1;
      break;
    }
  }
  const droppedLines = lines.slice(keepCount).filter((line) => line.trim().length > 0);

  if (keepCount === 0) {
    // header 不合法：无法定位重建起点，整文件隔离（调用方按会话缺失处理，
    // ensureSession 会以同一 id 重建）。
    try {
      renameSync(filePath, quarantinePathFor(filePath));
      return { status: "quarantined" };
    } catch {
      return { status: "failed" };
    }
  }

  if (droppedLines.length === 0) {
    // 找不到损坏行（如 header 之后全部合法却仍抛错）：不自愈，原样上报。
    return { status: "failed" };
  }

  const quarantinePath = quarantinePathFor(filePath);
  try {
    // 隔离件先落盘（留痕优先于修复：失败则放弃自愈、原样上报）。
    writeFileSync(quarantinePath, droppedLines.join("\n") + "\n", "utf8");
    // 原文件 tmp+rename 原子替换，杜绝「重建写一半」二次撕裂。
    const tmpPath = `${filePath}.repair-tmp`;
    writeFileSync(tmpPath, lines.slice(0, keepCount).map((line) => line + "\n").join(""), "utf8");
    renameSync(tmpPath, filePath);
  } catch {
    return { status: "failed" };
  }
  return { status: "rebuilt", keptLines: keepCount, droppedLines: droppedLines.length, firstBadLine };
}

export async function openSession(threadId: number): Promise<SessionLike | null> {
  const metadata = await findSessionMetadata(threadId);
  if (!metadata) return null;
  const repo = await getSessionRepo();
  try {
    return await repo.open(metadata);
  } catch (error) {
    const cause = sessionCorruptionCause(error);
    if (!cause) throw error;
    const path = metadata.path;
    try {
      const outcome = repairCorruptSessionFile(path);
      if (outcome.status === "quarantined") {
        metadataByThread.delete(threadId);
        uiMessagesCache.delete(threadId);
        logCoachCause(`${new Date().toISOString()} [session-repo] corrupt_session_quarantined cause=${cause} path=${path}`);
        return null; // ensureSession 会以同一 id 重建空会话（meta 标题保留）
      }
      if (outcome.status === "rebuilt") {
        logCoachCause(
          `${new Date().toISOString()} [session-repo] corrupt_session_repaired cause=${cause} path=${path}`
          + ` kept_lines=${outcome.keptLines} dropped_lines=${outcome.droppedLines} first_bad_line=${outcome.firstBadLine}`,
        );
        return await repo.open(metadata);
      }
    } catch (repairError) {
      logCoachCause(
        `${new Date().toISOString()} [session-repo] corrupt_session_repair_failed cause=${cause} path=${path}`
        + ` error=${repairError instanceof Error ? repairError.message : String(repairError)}`,
      );
    }
    throw error;
  }
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
  if (migrated) {
    // 迁移建的 pi 会话元数据回填查找缓存（避免下次再全扫）。
    try {
      metadataByThread.set(threadId, await migrated.getMetadata());
    } catch {
      // 元数据不可得时下次全扫兜底
    }
    return migrated;
  }
  const repo = await getSessionRepo();
  const session = await repo.create({ cwd: SESSION_CWD, id: String(threadId) });
  try {
    metadataByThread.set(threadId, await session.getMetadata());
  } catch {
    // 元数据不可得时下次全扫兜底
  }
  return session;
}

export async function sessionExists(threadId: number): Promise<boolean> {
  if (await openSession(threadId)) return true;
  return existsSync(join(getConversationsDir(), `${threadId}.jsonl`));
}

export async function deleteSessionFile(threadId: number): Promise<void> {
  const metadata = await findSessionMetadata(threadId);
  if (!metadata) return;
  await (await getSessionRepo()).delete(metadata);
  metadataByThread.delete(threadId);
  uiMessagesCache.delete(threadId);
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
 *
 * `trailingTurnInProcess`：调用方已知尾部回合在本进程内（内存里有 run 记录）
 * 时置位——合成尾标记不算「重启打断」。该标志随 run 生命周期变化但不改
 * 文件 stat，读写都绕过 stat 缓存，避免标签被旧解析结果钉死。
 */
export async function readSessionMessagesForUi(
  threadId: number,
  opts: {
    suppressTrailingInterruptMarker?: boolean;
    trailingTurnInProcess?: boolean;
  } = {},
): Promise<SessionMessage[]> {
  // TS-4：无抑制位的常规读取（会话列表轮询）按文件 stat 缓存——文件未变时
  // 直接复用上次解析结果，3s/10s 轮询不再对每会话全量解析 JSONL。缓存数组
  // 视为只读（消费方 shapeSession/detail 只读遍历）。
  if (!opts.suppressTrailingInterruptMarker && !opts.trailingTurnInProcess) {
    const filePath = metadataByThread.get(threadId)?.path
      ?? (() => {
        const legacy = join(getConversationsDir(), `${threadId}.jsonl`);
        return existsSync(legacy) ? legacy : null;
      })();
    if (filePath) {
      let stat: { mtimeMs: number; size: number } | null = null;
      try {
        const info = statSync(filePath);
        stat = { mtimeMs: info.mtimeMs, size: info.size };
      } catch {
        stat = null; // 文件刚被删除等：走无缓存路径
      }
      const hit = stat ? uiMessagesCache.get(threadId) : undefined;
      if (hit && stat && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) return hit.messages;
      const messages = await readSessionMessagesForUiUncached(threadId, opts);
      if (stat) uiMessagesCache.set(threadId, { ...stat, messages });
      return messages;
    }
  }
  return readSessionMessagesForUiUncached(threadId, opts);
}

async function readSessionMessagesForUiUncached(
  threadId: number,
  opts: {
    suppressTrailingInterruptMarker?: boolean;
    trailingTurnInProcess?: boolean;
  } = {},
): Promise<SessionMessage[]> {
  const session = await openSession(threadId);
  if (session) {
    return messagesFromSession(session, {
      withInterruptMarkers: true,
      ...(opts.suppressTrailingInterruptMarker ? { suppressTrailingInterruptMarker: true } : {}),
      ...(opts.trailingTurnInProcess ? { trailingTurnInProcess: true } : {}),
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
  opts: {
    withInterruptMarkers?: boolean;
    suppressTrailingInterruptMarker?: boolean;
    trailingTurnInProcess?: boolean;
  } = {},
): VisibleMessage[] {
  const visible: VisibleMessage[] = [];
  // 自上一条可见消息以来，本轮是否出现过 assistant 工具/思考活动而始终
  // 没有产出正文（停止/中断的签名）。见到 assistant 正文或 user 边界时结算。
  let pendingInterruptStart: number | null = null;
  // 回合「工具阶段未收尾」跟踪（1012 报障补）：assistant 带 toolCall（且非用户
  // 停止）却始终没等到终稿——即使该条已输出部分正文，回复也是被切断的半截话。
  // 此前只有「无正文」形态会合成标记，有正文的切断形态重启后完全隐形
  // （报障实锤：回复停在「我再读一下事件数据」+ 工具结果，无任何提示）。
  let openToolActivity = false;
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
    const needsMarker = opts.withInterruptMarkers && !suppressTrailing
      && (pendingInterruptStart !== null || openToolActivity);
    if (needsMarker) {
      if (pendingInterruptStart !== null) {
        closeGroupBefore(pendingInterruptStart);
      } else {
        // 有正文形态：标记追加在末条可见项（半截话）之后，并让它覆盖本回合
        // 的工具结果，避免截断路径留下悬空 toolCall。
        closeGroupBefore(nextStart ?? entries.length);
      }
      visible.push({
        message: {
          role: "assistant",
          content: "",
          timestamp: lastTimestamp || new Date().toISOString(),
          stopped: true,
          // 只有「尾部」标记才断言原因：尾部回合在本进程内（内存里有 run 记录）
          // → 本进程内结束（用户停止等），不算重启打断；无记录（进程死亡清空
          // Map）→ 重启打断。边界结算的历史标记一律中性（stopped 无 interrupted）
          // ——编辑重发截断与重启打断在数据上同形，历史位断言原因会误标
          // （1012 修：编辑重发也是「回合没说完」，但不能说成「应用重启」）。
          interrupted: nextStart === null && opts.trailingTurnInProcess !== true,
        },
        keepThroughIndex: -1,
      });
    }
    pendingInterruptStart = null;
    openToolActivity = false;
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
    // assistant 条目：带 toolCall 且非用户停止/非错误收尾（stopReason 非
    // aborted/error）时记为「工具阶段未收尾」；后续出现不带 toolCall 的终稿
    // 或 user 边界才结算。error 排除：provider 断流的失败回合已有错误卡与
    // 重试入口，再合成「被打断」标记是重复且误导。
    if (message.role === "assistant") {
      const hasToolCall = Array.isArray(message.content)
        && message.content.some((c) => isRecord(c) && c.type === "toolCall");
      const terminalStop = message.stopReason === "aborted" || message.stopReason === "error";
      if (hasToolCall && !terminalStop) {
        openToolActivity = true;
      } else if ((content.trim() && !hasToolCall) || message.stopReason === "aborted" || message.stopReason === "error") {
        openToolActivity = false;
      }
    }
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
  opts: {
    withInterruptMarkers?: boolean;
    suppressTrailingInterruptMarker?: boolean;
    trailingTurnInProcess?: boolean;
  } = {},
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
  // P0-1 写路径原子性：meta 是整文件重写的小文件，tmp+rename 原子替换——
  // 进程死在写入中途不会留下半截 meta（旧文件保持完整旧态）。
  const path = metaFilePath(threadId);
  const tmpPath = `${path}.tmp`;
  writeFileSync(tmpPath, JSON.stringify(meta, null, 2), "utf8");
  renameSync(tmpPath, path);
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
