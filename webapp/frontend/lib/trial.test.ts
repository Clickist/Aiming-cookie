import assert from "node:assert/strict";
import { test } from "node:test";

import {
  TRIAL_PENDING_KEY,
  TRIAL_REPORTED_KEY,
  enqueuePendingTrialEvent,
  markTrialReported,
  parseTrialState,
  planTrialEvent,
  readPendingTrialEvents,
  readReportedTrialKeys,
  removePendingTrialEvent,
  shouldReportTrialEvent,
  trialRemainingFor,
  type PendingTrialEvent,
} from "./trial";

function fakeStorage(entries: Map<string, string> = new Map()): Storage {
  return {
    length: entries.size,
    clear: () => entries.clear(),
    getItem: (key: string) => (entries.has(key) ? (entries.get(key) as string) : null),
    key: () => null,
    removeItem: (key: string) => void entries.delete(key),
    setItem: (key: string, value: string) => void entries.set(key, value),
  } as Storage;
}

function brokenStorage(): Storage {
  return {
    length: 0,
    clear: () => {},
    getItem: () => null,
    key: () => null,
    removeItem: () => {},
    setItem: () => {
      throw new Error("storage disabled");
    },
  } as Storage;
}

const TRIAL = { active: true, analyses_remaining: 1, questions_remaining: 2, verified: false };
const ME = { user: { id: "u", email: "u@g", name: null }, trial: TRIAL };

test("试用态宽松解析：/me 的 trial 字段形状完整才进入试用模式", () => {
  const trial = parseTrialState(ME);
  assert.ok(trial);
  assert.equal(trial.active, true);
  assert.equal(trial.analysesRemaining, 1);
  assert.equal(trial.questionsRemaining, 2);
  assert.equal(trial.verified, false);
});

test("试用态宽松解析（fail-open）：字段缺失/形状不对一律视为无试用态", () => {
  // 跨端契约：/me 形状变化不得崩客户端，缺 trial = 无试用态，走现有路径。
  assert.equal(parseTrialState(null), null);
  assert.equal(parseTrialState(undefined), null);
  assert.equal(parseTrialState("nope"), null);
  assert.equal(parseTrialState({ user: {} }), null); // 无 trial 字段
  assert.equal(parseTrialState({ trial: "yes" }), null); // 非对象
  assert.equal(parseTrialState({ trial: {} }), null); // active 缺失
  assert.equal(parseTrialState({ trial: { active: false, analyses_remaining: 1, questions_remaining: 2 } }), null);
  assert.equal(parseTrialState({ trial: { active: true, questions_remaining: 2 } }), null); // 缺 analyses_remaining
  assert.equal(parseTrialState({ trial: { active: true, analyses_remaining: "1", questions_remaining: 2 } }), null); // 非数字
  assert.equal(parseTrialState({ trial: { active: true, analyses_remaining: -1, questions_remaining: 2 } }), null); // 负数
  assert.equal(parseTrialState({ trial: { active: true, analyses_remaining: 1.5, questions_remaining: 2 } })?.analysesRemaining, 1, "小数宽松收敛为整数");
});

test("试用态宽松解析：verified 缺失按「分析余量扣完」推导，显式布尔优先", () => {
  assert.equal(parseTrialState({ trial: { active: true, analyses_remaining: 0, questions_remaining: 2 } })?.verified, true);
  assert.equal(parseTrialState({ trial: { active: true, analyses_remaining: 1, questions_remaining: 2 } })?.verified, false);
  assert.equal(parseTrialState({ trial: { ...TRIAL, verified: true } })?.verified, true);
});

test("上报闸：仅登录试用态且对应 remaining>0 时放行", () => {
  const trial = parseTrialState(ME);
  assert.ok(trial);
  assert.equal(shouldReportTrialEvent("analysis_done", trial), true);
  assert.equal(shouldReportTrialEvent("question_answered", trial), true);
  assert.equal(shouldReportTrialEvent("analysis_done", null), false, "无试用态不过闸");
  assert.equal(shouldReportTrialEvent("analysis_done", { active: true, analysesRemaining: 0, questionsRemaining: 2, verified: true }), false);
  assert.equal(shouldReportTrialEvent("question_answered", { active: true, analysesRemaining: 1, questionsRemaining: 0, verified: false }), false);
  assert.equal(trialRemainingFor("analysis_done", trial), 1);
  assert.equal(trialRemainingFor("question_answered", trial), 2);
  assert.equal(trialRemainingFor("analysis_done", null), 0);
});

test("上报计划：闸关/已上报/可发 三态分明（并发双触发靠已报标记去重）", () => {
  const trial = parseTrialState(ME);
  assert.ok(trial);
  assert.equal(planTrialEvent("analysis_done", "analysis:42", trial, new Set()), "send");
  assert.equal(planTrialEvent("analysis_done", "analysis:42", trial, new Set(["analysis:42"])), "duplicate");
  assert.equal(planTrialEvent("analysis_done", "analysis:42", null, new Set()), "gate");
  assert.equal(planTrialEvent("analysis_done", "analysis:42", undefined, new Set()), "gate", "/me 未取到时闸视为关闭");
  assert.equal(planTrialEvent("question_answered", "coach-run:1", { active: true, analysesRemaining: 0, questionsRemaining: 0, verified: true }, new Set()), "gate", "两项耗尽后不再上报");
});

test("本地去重与补报队列：读写回路、坏数据按空集/空队列", () => {
  const store = fakeStorage();
  assert.deepEqual([...readReportedTrialKeys(store)], []);
  markTrialReported(store, "analysis:42");
  markTrialReported(store, "coach-run:1");
  assert.deepEqual([...readReportedTrialKeys(store)].sort(), ["analysis:42", "coach-run:1"]);

  const event: PendingTrialEvent = { type: "analysis_done", key: "analysis:42" };
  enqueuePendingTrialEvent(store, event);
  enqueuePendingTrialEvent(store, { type: "question_answered", key: "coach-run:1" });
  enqueuePendingTrialEvent(store, { type: "analysis_done", key: "analysis:42" }, );
  assert.deepEqual(readPendingTrialEvents(store), [event, { type: "question_answered", key: "coach-run:1" }], "同键重复入队会被吞");
  removePendingTrialEvent(store, event);
  assert.deepEqual(readPendingTrialEvents(store), [{ type: "question_answered", key: "coach-run:1" }]);

  const broken = brokenStorage();
  assert.deepEqual([...readReportedTrialKeys(broken)], []);
  assert.deepEqual(readPendingTrialEvents(broken), []);
  assert.doesNotThrow(() => markTrialReported(broken, "x"));
  assert.doesNotThrow(() => enqueuePendingTrialEvent(broken, event));
  assert.doesNotThrow(() => removePendingTrialEvent(broken, event));

  const corrupt = fakeStorage(new Map([
    [TRIAL_REPORTED_KEY, "{not json"],
    [TRIAL_PENDING_KEY, "{\"weird\": true}"],
  ]));
  assert.deepEqual([...readReportedTrialKeys(corrupt)], []);
  assert.deepEqual(readPendingTrialEvents(corrupt), []);
});
