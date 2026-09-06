/**
 * 「本次讨论」挂载条的平铺/溢出分组（0905 拍板）。
 *
 * 讨论过的训练越多条越长、越挤占对话区：已完成 chip 只平铺前
 * DISCUSSION_BAR_MAX_PINNED 个，余量收进行尾 ▾ 按钮的下拉菜单里。进行中的
 * pending chip 不参与折叠（通常 0-2 个），由调用方永远平铺渲染。纯逻辑、
 * 无 React 依赖；顺序语义与输入数组一致（前 3 个平铺，其余按原顺序进菜单）。
 */

/** 已完成 chip 的平铺上限；超出部分折叠进下拉菜单。 */
export const DISCUSSION_BAR_MAX_PINNED = 3;

/** 讨论条上一个已完成分析的展示数据：id 供打开视频，label 为 chip 文案。 */
export interface DiscussionChip {
  id: number;
  label: string;
}

/**
 * chip 文案与既有 chip 完全一致：场景名缺失回落「分析 #id」，run 号存在时
 * 以「 · run N」缀在场景名后（CoachPanel 讨论条的原拼装规则原样收口）。
 */
export function discussionChipLabel(
  id: number,
  info: { scenario: string | null; runId: number | null } | undefined,
): string {
  return `${info?.scenario ?? `分析 #${id}`}${info?.runId != null ? ` · run ${info.runId}` : ""}`;
}

/**
 * 把已完成 chip 按数组顺序分成平铺组（前 maxPinned 个）与溢出组（其余）。
 * 已完成 ≤ 上限时溢出组为空，调用方不渲染折叠按钮，行为与折叠前一致。
 */
export function groupDiscussionChips(
  chips: readonly DiscussionChip[],
  maxPinned: number = DISCUSSION_BAR_MAX_PINNED,
): { pinned: DiscussionChip[]; overflow: DiscussionChip[] } {
  return {
    pinned: chips.slice(0, maxPinned),
    overflow: chips.slice(maxPinned),
  };
}
