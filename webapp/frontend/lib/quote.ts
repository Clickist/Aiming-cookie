/**
 * 划选引用（quote-reply）纯逻辑模块。
 *
 * 规格出处：docs/quote-feature-research.md（§2 交互规格 / §3 数据流 /
 * §3.4 拼装格式与长度预算 / §5 测试策略）。不 import React，node:test 直测；
 * DOM 探测参数全部可注入，划选判定与浮层几何在无 DOM 环境可覆盖。
 *
 * 拍板结论：
 * ① quote-only 禁止发送（composeQuotedContent 对空正文返回 null）；
 * ② chips / 编辑重发路径中引用降级为纯文本（CoachPanel 接线处落地）；
 * ③ 引用块随草稿持久化（draft envelope v2，见 lib/composer.ts）。
 */

// ── 常量 ────────────────────────────────────────────────────────────────

/** 引文段头部。刻意不用 `#` 标题——批 7 归一化会剥除标题记号（调研 §3.4）。 */
export const QUOTE_HEADER = "[引用 Coach]";
/** blockquote 逐行前缀；模型的母语标记，边界清晰。 */
export const QUOTE_PREFIX = "> ";
/** 单条引文长度上限（UTF-16 码元，与 sidecar slice 语义一致）；超长拒收并提示。 */
export const QUOTE_MAX_CHARS = 2000;
/**
 * 发送前总预算：sidecar 对 content 静默执行 `slice(0, 12_000)` 且从尾切，
 * 引文前置会把正文尾巴无声吞掉——必须在发送端可见地拒绝（调研 §3.4/§6.4）。
 * 预留 ~1000 余量给 sidecar 内部包装，避免贴着硬上限发送。
 */
export const SEND_BUDGET_CHARS = 11000;

/** composer 中的单条引用块（独立 state 数组，不混进 textarea 字符串）。 */
export type CoachQuote = { id: number; text: string };

// ── 快照（划选 → 引用块）──────────────────────────────────────────────

/**
 * 归一化引文原文：CRLF 折成 LF，并逐行剥离行首 `>`（含紧随空格）。
 * 行首 `>` 是拼装格式的结构记号，来源文本若携带会破坏 compose/parse 的
 * round-trip（调研 §5.1 防御要求）。
 */
export function stripQuoteMarkers(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => {
      if (line === ">") return "";
      return line.replace(/^> ?/, "");
    })
    .join("\n");
}

export type QuoteSnapshot =
  | { ok: true; text: string }
  | { ok: false; reason: "empty" | "too_long" };

/**
 * 点击「引用」时的快照：取浏览器对渲染链路已完成富文本降维的纯文本
 * （所见即所引），trim 两端、剥结构记号、拒空、按上限拒收超长。
 * 注意拿的是 DOM 展示文本而非 sidecar 存储的模型原始输出——这是期望性质，
 * 由 round-trip 与合同测试锁定该认知（调研 §2.2）。
 */
export function snapshotQuote(raw: string): QuoteSnapshot {
  const text = stripQuoteMarkers(raw).trim();
  if (!text) return { ok: false, reason: "empty" };
  if (text.length > QUOTE_MAX_CHARS) return { ok: false, reason: "too_long" };
  return { ok: true, text };
}

// ── 拼装与解析（构造器与解析器共享常量，round-trip 锁定）──────────────

/**
 * 最终发出的 user content 形态（调研 §3.4）：
 *
 * ```
 * [引用 Coach]
 * > 第一段第一行
 * > 第二行
 *
 * [引用 Coach]
 * > 另一段
 *
 * （用户正文）
 * ```
 *
 * 返回 null 的情形：没有任何内容；或**有引用但正文为空**（拍板①，
 * quote-only 禁止发送）。无引用时原样返回 trim 后正文。
 */
export function composeQuotedContent(input: { quotes: readonly CoachQuote[]; text: string }): string | null {
  const body = input.text.trim();
  const quotes = input.quotes.filter((quote) => quote.text.trim().length > 0);
  if (!body) return null;
  if (quotes.length === 0) return body;
  const sections = quotes.map((quote) =>
    [
      QUOTE_HEADER,
      ...quote.text.split("\n").map((line) => (line.length > 0 ? `${QUOTE_PREFIX}${line}` : ">")),
    ].join("\n"),
  );
  return `${sections.join("\n\n")}\n\n${body}`;
}

export type ParsedQuotedContent = { quotes: string[]; text: string };

const MALFORMED: ParsedQuotedContent = { quotes: [], text: "" };

