/**
 * 受限富文本解析器（frontend-parity 批 7，digests §10）。
 *
 * 只解析 sidecar 白名单归一化后放行的受控子集：GFM 表格、有序/无序列表、
 * 行内加粗（**…**）。纯逻辑、无 React 依赖，流式容错原则＝未闭合的表格/
 * 列表按已收到部分渲染、不吞任何字符；唯一例外是代码围栏——沿用 sidecar
 * 归一化语义，围栏开启后的内容一并隐藏，终稿闭合后自然收敛。
 * 输出为纯数据节点树，由 CoachMessageText 渲染成 React 结构；全程不经
 * dangerouslySetInnerHTML，任意 HTML 由 React 转义为惰性文本（XSS 红线）。
 */

export const MAX_TABLE_COLUMNS = 5;

// ── 聊天 @time 时间标记（视频面板复盘升级 P0.3，brief §二.3 D7）─────────────
//
// 渲染层格式化＝JSONL 原文不动、历史消息零迁移：解析层只产出 chip 数据，
// 显示文案（00:51.5 / 00:38–00:43）由格式化函数给出，点击仍跳原始毫秒。

/** 单点 @51.5s；区间 @38.2-43.7s——连字符衔接两个数值，秒缀只在区间末尾。 */
export const TIME_TOKEN_PATTERN = /@(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?s/g;

export type TimeChip =
  | { kind: "point"; raw: string; label: string; timeMs: number }
  | { kind: "range"; raw: string; label: string; startMs: number; endMs: number };

export type TimeSegment = { text: string; chip?: TimeChip };

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * 时间码显示格式（D7）：分钟补两位 00:xx 起步；秒带小数时保留一位
 * （51.5 → 00:51.5，整数不补 .0）；超过 1 小时进位到 h:mm:ss 且不再展示
 * 亚秒精度。
 */
export function formatTimecode(seconds: number): string {
  if (!Number.isFinite(seconds)) return "00:00";
  const totalTenths = Math.max(0, Math.round(seconds * 10));
  if (totalTenths >= 36000) {
    const hours = Math.floor(totalTenths / 36000);
    return `${hours}:${pad2(Math.floor((totalTenths % 36000) / 600))}:${pad2(Math.floor((totalTenths % 600) / 10))}`;
  }
  const minutes = Math.floor(totalTenths / 600);
  const remainder = totalTenths % 600;
  const wholeSeconds = Math.floor(remainder / 10);
  const tenths = remainder % 10;
  return tenths
    ? `${pad2(minutes)}:${pad2(wholeSeconds)}.${tenths}`
    : `${pad2(minutes)}:${pad2(wholeSeconds)}`;
}

/** 区间渲染为「起–止」整秒（en dash）：38.2–43.7 → 00:38–00:43。 */
export function formatTimecodeRange(startSeconds: number, endSeconds: number): string {
  return `${formatTimecode(Math.max(0, Math.floor(startSeconds)))}–${formatTimecode(Math.max(0, Math.floor(endSeconds)))}`;
}

function chipFromMatch(match: RegExpExecArray): TimeChip {
  const startSeconds = parseFloat(match[1]);
  if (match[2] !== undefined) {
    const endSeconds = parseFloat(match[2]);
    return {
      kind: "range",
      raw: match[0],
      label: formatTimecodeRange(startSeconds, endSeconds),
      startMs: Math.round(startSeconds * 1000),
      endMs: Math.round(endSeconds * 1000),
    };
  }
  return {
    kind: "point",
    raw: match[0],
    label: formatTimecode(startSeconds),
    timeMs: Math.round(startSeconds * 1000),
  };
}

/**
 * 把一段文本拆成 @time 芯片与普通文本分段（芯片原文保留在 chip.raw，
 * 展示文案取 chip.label）。无命中时原样单段返回，一字不动。
 */
export function parseTimeSegments(text: string): TimeSegment[] {
  const segments: TimeSegment[] = [];
  let lastIndex = 0;
  TIME_TOKEN_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TIME_TOKEN_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index) });
    }
    segments.push({ text: match[0], chip: chipFromMatch(match) });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex) });
  }
  return segments.length ? segments : [{ text }];
}

