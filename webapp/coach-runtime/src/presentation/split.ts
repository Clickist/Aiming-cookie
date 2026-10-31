/**
 * 有状态分流器：把模型原始输出切成 coach_presentation.v1 的 segments。
 *
 * 语法（ARCHITECTURE §5.3，冻结 fixtures：
 * test/fixtures/presentation/split-fixtures.json）：
 * - 只有语言标识精确为 `openui-lang` 的 ``` 围栏是 UI 区段；大小写敏感，
 *   围栏记号后必须紧跟该标识（允许行尾空白），`openui-langs` 等相似标识
 *   不匹配。`~~~openui-lang` 不视为 UI 区段（OpenUI inlineMode 用反引号），
 *   按普通 fence 交给 text 归一化剥除——安全的降级方向。
 * - 普通 fence（python/mermaid/无标识）仍按既有 stripFencedBlocks 语义
 *   在 text 段内连内容剥除。扫描器只跟踪普通 fence 的开闭，避免把普通
 *   代码块里的 `openui-lang` 行误认成 UI 区段。
 * - 未闭合的 openui fence：已收 DSL 作为缓冲中的 openui 段输出（流式
 *   语义）。行首尚未判定完成的 fence 前缀（如刚到的 "```openui-la"）
 *   暂扣不展示，防止半截 DSL 泄进 text。
 * - CRLF：围栏识别与 DSL 内容都按 CRLF 工作；闭合 fence 行尾的换行属于
 *   后续 text（fixtures 冻结语义），开围栏行连同换行整体消费。
 * - text 段只做 normalizeUserFacingText + secret redaction；openui 段原文
 *   透传（只过 redaction，不做格式清洗）。
 * - 预算：单 openui 段 ≤64 KiB、单回复 1 个区段。超限区段不静默丢弃，
 *   降级为 text 保留原文，并在投影层带 cause=budget_exceeded（呈现由前端）。
 */

import { redactRuntimeSecrets } from "../provider-profile.ts";
import { normalizeUserFacingText } from "./normalize-text.ts";
import {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  COACH_PRESENTATION_SCHEMA,
  MAX_OPENUI_SEGMENT_BYTES,
  MAX_OPENUI_SEGMENTS,
  OPENUI_FENCE_LANG,
  TEXT_SEGMENT_TRANSPORT_LIMIT,
  type CoachGenerationStatus,
  type CoachPresentation,
  type CoachPresentationCause,
  type CoachPresentationSegment,
} from "./types.ts";

type Fence = { char: "`" | "~"; count: number };

type SegmentDraft =
  | { kind: "text"; text: string }
  | { kind: "openui"; source: string };

export type SplitPresentationOptions = {
  /**
   * revision：live 路径由调用方单调传入（turn 的 partial revision 与投影
   * 一一对应）；不传时由 PresentationSplitter 内部计数，每次 snapshot +1。
   * 历史重放/单次投影传 1。
   */
  revision?: number;
  messageId?: string | number | null;
  runId?: string | null;
  /** secret redaction 词表；text 与 openui 段都要过（DSL 不豁免 redaction）。 */
  secrets?: string[];
  /** 仅供测试：覆盖 openui 段预算。 */
  maxOpenUiBytes?: number;
  /** 仅供测试：覆盖 openui 区段数上限。 */
  maxOpenUiSegments?: number;
};

/** 行（不含换行符）的 fence 记号；info string 原样返回。 */
function parseFenceLine(line: string): { fence: Fence; info: string } | null {
  const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match) return null;
  return {
    fence: { char: match[1]![0] as "`" | "~", count: match[1]!.length },
    info: match[2]!,
  };
}

/** 闭合行：同一字符的 fence 记号 + 行尾空白，无 info string。 */
function closingFence(line: string): Fence | null {
  const match = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  if (!match) return null;
  return { char: match[1]![0] as "`" | "~", count: match[1]!.length };
}

/** 精确的 openui 开围栏：反引号 fence，info 去行尾空白后逐字等于 `openui-lang`。 */
function isOpenUiOpener(fence: Fence, info: string): boolean {
  return fence.char === "`" && info.replace(/[ \t]+$/, "") === OPENUI_FENCE_LANG;
}

