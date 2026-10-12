import assert from "node:assert/strict";
import { test } from "node:test";

import {
  TRIAL_WAIT_POLL_INTERVAL_MS,
  TRIAL_WAIT_TIMEOUT_MS,
  nextTrialWaitDelay,
  trialWaitArrived,
  trialWaitVanished,
} from "./trial-provisioning-wait";
import type { MemberMe } from "./types";

function me(overrides: Partial<MemberMe> = {}): MemberMe {
  return {
    user: { id: "u1", email: "u***@gmail.com", name: null },
    member: false,
    plan: null,
    status: "none",
    cancel_at_period_end: false,
    period_start: null,
    period_end: null,
    dunning: false,
    pools: {
      sub: null,
      boost: null,
    },
    current_pool: null,
    boost_buyable: false,
    server_time: "2026-10-11T00:00:00.000Z",
    ...overrides,
  };
}

// ── 到达判定：不依赖 trial.provisioning（fail-open 主路径）────────────────

test("trialWaitArrived: 任一池非空或 member=true 即到账", () => {
  assert.equal(trialWaitArrived(me({ pools: { sub: { remaining: 300_000, grant: 300_000, pct: 100 }, boost: null } })), true);
  assert.equal(trialWaitArrived(me({ pools: { sub: null, boost: { remaining: 10, grant: 100, pct: 10 } } })), true);
  assert.equal(trialWaitArrived(me({ member: true })), true);
  // 全空（在途窗口的 /me 形态）：未到达。
  assert.equal(trialWaitArrived(me()), false);
  // 拉取失败/未登录（null）：未到达，继续等。
  assert.equal(trialWaitArrived(null), false);
});

// ── 提前终止：只在字段齐全且明确 failed 时触发（缺省 fail-open）───────────

test("trialWaitVanished: active+非在途+无池无会员 → failed 提前终止", () => {
  assert.equal(
    trialWaitVanished(me({ trial: { active: true, analyses_remaining: 3, questions_remaining: 3, verified_at: null, provisioning: false } })),
    true,
  );
});

test("trialWaitVanished: 字段缺省/形态不符一律不提前终止（fail-open）", () => {
  // provisioning 缺省（老 Worker 未发新字段）→ 未知，等满超时。
  assert.equal(
    trialWaitVanished(me({ trial: { active: true, analyses_remaining: 3, questions_remaining: 3, verified_at: null } })),
    false,
  );
  // provisioning=true（仍在途）→ 不终止。
  assert.equal(
    trialWaitVanished(me({ trial: { active: true, analyses_remaining: 3, questions_remaining: 3, verified_at: null, provisioning: true } })),
    false,
  );
  // active=false（无试用态）→ 不适用本判定。
  assert.equal(
    trialWaitVanished(me({ trial: { active: false, analyses_remaining: 0, questions_remaining: 0, verified_at: null, provisioning: false } })),
    false,
  );
  // trial 缺席 / me 为 null → 不终止。
  assert.equal(trialWaitVanished(me()), false);
  assert.equal(trialWaitVanished(null), false);
});

// ── 轮询计划：5s 步进、墙钟 120s 封顶 ─────────────────────────────────────

test("nextTrialWaitDelay: 5s 步进，超时前收尾步进，到点停止", () => {
  assert.equal(TRIAL_WAIT_POLL_INTERVAL_MS, 5_000);
  assert.equal(TRIAL_WAIT_TIMEOUT_MS, 120_000);
  assert.equal(nextTrialWaitDelay(0), 5_000);
  assert.equal(nextTrialWaitDelay(119_000), 1_000);
  assert.equal(nextTrialWaitDelay(120_000), null);
  assert.equal(nextTrialWaitDelay(130_000), null);
});