export type RichInline = { text: string; bold: boolean; /** 受控命名链接（白名单域，仅 https）的 href。 */ link?: string };

export type RichItem = {
  segments: RichInline[];
  /** 深一级的嵌套子块（实践中是嵌套列表）。 */
  children: RichNode[];
};

export type RichCell = RichInline[];

export type RichNode =
  | { kind: "paragraph"; segments: RichInline[] }
  | { kind: "list"; ordered: boolean; items: RichItem[] }
  | { kind: "table"; header: RichCell[] | null; rows: RichCell[][]; numericCols: boolean[] };

// ── 受控命名链接 ─────────────────────────────────────────────────────────
//
// 只放行联盟域的 https 链接渲染为可点击 <a>（域名白名单单一来源在此）；
// 其余一切链接形状（非白名单域、http、javascript: 等）一律保持字面文本。
// React 转义 + 无 innerHTML 的红线不因链接放行而松动。

export const COACH_LINK_HOSTS: ReadonlySet<string> = new Set([
  "s.click.taobao.com",
  "item.jd.com",
  "space.bilibili.com",
]);

function isAllowedHref(href: string): boolean {
  try {
    const url = new URL(href);
    return url.protocol === "https:" && COACH_LINK_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

// ── 行内加粗与命名链接 ───────────────────────────────────────────────────

/** 裸 URL 的终止字符：空白与中英文常见标点（URL 本体不含这些）。 */
const BARE_URL_TERMINATOR = /[\s）」】》>"'，。；、！？：,;:!?)}\]]/;

/**
 * 扫描一段文本里最早出现的白名单域裸 https URL（模型不按命名链接形状
 * 输出时的兜底，1.0.0 前实测身份问答必现）；返回 [起点, 终点)，无则 null。
 * 终点 = 第一个终止字符或文本末尾；host 不在白名单按字面文本处理。
 */
function findBareAllowedUrl(text: string, from: number): { start: number; end: number } | null {
  let at = text.indexOf("https://", from);
  while (at !== -1) {
    let end = at + 8;
    while (end < text.length && !BARE_URL_TERMINATOR.test(text[end])) end += 1;
    const candidate = text.slice(at, end);
    try {
      const url = new URL(candidate);
      if (url.protocol === "https:" && COACH_LINK_HOSTS.has(url.hostname) && url.hostname.length > 0) {
        return { start: at, end };
      }
    } catch {
      // 不是完整 URL（如仅 "https://"），按字面处理，继续找下一处。
    }
    at = text.indexOf("https://", end);
  }
  return null;
}

/**
 * 把一段文本拆成行内分段：** 配对为加粗，`[文字](https://…)` 且域名在
 * 白名单内配对为链接；白名单域的裸 https URL 也链接化（兜底，不吞字面）；
 * 落单的 ** 与不成形状的 [ 连同其内容保持字面（流式
 * 中途未闭合时一字不吞，闭合片段到达后由下一次全量重解析自然收敛）。
 * 链接 href 按第一个 `)` 截断——联盟链接（淘宝/京东）均为百分号编码，
 * 不含裸括号。
 */
export function parseBoldSegments(text: string): RichInline[] {
  const out: RichInline[] = [];
  let start = 0;
  let i = 0;
  while (i < text.length) {
    const boldAt = text.indexOf("**", i);
    const linkAt = text.indexOf("[", i);
    const bareUrl = findBareAllowedUrl(text, i);
    if (boldAt === -1 && linkAt === -1 && bareUrl === null) break;
    if (
      bareUrl !== null
      && (boldAt === -1 || bareUrl.start < boldAt)
      && (linkAt === -1 || bareUrl.start < linkAt)
    ) {
      const href = text.slice(bareUrl.start, bareUrl.end);
      if (bareUrl.start > start) out.push({ text: text.slice(start, bareUrl.start), bold: false });
      out.push({ text: href, bold: false, link: href });
      start = bareUrl.end;
      i = start;
      continue;
    }
    if (linkAt !== -1 && (boldAt === -1 || linkAt < boldAt)) {
      const closeBracket = text.indexOf("](https://", linkAt + 1);
      const lineEnd = text.indexOf("\n", linkAt);
      if (closeBracket !== -1) {
        const closeParen = text.indexOf(")", closeBracket);
        if (closeParen !== -1 && (lineEnd === -1 || closeParen < lineEnd)) {
          const label = text.slice(linkAt + 1, closeBracket);
          const href = text.slice(closeBracket + 2, closeParen);
          if (label.length > 0 && !label.includes("[") && !label.includes("]") && isAllowedHref(href)) {
            if (linkAt > start) out.push({ text: text.slice(start, linkAt), bold: false });
            out.push({ text: label, bold: false, link: href });
            start = closeParen + 1;
            i = start;
            continue;
          }
        }
      }
      i = linkAt + 1;
      continue;
    }
    const close = text.indexOf("**", boldAt + 2);
    if (close === -1) break;
    if (boldAt > start) out.push({ text: text.slice(start, boldAt), bold: false });
    out.push({ text: text.slice(boldAt + 2, close), bold: true });
    start = close + 2;
    i = start;
  }
  if (start < text.length) out.push({ text: text.slice(start), bold: false });
  return out;
}

// ── 表格 ─────────────────────────────────────────────────────────────────

/** 数值形状：41.2% / 1.8s / 120ms / 1:30 / 12/20 / 87 等。 */
const NUMERIC_CELL_RE = /^[-+(]?\d[\d.,]*(?:[:/]\d[\d.,]*)?\s?(?:%|[a-z]{1,3}|分钟|秒|米)?[)\]]?$/;

/** 按未转义竖线切行；`\|` 还原成字面 `|`。 */
function splitRowCells(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === "\\" && line[i + 1] === "|") {
      current += "|";
      i += 1;
    } else if (char === "|") {
      cells.push(current.trim());
      current = "";
    } else {
      current += char;
    }
  }
  cells.push(current.trim());
  return cells;
}

