import assert from "node:assert/strict";
import test from "node:test";

import { executeNativeKovaakLeaderboard } from "../src/kovaak-leaderboard-native.ts";

// ── Fixtures ───────────────────────────────────────────────────────────

type Entry = { steamId: string; score: number; rank: number };

function boardEntry({ steamId, score, rank }: Entry): Record<string, unknown> {
  return { steamId, score, rank, steamAccountName: `name-${rank}`, webappUsername: `name-${rank}` };
}

/** A descending board with `total` synthetic entries, 100 per page. */
function boardPage(total: number, page: number, max: number): Record<string, unknown> {
  const start = page * max;
  const data: Record<string, unknown>[] = [];
  for (let index = start; index < Math.min(start + max, total); index++) {
    data.push({ steamId: String(70000000000000000n + BigInt(index)), score: 20_000 - index, rank: index + 1 });
  }
  return { total, page, max, data };
}

function popularPayload(): Record<string, unknown> {
  return {
    page: 0,
    max: 20,
    total: 2,
    data: [
      {
        rank: 1,
        leaderboardId: 185342,
        scenarioName: "Smoothsphere Viscose Easier",
        scenario: { aimType: "Tracking" },
      },
      {
        rank: 2,
        leaderboardId: 184106,
        scenarioName: "Smoothsphere Viscose",
        scenario: { aimType: "Tracking" },
      },
    ],
  };
}

const STEAM_ID = "76561198012668923";
const PERSONA_HTML = '<html><span class="actual_persona_name">riler</span></html>';

type RouteHandlers = {
  leaderboard?: (page: number, usernameSearch: string | null) => Record<string, unknown> | Response;
  details?: () => Record<string, unknown> | Response;
  popular?: () => Record<string, unknown> | Response;
  accountNames?: () => Record<string, unknown>[];
  steamHtml?: string | Response;
};

/** Installs a fetch stub keyed by URL shape and returns the recorded calls. */
function stubFetch(handlers: RouteHandlers): string[] {
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL) => {
    const url = new URL(String(input));
    calls.push(url.toString());
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

    if (url.hostname === "steamcommunity.com") {
      const html = handlers.steamHtml ?? PERSONA_HTML;
      if (html instanceof Response) return html;
      return new Response(html, { status: 200, headers: { "Content-Type": "text/html" } });
    }
    if (url.pathname.endsWith("/leaderboard/scores/global")) {
      const page = Number(url.searchParams.get("page") ?? "0");
      const max = Number(url.searchParams.get("max") ?? "100");
      if (handlers.leaderboard) {
        const result = handlers.leaderboard(page, url.searchParams.get("usernameSearch"));
        if (result instanceof Response) return result;
        if (typeof result.total === "number" && result.data === undefined) {
          // Convenience: build a standard descending board of `total` entries,
          // optionally filtered to a single steamId.
          const full = boardPage(result.total, page, max);
          if (typeof result.searchSteamId === "string") {
            full.data = (full.data as Record<string, unknown>[]).filter(
              (entry) => entry.steamId === result.searchSteamId,
            );
          }
          return json(full);
        }
        return json(result);
      }
      return json(boardPage(1000, page, max));
    }
    if (url.pathname.endsWith("/scenario/details")) {
      return json(handlers.details?.() ?? { scenarioName: "Smoothsphere Viscose Easier", aimType: "Tracking" });
    }
    if (url.pathname.endsWith("/scenario/popular")) {
      return json(handlers.popular?.() ?? popularPayload());
    }
    if (url.pathname.endsWith("/leaderboard/global/search/account-names")) {
      return json(handlers.accountNames?.() ?? []);
    }
    throw new Error(`unexpected fetch: ${url}`);
  }) as typeof fetch;
  return calls;
}

async function withStubbedFetch<T>(handlers: RouteHandlers, run: (calls: string[]) => Promise<T>): Promise<T> {
  const originalFetch = globalThis.fetch;
  const calls = stubFetch(handlers);
  try {
    return await run(calls);
  } finally {
    globalThis.fetch = originalFetch;
  }
}

