"use client";

import { Fragment, type ReactNode } from "react";

import { isDesktopRuntime, openExternalUrl } from "@/lib/desktop";
import {
  parseRichText,
  parseTimeSegments,
  type RichInline,
  type RichItem,
  type RichNode,
  type TimeChip,
} from "@/lib/rich-text";

/** 区间 chip 点击跳到起点（brief P0.3：点击行为不变，跳转＋暂停）。 */
function chipTargetMs(chip: TimeChip): number {
  return chip.kind === "range" ? chip.startMs : chip.timeMs;
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
    // D7：显示层不再透出原文 @51.5s / @38.2-43.7s，渲染为时间码 chip；
    // task6-time-link 语义色与「跳转＋暂停」点击行为原样保留。
    const pieces = parseTimeSegments(segment.text).map((piece, pieceIndex) => {
      const pieceKey = `${keyPrefix}-${segIndex}-${pieceIndex}`;
      const chip = piece.chip;
      if (!chip) return <Fragment key={pieceKey}>{piece.text}</Fragment>;
      if (ctx.analysisRef && ctx.onOpenVideo) {
        return (
          <button
            className="task6-time-link"
            key={pieceKey}
            onClick={() => ctx.onOpenVideo?.(ctx.analysisRef as string, chipTargetMs(chip))}
            type="button"
          >
            {chip.label}
          </button>
        );
      }
      return <span className="task6-time-link task6-time-link--static" key={pieceKey}>{chip.label}</span>;
    });
    if (segment.bold) {
      out.push(
        <strong className="task7-rich-bold" key={`${keyPrefix}-${segIndex}`}>{pieces}</strong>,
      );
    } else if (segment.link) {
      // 受控命名链接（白名单域，解析层已把关）：主色下划线，新标签打开。
      const linkHref = segment.link;
      out.push(
        <a
          className="task7-link"
          key={`${keyPrefix}-${segIndex}`}
          href={linkHref}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => {
            // 桌面 WebView2 里 target=_blank 导航被拦（点了没反应，1.0.0
            // 内测实测）：拦截后经 opener 插件唤默认浏览器。
            if (isDesktopRuntime()) {
              event.preventDefault();
              void openExternalUrl(linkHref);
            }
          }}
        >
          {pieces}
        </a>,
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
    case "blockquote":
      return (
        <blockquote className="task7-rich-quote" key={key}>
          {node.children.map((child, index) => renderNode(child, ctx, `${key}-q${index}`))}
        </blockquote>
      );
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
