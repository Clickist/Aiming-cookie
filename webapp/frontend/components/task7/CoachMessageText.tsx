"use client";

import { Fragment, type ReactNode } from "react";

import {
  parseRichText,
  type RichInline,
  type RichItem,
  type RichNode,
} from "@/lib/rich-text";

const TIME_POINT_PATTERN = /@(\d+\.?\d*)s/g;

interface TextSegment {
  text: string;
  timeMs: number | null;
}

function parseTimePoints(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  TIME_POINT_PATTERN.lastIndex = 0;
  while ((match = TIME_POINT_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), timeMs: null });
    }
    segments.push({ text: match[0], timeMs: Math.round(parseFloat(match[1]) * 1000) });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), timeMs: null });
  }
  return segments.length ? segments : [{ text, timeMs: null }];
}

type TimeLinkCtx = {
  analysisRef?: string | null;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
};

/** 行内分段：加粗（600）与 @time 芯片在同一文本上叠加解析。 */
function renderSegments(
  segments: RichInline[],
  ctx: TimeLinkCtx,
  keyPrefix: string,
  tail?: ReactNode,
): ReactNode[] {
  const out: ReactNode[] = [];
  segments.forEach((segment, segIndex) => {
    const pieces = parseTimePoints(segment.text).map((piece, pieceIndex) =>
      piece.timeMs !== null && ctx.analysisRef && ctx.onOpenVideo ? (
        <button
          className="task6-time-link"
          key={pieceIndex}
          onClick={() => ctx.onOpenVideo?.(ctx.analysisRef as string, piece.timeMs ?? undefined)}
          type="button"
        >
          {piece.text}
        </button>
      ) : piece.timeMs !== null ? (
        <span className="task6-time-link task6-time-link--static" key={pieceIndex}>{piece.text}</span>
      ) : (
        <Fragment key={pieceIndex}>{piece.text}</Fragment>
      ),
    );
    if (segment.bold) {
      out.push(
        <strong className="task7-rich-bold" key={`${keyPrefix}-${segIndex}`}>{pieces}</strong>,
      );
    } else {
      out.push(...pieces);
    }
  });
  if (tail) out.push(<Fragment key={`${keyPrefix}-tail`}>{tail}</Fragment>);
  return out;
}

function renderListItem(item: RichItem, ctx: TimeLinkCtx, keyPrefix: string, tail?: ReactNode): ReactNode {
  // 尾巴递归让给最深一层子列表；没有子块时挂在本项末尾。
  const tailForSegments = item.children.length === 0 ? tail : undefined;
  const childBlocks = item.children.map((child, index) =>
    renderNode(child, ctx, `${keyPrefix}-c${index}`, index === item.children.length - 1 ? tail : undefined),
  );
  return (
    <>
      {renderSegments(item.segments, ctx, `${keyPrefix}-s`, tailForSegments)}
      {childBlocks}
    </>
  );
}

function renderNode(node: RichNode, ctx: TimeLinkCtx, key: string, tail?: ReactNode): ReactNode {
  switch (node.kind) {
    case "paragraph":
      return <p key={key}>{renderSegments(node.segments, ctx, `${key}-p`, tail)}</p>;
    case "list": {
      const Tag = node.ordered ? "ol" : "ul";
      return (
        <Tag key={key}>
          {node.items.map((item, index) => (
            <li key={`${key}-i${index}`}>
              {renderListItem(item, ctx, `${key}-i${index}`, index === node.items.length - 1 ? tail : undefined)}
            </li>
          ))}
        </Tag>
      );
    }
    case "table": {
      // 表格流中不挂尾：光标插进表结构会破坏滚动容器语义。
      const numericAttr = (column: number): { "data-num"?: "true" } =>
        node.numericCols[column] ? { "data-num": "true" } : {};
      return (
        <div className="task7-rich-table-scroll" key={key}>
          <table>
            {node.header ? (
              <thead>
                <tr>
                  {node.header.map((cell, c) => (
                    <th {...numericAttr(c)} key={c} scope="col">{renderSegments(cell, ctx, `${key}-h${c}`)}</th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              {node.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td {...numericAttr(c)} key={c}>{renderSegments(cell, ctx, `${key}-r${r}c${c}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }
}

/**
 * Coach 消息受限富渲染入口（frontend-parity 批 7，digests §10）：
 * 白名单归一化后的文本经 lib/rich-text 解析为节点树在此落成 React 结构。
 * 全程无 dangerouslySetInnerHTML；未知形状（含任意 HTML）由 React 转义为
 * 惰性文本。流式与最终答案共用同一路径，未闭合结构按已收到部分渲染。
 */
export function CoachMessageText({
  text,
  analysisRef,
  onOpenVideo,
  tail,
}: {
  text: string;
  analysisRef?: string | null;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
  /** 流式光标等行内尾巴：插入最后一个段落/列表项的续写位。 */
  tail?: ReactNode;
}): ReactNode {
  const ctx: TimeLinkCtx = { analysisRef, onOpenVideo };
  const blocks = parseRichText(text);
  return (
    <div className="task7-rich">
      {blocks.map((node, index) => renderNode(node, ctx, `b${index}`, index === blocks.length - 1 ? tail : undefined))}
    </div>
  );
}