/** `openui-lang` 后跟行尾空白：完整标识的行内前缀形态。 */
const OPENUI_FENCE_LANG_TRAILING_RE = new RegExp(`^${OPENUI_FENCE_LANG}[ \\t]+$`);

/**
 * 未判定完成的行是否仍可能长成 openui 开围栏。成立时快照暂扣该行，
 * 不让 fence 记号或半截语言标识进入 text（防 DSL 泄漏）。
 */
function isPotentialOpenUiFencePrefix(line: string): boolean {
  const candidate = line.endsWith("\r") ? line.slice(0, -1) : line;
  const match = /^ {0,3}(`+)(.*)$/.exec(candidate);
  if (!match) return false;
  const ticks = match[1]!;
  const rest = match[2]!;
  // 不足 3 个反引号：只有仍全是反引号时还可能长成 fence。
  if (ticks.length < 3) return rest.length === 0;
  if (OPENUI_FENCE_LANG.startsWith(rest)) return true;
  return OPENUI_FENCE_LANG_TRAILING_RE.test(rest);
}

/** 去掉恰好一个行尾换行（CRLF/LF/CR）；闭合 fence 前的换行是结构分隔符，不是 DSL。 */
function stripTrailingLineTerminator(text: string): string {
  return text.replace(/\r?\n$/, "").replace(/\r$/, "");
}

function utf8ByteLength(text: string): number {
  return Buffer.byteLength(text, "utf8");
}

const LEADING_WS_RE = /^\s*/;
const TRAILING_WS_RE = /\s*$/;

/**
 * 对 text 段做既有归一化，但保留段首/段尾空白：fixtures 冻结了「区段之间
 * 的换行属于 text 段」的语义（如 fence 前的 "\n\n"），normalizeUserFacingText
 * 的 .trim() 会吃掉它们。核心内容仍走同一函数，行为零分叉。
 */
function normalizeTextSegment(raw: string, secrets: string[]): string {
  const redacted = redactRuntimeSecrets(raw, secrets);
  if (!redacted.trim()) return "";
  const leading = LEADING_WS_RE.exec(redacted)?.[0] ?? "";
  const trailing = TRAILING_WS_RE.exec(redacted)?.[0] ?? "";
  const core = redacted.slice(leading.length, redacted.length - trailing.length);
  const normalized = normalizeUserFacingText(core);
  if (!normalized) return "";
  return `${leading}${normalized}${trailing}`;
}

function statusCause(generationStatus: CoachGenerationStatus): CoachPresentationCause | null {
  if (generationStatus === "stopped") return "stopped";
  if (generationStatus === "interrupted") return "process_interrupted";
  return null;
}

/**
 * 逐行有状态扫描。一个实例只服务一条消息：push 追加分片，snapshot 取全量
 * 快照（可重复调用，幂等）。
 */
export class PresentationSplitter {
  private readonly secrets: string[];
  private readonly maxOpenUiBytes: number;
  private readonly maxOpenUiSegments: number;
  private raw = "";
  private cursor = 0;
  /** text＝正文；fence＝普通围栏内部（不识别 openui）；openui＝UI 区段内部。 */
  private mode: "text" | "fence" | "openui" = "text";
  private normalFenceChar: "`" | "~" = "`";
  private openFence: Fence = { char: "`", count: 3 };
  private textBuf = "";
  private dslBuf = "";
  /** 普通围栏闭合后吞掉紧邻的一个空行（fixtures 冻结的普通 fence 剥除语义）。 */
  private skipBlankAfterNormalFence = false;
  private acceptedOpenUi = 0;
  private finalized: SegmentDraft[] = [];
  private degraded = false;
  private revision = 0;

  constructor(options: { secrets?: string[]; maxOpenUiBytes?: number; maxOpenUiSegments?: number } = {}) {
    this.secrets = [...(options.secrets ?? [])];
    this.maxOpenUiBytes = options.maxOpenUiBytes ?? MAX_OPENUI_SEGMENT_BYTES;
    this.maxOpenUiSegments = options.maxOpenUiSegments ?? MAX_OPENUI_SEGMENTS;
  }

  push(chunk: string): void {
    if (!chunk) return;
    this.raw += chunk;
    this.#scan();
  }

  snapshot(generationStatus: CoachGenerationStatus, options: SplitPresentationOptions = {}): CoachPresentation {
    const drafts = [...this.finalized];
    let degraded = this.degraded;

    if (this.mode === "openui") {
      // 未闭合（流式中途）：已收 DSL 全部进入缓冲中的 openui 段。
      const tail = this.raw.slice(this.cursor);
      const source = redactRuntimeSecrets(stripTrailingLineTerminator(this.dslBuf + tail), this.secrets);
      if (source) {
        if (this.acceptedOpenUi < this.maxOpenUiSegments && utf8ByteLength(source) <= this.maxOpenUiBytes) {
          drafts.push({ kind: "openui", source });
        } else {
          degraded = true;
          drafts.push({ kind: "text", text: source });
        }
      }
    } else {
      // 普通围栏内部的尾部照常进 text（会被归一化连内容剥除）；text 模式
      // 下只是「可能长成 openui 开围栏」的最后一截行暂扣。
      const tail = this.raw.slice(this.cursor);
      const pendingTail = this.mode === "text"
        ? (isPotentialOpenUiFencePrefix(tail) || (this.skipBlankAfterNormalFence && tail.trim() === "") ? "" : tail)
        : tail;
      const pending = this.textBuf + pendingTail;
      if (pending) {
        const normalized = normalizeTextSegment(pending, this.secrets);
        if (normalized) drafts.push({ kind: "text", text: normalized });
      }
    }

    const segments: CoachPresentationSegment[] = drafts.map((draft, index) =>
      draft.kind === "openui"
        ? {
            id: `seg:${index}`,
            kind: "openui",
            library_id: COACH_OPENUI_LIBRARY_ID,
            library_version: COACH_OPENUI_LIBRARY_VERSION,
            source: draft.source,
          }
        : { id: `seg:${index}`, kind: "text", text: draft.text },
    );

    const revision = options.revision ?? (this.revision += 1);
    this.revision = Math.max(this.revision, revision);
    const cause = degraded
      ? "budget_exceeded"
      : statusCause(generationStatus);

    return {
      schema_version: COACH_PRESENTATION_SCHEMA,
      message_id: options.messageId ?? null,
      run_id: options.runId ?? null,
      revision,
      generation_status: generationStatus,
      ...(cause ? { cause } : {}),
      segments,
    };
  }

  #scan(): void {
    for (;;) {
      const nl = this.raw.indexOf("\n", this.cursor);
      if (nl === -1) return;
      const lineEnd = nl + 1;
      const rawLine = this.raw.slice(this.cursor, nl);
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;

      // 普通围栏闭合后紧邻的空白行随围栏一起消失（只吞一行；fixture
      // lookalike-fences-not-matched 冻结了「段间只留一个空行」的结果）。
      if (this.mode === "text" && this.skipBlankAfterNormalFence) {
        if (line.trim() === "") {
          this.skipBlankAfterNormalFence = false;
          this.cursor = lineEnd;
          continue;
        }
        this.skipBlankAfterNormalFence = false;
      }

      if (this.mode === "openui") {
        const close = closingFence(line);
        if (close && close.char === this.openFence.char && close.count >= this.openFence.count) {
          this.#finalizeOpenUi();
          this.mode = "text";
          // 闭合 fence 的换行属于后续 text（fixtures 冻结语义）。
          this.cursor = rawLine.endsWith("\r") ? nl - 1 : nl;
          continue;
        }
        this.dslBuf += this.raw.slice(this.cursor, lineEnd);
        this.cursor = lineEnd;
        continue;
      }

      if (this.mode === "text") {
        const parsed = parseFenceLine(line);
        if (parsed && isOpenUiOpener(parsed.fence, parsed.info)) {
          this.#flushText();
          this.openFence = parsed.fence;
          this.dslBuf = "";
          this.mode = "openui";
          this.cursor = lineEnd;
          continue;
        }
        if (parsed) {
          this.normalFenceChar = parsed.fence.char;
          this.mode = "fence";
        }
      } else if (closingFence(line)?.char === this.normalFenceChar) {
        // 普通围栏闭合：与 stripFencedBlocks 同判据（同字符闭合行，不比长度）。
        this.mode = "text";
        this.skipBlankAfterNormalFence = true;
      }

      this.textBuf += this.raw.slice(this.cursor, lineEnd);
      this.cursor = lineEnd;
    }
  }

  #flushText(): void {
    if (!this.textBuf) return;
    const normalized = normalizeTextSegment(this.textBuf, this.secrets);
    this.textBuf = "";
    if (normalized) this.finalized.push({ kind: "text", text: normalized });
  }

  #finalizeOpenUi(): void {
    const source = stripTrailingLineTerminator(this.dslBuf);
    this.dslBuf = "";
    const redacted = redactRuntimeSecrets(source, this.secrets);
    if (!redacted) return;
    if (this.acceptedOpenUi < this.maxOpenUiSegments && utf8ByteLength(redacted) <= this.maxOpenUiBytes) {
      this.acceptedOpenUi += 1;
      this.finalized.push({ kind: "openui", source: redacted });
      return;
    }
    this.degraded = true;
    this.finalized.push({ kind: "text", text: redacted });
  }
}

