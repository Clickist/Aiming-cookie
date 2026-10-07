import { loadPiAgent } from "./pi-source.ts";
import { COMPACTION_CLEANUP_CUSTOM_INSTRUCTIONS } from "./compaction-cleanup.ts";

/**
 * 怪物会话 compaction 分块摘要兜底（1002 事故修复，commit B）。
 *
 * 背景：pi 的 compact() 把待压缩历史序列化成**单次** LLM 摘要调用。
 * 触发修复（turn.ts shouldCompactNow）救活判定后，滑窗时代遗留的
 * 62 万 tokens 级会话首次压缩时，序列化 prompt 自身就远超同模型窗口
 * （128K）→ 摘要调用 400 → compaction 失败 → 全量历史照发 → 用户死循环。
 *
 * 机制：pi 0.83 的 session_before_compact hook 允许在 harness 发起原生
 * 摘要调用之前接管（SessionBeforeCompactResult.compaction，agent-harness
 * compact() 的 provided 分支）。本模块在待压缩总量超过安全阈值时，把
 * 历史按消息边界切块、逐块调 pi 自己的 generateSummaryWithUsage（原生
 * 支持 previousSummary 链式 UPDATE），末端产出即最终摘要；未超阈值返回
 * undefined，行为与 pi 原生单次调用完全一致。
 *
 * 忠实镜像原生 compact() 的产物形状：summary 末尾追加 read/modified 文件
 * 清单标签、CompactResult 带 usage（记账=真实消耗）与 details（后续压缩
 * 的文件台账不中断）。split-turn 的 turnPrefixMessages 并入链式分块——
 * 原生的专用 Turn Context 小节在超窗场景保不住（generateTurnPrefixSummary
 * 未从 pi-agent-core 导出），并入链式是可接受的保真降级。
 */

/** 摘要调用重试（与 turn.ts harness 构造的 compaction retry 同款，审计#19）。 */
const SUMMARY_RETRY = { enabled: true, maxRetries: 2, baseDelayMs: 1_000 };

/** 单块超过该估算（tokens）才走分块；否则 undefined → pi 原生单次摘要。
 * 96K + 20K keepRecent + 摘要 prompt 在 128K 窗内仍有余量。 */
const DEFAULT_THRESHOLD_TOKENS = 96_000;
/** 每块的目标估算（tokens）：序列化后 + 系统提示 + previousSummary 链
 * （前块摘要会进下一块 prompt）远小于 128K 窗。 */
const DEFAULT_CHUNK_TOKENS = 48_000;

export interface CompactionFallbackOptions {
  /** pi Models 实例；摘要调用走 models.completeSimple（测试经此注入 fake）。 */
  models: unknown;
  /** pi Model（与对话同模型，镜像原生 compact 行为）。 */
  model: unknown;
  thinkingLevel?: unknown;
  /** 消息级 token 估算（turn.ts 的 CJK 感知实现）；测试可注入。 */
  estimateMessage: (message: unknown) => number;
  thresholdTokens?: number;
  chunkTokens?: number;
}

/** pi CompactionPreparation 的局部形状（hook 事件携带）。 */
interface PreparationLike {
  firstKeptEntryId: string;
  messagesToSummarize: unknown[];
  turnPrefixMessages: unknown[];
  isSplitTurn?: boolean;
  tokensBefore: number;
  previousSummary?: string;
  retainedTail?: unknown[];
  fileOps?: { read?: Set<string>; written?: Set<string>; edited?: Set<string> };
}

/** pi Usage 的局部形状（分块 usage 汇总用）。 */
interface UsageLike {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cacheWrite1h?: number;
  reasoning?: number;
  totalTokens: number;
  cost: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
    total: number;
  };
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function usageOf(value: unknown): UsageLike {
  const raw = (value ?? {}) as Record<string, unknown>;
  const cost = (raw.cost ?? {}) as Record<string, unknown>;
  return {
    input: num(raw.input),
    output: num(raw.output),
    cacheRead: num(raw.cacheRead),
    cacheWrite: num(raw.cacheWrite),
    totalTokens: num(raw.totalTokens),
    cost: {
      input: num(cost.input),
      output: num(cost.output),
      cacheRead: num(cost.cacheRead),
      cacheWrite: num(cost.cacheWrite),
      total: num(cost.total),
    },
  };
}

