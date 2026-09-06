import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DISCUSSION_BAR_MAX_PINNED,
  discussionChipLabel,
  groupDiscussionChips,
  type DiscussionChip,
} from "./discussion-bar";

// 「本次讨论」挂载条平铺/溢出分组（0905 拍板）：已完成 chip 只平铺前 3 个，
// 余量折叠进下拉；文案与既有 chip 完全一致；顺序语义不变。

function chip(id: number): DiscussionChip {
  return { id, label: `场景${id}` };
}

test("discussion bar pins the first three finished chips in array order", () => {
  const { pinned, overflow } = groupDiscussionChips([chip(1), chip(2), chip(3), chip(4), chip(5)]);
  assert.deepEqual(pinned.map((entry) => entry.id), [1, 2, 3]);
  // 溢出组按原顺序进下拉菜单。
  assert.deepEqual(overflow.map((entry) => entry.id), [4, 5]);
});

test("discussion bar skips the overflow toggle when finished chips fit the flat row", () => {
  // 恰好 3 个：全部平铺，溢出为空（调用方不渲染箭头）。
  const exact = groupDiscussionChips([chip(1), chip(2), chip(3)]);
  assert.equal(exact.pinned.length, DISCUSSION_BAR_MAX_PINNED);
  assert.deepEqual(exact.overflow, []);
  // 空列表同样安全。
  const empty = groupDiscussionChips([]);
  assert.deepEqual(empty.pinned, []);
  assert.deepEqual(empty.overflow, []);
  // 上限可覆盖（当前调用方不用，仅为纯函数完整性）。
  const wider = groupDiscussionChips([chip(1), chip(2)], 5);
  assert.equal(wider.pinned.length, 2);
  assert.deepEqual(wider.overflow, []);
});

test("discussion chip label matches the original chip copy", () => {
  assert.equal(discussionChipLabel(7, { scenario: "Gridshot", runId: 12 }), "Gridshot · run 12");
  // 无 run 号：只有场景名。
  assert.equal(discussionChipLabel(7, { scenario: "Gridshot", runId: null }), "Gridshot");
  // 场景名缺失回落「分析 #id」（与原 chip 的 ?? 回退一致）。
  assert.equal(discussionChipLabel(7, { scenario: null, runId: 3 }), "分析 #7 · run 3");
  assert.equal(discussionChipLabel(7, undefined), "分析 #7");
});
