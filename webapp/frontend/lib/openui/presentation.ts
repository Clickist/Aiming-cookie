/**
 * coach_presentation.v1 前端消费层（ARCHITECTURE §5.3）。
 *
 * 展示边界由 sidecar 投影（C 块）提供；本模块只做三件事：
 * 1. 对兼容期的原始回复文本执行与冻结合同同语义的 openui 围栏切分；
 * 2. 校验区段预算/library 身份并派生前端局部渲染状态（rendering|ready|degraded|failed）；
 * 3. 把 @ToAssistant 手势构造成 coach_ui_action.v1（动作权限见合同）。
 *
 * 切分语法必须与 `webapp/coach-runtime/test/fixtures/presentation/split-fixtures.json`
 * 逐字一致（该 fixtures 同时约束 sidecar 与前端兼容层）。
 */

export const COACH_PRESENTATION_SCHEMA = "coach_presentation.v1" as const;
export const OPENUI_FENCE_LANG = "openui-lang";
export const MAX_OPENUI_SEGMENT_BYTES = 64 * 1024;
export const MAX_OPENUI_SEGMENTS = 1;
export const MAX_OPENUI_CHARTS = 2;

/**
 * 组件库身份（单一来源：提示词生成与运行时 Renderer 共用）。
 * 数值与 sidecar `src/presentation/types.ts` 冻结值一致；
 * 一致性由测试跨文件断言，改值必须双侧同步。
 */
export const COACH_OPENUI_LIBRARY_ID = "coach-openui";
export const COACH_OPENUI_LIBRARY_VERSION = "1.0.0";

export type CoachGenerationStatus =
  | "streaming"
  | "completed"
  | "stopped"
  | "interrupted"
  | "failed";

/** §5.3 冻结的 cause 枚举（前端渲染侧同样只使用这些值）。 */
export type CoachPresentationCause =
  | "parse_error"
  | "unknown_component"
  | "render_error"
  | "unsupported_library"
  | "budget_exceeded"
  | "process_interrupted"
  | "stopped";

export type CoachPresentationTextSegment = {
  id: string;
  kind: "text";
  text: string;
};

export type CoachPresentationOpenUiSegment = {
  id: string;
  kind: "openui";
  library_id: string;
  library_version: string;
  source: string;
};

export type CoachPresentationSegment =
  | CoachPresentationTextSegment
  | CoachPresentationOpenUiSegment;

export type CoachPresentation = {
  schema_version: typeof COACH_PRESENTATION_SCHEMA;
  message_id: string | number | null;
  run_id: string | null;
  /** 单调递增；旧 revision 不得覆盖新状态。 */
  revision: number;
  generation_status: CoachGenerationStatus;
  cause?: CoachPresentationCause;
  segments: CoachPresentationSegment[];
};

export type CoachUiActionV1 = {
  schema_version: "coach_ui_action.v1";
  interaction_id: string;
  message_id: string | number | null;
  segment_id: string;
  kind: "to_assistant";
  message: string;
};

export type OpenUIRenderState = "rendering" | "ready" | "degraded" | "failed";

export type DerivedOpenUIRenderState = {
  state: OpenUIRenderState;
  cause?: CoachPresentationCause;
};

export type SegmentDraft =
  | { kind: "text"; text: string }
  | { kind: "openui"; source: string };

type LinePart = { content: string; eol: string };

function splitLines(input: string): LinePart[] {
  const lines: LinePart[] = [];
  let start = 0;
  for (let index = 0; index < input.length; index += 1) {
    const char = input[index];
    if (char === "\n") {
      lines.push({ content: input.slice(start, index), eol: "\n" });
      start = index + 1;
      continue;
    }
    if (char === "\r") {
      if (input[index + 1] === "\n") {
        lines.push({ content: input.slice(start, index), eol: "\r\n" });
        index += 1;
      } else {
        lines.push({ content: input.slice(start, index), eol: "\r" });
      }
      start = index + 1;
    }
  }
  if (start < input.length) lines.push({ content: input.slice(start), eol: "" });
  return lines;
}