function addUsage(first: UsageLike, second: UsageLike): UsageLike {
  const sum: UsageLike = {
    input: first.input + second.input,
    output: first.output + second.output,
    cacheRead: first.cacheRead + second.cacheRead,
    cacheWrite: first.cacheWrite + second.cacheWrite,
    totalTokens: first.totalTokens + second.totalTokens,
    cost: {
      input: first.cost.input + second.cost.input,
      output: first.cost.output + second.cost.output,
      cacheRead: first.cost.cacheRead + second.cost.cacheRead,
      cacheWrite: first.cost.cacheWrite + second.cost.cacheWrite,
      total: first.cost.total + second.cost.total,
    },
  };
  if (first.cacheWrite1h !== undefined || second.cacheWrite1h !== undefined) {
    sum.cacheWrite1h = num(first.cacheWrite1h) + num(second.cacheWrite1h);
  }
  if (first.reasoning !== undefined || second.reasoning !== undefined) {
    sum.reasoning = num(first.reasoning) + num(second.reasoning);
  }
  return sum;
}

/** 镜像 pi computeFileLists：written∪edited 为 modified，read 减之。 */
function computeFileLists(fileOps: PreparationLike["fileOps"]): { readFiles: string[]; modifiedFiles: string[] } {
  const modified = new Set([...(fileOps?.edited ?? []), ...(fileOps?.written ?? [])]);
  const readFiles = [...(fileOps?.read ?? [])].filter((f) => !modified.has(f)).sort();
  return { readFiles, modifiedFiles: [...modified].sort() };
}

/** 镜像 pi formatFileOperations 的输出格式（后续压缩的台账兼容）。 */
function formatFileOperations(readFiles: string[], modifiedFiles: string[]): string {
  const sections: string[] = [];
  if (readFiles.length > 0) sections.push(`<read-files>\n${readFiles.join("\n")}\n</read-files>`);
  if (modifiedFiles.length > 0) sections.push(`<modified-files>\n${modifiedFiles.join("\n")}\n</modified-files>`);
  if (sections.length === 0) return "";
  return `\n\n${sections.join("\n\n")}`;
}

/** 块内截断的最坏折算系数：全 CJK 时 1 字符 ≈ 0.7 token，字符预算按
 * 此一次到位（chunkTokens / 0.7），避免截断后仍超块的迭代。 */
const CJK_WORST_TOKENS_PER_CHAR = 0.7;

function truncationMarker(cutChars: number): string {
  return `\n\n[... message truncated: ~${cutChars} chars cut for compaction chunk]`;
}

/**
 * 单条消息自身超过块上限时的块内截断兜底（业界通行防线：Aider/
 * OpenHands/Letta/pi 对超长消息都在其所属块内截断，保留头部并标注，
 * 不整块放弃）。pi 的 serializeConversation 已按 2000 字符截 toolResult，
 * 这里只管用户/助手手打正文超长的情况：只截 text/thinking 文本，
 * image/toolCall 等非文本块原样保留；截断的是摘要 prompt 用的副本，
 * 会话数据本身零改动。
 */
function truncateMessageForChunk(
  message: unknown,
  chunkTokens: number,
  estimateMessage: (message: unknown) => number,
): unknown {
  if (typeof message !== "object" || message === null) return message;
  if (estimateMessage(message) <= chunkTokens) return message;
  const record = message as { content?: unknown };
  // 预算留 100 字符余量给尾注 marker 自身。
  const budgetChars = Math.max(200, Math.floor(chunkTokens / CJK_WORST_TOKENS_PER_CHAR) - 100);

  if (typeof record.content === "string") {
    const cut = Math.max(0, record.content.length - budgetChars);
    return {
      ...record,
      content: `${record.content.slice(0, budgetChars)}${truncationMarker(cut)}`,
    };
  }
  if (!Array.isArray(record.content)) return message;

  let used = 0;
  let cutChars = 0;
  let markerIndex = -1;
  const blocks = record.content.map((block) => {
    if (typeof block !== "object" || block === null) return block;
    const b = block as { type?: unknown; text?: unknown; thinking?: unknown };
    const key = typeof b.text === "string" ? "text" : typeof b.thinking === "string" ? "thinking" : null;
    if (!key) return block; // 非文本块（image/toolCall）原样保留
    const text = b[key] as string;
    const remaining = budgetChars - used;
    if (remaining <= 0 || text.length > remaining) {
      const kept = Math.max(0, remaining);
      cutChars += text.length - kept;
      used = budgetChars;
      markerIndex = record.content.indexOf(block);
      return { ...b, [key]: text.slice(0, kept) };
    }
    used += text.length;
    return block;
  });
  if (markerIndex < 0) return message;
  const marked = blocks[markerIndex] as { text?: string; thinking?: string };
  const key = typeof marked.text === "string" ? "text" : "thinking";
  blocks[markerIndex] = { ...marked, [key]: `${marked[key]}${truncationMarker(cutChars)}` };
  return { ...record, content: blocks };
}