// ── Tests ──────────────────────────────────────────────────────────────

test("leaderboard lookup by score reports rank, percentile, median and top score", async () => {
  await withStubbedFetch({ leaderboard: () => ({ total: 37_353 }) }, async (calls) => {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
      score: 15_000,
    });
    assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
    const summary = result.result as Record<string, unknown>;
    assert.equal(summary.schema_version, "kovaak_leaderboard.v1");
    assert.equal(summary.leaderboard_id, 185342);
    assert.equal(summary.scenario_name, "Smoothsphere Viscose Easier");
    assert.equal(summary.aim_type, "Tracking");
    assert.equal(summary.total_entries, 37_353);
    assert.equal(summary.on_board, true);
    // Score 15000 corresponds to index 5000 → rank 5001.
    assert.equal(summary.rank, 5001);
    assert.equal(summary.percentile_top, Number(((5001 / 37_353) * 100).toFixed(3)));
    // Median sits at global index floor(total/2) on the descending board.
    assert.equal(summary.median_score, 20_000 - Math.floor(37_353 / 2));
    assert.equal(summary.top_score, 20_000);
    assert.deepEqual(summary.queried, { kind: "score", value: 15_000 });
    // The locator must not be exposed in the summary.
    assert.ok(!("profile_ref" in summary));
    assert.ok(!JSON.stringify(summary).includes(STEAM_ID));

    const detailCalls = calls.filter((call) => call.includes("/scenario/details"));
    assert.equal(detailCalls.length, 1);
    assert.ok(detailCalls[0].includes("leaderboardId=185342"));
  });
});

test("score locator uses logarithmic page probes, not a linear scan", async () => {
  await withStubbedFetch({ leaderboard: () => ({ total: 37_353 }) }, async (calls) => {
    await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
      score: 12_000,
    });
    const boardCalls = calls.filter((call) => call.includes("/leaderboard/scores/global"));
    // 1 top page + 1 median page + ~log2(374) probes; a linear scan would be hundreds.
    assert.ok(boardCalls.length <= 16, `expected bounded page probes, got ${boardCalls.length}`);
  });
});

test("leaderboard lookup without a locator returns board stats only", async () => {
  await withStubbedFetch({ leaderboard: () => ({ total: 500 }) }, async () => {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
    });
    assert.equal(result.status, "succeeded");
    const summary = result.result as Record<string, unknown>;
    assert.equal(summary.on_board, false);
    assert.equal(summary.rank, null);
    assert.equal(summary.percentile_top, null);
    assert.equal(summary.total_entries, 500);
    assert.equal(summary.top_score, 20_000);
    assert.equal(summary.median_score, 20_000 - 250);
    assert.deepEqual(summary.queried, { kind: "none", value: null });
  });
});

test("a profile_ref not on the board reports on_board=false instead of a rank", async () => {
  await withStubbedFetch(
    {
      leaderboard: (page) => {
        // The searched board returns a row for some other player only.
        if (page === 0) {
          return { total: 42, data: [boardEntry({ steamId: "76561190000000001", score: 9000, rank: 1 })] };
        }
        return { total: 42 };
      },
      accountNames: () => [{ steamId: "76561190000000009", username: "someone-else" }],
    },
    async () => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        leaderboard_id: 185342,
        profile_ref: STEAM_ID,
      });
      assert.equal(result.status, "succeeded");
      const summary = result.result as Record<string, unknown>;
      assert.equal(summary.on_board, false);
      assert.equal(summary.rank, null);
      assert.equal(summary.percentile_top, null);
      assert.deepEqual(summary.queried, { kind: "steam_id_suffix", value: STEAM_ID.slice(-4) });
      // Only the last 4 digits are echoed back.
      assert.ok(!JSON.stringify(summary).includes(STEAM_ID));
    },
  );
});

