// Coach 消息块字符守卫（显示层兜底）：模型流损坏时正文会出现成片 █（U+2588）
// 或 U+FFFD 替换符。管流内的熔断/自动恢复在 coach-runtime 侧（逐段阈值 200）；
// 这里是显示层的第二道兜底，针对已落库的存量与自动恢复漏网的回复——渲染时
// 派生计算，不持久化、不写 session 投影层。两套阈值分层不共用：
// 64 是显示层兜存量，200 是管流内。

/** 连续块字符 run 的命中阈值：█/U+FFFD 合并计数（同一损坏指纹）。 */
export const COACH_DISPLAY_GUARD_MIN_BLOCK_RUN = 64;

/** 密度路径的最低文本长度：更短的文本即使占比高也不算损坏（避免误伤短回复）。 */
export const COACH_DISPLAY_GUARD_DENSITY_MIN_CHARS = 120;

/** 密度路径阈值：块字符占非空白字符比例 ≥30% 判损坏。 */
export const COACH_DISPLAY_GUARD_DENSITY_THRESHOLD = 0.3;

/**
 * 出站 FFFD 拦截阈值：输入法故障常产生零星 U+FFFD，≥2 个才拦（单个可能是
 * 用户有意粘贴的字符，不替用户做主）。
 */
export const COACH_SEND_MIN_REPLACEMENT_CHARS = 2;

export interface CoachDisplayGuardResult {
  hit: boolean;
  maxRun: number;
  /** 块字符占非空白字符的比例（0..1；分母为 0 时记 0）。 */
  density: number;
  /** 原文长度（埋点用，避免调用方重复取 length）。 */
  len: number;
}

function isBlockChar(char: string): boolean {
  return char === "\u2588" || char === "\uFFFD";
}

/** 出站文本的 U+FFFD 计数（sendText 汇聚点用，挡输入法故障输入）。 */
export function countReplacementChars(text: string): number {
  let count = 0;
  for (const char of text) {
    if (char === "\uFFFD") count += 1;
  }
  return count;
}

/**
 * 检测一条回复是否呈流损坏形态。判据（命中任一即 hit）：
 * a) 连续 [\u2588\uFFFD] run（两种字符合并计数）≥ 64；
 * b) 文本 ≥120 字符且 [\u2588\uFFFD] 占非空白字符 ≥30%。
 */
export function detectCorruptedReply(text: string): CoachDisplayGuardResult {
  let maxRun = 0;
  let currentRun = 0;
  let blockChars = 0;
  let nonWhitespaceChars = 0;
  for (const char of text) {
    if (isBlockChar(char)) {
      currentRun += 1;
      blockChars += 1;
      if (currentRun > maxRun) maxRun = currentRun;
    } else {
      currentRun = 0;
      if (!/\s/.test(char)) nonWhitespaceChars += 1;
    }
  }
  // 块字符本身也是非空白字符，计入分母。
  const nonWsTotal = nonWhitespaceChars + blockChars;
  const density = nonWsTotal === 0 ? 0 : blockChars / nonWsTotal;
  const hit =
    maxRun >= COACH_DISPLAY_GUARD_MIN_BLOCK_RUN
    || (text.length >= COACH_DISPLAY_GUARD_DENSITY_MIN_CHARS
      && density >= COACH_DISPLAY_GUARD_DENSITY_THRESHOLD);
  return { hit, maxRun, density, len: text.length };
}
