/**
 * Composer 编排纯助手（frontend-parity 批 5，digests §11）。
 *
 * 只承载可独立验证的纯逻辑：队列 chips 预览截断、草稿三级持久键、
 * @ 引用下拉的触发/过滤/落词、↑ 发送历史索引步进。React 状态编排留在
 * CoachPanel；这里不 import React，便于 node:test 直接覆盖。
 */

import type { CoachQuote } from "./quote";

// ── 运行中队列 chips（item 1）────────────────────────────────────────────

/** chips 预览 96 字符截断（超出补省略号），规格出处 digests §11。 */
export const QUEUE_PREVIEW_MAX_CHARS = 96;

export function truncateQueuePreview(text: string, max: number = QUEUE_PREVIEW_MAX_CHARS): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > max ? `${collapsed.slice(0, max)}…` : collapsed;
}

/** 本批队列是前端权威态（引擎 steer/followUp 无逐条取消动词）：每条可视可编辑可删。 */
export type QueuedChip = { id: number; text: string; /** 随这条消息一起发送的结构化分析引用。 */ refs?: string[] };

export function removeQueuedChip(items: QueuedChip[], id: number): QueuedChip[] {
  return items.filter((item) => item.id !== id);
}

export function updateQueuedChip(items: QueuedChip[], id: number, text: string): QueuedChip[] {
  return items.map((item) => (item.id === id ? { ...item, text } : item));
}

// ── 草稿三级持久键（item 4）──────────────────────────────────────────────

/**
 * 三级键：sessionId 会话级 | PENDING_CONVO 待定会话 | NEW_CONVO 新对话。
 * 多窗格实例追加 pane 后缀防互相覆盖；空后缀保持键稳定。
 */
export type CoachDraftScope =
  | { kind: "session"; sessionId: number }
  | { kind: "pending-convo" }
  | { kind: "new-convo" };

export const COACH_DRAFT_STORAGE_PREFIX = "aiming-cookie.coach-draft";
export const COACH_DRAFT_DEBOUNCE_MS = 400;

export function coachDraftStorageKey(scope: CoachDraftScope, paneSuffix?: string): string {
  let key: string;
  switch (scope.kind) {
    case "session":
      key = `${COACH_DRAFT_STORAGE_PREFIX}.session.${scope.sessionId}`;
      break;
    case "pending-convo":
      key = `${COACH_DRAFT_STORAGE_PREFIX}.PENDING_CONVO`;
      break;
    case "new-convo":
      key = `${COACH_DRAFT_STORAGE_PREFIX}.NEW_CONVO`;
      break;
  }
  const suffix = typeof paneSuffix === "string" ? paneSuffix.trim() : "";
  return suffix ? `${key}.pane-${suffix}` : key;
}

function storageAvailable(storage: Storage | null | undefined): storage is Storage {
  try {
    return Boolean(storage && typeof storage.getItem === "function");
  } catch {
    return false;
  }
}

