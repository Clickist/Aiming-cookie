import assert from "node:assert/strict";
import { test } from "node:test";

import { coachGreeting, coachHomeChips } from "./coach-home";

// 空对话首页拍板（点点 0910）：问候不带称呼、chips 一行三颗且只填入不直发。
// 0910 二轮拍板：问候升级为完整引导句，原「能力提示」行删除。

test("问候语按时段分桶给出完整引导句，且任何时刻都不带称呼", () => {
  // 本地时间构造，避开时区；10 号 → 10 % 2 = 0 取池内第一句，11 号取第二句。
  assert.equal(coachGreeting(new Date(2026, 8, 10, 7, 30)), "早上好，今天想练什么？");
  assert.equal(coachGreeting(new Date(2026, 8, 11, 7, 30)), "早上好，开练前聊聊昨天的问题？");
  assert.equal(coachGreeting(new Date(2026, 8, 10, 13, 0)), "中午好，有什么想让我看的？");
  assert.equal(coachGreeting(new Date(2026, 8, 10, 15, 30)), "下午好，今天练得怎么样？");
  assert.equal(coachGreeting(new Date(2026, 8, 10, 21, 0)), "晚上好，练过了吗？要我看看？");
  assert.equal(coachGreeting(new Date(2026, 8, 10, 2, 0)), "夜深了，还没休息？");
  for (let hour = 0; hour < 24; hour += 1) {
    const text = coachGreeting(new Date(2026, 8, 10, hour, 0));
    assert.ok(!text.includes("点点"), `问候不得携带称呼：${text}`);
    assert.ok(text.length > 0 && text.length <= 20, `问候应为短句：${text}`);
    // 完整引导句：以问句或陈述收尾，不再出现光秃秃的「早上好」式单词问候。
    assert.ok(text.includes("，"), `问候应含引导半句：${text}`);
  }
});

test("建议 chips 恰好三颗（一行放完），只给草稿不给直发行为", () => {
  const chips = coachHomeChips();
  assert.equal(chips.length, 3);
  const ids = new Set(chips.map((chip) => chip.id));
  assert.equal(ids.size, 3);
  for (const chip of chips) {
    assert.ok(chip.label.length > 0 && chip.label.length <= 12, `标签应短：${chip.label}`);
    assert.ok(chip.prompt.length > 0 && chip.prompt.length <= 100);
    // 当前未接分析 id：prompt 是自然语言请求，不内嵌 analysis:N token。
    assert.ok(!chip.prompt.includes("analysis:"));
  }
});