test("profile_ref locates the official rank through the steam persona name", async () => {
  await withStubbedFetch(
    {
      leaderboard: (page) =>
        page === 0
          ? { total: 1000, data: [boardEntry({ steamId: STEAM_ID, score: 16_500, rank: 143 })], searchSteamId: STEAM_ID }
          : { total: 1000 },
    },
    async (calls) => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        leaderboard_id: 185342,
        profile_ref: `https://steamcommunity.com/profiles/${STEAM_ID}`,
      });
      assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
      const summary = result.result as Record<string, unknown>;
      assert.equal(summary.rank, 143);
      assert.equal(summary.percentile_top, Number(((143 / 1000) * 100).toFixed(3)));
      assert.equal(summary.on_board, true);
      const searchUrl = calls.find((call) => call.includes("usernameSearch="));
      assert.ok(searchUrl, "board search must carry the queried name");
      assert.ok(searchUrl.includes("usernameSearch=riler"));
    },
  );
});

test("profile_ref falls back to the KovaaKs username when the persona search misses", async () => {
  const searches: Array<string | null> = [];
  await withStubbedFetch(
    {
      // Only the KovaaKs webapp username ("webapp-name") matches on this board;
      // the Steam persona name ("riler") is a dead end.
      leaderboard: (page, usernameSearch) => {
        searches.push(usernameSearch);
        if (page !== 0) return { total: 1000 };
        if (usernameSearch === "webapp-name") {
          return { total: 1, data: [boardEntry({ steamId: STEAM_ID, score: 15_000, rank: 4563 })] };
        }
        return { total: 0, data: [] };
      },
      accountNames: () => [{ steamId: STEAM_ID, username: "webapp-name" }],
    },
    async () => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        leaderboard_id: 185342,
        profile_ref: STEAM_ID,
      });
      assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
      const summary = result.result as Record<string, unknown>;
      assert.equal(summary.rank, 4563);
      assert.equal(summary.on_board, true);
      // The stats probe carries no search, then the persona search, then the username search.
      assert.deepEqual(searches, [null, "riler", "webapp-name"]);
    },
  );
});

test("scenario_name resolves through scenario/popular and prefers an exact match", async () => {
  await withStubbedFetch(
    {
      popular: () => popularPayload(),
      leaderboard: () => ({ total: 100 }),
    },
    async (calls) => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        scenario_name: "Smoothsphere Viscose",
      });
      assert.equal(result.status, "succeeded");
      const summary = result.result as Record<string, unknown>;
      // Exact match "Smoothsphere Viscose" wins over the first result ("... Easier").
      assert.equal(summary.leaderboard_id, 184106);
      assert.equal(summary.scenario_name, "Smoothsphere Viscose");
      const popularCall = calls.find((call) => call.includes("/scenario/popular"));
      assert.ok(popularCall, "scenario/popular must be called for name resolution");
      assert.ok(popularCall.includes("scenarioNameSearch=Smoothsphere"));
    },
  );
});

test("an unknown scenario_name fails with scenario_not_found", async () => {
  await withStubbedFetch({ popular: () => ({ page: 0, max: 20, total: 0, data: [] }) }, async () => {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      scenario_name: "definitely not a scenario",
    });
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "scenario_not_found");
  });
});

test("total=0 yields a succeeded summary with null rank and null median", async () => {
  await withStubbedFetch({ leaderboard: () => ({ total: 0, data: [] }) }, async () => {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
      score: 12_000,
    });
    assert.equal(result.status, "succeeded");
    const summary = result.result as Record<string, unknown>;
    assert.equal(summary.total_entries, 0);
    assert.equal(summary.top_score, null);
    assert.equal(summary.median_score, null);
    assert.equal(summary.percentile_top, null);
    assert.equal(summary.on_board, false);
  });
});

test("score=0 is treated as no entry, not as the last place", async () => {
  await withStubbedFetch({ leaderboard: () => ({ total: 1000 }) }, async () => {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
      score: 0,
    });
    assert.equal(result.status, "succeeded");
    const summary = result.result as Record<string, unknown>;
    assert.equal(summary.on_board, false);
    assert.equal(summary.rank, null);
    assert.deepEqual(summary.queried, { kind: "score", value: 0 });
  });
});

