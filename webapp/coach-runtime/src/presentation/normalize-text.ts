/**
 * 面向前端展示的文本归一化（frontend-parity 批 7，digests §10）。
 *
 * 从 turn.ts 原样搬出到 presentation 模块：分流器只对 text 段调用它，
 * turn.ts 重新导出保持既有 import 不变。JSONL 持久化的是模型原始输出，
 * 本函数只作用于展示链路，历史消息零迁移。
 */

/** 代码块 fence 行（``` 或 ~~~，GFM 允许 ≤3 空格缩进）。 */
const FENCE_OPEN_RE = /^ {0,3}(?:`{3,}|~{3,})/;
/** 围栏闭合行：只有同一字符的 fence 记号（允许更长），无 info string。 */
const FENCE_CLOSE_RE = /^ {0,3}(`{3,}|~{3,})\s*$/;

/**
 * 剥除 fenced code / mermaid：连围栏带内容整体删除。流式片段里未闭合的
 * fence 同样吞掉其后残余，终稿闭合后自然收敛——这是「消 fence 流式陷阱」
 * 的代价面，只发生在展示链路。
 */
function stripFencedBlocks(text: string): string {
  const out: string[] = [];
  let fenceToken: string | null = null;
  for (const line of text.split("\n")) {
    if (fenceToken === null) {
      if (FENCE_OPEN_RE.test(line)) fenceToken = line.trim().slice(0, 1);
      else out.push(line);
      continue;
    }
    const trimmed = line.trim();
    if (trimmed.startsWith(fenceToken) && FENCE_CLOSE_RE.test(line)) fenceToken = null;
  }
  return out.join("\n");
}

/**
 * 受限白名单归一化（frontend-parity 批 7，digests §10）。
 *
 * 展示端从「删字符」改为「白名单透传」：放行训练计划天然的受限子集——
 * GFM 表格、有序/无序列表标记、行内加粗 **…** 与 @time 标记；其余继续剥除：
 * H1–H6 标题记号、代码块 fence（含内容）、引用块记号、Mermaid（属 fence 家族）、
 * 图片语法、一切 HTML。红线：任意 HTML 渲染＝XSS 面，受限解析器绝不引入
 * rehype-raw 式通道，这里先在源头剥干净。行内代码不在白名单，保留内容去反引号。
 * JSONL 持久化的是模型原始输出，本函数只作用于展示链路，历史消息零迁移。
 */
export function normalizeUserFacingText(value: string): string {
  return stripFencedBlocks(value)
    .replace(/<(script|style|iframe)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<[^>\n]*>/g, "")
    // 流式片段里未闭合的标签形（<tag… 到行尾无 >）：只吞掉确有标签开头的
    // 形状，普通文本里的比较符（如 5<6）不受影响。
    .replace(/(^|[ \t])<(?:[a-zA-Z/!?][^>\n]*)$/gm, "$1")
    .replace(/^ {0,3}#{1,6}\s+/gm, "")
    .replace(/^ {0,3}>\s?/gm, "")
    .replace(/!\[[^\]\n]*\]\([^)\n]*\)/g, "")
    .replace(/`([^`\n]+)`/g, "$1")
    .trim();
}
