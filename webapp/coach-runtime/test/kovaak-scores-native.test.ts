import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { executeNativeKovaakScore } from "../src/kovaak-scores-native.ts";

// ── Fixtures ───────────────────────────────────────────────────────────

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const CATALOG = JSON.parse(
  readFileSync(resolve(REPO_ROOT, "knowledge", "benchmarks", "viscose-s2.v1.json"), "utf8"),
) as {
  benchmark_ids: Record<string, number>;
  pairs: Array<Record<string, { scenario_name: string }>>;
};

const DIFFICULTIES = ["easier", "medium"] as const;
const BENCHMARK_ID_TO_DIFFICULTY = new Map(
  DIFFICULTIES.map((difficulty) => [CATALOG.benchmark_ids[difficulty], difficulty]),
);

/** Per (difficulty, scenario) expected normalized values, keyed for lookup. */
type Expectation = { index: number; rawScore: number; rankMaxes: number[] };

function buildExpectations(): Map<string, Expectation> {
  const map = new Map<string, Expectation>();
  for (const difficulty of DIFFICULTIES) {
    CATALOG.pairs.forEach((pair, index) => {
      const name = pair[difficulty].scenario_name;
      // score is ×100 of the displayed value; rank_maxes is already displayed-scale.
      const rawScore = 100 * (index + 1);
      const rankMaxes = Array.from({ length: 9 }, (_, rank) => (index + 1) * 1000 + rank);
      map.set(`${difficulty}:${name}`, { index, rawScore, rankMaxes });
    });
  }
  return map;
}

const EXPECTATIONS = buildExpectations();

function benchmarkPayload(difficulty: (typeof DIFFICULTIES)[number]): Record<string, unknown> {
  const scenarios: Record<string, unknown> = {};
  CATALOG.pairs.forEach((pair, index) => {
    const name = pair[difficulty].scenario_name;
    scenarios[name] = {
      score: 100 * (index + 1),
      scenario_rank: 0,
      rank_maxes: Array.from({ length: 9 }, (_, rank) => (index + 1) * 1000 + rank),
    };
  });
  return { benchmark_progress: 100, overall_rank: 0, categories: { fixture: { scenarios } } };
}

const STEAM_ID = "76561199033719938";

async function lookupWithStubbedFetch(): Promise<Record<string, unknown>> {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL) => {
    const url = new URL(String(input));
    assert.ok(url.pathname.endsWith("/benchmarks/player-progress-rank-benchmark"));
    assert.equal(url.searchParams.get("max"), "100");
    const difficulty = BENCHMARK_ID_TO_DIFFICULTY.get(Number(url.searchParams.get("benchmarkId")));
    assert.ok(difficulty, "unknown benchmark id");
    return new Response(JSON.stringify(benchmarkPayload(difficulty)), { status: 200 });
  }) as typeof fetch;
  try {
    const result = await executeNativeKovaakScore(
      "kovaak_scores.lookup",
      { profile_ref: STEAM_ID },
      "owner",
    );
    assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
    return result.result as Record<string, unknown>;
  } finally {
    globalThis.fetch = originalFetch;
  }
}

// ── Tests ──────────────────────────────────────────────────────────────

test("rank_maxes are passed through at the displayed scale, not divided by 100", async () => {
  const summary = await lookupWithStubbedFetch();
  const items = summary.items as Array<Record<string, unknown>>;
  assert.ok(items.length > 0, "summary must contain items");

  for (const item of items) {
    const key = `${item.stage}:${item.name}`;
    const expected = EXPECTATIONS.get(key);
    assert.ok(expected, `unexpected item ${key}`);
    // score: raw/100 (the raw payload used 100×(index+1)).
    assert.equal(item.score, expected.index + 1, `score unit drift for ${key}`);
    // rank_maxes: exactly the upstream array — a /100 would shrink each value 100×.
    assert.deepEqual(item.rank_maxes, expected.rankMaxes, `rank_maxes unit drift for ${key}`);
  }
});

test("rank_maxes keep their relation to score on the same scale", async () => {
  const summary = await lookupWithStubbedFetch();
  const items = summary.items as Array<Record<string, unknown>>;
  const sample = items[0];
  const rankMaxes = sample.rank_maxes as number[];
  assert.equal(rankMaxes.length, 9);
  // Same scale check: dividing them by 100 would put every threshold far below
  // the score, which is what a wrong unit conversion looks like.
  assert.ok(rankMaxes[0] > (sample.score as number) / 1000, "rank_maxes look unit-divided");
  for (let index = 1; index < rankMaxes.length; index++) {
    assert.ok(rankMaxes[index] > rankMaxes[index - 1], "rank_maxes must be ascending");
  }
});

test("a payload without rank_maxes yields an empty threshold list", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL) => {
    const url = new URL(String(input));
    const difficulty = BENCHMARK_ID_TO_DIFFICULTY.get(Number(url.searchParams.get("benchmarkId")));
    const payload = benchmarkPayload(difficulty as (typeof DIFFICULTIES)[number]);
    for (const category of Object.values(payload.categories as Record<string, { scenarios: Record<string, Record<string, unknown>> }>)) {
      for (const scenario of Object.values(category.scenarios)) delete scenario.rank_maxes;
    }
    return new Response(JSON.stringify(payload), { status: 200 });
  }) as typeof fetch;
  try {
    const result = await executeNativeKovaakScore(
      "kovaak_scores.lookup",
      { profile_ref: STEAM_ID },
      "owner",
    );
    assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
    const items = (result.result as Record<string, unknown>).items as Array<Record<string, unknown>>;
    for (const item of items) {
      // A missing/odd field degrades to [] rather than dropping the whole item.
      assert.deepEqual(item.rank_maxes, []);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a malformed rank_maxes entry degrades to an empty list without failing the lookup", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL) => {
    const url = new URL(String(input));
    const difficulty = BENCHMARK_ID_TO_DIFFICULTY.get(Number(url.searchParams.get("benchmarkId")));
    const payload = benchmarkPayload(difficulty as (typeof DIFFICULTIES)[number]);
    const categories = payload.categories as Record<string, { scenarios: Record<string, Record<string, unknown>> }>;
    const firstCategory = Object.values(categories)[0];
    const firstName = Object.keys(firstCategory.scenarios)[0];
    firstCategory.scenarios[firstName].rank_maxes = [1, "two", 3];
    return new Response(JSON.stringify(payload), { status: 200 });
  }) as typeof fetch;
  try {
    const result = await executeNativeKovaakScore(
      "kovaak_scores.lookup",
      { profile_ref: STEAM_ID },
      "owner",
    );
    assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
    const items = (result.result as Record<string, unknown>).items as Array<Record<string, unknown>>;
    assert.ok(items.some((item) => JSON.stringify(item.rank_maxes) === "[]"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