type Fence = { char: "`" | "~"; count: number };

function parseFenceLine(line: string): { fence: Fence; info: string } | null {
  const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match) return null;
  return {
    fence: { char: match[1]![0] as "`" | "~", count: match[1]!.length },
    info: match[2]!,
  };
}

function closingFence(line: string): Fence | null {
  const match = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  if (!match) return null;
  return { char: match[1]![0] as "`" | "~", count: match[1]!.length };
}

/** 只有反引号围栏 + info 精确为 openui-lang 才是 UI 区段（大小写敏感）。 */
function isOpenUIOpener(fence: Fence, info: string): boolean {
  return fence.char === "`" && info.replace(/[ \t]+$/, "") === OPENUI_FENCE_LANG;
}

/**
 * 流式中途可能长成 openui 开围栏的半截行（如 "```openui-la"）：
 * 暂扣不展示，防止 fence 记号或半截 DSL 泄进 text。
 */
function isPotentialOpenUIOpener(line: string): boolean {
  const match = /^ {0,3}(`+)(.*)$/.exec(line);
  if (!match) return false;
  const ticks = match[1]!;
  const rest = match[2]!;
  if (ticks.length < 3) return rest.length === 0;
  if (OPENUI_FENCE_LANG.startsWith(rest)) return true;
  return new RegExp(`^${OPENUI_FENCE_LANG}[ \\t]+$`).test(rest);
}

/**
 * 兼容期文本切分：与冻结 fixtures 同语义。
 * - openui 围栏：开围栏行连同换行整体消费；DSL 原文透传；
 *   闭合围栏行尾换行属于后续 text（fixtures 冻结语义）。
 * - 普通围栏（python/无标识/相似语言标识）：连围栏带内容删除；
 *   删除时吞掉围栏块前的一个空行（fixtures 冻结的段落收敛语义），
 *   闭合行尾不额外产生空行。
 * - 未闭合 openui 围栏：已收 DSL 作为进行中的 openui 段返回。
 * - 未闭合普通围栏：吞掉其后残余（与既有 normalizeUserFacingText 一致）。
 * - CRLF：识别与内容都按 CRLF 工作。
 */
export function splitOpenUISegments(input: string): SegmentDraft[] {
  if (!input) return [];
  const lines = splitLines(input);
  const drafts: SegmentDraft[] = [];
  let text = "";

  const flushText = (): void => {
    if (!text) return;
    drafts.push({ kind: "text", text });
    text = "";
  };

  const consumeOpenUI = (openerIndex: number): number => {
    const opener = parseFenceLine(lines[openerIndex]!.content)!.fence;
    let source = "";
    let index = openerIndex + 1;
    let closed = false;
    for (; index < lines.length; index += 1) {
      const line = lines[index]!;
      const close = closingFence(line.content);
      if (close && close.char === opener.char && close.count >= opener.count) {
        closed = true;
        break;
      }
      source += line.content + line.eol;
    }
    flushText();
    // 闭合围栏前的换行是结构分隔符，不属于 DSL。
    if (closed) source = source.replace(/\r\n$/, "").replace(/\n$/, "").replace(/\r$/, "");
    if (source) drafts.push({ kind: "openui", source });
    if (!closed) return lines.length;
    // 闭合 fence 行的换行属于后续 text。
    text += lines[index]!.eol;
    return index + 1;
  };

  const consumeOrdinaryFence = (openerIndex: number): number => {
    const opener = parseFenceLine(lines[openerIndex]!.content)!.fence;
    let index = openerIndex + 1;
    let closed = false;
    for (; index < lines.length; index += 1) {
      const close = closingFence(lines[index]!.content);
      if (close && close.char === opener.char) {
        closed = true;
        break;
      }
    }
    if (!closed) return lines.length;
    // 闭合行尾换行与紧随其后的空行都不再进入 text（fixtures 冻结的块间
    // 收敛语义：普通围栏删除后只保留围栏前的空行）。
    let next = index + 1;
    if (next < lines.length && lines[next]!.content === "") next += 1;
    return next;
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index]!;
    const parsed = parseFenceLine(line.content);
    if (parsed && isOpenUIOpener(parsed.fence, parsed.info)) {
      index = consumeOpenUI(index);
      continue;
    }
    if (parsed) {
      index = consumeOrdinaryFence(index);
      continue;
    }
    // 最后一行尚无完整换行且可能长成 openui 开围栏：暂扣，不下发半截 fence。
    const pendingLine =
      index === lines.length - 1 && (line.eol === "" || line.eol === "\r");
    if (pendingLine && isPotentialOpenUIOpener(line.content)) {
      index += 1;
      continue;
    }
    text += line.content + line.eol;
    index += 1;
  }
  flushText();
  return drafts;
}

/**
 * 兼容期投影：把完整/流式原文转成 coach_presentation.v1。
 * sidecar 投影就绪后由调用方直接传 presentation，本函数只作回退。
 */
export function presentationFromText(
  rawText: string,
  options: {
    messageId?: string | number | null;
    runId?: string | null;
    revision?: number;
    generationStatus?: CoachGenerationStatus;
  } = {},
): CoachPresentation {
  const generationStatus = options.generationStatus ?? "completed";
  const segments: CoachPresentationSegment[] = splitOpenUISegments(rawText).map(
    (draft, index) =>
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
  const cause: CoachPresentationCause | undefined =
    generationStatus === "stopped"
      ? "stopped"
      : generationStatus === "interrupted"
        ? "process_interrupted"
        : undefined;
  return {
    schema_version: COACH_PRESENTATION_SCHEMA,
    message_id: options.messageId ?? null,
    run_id: options.runId ?? null,
    revision: options.revision ?? 1,
    generation_status: generationStatus,
    ...(cause ? { cause } : {}),
    segments,
  };
}

/**
 * 运行时校验 sidecar 下发的投影（SSE/轮询/完成响应都可能来自旧版本 sidecar）。
 * 形状不符时返回 null，由调用方回退到兼容期文本切分；绝不把半截对象塞给
 * Renderer。
 */
export function asCoachPresentation(value: unknown): CoachPresentation | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.schema_version !== COACH_PRESENTATION_SCHEMA) return null;
  if (typeof candidate.revision !== "number" || !Number.isFinite(candidate.revision)) return null;
  if (
    candidate.generation_status !== "streaming" &&
    candidate.generation_status !== "completed" &&
    candidate.generation_status !== "stopped" &&
    candidate.generation_status !== "interrupted" &&
    candidate.generation_status !== "failed"
  ) {
    return null;
  }
  if (!Array.isArray(candidate.segments)) return null;
  for (const segment of candidate.segments) {
    if (!segment || typeof segment !== "object") return null;
    const item = segment as Record<string, unknown>;
    if (typeof item.id !== "string") return null;
    if (item.kind === "text") {
      if (typeof item.text !== "string") return null;
    } else if (item.kind === "openui") {
      if (
        typeof item.source !== "string" ||
        typeof item.library_id !== "string" ||
        typeof item.library_version !== "string"
      ) {
        return null;
      }
    } else {
      return null;
    }
  }
  return candidate as unknown as CoachPresentation;
}

/**
 * 兼容期防御：sidecar 在预算超限时会把 openui 区段降级成 text 保留原文
 * （带 cause=budget_exceeded）。展示层绝不能把这种 text 当普通正文渲染成
 * 裸 DSL；命中 DSL 形状时改呈现 cause 文案。
 */
export function looksLikeOpenUIDSL(text: string): boolean {
  return /^\s*(?:root\s*=\s*|(?:[A-Za-z_]\w*\s*=\s*)?(?:Card|CardHeader|Stack|TextContent|BoldText|MetricIndicatorInline|Callout|BarChart|LineChart|Series|Buttons|Button)\s*\()/m.test(
    text,
  );
}

/** 历史消息的生成状态：interrupted 优先于 stopped，否则视为已完成。 */
export function generationStatusFromMessage(message: {
  interrupted?: boolean;
  stopped?: boolean;
}): CoachGenerationStatus {
  if (message.interrupted) return "interrupted";
  if (message.stopped) return "stopped";
  return "completed";
}

/** sidecar run 状态 → 展示生成状态（streaming 只到 queued/running）。 */
export function generationStatusFromRunStatus(
  status: "queued" | "running" | "succeeded" | "failed" | "stopped",
): CoachGenerationStatus {
  switch (status) {
    case "queued":
    case "running":
      return "streaming";
    case "stopped":
      return "stopped";
    case "failed":
      return "failed";
    case "succeeded":
      return "completed";
  }
}

/** 旧 revision 不覆盖新状态（同一 message_id 内比较）。 */
export function isStalePresentation(
  previous: CoachPresentation | null | undefined,
  next: CoachPresentation,
): boolean {
  if (!previous) return false;
  if (previous.message_id !== next.message_id || previous.run_id !== next.run_id) return false;
  return next.revision < previous.revision;
}

export function mergeCoachPartialSnapshot<T extends {
  run_ref: string;
  partial_text: string | null;
  partial_presentation?: unknown;
}>(current: T, frame: { text?: unknown; presentation?: unknown }): T & { partial_presentation?: unknown } {
  const presentation = asCoachPresentation(frame.presentation);
  const previous = asCoachPresentation(current.partial_presentation);
  if (presentation && (
    (presentation.run_id != null && presentation.run_id !== current.run_ref) ||
    isStalePresentation(previous, presentation)
  )) return current;
  return {
    ...current,
    ...(typeof frame.text === "string" ? { partial_text: frame.text } : {}),
    ...(presentation ? { partial_presentation: presentation } : {}),
  };
}

function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** 区段内图表调用数（BarChart/LineChart）；用于合同「≤2 张图」的前端兜底。 */
export function countOpenUICharts(source: string): number {
  const matches = source.match(/\b(?:BarChart|LineChart)\s*\(/g);
  return matches ? matches.length : 0;
}

/**
 * 单区段预算：≤64 KiB 且图表 ≤2 张。跨区段「单回复 1 个 openui 区段」
 * 由 sidecar 投影负责；前端对收到的每个区段做最后一道校验。
 */
export function openUISegmentBudgetIssue(source: string): "budget_exceeded" | null {
  if (utf8ByteLength(source) > MAX_OPENUI_SEGMENT_BYTES) return "budget_exceeded";
  if (countOpenUICharts(source) > MAX_OPENUI_CHARTS) return "budget_exceeded";
  return null;
}

export type OpenUIErrorLike = {
  code?: string;
  message?: string;
  severity?: "warning";
};

function isErrorLike(error: OpenUIErrorLike): boolean {
  return error.severity !== "warning";
}

function isParseCode(code: string | undefined): boolean {
  return (
    code === "parse_error" ||
    code === "parse-failed" ||
    code === "parse-exception" ||
    code === "no-root" ||
    code === "missing-required" ||
    code === "null-required" ||
    code === "inline-reserved" ||
    code === "excess-args" ||
    code === "type-mismatch" ||
    code === "unknown-function"
  );
}

/**
 * 前端局部渲染状态机。生成状态与渲染状态严格分开：
 * sidecar 生成失败/中断由 generation_status 表达，客户端解析/渲染失败
 * 另行标 cause，绝不冒充 sidecar 生成失败。
 */
export function deriveOpenUIRenderState(input: {
  generationStatus: CoachGenerationStatus;
  libraryId: string;
  libraryVersion: string;
  source: string;
  hasParsed: boolean;
  rootPresent: boolean;
  parseErrors?: OpenUIErrorLike[];
  renderErrors?: OpenUIErrorLike[];
  /** DSL 中出现 Query/Mutation（第一刀不注册、不接 toolProvider）。 */
  hasForbiddenCalls?: boolean;
}): DerivedOpenUIRenderState {
  if (
    input.libraryId !== COACH_OPENUI_LIBRARY_ID ||
    input.libraryVersion !== COACH_OPENUI_LIBRARY_VERSION
  ) {
    return { state: "failed", cause: "unsupported_library" };
  }
  const budget = openUISegmentBudgetIssue(input.source);
  if (budget) return { state: "failed", cause: budget };

  // Query/Mutation 是解析器内建调用，即使不在 library 里也能被接受；
  // 第一刀明确不注册、不接 toolProvider，出现即拒收（不静默执行）。
  if (input.hasForbiddenCalls) {
    return { state: "failed", cause: "parse_error" };
  }

  // 首次解析回调尚未到达：保持 rendering，避免完成态首帧误报「图表无法显示」。
  if (!input.hasParsed) return { state: "rendering" };

  if (input.generationStatus === "streaming") {
    return { state: "rendering" };
  }

  const parseErrors = (input.parseErrors ?? []).filter(isErrorLike);
  const renderErrors = (input.renderErrors ?? []).filter(isErrorLike);
  const hasUnknownComponent = parseErrors.some((error) => error.code === "unknown-component");
  const hasParseError = parseErrors.some((error) => isParseCode(error.code));
  const hasRenderError = renderErrors.some(
    (error) => error.code === "render-error" || error.code === "runtime-error",
  );

  if (input.generationStatus === "stopped" || input.generationStatus === "interrupted") {
    const cause =
      input.generationStatus === "stopped" ? "stopped" : "process_interrupted";
    return { state: input.rootPresent ? "degraded" : "failed", cause };
  }

  if (input.generationStatus === "failed") {
    return { state: input.rootPresent ? "degraded" : "failed" };
  }

  // completed 终态校验：渲染器内部会吞错，不能只依赖 React boundary。
  if (!input.rootPresent) {
    return {
      state: "failed",
      cause: hasUnknownComponent ? "unknown_component" : "parse_error",
    };
  }
  if (hasUnknownComponent) return { state: "degraded", cause: "unknown_component" };
  if (hasParseError) return { state: "degraded", cause: "parse_error" };
  if (hasRenderError) return { state: "degraded", cause: "render_error" };
  return { state: "ready" };
}

/** 只有生成完成且渲染 ready 才允许 @ToAssistant 手势。 */
export function canUseOpenUIActions(
  generationStatus: CoachGenerationStatus,
  renderState: OpenUIRenderState,
): boolean {
  return generationStatus === "completed" && renderState === "ready";
}

export const MAX_UI_ACTION_MESSAGE_CHARS = 2_000;

export function isCoachUiActionMessage(message: string): boolean {
  return message.trim().length > 0 && message.length <= MAX_UI_ACTION_MESSAGE_CHARS;
}

let interactionCounter = 0;

function newInteractionId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
  interactionCounter += 1;
  return `openui-action-${Date.now().toString(36)}-${interactionCounter.toString(36)}`;
}

/** 构造合同 §5.3 的动作来源标记；调用方只允许 kind=to_assistant。 */
export function createCoachUiAction(args: {
  messageId: string | number | null;
  segmentId: string;
  message: string;
}): CoachUiActionV1 {
  return {
    schema_version: "coach_ui_action.v1",
    interaction_id: newInteractionId(),
    message_id: args.messageId,
    segment_id: args.segmentId,
    kind: "to_assistant",
    message: args.message,
  };
}

/**
 * Renderer.onAction 的唯一落点：只接受 continue_conversation（@ToAssistant），
 * 其它动作（@OpenUrl/自定义动作）即使被注入也一律拒绝。
 */
export function toAssistantMessageFromEvent(event: {
  type?: string;
  humanFriendlyMessage?: string;
}): string | null {
  if (event.type !== "continue_conversation") return null;
  const message = (event.humanFriendlyMessage ?? "").trim();
  return isCoachUiActionMessage(message) ? message : null;
}