const DELIMITER_CELL_RE = /^:?-{1,}:?$/;

/** 去掉行首尾围管后按竖线切格（`\|` 还原为字面）。 */
function rowToCells(line: string): string[] {
  return splitRowCells(line.trim().replace(/^\|/, "").replace(/\|$/, ""));
}

/** GFM 分隔行（| --- | :---: | …）。 */
function isTableDelimiter(line: string): boolean {
  const cells = rowToCells(line);
  return cells.length >= 2 && cells.every((cell) => DELIMITER_CELL_RE.test(cell));
}

/** 数值列判定：>60% 非空单元格命中数值形状 → tabular-nums 对齐语义。 */
function detectNumericColumns(rows: string[][], columnCount: number): boolean[] {
  return Array.from({ length: columnCount }, (_, column) => {
    let filled = 0;
    let hits = 0;
    for (const row of rows) {
      const cell = (row[column] ?? "").trim();
      if (!cell) continue;
      filled += 1;
      if (NUMERIC_CELL_RE.test(cell)) hits += 1;
    }
    return filled > 0 && hits / filled > 0.6;
  });
}

// ── 列表 ─────────────────────────────────────────────────────────────────

/** ASCII 标记后必须有空格；中文顿号「、」习惯上不空格也认。 */
const LIST_ITEM_RE = /^(\s*)([-*+] |\d{1,9}[.)] |\d{1,9}、 ?)(.*)$/;

type OpenList = { node: Extract<RichNode, { kind: "list" }>; level: number };

// ── 主解析 ───────────────────────────────────────────────────────────────

const FENCE_RE = /^(?:`{3,}|~{3,})/;
const FENCE_CLOSE_RE = /^ {0,3}(?:`{3,}|~{3,})\s*$/;

/**
 * 把受限 Markdown 文本解析为节点树。每条流式 revision 全量重解析，
 * 无跨调用状态；容错约定见文件头。
 */
