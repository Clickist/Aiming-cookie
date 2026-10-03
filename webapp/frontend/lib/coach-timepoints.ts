/**
 * Coach 讲解回看点 → 视频面板 chips（拍板：chips 跟随讲解内容，不再由
 * 分析器自动切片驱动 UI）。
 *
 * 数据源＝当前会话的 assistant 消息文本。@time 解析复用 lib/rich-text 的
 * parseTimeSegments（同一套 TIME_TOKEN_PATTERN，不设第二套正则），正文与
 * 底部 chips 引用同一份时间锚点，两者不再对不上。
 *
 * 纯函数，无渲染、无取数；解析失败/无锚点时返回空数组，由调用方降级。
 */

import { parseTimeSegments } from "./rich-text";

/** 视频底部回看按钮的数据形状：视频相对毫秒＋讲解短语。 */
export interface CoachTimepoint {
  /** 稳定键：assistant 消息序 + 消息内 chip 序。 */
  id: string;
  timeMs: number;
  label: string;
}

/** Coach prompt 约束每次 4-6 个；多回合合并后截断前 8 个。 */
export const COACH_TIMEPOINT_LIMIT = 8;
/** ±300ms 内视为同一回看点（多回合反复指向同一锚点），保留先出现的。 */
export const COACH_TIMEPOINT_DEDUPE_MS = 300;

const LABEL_MIN_CHARS = 4;
const LABEL_MAX_CHARS = 12;
const COACH_TIMEPOINT_FALLBACK_LABEL = "回看点";
/** 英文消息（无汉字）下同位兜底标签：chip 标签跟随讲解消息语言，不跟随 UI 语言。 */
const COACH_TIMEPOINT_FALLBACK_LABEL_EN = "replay";

/** 汉字判定（含扩展 A 区）；标点、空白、拉丁字母一律算边界。 */
const HAN_RE = /[\u3400-\u4dbf\u4e00-\u9fff]/;

/** @time 前最近的连续汉字；不足 LABEL_MIN_CHARS 视为无上下文。 */
function prefixPhrase(prefix: string): string {
  let end = prefix.length;
  while (end > 0 && !HAN_RE.test(prefix[end - 1])) end -= 1;
  let start = end;
  while (start > 0 && HAN_RE.test(prefix[start - 1])) start -= 1;
  const run = prefix.slice(start, end);
  return run.length >= LABEL_MIN_CHARS ? run.slice(-LABEL_MAX_CHARS) : "";
}

/** @time 后最近的短语：跳过标点/空白后取字母数字或汉字，截到标点。 */
function suffixPhrase(suffix: string): string {
  let index = 0;
  while (index < suffix.length && !HAN_RE.test(suffix[index]) && !/[A-Za-z0-9]/.test(suffix[index])) {
    index += 1;
  }
  let phrase = "";
  while (
    index < suffix.length
    && phrase.length < LABEL_MAX_CHARS
    && (HAN_RE.test(suffix[index]) || /[A-Za-z0-9]/.test(suffix[index]))
  ) {
    phrase += suffix[index];
    index += 1;
  }
  return phrase;
}

// ── 英文消息分支（教练语言跟随用户消息语言，2026-09 语言指令块拍板；同一
// 会话内消息语言可逐条切换，故按消息粒度选分支：含汉字走上面的中文提取，
// 行为逐字节不变；无汉字消息走英文词组提取）──────────────────────────────

