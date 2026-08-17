import assert from "node:assert/strict";
import { test } from "node:test";

import { computeAnalysisEtaSeconds } from "@/lib/contracts";
import type { SessionListItem } from "@/lib/types";

type EtaSample = Pick<SessionListItem, "status" | "created_at" | "started_at" | "finished_at">;

function session(overrides: Partial<EtaSample> = {}): EtaSample {
  const created = new Date("2026-08-18T10:00:00Z");
  return {
    status: "done",
    created_at: created.toISOString(),
    finished_at: new Date(created.getTime() + 40_000).toISOString(),
    ...overrides,
  };
}

test("computeAnalysisEtaSeconds uses started_at when available", () => {
  // 排队 8 小时后执行 30 秒的分析：ETA 应基于 30 秒，而不是 8 小时。
  const created = new Date("2026-08-17T22:00:00Z");
  const started = new Date("2026-08-18T06:00:00Z");
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
  const created = new Date("2026-08-17T22:00:00Z");
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
    finished_at: new Date(new Date("2026-08-18T10:00:00Z").getTime() + seconds * 1000).toISOString(),
  });
  const eta = computeAnalysisEtaSeconds([mk(20), mk(42), mk(300)]);
  // 中位数 42 → 45 秒档。
  assert.equal(eta, 45);
});