test("an upstream max>100 rejection surfaces as unavailable, never an invalid rank", async () => {
  await withStubbedFetch(
    {
      leaderboard: () =>
        new Response(JSON.stringify({ error: '"query.max" must be less than or equal to 100' }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    },
    async (calls) => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        leaderboard_id: 185342,
        score: 12_000,
      });
      assert.equal(result.status, "unavailable");
      assert.equal(result.warning_or_error?.code, "kovaak_leaderboard_unavailable");
      assert.equal(result.result, undefined);
      // The command must never request more than the hard upstream cap.
      const boardCalls = calls.filter((call) => call.includes("/leaderboard/scores/global"));
      const maxValues = boardCalls.map((call) => Number(new URL(call).searchParams.get("max")));
      assert.ok(maxValues.length > 0 && maxValues.every((value) => value > 0 && value <= 100), `bad max: ${maxValues}`);
    },
  );
});

test("a 200 response carrying an error envelope is unavailable", async () => {
  await withStubbedFetch(
    {
      leaderboard: () => new Response(JSON.stringify({ error: "boom" }), { status: 200 }),
    },
    async () => {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
        leaderboard_id: 185342,
      });
      assert.equal(result.status, "unavailable");
    },
  );
});

test("parameter validation rejects ambiguous and malformed inputs", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("network must not be reached for invalid parameters");
  }) as typeof fetch;
  try {
    const cases: Array<[Record<string, unknown>, RegExp]> = [
      [{}, /exactly one of/],
      [{ leaderboard_id: 185342, scenario_name: "x" }, /exactly one of/],
      [{ leaderboard_id: 0 }, /positive integer/],
      [{ leaderboard_id: 1.5 }, /positive integer/],
      [{ scenario_name: "" }, /scenario_name/],
      [{ leaderboard_id: 1, profile_ref: STEAM_ID, score: 100 }, /only one of/],
      [{ leaderboard_id: 1, profile_ref: "not-a-steam-id" }, /profile_ref/],
      [{ leaderboard_id: 1, score: -5 }, /score/],
      [{ leaderboard_id: 1, score: "100" }, /score/],
      [{ leaderboard_id: 1, score: Number.POSITIVE_INFINITY }, /score/],
    ];
    for (const [params, pattern] of cases) {
      const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", params);
      assert.equal(result.status, "failed", JSON.stringify(params));
      assert.match(result.warning_or_error?.message ?? "", pattern, JSON.stringify(params));
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("an unexpected command name fails without touching the network", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("network must not be reached");
  }) as typeof fetch;
  try {
    const result = await executeNativeKovaakLeaderboard("kovaak_leaderboard.other", {});
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "unknown_command");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("every upstream leaderboard request carries a browser User-Agent", async () => {
  const originalFetch = globalThis.fetch;
  const seen: Array<Record<string, string>> = [];
  globalThis.fetch = (async (input: string | URL, init?: RequestInit) => {
    seen.push((init?.headers ?? {}) as Record<string, string>);
    const url = new URL(String(input));
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url.pathname.endsWith("/leaderboard/scores/global")) return json(boardPage(100, Number(url.searchParams.get("page")), 100));
    if (url.pathname.endsWith("/scenario/details")) return json({ scenarioName: "s", aimType: null });
    if (url.hostname === "steamcommunity.com") return new Response(PERSONA_HTML, { status: 200 });
    throw new Error(`unexpected fetch: ${url}`);
  }) as typeof fetch;
  try {
    await executeNativeKovaakLeaderboard("kovaak_leaderboard.lookup", {
      leaderboard_id: 185342,
      profile_ref: STEAM_ID,
    });
    assert.ok(seen.length > 0);
    for (const headers of seen) {
      assert.match(headers["User-Agent"] ?? "", /Mozilla\/5\.0/);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
