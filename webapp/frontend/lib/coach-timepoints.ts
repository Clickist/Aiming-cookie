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

/**
 * 把当前会话的 assistant 消息文本投影为回看 chips：
 * 逐条解析 @time → 取所在句子短语作 label → 合并、±300ms 去重（保留先
 * 出现的）、按时间升序、截断上限。maxMs 提供时丢弃超出视频时长的锚点。
 */
export function projectCoachTimepoints(
  messages: ReadonlyArray<string>,
  options: { limit?: number; maxMs?: number } = {},
): CoachTimepoint[] {
  const { limit = COACH_TIMEPOINT_LIMIT, maxMs } = options;
  const gathered: CoachTimepoint[] = [];
  messages.forEach((text, messageIndex) => {
    const segments = parseTimeSegments(text);
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
      const label = prefixPhrase(previousText) || suffixPhrase(suffix) || COACH_TIMEPOINT_FALLBACK_LABEL;
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
