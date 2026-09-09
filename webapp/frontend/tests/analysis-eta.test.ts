import assert from "node:assert/strict";
import { test } from "node:test";

import { computeAnalysisEtaSeconds } from "@/lib/contracts";
import type { SessionListItem } from "@/lib/types";

type EtaSample = Pick<SessionListItem, "status" | "created_at" | "started_at" | "finished_at" | "analysis_type">;

function session(overrides: Partial<EtaSample> = {}): EtaSample {
  // 新鲜度门槛（14 天）内 的相对日期：昨天。
  const created = new Date(Date.now() - 24 * 3600_000);
  return {
    status: "done",
    created_at: created.toISOString(),
    finished_at: new Date(created.getTime() + 40_000).toISOString(),
    analysis_type: "static_clicking",
    ...overrides,
  };
}

test("computeAnalysisEtaSeconds uses started_at when available", () => {
  // 排队 8 小时后执行 30 秒的分析：ETA 应基于 30 秒，而不是 8 小时。
  const started = new Date(Date.now() - 8 * 3600_000);
  const created = new Date(started.getTime() - 8 * 3600_000);
  const eta = computeAnalysisEtaSeconds([
    session({
      created_at: created.toISOString(),
      started_at: started.toISOString(),
      finished_at: new Date(started.getTime() + 30_000).toISOString(),
    }),
  ]);
  assert.equal(eta, 30);
});

test("computeAnalysisEtaSeconds falls back to created_at without started_at", () => {
  const eta = computeAnalysisEtaSeconds([session()]);
  // 40 秒样本向上取整到 5 秒档。
  assert.equal(eta, 40);
});

test("computeAnalysisEtaSeconds drops queue-distorted and non-done samples", () => {
  // 旧会话无 started_at 且排队 8 小时：样本失真，须过滤；running 会话不计。
  const created = new Date(Date.now() - 16 * 3600_000);
  const eta = computeAnalysisEtaSeconds([
    session({
      created_at: created.toISOString(),
      finished_at: new Date(created.getTime() + 8 * 3600_000).toISOString(),
    }),
    session({ status: "running", finished_at: new Date().toISOString() }),
    session(),
  ]);
  assert.equal(eta, 40);
});

test("computeAnalysisEtaSeconds returns null without usable samples", () => {
  assert.equal(computeAnalysisEtaSeconds([]), null);
  assert.equal(
    computeAnalysisEtaSeconds([session({ finished_at: undefined })]),
    null,
  );
});

test("computeAnalysisEtaSeconds takes the median and rounds up to 5 seconds", () => {
  const mk = (seconds: number) => session({
    created_at: new Date(Date.now() - 3600_000).toISOString(),
    finished_at: new Date(Date.now() - 3600_000 + seconds * 1000).toISOString(),
  });
  const eta = computeAnalysisEtaSeconds([mk(20), mk(42), mk(300)]);
  // 中位数 42 → 45 秒档。
  assert.equal(eta, 45);
});

test("computeAnalysisEtaSeconds buckets by analysis_type when enough same-type samples exist (点点 09-08)", () => {
  const hourAgo = new Date(Date.now() - 3600_000).toISOString();
  const mk = (seconds: number, analysis_type: string) => session({
    created_at: hourAgo,
    finished_at: new Date(Date.parse(hourAgo) + seconds * 1000).toISOString(),
    analysis_type,
  });
  // 点击类样本 3 条（中位 15s），甩枪类样本 3 条（中位 130s）：
  // 当前分析是甩枪 → 用甩枪桶，而不是被点击类拉低的全局中位数。
  const sessions = [
    mk(12, "dynamic_clicking"), mk(14, "static_clicking"), mk(15, "static_clicking"), mk(18, "static_clicking"),
    mk(120, "flicking"), mk(130, "flicking"), mk(140, "flicking"),
  ];
  const flickingEta = computeAnalysisEtaSeconds(sessions, { currentAnalysisType: "flicking" });
  assert.equal(flickingEta, 130);
  const clickingEta = computeAnalysisEtaSeconds(sessions, { currentAnalysisType: "static_clicking" });
  assert.equal(clickingEta, 15);
  // 同类型样本不足（<3）时回退全局中位数。
  const fallback = computeAnalysisEtaSeconds([
    mk(12, "dynamic_clicking"), mk(15, "static_clicking"), mk(18, "static_clicking"), mk(130, "flicking"),
  ], { currentAnalysisType: "flicking" });
  // 全局中位数 17 → 20 秒档。
  assert.equal(fallback, 20);
});

test("computeAnalysisEtaSeconds ignores samples older than the freshness window", () => {
  // 15 天前的样本不再支撑预估：宁可显示空也不显示误导数字。
  const stale = new Date(Date.now() - 15 * 24 * 3600_000);
  const eta = computeAnalysisEtaSeconds([
    session({
      created_at: stale.toISOString(),
      finished_at: new Date(stale.getTime() + 40_000).toISOString(),
    }),
  ]);
  assert.equal(eta, null);
  // 新鲜样本照常参与。
  const mixed = computeAnalysisEtaSeconds([
    session({
      created_at: stale.toISOString(),
      finished_at: new Date(stale.getTime() + 40_000).toISOString(),
    }),
    session(),
  ]);
  assert.equal(mixed, 40);
});
