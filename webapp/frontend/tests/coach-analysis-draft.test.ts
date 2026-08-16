import assert from "node:assert/strict";
import { test } from "node:test";

import { buildCoachAnalysisDraft, formatHistoryDate } from "../lib/contracts";

function run(overrides: Partial<Parameters<typeof buildCoachAnalysisDraft>[0]["runs"][number]> = {}) {
  return {
    run_ref: "run:1",
    scenario: "KovaaK's Smooth Tracking",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

test("draft for a single run quotes scenario, relative time and the run ref", () => {
  const draft = buildCoachAnalysisDraft({ runs: [run()], analyses: [] });
  assert.match(draft, /^请分析我这局训练：/);
  assert.match(draft, /KovaaK's Smooth Tracking，今天 \d{2}:\d{2}（run:1）/);
  assert.match(draft, /，讲讲主要问题和改进方向。$/);
});

test("draft for multiple runs switches to the plural lead and joins with ；", () => {
  const draft = buildCoachAnalysisDraft({
    runs: [
      run({ run_ref: "run:1", scenario: "场景A" }),
      run({ run_ref: "run:2", scenario: "场景B" }),
    ],
    analyses: [],
  });
  assert.match(draft, /^请分析我这几局训练：场景A，今天 \d{2}:\d{2}（run:1）；场景B，今天 \d{2}:\d{2}（run:2），讲讲主要问题和改进方向。$/);
});

test("draft for finished analyses asks the Coach to read them by analysis ref", () => {
  const single = buildCoachAnalysisDraft({
    runs: [],
    analyses: [run({ run_ref: "analysis:3", scenario: "场景C" })],
  });
  assert.match(single, /^请结合这份分析：场景C，今天 \d{2}:\d{2}（analysis:3），讲讲主要问题和改进方向。$/);

  const plural = buildCoachAnalysisDraft({
    runs: [],
    analyses: [
      run({ run_ref: "analysis:3", scenario: "场景C" }),
      run({ run_ref: "analysis:4", scenario: "场景D" }),
    ],
  });
  assert.match(plural, /^请结合这几份分析：场景C，今天 \d{2}:\d{2}（analysis:3）；场景D，今天 \d{2}:\d{2}（analysis:4），讲讲主要问题和改进方向。$/);
});

test("draft mixes pending runs and finished analyses in one request", () => {
  const draft = buildCoachAnalysisDraft({
    runs: [run({ run_ref: "run:7", scenario: "场景A" })],
    analyses: [run({ run_ref: "analysis:3", scenario: "场景C" })],
  });
  assert.match(draft, /^请分析我这局训练：场景A，今天 \d{2}:\d{2}（run:7），请结合这份分析：场景C，今天 \d{2}:\d{2}（analysis:3），讲讲主要问题和改进方向。$/);
});

test("long scenario names are truncated but refs stay complete", () => {
  const longScenario = "KovaaK's_tile_flick_strafe Tracking challenge with a very long scenario name";
  const draft = buildCoachAnalysisDraft({ runs: [run({ run_ref: "run:9", scenario: longScenario })], analyses: [] });
  assert.ok(!draft.includes(longScenario));
  assert.match(draft, /（run:9）/);
});

test("five max-length selections degrade to time+ref form within the 240-char intent limit", () => {
  // 长日期形式（如 12月31日 14:32，11 字符）+ 24 字符截断场景名才可能把 5 条拼过 240，
  // 触发「去掉场景名只留时间+ref」的退化分支。找一个两位月日的过去日期，
  // 保证落在 M月d日 长形式且绝不是今天/昨天。
  let longDate = new Date();
  longDate.setDate(longDate.getDate() - 400);
  while (!(longDate.getMonth() >= 9 && longDate.getDate() >= 10)) {
    longDate.setDate(longDate.getDate() - 1);
  }
  const longDateIso = longDate.toISOString();
  const draft = buildCoachAnalysisDraft({
    runs: Array.from({ length: 3 }, (_, i) => run({
      run_ref: `run:${i + 1}`,
      scenario: `超长场景名测试超长场景名测试超长场景名测试超长场景名测试超长场景名${i}`,
      created_at: longDateIso,
    })),
    analyses: Array.from({ length: 2 }, (_, i) => run({
      run_ref: `analysis:${i + 1}`,
      scenario: `超长场景名测试超长场景名测试超长场景名测试超长场景名测试超长场景名A${i}`,
      created_at: longDateIso,
    })),
  });
  assert.ok(draft.length <= 240, `draft length ${draft.length} exceeds the intent limit`);
  assert.ok(!draft.includes("超长场景名测试"), "expected the degraded time+ref form without scenario names");
  for (const ref of ["run:1", "run:2", "run:3", "analysis:1", "analysis:2"]) {
    assert.match(draft, new RegExp(`（${ref}）`));
  }
});

test("missing fields fall back without dropping the ref", () => {
  const draft = buildCoachAnalysisDraft({ runs: [run({ scenario: null, created_at: null })], analyses: [] });
  assert.match(draft, /时间未知（run:1）/);
  assert.equal(buildCoachAnalysisDraft({ runs: [], analyses: [] }), "");
});

test("formatHistoryDate renders relative buckets and passes through unknown input", () => {
  assert.equal(formatHistoryDate(null), "时间未知");
  assert.equal(formatHistoryDate(undefined), "时间未知");
  assert.equal(formatHistoryDate("not-a-date"), "not-a-date");
  const today = new Date();
  assert.match(formatHistoryDate(today.toISOString()), /^今天 \d{2}:\d{2}$/);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  assert.match(formatHistoryDate(yesterday.toISOString()), /^昨天 \d{2}:\d{2}$/);
});