/** 英文词组形状：词＝[A-Za-z0-9'’-]（含连写连字符/撇号），词间空格；标点/换行是边界。 */
const EN_PHRASE_PREFIX_RE = /[A-Za-z0-9][A-Za-z0-9'’ -]*$/;
const EN_PHRASE_RE = /[A-Za-z0-9][A-Za-z0-9'’ -]*/;

/**
 * 词组截到 ≤LABEL_MAX_CHARS：截断点落在词中间时丢弃被截半的词，本来就在词
 * 边界则整段保留；单个超长词（词本身超上限）整词保留——截半个词是乱码，
 * chip 自带截断兜底。
 */
function capEnPhraseAtWordBoundary(phrase: string, fromEnd: boolean): string {
  if (phrase.length <= LABEL_MAX_CHARS) return phrase;
  const cutIndex = fromEnd ? phrase.length - LABEL_MAX_CHARS : LABEL_MAX_CHARS;
  const cut = fromEnd ? phrase.slice(cutIndex) : phrase.slice(0, cutIndex);
  const midWord = fromEnd ? phrase[cutIndex - 1] !== " " : phrase[cutIndex] !== " ";
  if (!midWord) return cut.trim();
  const boundary = fromEnd ? cut.indexOf(" ") : cut.lastIndexOf(" ");
  if (boundary >= 0) return fromEnd ? cut.slice(boundary + 1) : cut.slice(0, boundary);
  // 截断窗口里没有词边界：退到整个词组的第一个/最后一个词。
  const wordBoundary = fromEnd ? phrase.lastIndexOf(" ") : phrase.indexOf(" ");
  return wordBoundary > 0 ? (fromEnd ? phrase.slice(wordBoundary + 1) : phrase.slice(0, wordBoundary)) : phrase;
}

/** @time 前最近的英文词组；不足 LABEL_MIN_CHARS 视为无上下文。 */
function prefixPhraseEn(prefix: string): string {
  const run = EN_PHRASE_PREFIX_RE.exec(prefix)?.[0];
  if (!run) return "";
  const phrase = run.replace(/\s+/g, " ").trim();
  if (phrase.length < LABEL_MIN_CHARS) return "";
  return capEnPhraseAtWordBoundary(phrase, true);
}

/** @time 后最近的英文词组：跳过标点/空白后取词组，截到标点/换行。 */
function suffixPhraseEn(suffix: string): string {
  const run = EN_PHRASE_RE.exec(suffix)?.[0];
  if (!run) return "";
  const phrase = run.replace(/\s+/g, " ").trim();
  if (phrase.length < LABEL_MIN_CHARS) return "";
  return capEnPhraseAtWordBoundary(phrase, false);
}

/**
 * 把当前会话的 assistant 消息文本投影为回看 chips：
 * 逐条解析 @time → 取所在句子短语作 label（按消息语言走中文/英文提取）→ 合并、
 * ±300ms 去重（保留先出现的）、按时间升序、截断上限。maxMs 提供时丢弃超出
 * 视频时长的锚点。
 */
export function projectCoachTimepoints(
  messages: ReadonlyArray<string>,
  options: { limit?: number; maxMs?: number } = {},
): CoachTimepoint[] {
  const { limit = COACH_TIMEPOINT_LIMIT, maxMs } = options;
  const gathered: CoachTimepoint[] = [];
  messages.forEach((text, messageIndex) => {
    const segments = parseTimeSegments(text);
    // 消息粒度选语言分支，语言看正文（剔除 @time 记号——其中的 s 是时间码
    // 后缀，不是英文词）：含任何汉字即走中文提取（zh 行为逐字节不变）；无
    // 汉字但正文确有英文字母才走英文提取；纯符号/数字正文（如裸 @time）
    // 声明不了语言，维持默认中文路径与兜底标签（既有 fixture 行为不变）。
    const prose = segments.filter((piece) => !piece.chip).map((piece) => piece.text).join("");
    const isZh = HAN_RE.test(prose) || !/[A-Za-z]/.test(prose);
    const fallbackLabel = isZh ? COACH_TIMEPOINT_FALLBACK_LABEL : COACH_TIMEPOINT_FALLBACK_LABEL_EN;
    let chipIndex = 0;
    let previousText = "";
    segments.forEach((piece, pieceIndex) => {
      if (!piece.chip) {
        previousText = piece.text;
        return;
      }
      const chip = piece.chip;
      const timeMs = chip.kind === "range" ? chip.startMs : chip.timeMs;
      const suffix = segments.slice(pieceIndex + 1).find((next) => !next.chip)?.text ?? "";
      const label = isZh
        ? (prefixPhrase(previousText) || suffixPhrase(suffix) || fallbackLabel)
        : (prefixPhraseEn(previousText) || suffixPhraseEn(suffix) || fallbackLabel);
      const withinRange = timeMs >= 0 && (maxMs === undefined || timeMs <= maxMs);
      if (withinRange) {
        gathered.push({ id: `coach-${messageIndex}-${chipIndex}`, timeMs, label });
      }
      chipIndex += 1;
    });
  });
  const deduped: CoachTimepoint[] = [];
  for (const point of gathered) {
    if (deduped.some((kept) => Math.abs(kept.timeMs - point.timeMs) <= COACH_TIMEPOINT_DEDUPE_MS)) continue;
    deduped.push(point);
  }
  return deduped
    .sort((left, right) => left.timeMs - right.timeMs)
    .slice(0, limit);
}

// ── 历史 @time 链接的视频归属（1002 串视频修复）──────────────────────────
//
// @time chip 点击打开哪个分析的视频，过去用会话级单值（defaultAnalysisRef）：
// 一个会话先后挂载 ≥2 个分析时，历史消息的 @time 会全部落到同一个分析上，
// 点开别的训练项目的视频（用户报障）。归属改为逐消息计算。

/** resolveCoachMessageAnalysisRefs 的消息入参（只需 id、role 与 created_at）。 */
export interface CoachMessageRefInput {
  id: number;
  role: string;
  created_at?: string | null;
}

/** 挂载记录入参（detail 的 analysis_refs，后端已按 attached_at 升序给出）。 */
export interface CoachAnalysisRefInput {
  id: number;
  attached_at?: string | null;
}

/**
 * 挂载/消息时间统一到 epoch ms：ISO 8601 带时区（pi 会话条目与 meta
 * attached_at 都是 toISOString 的 UTC Z 形态）与 sqlite CURRENT_TIMESTAMP 的
 * 无时区形态（"YYYY-MM-DD HH:MM:SS"，直接 Date.parse 会误按本地时区解析，
 * 须按 UTC 补 Z）都接受；空串/不可解析回 null（挂载时间未知）。
 */
export function normalizeCoachTimestamp(value: string | null | undefined): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  const utcNaive = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(trimmed);
  const parsed = Date.parse(utcNaive ? `${trimmed.replace(" ", "T")}Z` : trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * 历史 assistant 消息的 @time 视频归属：时间就近——每条回复归属「attached_at
 * ≤ 该消息 created_at 的最近一次挂载」；没有任何更早挂载（或消息缺时间）时
 * 归第一个挂载。refs 为空返回 null 表（调用方再走深读兜底链）。
 *
 * 已知边界（生成侧问题，本函数不覆盖）：同一条回复引用多个分析的时间码无法
 * 区分归属，整条消息共用算出的单个 ref；同一回合挂载多个分析时它们时间戳
 * 相同，该回合的回复共用其中最后一个（并列取靠后的挂载）。
 *
 * 升级前的旧会话没有挂载时间戳（attached_at 全空）：退回序数近似——第 k 条
 * 回复 → refs 里第 k 个挂载，超出挂载数的后续追问留在最后一个分析上（与
 * 上一版行为一致，旧会话不回退）。
 */
export function resolveCoachMessageAnalysisRefs(
  messages: ReadonlyArray<CoachMessageRefInput>,
  refs: ReadonlyArray<CoachAnalysisRefInput>,
): Map<number, string | null> {
  const table = new Map<number, string | null>();
  const mountTimes = refs.map((ref) => normalizeCoachTimestamp(ref.attached_at));
  const allMountTimesUnknown = mountTimes.every((time) => time === null);
  let assistantSeen = 0;
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    if (refs.length === 0) {
      table.set(message.id, null);
    } else if (allMountTimesUnknown) {
      // 旧会话无挂载时间：序数近似（第 k 条回复 → 第 k 个挂载，超出留最后）。
      table.set(message.id, `analysis:${refs[Math.min(assistantSeen, refs.length - 1)]!.id}`);
      assistantSeen += 1;
    } else {
      // 时间就近：refs 已按 attached_at 升序，最后一个 ≤ 消息时间的挂载即最近。
      const at = normalizeCoachTimestamp(message.created_at);
      let nearest = -1;
      for (let index = 0; index < refs.length; index++) {
        const mountAt = mountTimes[index];
        if (at !== null && mountAt !== null && mountAt <= at) nearest = index;
      }
      // 消息早于一切挂载或缺消息时间 → 归第一个挂载。
      table.set(message.id, `analysis:${refs[nearest >= 0 ? nearest : 0]!.id}`);
    }
  }
  return table;
}
