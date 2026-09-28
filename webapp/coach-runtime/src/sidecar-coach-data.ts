/**
 * Sidecar Coach data-access layer (Phase 1 simplified).
 *
 * Conversation content lives in Pi JSONL sessions managed by session-repo.ts
 * (JsonlSessionRepo); this module shapes those sessions into the HTTP contract
 * consumed by the frontend. Context attach/detach is removed — Coach reads
 * files directly.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import type http from "node:http";
import { join } from "node:path";

import { ensureAppDataDirs, getConversationsDir } from "./app-data.ts";
import { isRecord } from "./contracts.ts";
import {
  deriveConversationTitle,
  ensureSession,
  listSessionIds,
  nextSessionIdSync,
  readConversationMeta,
  readSessionMessagesForUi,
  SESSION_CWD,
  sessionExists,
  deleteSessionFile,
  truncateSessionFromMessage,
  writeConversationMeta,
  type ConversationMeta,
  type SessionMessage,
} from "./session-repo.ts";
import { hasActiveAgentRunForSession } from "./agent-runs.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function ownerIdFromRequest(req: http.IncomingMessage): string {
  const raw = req.headers["x-user-id"];
  if (typeof raw === "string" && raw.trim()) return raw;
  return "desktop-local";
}

/**
 * B0 locale 管道：请求级 X-Locale 头（与 ownerIdFromRequest 同构）。默认
 * zh-CN，非法值回落——与前端 lib/i18n normalizeLocale、Python 侧
 * normalize_locale_header 保持同一口径。纯管道，本波不接消费方。
 */
export function localeFromRequest(req: http.IncomingMessage): "zh-CN" | "en-US" {
  return req.headers["x-locale"] === "en-US" ? "en-US" : "zh-CN";
}