export function parseRichText(text: string): RichNode[] {
  const lines = text.split("\n");
  const nodes: RichNode[] = [];

  let paragraph: string[] = [];
  const flushParagraph = (): void => {
    if (!paragraph.length) return;
    nodes.push({ kind: "paragraph", segments: parseBoldSegments(paragraph.join("\n")) });
    paragraph = [];
  };

  let openLists: OpenList[] = [];
  const closeLists = (): void => {
    flushParagraph();
    openLists = [];
  };

  /**
   * 弹栈至正确深度后返回承接容器：同层同类 → 续用现有列表；
   * 否则新建（必要时挂到上一层最后 item 的 children 下）。
   */
  const listFor = (level: number, ordered: boolean): RichItem[] => {
    let top = openLists[openLists.length - 1];
    while (top && (top.level > level || (top.level === level && top.node.ordered !== ordered))) {
      openLists.pop();
      top = openLists[openLists.length - 1];
    }
    if (top && top.level === level && top.node.ordered === ordered) return top.node.items;
    flushParagraph();
    const node: Extract<RichNode, { kind: "list" }> = { kind: "list", ordered, items: [] };
    const parent = openLists[openLists.length - 1];
    if (parent && parent.level < level) {
      const target = parent.node.items[parent.node.items.length - 1];
      if (target) target.children.push(node);
      else nodes.push(node);
    } else {
      nodes.push(node);
    }
    openLists.push({ node, level });
    return node.items;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    // 围栏：开启后吞至闭合行或文末（sidecar 已剥，前端兜底同一语义）。
    // 悬挂段落先落盘，防止围栏行吞掉它上面的正文。
    if (FENCE_RE.test(trimmed)) {
      closeLists();
      index += 1;
      while (index < lines.length && !FENCE_CLOSE_RE.test(lines[index])) index += 1;
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      continue;
    }

    // 表格开启：本行含竖线且下一行是分隔行。
    if (
      index + 1 < lines.length
      && line.includes("|")
      && !LIST_ITEM_RE.test(line)
      && isTableDelimiter(lines[index + 1])
    ) {
      closeLists();
      const rawRows = [line];
      let end = index + 2;
      while (end < lines.length && lines[end].includes("|") && lines[end].trim()) {
        rawRows.push(lines[end]);
        end += 1;
      }
      index = end - 1;
      nodes.push(buildTable(rawRows));
      continue;
    }

    const listItem = LIST_ITEM_RE.exec(line);
    if (listItem) {
      const level = Math.min(Math.floor(listItem[1].length / 2), 4);
      const marker = listItem[2];
      const ordered = marker.startsWith("-") || marker.startsWith("*") || marker.startsWith("+")
        ? false
        : true;
      listFor(level, ordered).push({ segments: parseBoldSegments(listItem[3]), children: [] });
      continue;
    }

    // 列表项的悬挂续行（缩进普通行）：并入最内层 item 的末段。
    if (openLists.length > 0 && /^\s{2,}\S/.test(line)) {
      const items = deepestItems(openLists);
      const last = items[items.length - 1];
      if (last) {
        last.segments.push({ text: `\n${trimmed}`, bold: false });
        continue;
      }
    }

    // 其余普通行：只在确有打开列表时结束它们；多行文本同段合并。
    if (openLists.length > 0) closeLists();
    paragraph.push(line);
  }
  flushParagraph();
  return nodes;
}

function deepestItems(stack: OpenList[]): RichItem[] {
  const top = stack[stack.length - 1];
  if (!top) throw new Error("no open list");
  return top.node.items;
}

/** 表格组装：列数钳制（>5 列整表降级为段落保真）＋数值列探测。
 *  rawRows = [表头行, ...数据行]（分隔行在主循环已被消费）。 */
function buildTable(rawRows: string[]): RichNode {
  const header = rowToCells(rawRows[0]);
  const bodyRaw = rawRows.slice(1);
  const bodyCells = bodyRaw.map(rowToCells);
  const columnCount = Math.max(header.length, ...bodyCells.map((row) => row.length));
  if (columnCount > MAX_TABLE_COLUMNS) {
    return { kind: "paragraph", segments: parseBoldSegments(rawRows.join("\n")) };
  }
  const rows = bodyCells.map((row) => {
    const padded = [...row];
    while (padded.length < columnCount) padded.push("");
    return padded.map((cell) => parseBoldSegments(cell));
  });
  return {
    kind: "table",
    header: header.map((cell) => parseBoldSegments(cell)),
    rows,
    numericCols: detectNumericColumns(bodyCells, columnCount),
  };
}
