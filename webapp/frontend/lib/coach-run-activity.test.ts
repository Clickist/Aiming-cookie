import assert from "node:assert/strict";
import { test } from "node:test";

import {
  getCoachRunActive,
  setCoachRunActive,
  subscribeCoachRunActive,
} from "./coach-run-activity";

// 模块级单例的用例各自还原初始值，互不泄漏（node --test 同进程顺序执行）。

test("setCoachRunActive stores and toggles the active flag", () => {
  const original = getCoachRunActive();
  try {
    setCoachRunActive(false);
    assert.equal(getCoachRunActive(), false);
    setCoachRunActive(true);
    assert.equal(getCoachRunActive(), true);
    setCoachRunActive(false);
    assert.equal(getCoachRunActive(), false);
  } finally {
    setCoachRunActive(original);
  }
});

test("subscribeCoachRunActive notifies only on real change and unsubscribe stops it", () => {
  const original = getCoachRunActive();
  try {
    setCoachRunActive(false);
    const seen: boolean[] = [];
    const unsubscribe = subscribeCoachRunActive(() => seen.push(getCoachRunActive()));
    setCoachRunActive(false); // 同值幂等：不广播。
    assert.deepEqual(seen, []);
    setCoachRunActive(true);
    setCoachRunActive(true);
    assert.deepEqual(seen, [true]);
    unsubscribe();
    setCoachRunActive(false); // 已退订：不再收到。
    assert.deepEqual(seen, [true]);
    assert.equal(getCoachRunActive(), false);
  } finally {
    setCoachRunActive(original);
  }
});