/**
 * 从完整原文一次性投影（历史重放、完成态、测试）。流式路径用
 * PresentationSplitter 增量 push；两者是同一状态机的两种入口。
 */
export function splitPresentation(
  rawText: string,
  generationStatus: CoachGenerationStatus,
  options: SplitPresentationOptions = {},
): CoachPresentation {
  const splitter = new PresentationSplitter({
    secrets: options.secrets,
    ...(options.maxOpenUiBytes !== undefined ? { maxOpenUiBytes: options.maxOpenUiBytes } : {}),
    ...(options.maxOpenUiSegments !== undefined ? { maxOpenUiSegments: options.maxOpenUiSegments } : {}),
  });
  splitter.push(rawText);
  return splitter.snapshot(generationStatus, options);
}

/**
 * 终态重投影：保留已有 segments，只替换 generation_status/revision。
 * agent-runs 的完成/失败/停止路径用最后一份流式快照收口。
 */
export function projectGenerationStatus(
  presentation: CoachPresentation | null,
  generationStatus: CoachGenerationStatus,
  options: { revision?: number; messageId?: string | number | null; runId?: string | null } = {},
): CoachPresentation | null {
  if (!presentation) return null;
  const cause = presentation.cause ?? statusCause(generationStatus);
  return {
    ...presentation,
    generation_status: generationStatus,
    revision: options.revision ?? presentation.revision + 1,
    ...(options.messageId !== undefined ? { message_id: options.messageId } : {}),
    ...(options.runId !== undefined ? { run_id: options.runId } : {}),
    ...(cause ? { cause } : {}),
  };
}