/** 按消息边界切块：累计估算超过 chunkTokens 就开新块；单条消息自身
 * 超过块上限时先做块内截断（保留头部 + 标注），不整块放弃。 */
function chunkMessages(
  messages: unknown[],
  chunkTokens: number,
  estimateMessage: (message: unknown) => number,
): unknown[][] {
  const chunks: unknown[][] = [];
  let current: unknown[] = [];
  let currentTokens = 0;
  for (const message of messages) {
    const bounded = truncateMessageForChunk(message, chunkTokens, estimateMessage);
    const estimate = Math.max(1, estimateMessage(bounded));
    if (current.length > 0 && currentTokens + estimate > chunkTokens) {
      chunks.push(current);
      current = [];
      currentTokens = 0;
    }
    current.push(bounded);
    currentTokens += estimate;
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}

/**
 * 在 harness 上注册 session_before_compact 兜底。harness.on 是 pi 0.83
 * 的 hook 注册面（0.99 升级时随 harness 适配层改 events.on，逻辑不变）。
 */
export function registerCompactionFallback(
  harness: {
    on: (
      type: "session_before_compact",
      handler: (event: { preparation: PreparationLike; signal?: AbortSignal; customInstructions?: string }) =>
        | Promise<{ compaction?: unknown } | undefined>
        | ({ compaction?: unknown } | undefined),
    ) => unknown;
  },
  options: CompactionFallbackOptions,
): void {
  const thresholdTokens = options.thresholdTokens ?? DEFAULT_THRESHOLD_TOKENS;
  const chunkTokens = Math.min(options.chunkTokens ?? DEFAULT_CHUNK_TOKENS, thresholdTokens);

  harness.on("session_before_compact", async (event) => {
    const preparation = event.preparation;
    const messages = [
      ...(preparation.messagesToSummarize ?? []),
      ...(preparation.turnPrefixMessages ?? []),
    ];
    let totalEstimate = 0;
    for (const message of messages) totalEstimate += Math.max(1, options.estimateMessage(message));

    // 未超阈值：返回 undefined，走 pi 原生单次摘要（与今天行为一致）。
    if (totalEstimate <= thresholdTokens) return undefined;

    const { generateSummaryWithUsage, DEFAULT_COMPACTION_SETTINGS } = (await loadPiAgent()) as {
      generateSummaryWithUsage: (
        currentMessages: unknown[],
        models: unknown,
        model: unknown,
        reserveTokens: number,
        signal?: AbortSignal,
        customInstructions?: string,
        previousSummary?: string,
        thinkingLevel?: unknown,
        retry?: unknown,
        callbacks?: unknown,
      ) => Promise<
        | { ok: true; value: { text: string; usage: unknown } }
        | { ok: false; error: { message?: string } }
      >;
      DEFAULT_COMPACTION_SETTINGS: { reserveTokens: number };
    };

    const chunks = chunkMessages(messages, chunkTokens, options.estimateMessage);
    let summary: string | undefined;
    let usage: UsageLike | undefined;
    for (const chunk of chunks) {
      const result = await generateSummaryWithUsage(
        chunk,
        options.models,
        options.model,
        DEFAULT_COMPACTION_SETTINGS.reserveTokens,
        event.signal,
        // 清洗指令（2026-10-07）：调用方没传 customInstructions（如 pi 内部
        // 触发的压缩）时兜底注入，保证每块摘要都带剔除规则。
        event.customInstructions ?? COMPACTION_CLEANUP_CUSTOM_INSTRUCTIONS,
        summary,
        options.thinkingLevel,
        SUMMARY_RETRY,
      );
      if (!result.ok) {
        // 抛错 → harness.compact() 整体失败 → turn.ts 现有 catch 落日志、
        // 不拦对话（与原生摘要失败同语义）。
        throw new Error(
          `Chunked compaction summary failed (chunk ${chunks.indexOf(chunk) + 1}/${chunks.length}): ${result.error?.message ?? "unknown"}`,
        );
      }
      summary = result.value.text;
      usage = usage ? addUsage(usage, usageOf(result.value.usage)) : usageOf(result.value.usage);
    }

    const { readFiles, modifiedFiles } = computeFileLists(preparation.fileOps);
    const summaryWithFiles = `${summary ?? ""}${formatFileOperations(readFiles, modifiedFiles)}`;

    return {
      compaction: {
        summary: summaryWithFiles,
        firstKeptEntryId: preparation.firstKeptEntryId,
        tokensBefore: preparation.tokensBefore,
        usage,
        retainedTail: preparation.retainedTail,
        details: { readFiles, modifiedFiles },
      },
    };
  });
}