export class CoachDataError extends Error {
  constructor(public statusCode: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------------------
// Session shaping
// ---------------------------------------------------------------------------

interface SessionOut {
  id: number;
  user_id: string;
  kind: string;
  title: string | null;
  status: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
  /** 首轮已落盘但模型命名尚未生成（前端据此安排一次延迟补刷）。 */
  title_pending?: boolean;
  analysis_session_ids: number[];
  /** Non-subject deep-read analyses (@time-link fallback; NOT 本次讨论). */
  deep_read_analysis_session_ids: number[];
}

function shapeSession(
  ownerId: string,
  id: number,
  meta: ConversationMeta,
  messages: SessionMessage[],
): SessionOut {
  const lastEntry = messages[messages.length - 1];
  // 侧栏预览向前找最后一条有正文的：末尾的 stopped 空标记不充当预览。
  const lastVisible = [...messages].reverse().find((message) => message.content.trim().length > 0);
  const title = deriveConversationTitle(messages, meta);
  const updatedAt = lastEntry ? lastEntry.timestamp : meta.updated_at;
  // 首轮已发生但 auto 命名还没落库（异步生成中）→ 前端安排一次延迟补刷
  const titlePending = !meta.title_source && messages.some((message) => message.role === "user");
  return {
    id,
    user_id: ownerId,
    kind: id === 1 ? "primary" : "conversation",
    title,
    status: meta.status,
    deleted_at: null,
    created_at: meta.created_at,
    updated_at: updatedAt,
    message_count: messages.length,
    last_message_preview: lastVisible ? lastVisible.content.slice(0, 240) : null,
    title_pending: titlePending || undefined,
    analysis_session_ids: meta.analysis_session_ids ?? [],
    deep_read_analysis_session_ids: meta.deep_read_analysis_session_ids ?? [],
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function listCoachSessions(
  ownerId: string,
  opts: { includeArchived?: boolean } = {},
): Promise<{ sessions: SessionOut[] }> {
  ensureAppDataDirs();
  const ids = await listSessionIds();
  const sessions: SessionOut[] = [];
  for (const id of ids) {
    const meta = readConversationMeta(id);
    const messages = await readSessionMessagesForUi(id);
    sessions.push(shapeSession(ownerId, id, meta, messages));
  }

  // Search is done client-side by the frontend SessionRail; the sidecar only
  // filters archived sessions.
  let filtered = sessions;
  if (!opts.includeArchived) {
    filtered = filtered.filter((s) => s.status === "active");
  }

  return { schema_version: "coach_session_list.v1", sessions: filtered } as unknown as { sessions: SessionOut[] };
}

export async function createCoachSession(ownerId: string, title?: string): Promise<SessionOut> {
  ensureAppDataDirs();
  const id = nextSessionIdSync();
  const normalizedTitle = title && title.trim() ? title.trim().slice(0, 120) : "新对话";
  const now = new Date().toISOString();
  await ensureSession(id);
  const meta: ConversationMeta = {
    id,
    title: normalizedTitle,
    status: "active",
    created_at: now,
    updated_at: now,
  };
  writeConversationMeta(id, meta);
  const messages = await readSessionMessagesForUi(id);
  return shapeSession(ownerId, id, meta, messages);
}

export async function updateCoachSession(
  ownerId: string,
  sessionId: number,
  update: { title?: string; status?: "archived" },
): Promise<SessionOut> {
  const meta = readConversationMeta(sessionId);
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  if (update.title !== undefined) {
    const title = update.title.trim();
    if (!title) throw new CoachDataError(400, "session title cannot be empty");
    meta.title = title.slice(0, 120);
    // 手动改名后自动命名永不再覆盖（title_source 守卫）。
    meta.title_source = "user";
  }
  if (update.status === "archived") {
    meta.status = "archived";
  }
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(sessionId, meta);
  const messages = await readSessionMessagesForUi(sessionId);
  return shapeSession(ownerId, sessionId, meta, messages);
}

export async function getCoachSessionDetail(
  ownerId: string,
  sessionId: number,
): Promise<SessionOut & { messages: Array<Record<string, unknown>> }> {
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  const meta = readConversationMeta(sessionId);
  // 活跃 run 期间，末尾「有工具/思考活动但尚无正文」的 assistant 回合是正常
  // 进行中的中间态：若照常合成空 stopped 标记，前端刷新会误挂「回答已停止」
  // 徽标（0915 CDP 真机实测；run 落定后徽标自行消失）。更早回合的真停止
  // 标记必须保留，故只抑制尾部这一条。
  const entries = await readSessionMessagesForUi(sessionId, {
    ...(hasActiveAgentRunForSession(sessionId) ? { suppressTrailingInterruptMarker: true } : {}),
  });
  const base = shapeSession(ownerId, sessionId, meta, entries);
  const messages = entries.map((entry, index) => ({
    id: index + 1,
    role: entry.role,
    content: entry.content,
    created_at: entry.timestamp,
    legacy_session_id: null,
    ...(entry.stopped ? { stopped: true } : {}),
  }));
  return { ...base, messages };
}

/**
 * 编辑重发截断（digests §11 item 7）：把会话可见消息截到前 `keepMessages`
 * 条，返回截断后的完整 session detail 供前端一次刷新。
 * 活跃 run 会继续向同一 JSONL 追加——先拒绝（409 session_busy），
 * 由前端在 run 结束后重试。
 */
export async function truncateCoachSession(
  ownerId: string,
  sessionId: number,
  keepMessages: number,
): Promise<SessionOut & { messages: Array<Record<string, unknown>> }> {
  if (!Number.isInteger(sessionId) || sessionId <= 0) {
    throw new CoachDataError(400, "Coach session id is invalid");
  }
  if (!Number.isInteger(keepMessages) || keepMessages < 0) {
    throw new CoachDataError(400, "keep_messages must be a non-negative integer");
  }
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  if (hasActiveAgentRunForSession(sessionId)) {
    throw new CoachDataError(409, "session_busy");
  }
  await truncateSessionFromMessage(sessionId, keepMessages);
  return getCoachSessionDetail(ownerId, sessionId);
}

export async function deleteCoachSession(ownerId: string, sessionId: number): Promise<SessionOut> {
  const meta = readConversationMeta(sessionId);
  meta.status = "archived";
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(sessionId, meta);
  // Remove conversation content but keep meta for audit
  await deleteSessionFile(sessionId);
  return shapeSession(ownerId, sessionId, meta, []);
}

// ---------------------------------------------------------------------------
// Usage records（「调用记录」：本机 Coach AI 调用逐笔明细 + 本月汇总）
// ---------------------------------------------------------------------------

/** 记录条数上限的服务端 clamp 口径（唯一事实源）。 */
export const USAGE_RECORDS_DEFAULT_LIMIT = 50;
export const USAGE_RECORDS_MAX_LIMIT = 200;

/** 单笔调用（一条带 usage 的 assistant 消息）；字段全 snake_case 与其它端点一致。 */
interface UsageRecordOut {
  session_id: number;
  session_title: string | null;
  model: string | null;
  provider: string | null;
  timestamp: string;
  /** null = provider 没报这个数（0 是真实计量）；前端展示时按 0 兜底。 */
  usage: {
    input: number | null;
    output: number | null;
    cache_read: number | null;
    cache_write: number | null;
    reasoning: number | null;
    total_tokens: number | null;
  };
}

export interface CoachUsageRecordsOut {
  generated_at: string;
  month: {
    /** 本地时区当月键，形如 "2026-09"。 */
    key: string;
    count: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    /** cache_read/(input+cache_read) 的比例（0..1）；分母为 0（无计量可算）时 null。 */
    cache_hit_rate: number | null;
  };
  records: UsageRecordOut[];
}

function usageNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 本地时区月份键（跨时区不串月：按机器本地日历算，不按 UTC）。 */
function localMonthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function clampUsageLimit(limit: number | undefined): number {
  if (limit === undefined || !Number.isFinite(limit)) return USAGE_RECORDS_DEFAULT_LIMIT;
  return Math.min(USAGE_RECORDS_MAX_LIMIT, Math.max(1, Math.trunc(limit)));
}

/** 会话标题 → 记录标题。readConversationMeta 对「从未命名」的会话返回兜底标题
 *  「新对话」（meta 缺失或标题为空时都是它），那不是真实命名——按 null 返回，
 *  由前端用自己的 i18n 兜底（否则 en-US 界面会漏出中文）。 */
function usageRecordTitle(title: unknown): string | null {
  if (typeof title !== "string") return null;
  const trimmed = title.trim();
  return trimmed && trimmed !== "新对话" ? trimmed : null;
}

/** 单条 message 条目 → 调用记录；不是「带 usage 的 assistant 消息」时回 null。 */
function usageRecordFromEntry(
  sessionId: number,
  sessionTitle: string | null,
  entry: { type: string; timestamp?: string; message?: unknown },
): UsageRecordOut | null {
  if (entry.type !== "message" || !isRecord(entry.message)) return null;
  const message = entry.message;
  if (message.role !== "assistant" || !isRecord(message.usage)) return null;
  if (typeof entry.timestamp !== "string" || !entry.timestamp) return null;
  const usage = message.usage;
  return {
    session_id: sessionId,
    session_title: sessionTitle,
    model: typeof message.model === "string" ? message.model : null,
    provider: typeof message.provider === "string" ? message.provider : null,
    timestamp: entry.timestamp,
    usage: {
      input: usageNumber(usage.input),
      output: usageNumber(usage.output),
      cache_read: usageNumber(usage.cacheRead),
      cache_write: usageNumber(usage.cacheWrite),
      reasoning: usageNumber(usage.reasoning),
      total_tokens: usageNumber(usage.totalTokens),
    },
  };
}

/**
 * 摊平本机全部 Pi 会话里 assistant 消息自带的 model/provider/usage，供用户中心
 * 「调用记录」卡展示逐笔明细与本月汇总（用户中心线框）。
 *
 * 口径：
 * - 纯本地 JSONL 直读，不依赖任何服务端：断网、未登录、BYOK 都照常可用，
 *   也不产生任何上传；
 * - usage 字段缺失记 null（provider 没报 ≠ 0）；本月合计把 null 当 0 计入；
 * - 列表按 timestamp 降序后截前 limit 条，本月汇总统计全部记录（不受截断影响）；
 * - 单个会话读取失败（文件损坏/已删除）只跳过它，不让一个坏文件拖垮整张列表；
 * - 只统计**活跃分支**：从文件最后一行沿 parentId 回溯到根（与 pi Session 的
 *   leaf 语义一致）——编辑重发截断留下的孤儿行留在文件里但不计入，否则截断
 *   过的调用会被重复统计。
 *
 * 性能注记（0928 真机）：此前经 openSession()+getBranch() 逐会话读取，190 个
 * 文件实测 12.5s/次且无缓存，用户中心打开后卡片空等十几秒。改为直读文件 +
 * 行级 JSON.parse（"usage" 子串预筛），避免为每个会话构建完整 pi Session——
 * 同数据规模实测 364ms。若未来退化到秒级，须回到缓存/增量方案而不是再调参。
 *
 * 统计口径（0928 复核）：只走活跃分支的原始条目（从最后一行沿 parentId 回溯），
 * 比旧 getBranch 视图**多**计 pi compaction 折叠进摘要的历史调用——那些调用
 * 真实消耗过额度，账单口径应当计入（实测差 126 条/9 月，全部在 1 号主会话）。
 */
export async function listCoachUsageRecords(limit?: number): Promise<CoachUsageRecordsOut> {
  ensureAppDataDirs();
  const bounded = clampUsageLimit(limit);

  // 枚举会话文件：--coach--/ 下的 pi 命名（{时间戳}_{id}.jsonl）优先，legacy 根
  // 目录 {id}.jsonl 兜底；同一 id 只取一份（pi 布局优先于 legacy）。
  const dir = getConversationsDir();
  const coachDir = join(dir, `--${SESSION_CWD}--`);
  const files: Array<{ path: string; id: number }> = [];
  if (existsSync(coachDir)) {
    for (const file of readdirSync(coachDir)) {
      const match = file.match(/_(\d+)\.jsonl$/);
      if (match) files.push({ path: join(coachDir, file), id: Number(match[1]) });
    }
  }
  if (existsSync(dir)) {
    for (const file of readdirSync(dir)) {
      const match = file.match(/^(\d+)\.jsonl$/);
      if (match) files.push({ path: join(dir, file), id: Number(match[1]) });
    }
  }

  const records: UsageRecordOut[] = [];
  const seenIds = new Set<number>();
  for (const { path: filePath, id } of files) {
    if (seenIds.has(id)) continue;
    seenIds.add(id);
    let content: string;
    try {
      content = readFileSync(filePath, "utf8");
    } catch {
      continue; // 坏会话文件：跳过该会话，其余记录照常
    }
    // 活跃分支重建：id → 条目 表 + 从最后一行沿 parentId 走到根。中途断链
    //（parentId 指向的行损坏/缺失）自然停在断点，环状引用由已访问集合兜底。
    const byId = new Map<string, { entry: Record<string, unknown>; parent: string | null; raw: string }>();
    let lastId: string | null = null;
    for (const line of content.split("\n")) {
      if (!line.trim()) continue;
      let entry: unknown;
      try {
        entry = JSON.parse(line);
      } catch {
        continue; // 损坏行：跳过
      }
      if (!isRecord(entry) || typeof entry.id !== "string") continue;
      byId.set(entry.id, {
        entry,
        parent: typeof entry.parentId === "string" ? entry.parentId : null,
        raw: line,
      });
      lastId = entry.id;
    }
    if (lastId === null) continue; // 全文件无有效条目
    const branchIds = new Set<string>();
    let cursor: string | null = lastId;
    while (cursor !== null && byId.has(cursor) && !branchIds.has(cursor)) {
      branchIds.add(cursor);
      cursor = byId.get(cursor)!.parent;
    }

    const meta = readConversationMeta(id);
    const title = usageRecordTitle(meta.title);
    for (const entryId of branchIds) {
      const item = byId.get(entryId)!;
      // 预筛用原始行：带 usage 的 assistant 条目必含这两个子串，绝大多数行免判断。
      if (!item.raw.includes('"assistant"') || !item.raw.includes('"usage"')) continue;
      const record = usageRecordFromEntry(id, title, item.entry);
      if (record) records.push(record);
    }
  }
  records.sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0));

  const monthKey = localMonthKey(new Date());
  let count = 0;
  let input = 0;
  let output = 0;
  let cacheRead = 0;
  for (const record of records) {
    const at = new Date(record.timestamp);
    if (Number.isNaN(at.getTime()) || localMonthKey(at) !== monthKey) continue;
    count += 1;
    input += record.usage.input ?? 0;
    output += record.usage.output ?? 0;
    cacheRead += record.usage.cache_read ?? 0;
  }
  const cacheDenominator = input + cacheRead;

  return {
    generated_at: new Date().toISOString(),
    month: {
      key: monthKey,
      count,
      input_tokens: input,
      output_tokens: output,
      cache_read_tokens: cacheRead,
      cache_hit_rate: cacheDenominator > 0 ? cacheRead / cacheDenominator : null,
    },
    records: records.slice(0, bounded),
  };
}

export async function getCoachPrimary(
  ownerId: string,
  sessionId?: number,
): Promise<{
  thread: { id: number; user_id: string; kind: string; created_at: string; updated_at: string };
  messages: Array<{ id: number; role: string; content: string; created_at: string }>;
  refs: unknown[];
}> {
  const targetId = sessionId ?? 1;
  const entries = await readSessionMessagesForUi(targetId);
  return {
    thread: {
      id: targetId,
      user_id: ownerId,
      kind: "primary",
      created_at: new Date().toISOString(),
      updated_at: entries.length > 0 ? entries[entries.length - 1].timestamp : new Date().toISOString(),
    },
    messages: entries.map((entry, index) => ({
      id: index + 1,
      role: entry.role,
      content: entry.content,
      created_at: entry.timestamp,
    })),
    refs: [],
  };
}