/**
 * 回显解析：只认「content 开头连续出现的 `[引用 Coach]\n> …` 固定形状」，
 * 其余一律当正文返回（保守错杀，调研 §6.5）。legacy 消息、编辑过的 chip
 * 文本天然不匹配开头链，原样透出向后兼容。
 */
export function parseQuotedContent(content: string): ParsedQuotedContent {
  if (!content.startsWith(QUOTE_HEADER)) return { quotes: [], text: content };
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const quotes: string[] = [];
  let i = 0;
  while (i < lines.length && lines[i] === QUOTE_HEADER) {
    i += 1;
    const quoteLines: string[] = [];
    while (i < lines.length && (lines[i] === ">" || lines[i].startsWith(QUOTE_PREFIX))) {
      quoteLines.push(lines[i] === ">" ? "" : lines[i].slice(QUOTE_PREFIX.length));
      i += 1;
    }
    // 头后没有 blockquote 行：不是我们产出的形状，整体回退为原文。
    if (quoteLines.length === 0) return { quotes: [], text: content };
    quotes.push(quoteLines.join("\n"));
    // 各引文段之间以一个空行分隔；缺失也接受（手动编辑过的串）。
    if (i < lines.length && lines[i] === "") i += 1;
  }
  if (quotes.length === 0) return { quotes: [], text: content };
  return { quotes, text: lines.slice(i).join("\n").trim() };
}

/**
 * 发送前预算校验：引用＋正文合计超出预算即拒绝（调用方给出用户可见提示），
 * 不再依赖 sidecar 的静默切尾。
 */
export function isWithinSendBudget(composed: string): boolean {
  return composed.length <= SEND_BUDGET_CHARS;
}

// ── 划选判定（DOM 探针注入版）─────────────────────────────────────────

/** 最小元素探针：真实 Element 天然满足，node:test 用字面量对象伪造。 */
export interface SelectionProbeElement {
  getAttribute(name: string): string | null;
}

export type AssistantSelectionProbe<E extends SelectionProbeElement> = {
  /** 选区是否折叠（折叠＝没有有效选择）。 */
  collapsed: boolean;
  /** selection.toString() 的原始产物。 */
  selectedText: string;
  /** anchorNode / focusNode 分别向上找到的消息 article。 */
  anchorArticle: E | null;
  focusArticle: E | null;
};

export type AssistantSelectionTarget<E extends SelectionProbeElement> = { article: E; text: string };

/**
 * 合格选区的充要条件（调研 §2.1）：未折叠、trim 后非空、anchor/focus 同属
 * 同一个 `article[data-role="assistant"]` 且该 article 不是流式消息
 * （流式 partial article 带 data-streaming="true"）。其余容器（用户气泡、
 * 训练卡、工具步骤、讨论条）上的划选一律 null＝不出浮层。
 */
export function evaluateAssistantSelection<E extends SelectionProbeElement>(
  probe: AssistantSelectionProbe<E>,
): AssistantSelectionTarget<E> | null {
  if (probe.collapsed) return null;
  const text = probe.selectedText.trim();
  if (!text) return null;
  if (!probe.anchorArticle || probe.anchorArticle !== probe.focusArticle) return null;
  if (probe.anchorArticle.getAttribute("data-role") !== "assistant") return null;
  if (probe.anchorArticle.getAttribute("data-streaming") === "true") return null;
  return { article: probe.anchorArticle, text };
}

// ── DOM 适配（薄壳，组件接线用；不进 node:test 覆盖面）────────────────

/** 从任意事件目标/选区端点向上找 assistant 消息 article 容器。 */
export function messageArticleFromNode(node: Node | null): HTMLElement | null {
  const el = node instanceof Element ? node : node?.parentElement ?? null;
  return el ? el.closest<HTMLElement>('article[data-role="assistant"]') : null;
}

export type SelectionAnchorRect = { cx: number; top: number; bottom: number } | null;

/**
 * 取选区第一行的视口锚点（水平中点 + 上/下边缘）。WKWebView 怪癖缓解
 * （调研 §6.1）：Range.getClientRects 在折叠边界可能返回空集合——先取
 * rects 里第一个非零矩形，退化为 getBoundingClientRect，仍无效则返回 null
 * 直接不出浮层。只消费这一个 bounding 信息，不做逐 rects 布局。
 */
export function selectionAnchorRect(selection: Selection | null): SelectionAnchorRect {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  const rect =
    Array.from(range.getClientRects()).find((item) => item.width > 0 || item.height > 0) ??
    range.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) return null;
  return { cx: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom };
}