export function readCoachDraft(storage: Storage | null | undefined, key: string): string {
  if (!storageAvailable(storage)) return "";
  try {
    return storage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function writeCoachDraft(storage: Storage | null | undefined, key: string, value: string): void {
  if (!storageAvailable(storage)) return;
  try {
    if (value) storage.setItem(key, value);
    else storage.removeItem(key);
  } catch {
    // 配额满/隐私模式等写失败必须静默：持久化是增强不是功能依赖。
  }
}

export function clearCoachDraft(storage: Storage | null | undefined, key: string): void {
  writeCoachDraft(storage, key, "");
}

// ── 草稿 envelope v2（划选引用持久化，digests §11 批5 + quote-feature 调研 §3.2）──

/**
 * 拍板③：引用块随草稿持久化。存储值升级为 JSON envelope
 * `{ v: 2, text, quotes }`；读取端遇到历史纯文本值（不以 `{` 开头）按
 * legacy v1 处理——text 原样、quotes 为空。text/quotes 双空时移除键，
 * 与旧 writeCoachDraft 的「空值清除」语义一致。
 */
export type CoachDraftEnvelopeV2 = { v: 2; text: string; quotes: CoachQuote[] };
export type RestoredCoachDraft = { text: string; quotes: CoachQuote[] };

function isCoachQuoteEntry(value: unknown): value is CoachQuote {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { id?: unknown; text?: unknown };
  return typeof candidate.id === "number" && Number.isFinite(candidate.id) && typeof candidate.text === "string";
}

export function readCoachDraftEnvelope(storage: Storage | null | undefined, key: string): RestoredCoachDraft {
  if (!storageAvailable(storage)) return { text: "", quotes: [] };
  let raw: string;
  try {
    raw = storage.getItem(key) ?? "";
  } catch {
    return { text: "", quotes: [] };
  }
  if (!raw) return { text: "", quotes: [] };
  // 不以 { 开头＝legacy v1 纯文本草稿。
  if (!raw.startsWith("{")) return { text: raw, quotes: [] };
  try {
    const parsed = JSON.parse(raw) as { v?: unknown; text?: unknown; quotes?: unknown };
    if (parsed.v !== 2) throw new Error("unsupported envelope version");
    return {
      text: typeof parsed.text === "string" ? parsed.text : "",
      quotes: Array.isArray(parsed.quotes) ? parsed.quotes.filter(isCoachQuoteEntry) : [],
    };
  } catch {
    // 以 { 开头但不是合法 envelope：按 legacy 文本保底，不丢用户草稿。
    return { text: raw, quotes: [] };
  }
}

export function writeCoachDraftEnvelope(
  storage: Storage | null | undefined,
  key: string,
  value: { text: string; quotes: CoachQuote[] },
): void {
  if (!storageAvailable(storage)) return;
  try {
    if (!value.text.trim() && value.quotes.length === 0) {
      storage.removeItem(key);
      return;
    }
    const envelope: CoachDraftEnvelopeV2 = { v: 2, text: value.text, quotes: value.quotes };
    storage.setItem(key, JSON.stringify(envelope));
  } catch {
    // 配额满/隐私模式等写失败必须静默：持久化是增强不是功能依赖。
  }
}

// ── @ 引用下拉（item 3，LibreChat Mention 骨架）──────────────────────────

export type MentionCandidate = {
  /** 机器引用（analysis:N，随发送结构化挂载）或场景名（写入正文）。 */
  token: string;
  /** 下拉展示主标签（人话：场景名 · 对局时间）。 */
  label: string;
  /** 下拉右侧类型说明。 */
  hint: string;
};

/**
 * 汇总引用候选：analysis:N 引用物取自讨论挂载/进行中分析/最近完成的分析，
 * 主标签由调用方合成（场景名 · 对局时间，人话，绝不出现 analysis:N 机器码——
 * 0911 点点）；场景名候选来自训练安排/会话快照。输入重复时去重，顺序稳定。
 */
export function buildMentionCandidates(input: {
  analysisIds?: number[];
  analysisLabels?: Record<number, string | null>;
  scenarioNames?: Array<string | null>;
}): MentionCandidate[] {
  const candidates: MentionCandidate[] = [];
  const seenTokens = new Set<string>();
  const seenLabels = new Set<string>();
  for (const id of input.analysisIds ?? []) {
    if (!Number.isInteger(id) || id <= 0 || seenTokens.has(`analysis:${id}`)) continue;
    seenTokens.add(`analysis:${id}`);
    const composed = input.analysisLabels?.[id];
    const label = composed && composed.trim() ? composed.trim() : `分析 #${id}`;
    candidates.push({ token: `analysis:${id}`, label, hint: "" });
  }
  for (const name of input.scenarioNames ?? []) {
    const trimmed = typeof name === "string" ? name.trim() : "";
    if (!trimmed || seenTokens.has(trimmed) || seenLabels.has(trimmed)) continue;
    seenTokens.add(trimmed);
    seenLabels.add(trimmed);
    candidates.push({ token: trimmed, label: trimmed, hint: "训练场景 · 聊成绩与计划" });
  }
  return candidates;
}

const MENTION_QUERY_ALLOWED = /[\p{L}\p{N}_.:#-]/u;

/**
 * 解析光标处正在输入的 @ 查询片段。无触发、@ 前不是边界（行首或空白）、
 * 片段内出现非法字符（空白/第二个 @ 等）都返回 null＝关闭下拉。
 */
export function activeMentionQuery(text: string, caret: number): string | null {
  if (!text || caret <= 0 || caret > text.length) return null;
  const before = text.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1] ?? "")) return null;
  const queryChars = [...before.slice(at + 1)];
  let query = "";
  for (const char of queryChars) {
    if (/\s/.test(char)) return null; // 已有空格：token 化完成，禁止再触发
    if (char === "@" || !MENTION_QUERY_ALLOWED.test(char)) return null;
    query += char;
  }
  return query;
}

export function filterMentionCandidates(candidates: MentionCandidate[], query: string): MentionCandidate[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return candidates;
  return candidates.filter(
    (candidate) => candidate.token.toLowerCase().includes(needle) || candidate.label.toLowerCase().includes(needle),
  );
}

/**
 * 用选中 token 替换光标前的 "@query" 片段，并补一个尾随空格：
 * 该空格保证 token 不会因继续输入被重新解析为查询（token 化防再触发）。
 */
export function applyMentionSelection(text: string, caret: number, token: string): { text: string; caret: number } {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return { text, caret };
  const nextText = `${text.slice(0, at)}${token} ${text.slice(caret)}`;
  const nextCaret = at + token.length + 1;
  return { text: nextText, caret: nextCaret };
}

// ── ↑ 发送历史（item 5，会话内存即可）────────────────────────────────────

/**
 * 在发送历史上步进。index===null 表示未在翻历史：
 * up 从最近一条开始；越过最旧/最新回退到 null（恢复原草稿）。
 */
export function stepSentHistory(
  historyLength: number,
  index: number | null,
  direction: "up" | "down",
): number | null {
  if (historyLength <= 0) return null;
  if (direction === "up") {
    if (index === null) return historyLength - 1;
    return Math.max(0, index - 1);
  }
  if (index === null) return null;
  const next = index + 1;
  return next >= historyLength ? null : next;
}