/**
 * 流式传输预算（SSE/轮询/NDJSON）：text 段沿用既有 12KB 截断；openui 段
 * 独立按 64KiB 预算。超限 openui 转 text 并带 cause，不再拦腰截断合法
 * 区段。完成响应与历史重放不走这里（一次性全量，不重复发送）。
 */
export function applyPresentationTransportBudget(presentation: CoachPresentation): CoachPresentation {
  let degraded = presentation.cause === "budget_exceeded";
  let openuiCount = 0;
  const segments: CoachPresentationSegment[] = [];
  for (const segment of presentation.segments) {
    if (segment.kind === "openui") {
      openuiCount += 1;
      if (openuiCount > MAX_OPENUI_SEGMENTS || utf8ByteLength(segment.source) > MAX_OPENUI_SEGMENT_BYTES) {
        degraded = true;
        segments.push({ id: segment.id, kind: "text", text: segment.source });
        continue;
      }
      segments.push(segment);
      continue;
    }
    segments.push(
      segment.text.length > TEXT_SEGMENT_TRANSPORT_LIMIT
        ? { ...segment, text: segment.text.slice(0, TEXT_SEGMENT_TRANSPORT_LIMIT) }
        : segment,
    );
  }
  return degraded ? { ...presentation, segments, cause: "budget_exceeded" } : { ...presentation, segments };
}
