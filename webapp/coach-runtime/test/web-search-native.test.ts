import assert from "node:assert/strict";
import test from "node:test";

import {
  WEB_SEARCH_TOOL_NAMES,
  createWebSearchTools,
  isValidHttpUrl,
  parseDuckDuckGoHtml,
  parseWikipediaSearch,
  htmlToText,
  runFetchPage,
  runWebSearch,
  stripHtml,
  truncatePage,
  unwrapResultUrl,
} from "../src/web-search-native.ts";

// Trimmed from a real https://html.duckduckgo.com/html/ response: title anchors
// carry DDG's `/l/?uddg=` redirect, snippets are `result__snippet` anchors.
const DDG_HTML = `
<div class="result links_main links_deep result__body">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fkovaaks.com%2Fplay%2Fscenario%2F1wall%25206targets%2520small&amp;rut=abc">1wall 6targets small - kovaaks.com</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fkovaaks.com%2Fplay%2Fscenario%2F1wall%25206targets%2520small&amp;rut=abc">Open scenario <b>1wall</b> <b>6targets</b> <b>small</b> in <b>KovaaK&#x27;s</b></a>
</div>
<div class="result links_main links_deep result__body">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsteamcommunity.com%2Fsharedfiles%2Ffiledetails%2F%3Fid%3D1337321696">Steam Workshop :: Voltaic Benchmarks</a>
  </h2>
  <a class="result__snippet" href="#">Community benchmark scenarios &amp; routines for aim training</a>
</div>
`;

test("parseDuckDuckGoHtml extracts titles, unwrapped URLs and snippets", () => {
  const items = parseDuckDuckGoHtml(DDG_HTML);
  assert.equal(items.length, 2);
  assert.equal(items[0]?.title, "1wall 6targets small - kovaaks.com");
  assert.equal(items[0]?.url, "https://kovaaks.com/play/scenario/1wall%206targets%20small");
  assert.equal(items[0]?.snippet, "Open scenario 1wall 6targets small in KovaaK's");
  assert.equal(items[1]?.url, "https://steamcommunity.com/sharedfiles/filedetails/?id=1337321696");
  assert.equal(items[1]?.snippet, "Community benchmark scenarios & routines for aim training");
});

test("parseDuckDuckGoHtml pairs snippets to the nearest preceding title and respects the limit", () => {
  const limited = parseDuckDuckGoHtml(DDG_HTML, 1);
  assert.equal(limited.length, 1);
  assert.equal(limited[0]?.title, "1wall 6targets small - kovaaks.com");
});

test("parseDuckDuckGoHtml returns nothing for a page without results", () => {
  assert.deepEqual(parseDuckDuckGoHtml("<html><body>no results</body></html>"), []);
  assert.deepEqual(parseDuckDuckGoHtml(""), []);
});

test("unwrapResultUrl decodes uddg, protocol-relative links, and rejects DDG wrappers", () => {
  assert.equal(
    unwrapResultUrl("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&amp;rut=x"),
    "https://example.com/a?b=1",
  );
  assert.equal(unwrapResultUrl("//example.com/plain"), "https://example.com/plain");
  assert.equal(unwrapResultUrl("javascript:alert(1)"), "");
  // Undecodable DDG wrapper must not be passed through as the destination.
  assert.equal(unwrapResultUrl("//duckduckgo.com/l/?rut=missing"), "");
});

test("stripHtml and truncatePage bound their output", () => {
  assert.equal(stripHtml("<b>a</b>\n  <i>b</i>"), "a b");
  const long = "x".repeat(9000);
  const clipped = truncatePage(long, 8000);
  assert.ok(clipped.startsWith("x".repeat(8000)));
  assert.match(clipped, /已截断/);
});

test("isValidHttpUrl accepts http/https only", () => {
  assert.equal(isValidHttpUrl("https://example.com"), true);
  assert.equal(isValidHttpUrl("http://example.com/x"), true);
  assert.equal(isValidHttpUrl("ftp://example.com"), false);
  assert.equal(isValidHttpUrl("not a url"), false);
  assert.equal(isValidHttpUrl(""), false);
  assert.equal(isValidHttpUrl(42), false);
});

test("runWebSearch uses DuckDuckGo when it responds", async () => {
  const calls: string[] = [];
  const fetchImpl = (async (url: string | URL) => {
    calls.push(String(url));
    return new Response(DDG_HTML, { status: 200 });
  }) as unknown as typeof fetch;
  const outcome = await runWebSearch("1wall 6targets", fetchImpl);
  assert.equal(outcome.status, "succeeded");
  assert.equal(outcome.provider, "duckduckgo");
  assert.equal(outcome.items.length, 2);
  assert.equal(calls.length, 1);
  assert.match(calls[0] ?? "", /html\.duckduckgo\.com\/html\/\?q=1wall/);
});

test("runWebSearch degrades to Wikipedia on DDG failure and reports the fallback", async () => {
  const urls: string[] = [];
  const fetchImpl = (async (url: string | URL) => {
    urls.push(String(url));
    if (String(url).includes("duckduckgo.com")) {
      return new Response("blocked", { status: 403 });
    }
    return new Response(JSON.stringify({
      query: { search: [
        { title: "Aim (video game)", snippet: "<b>Aim</b> training methods" },
        { title: "Mouse (computing)", snippet: "pointing device" },
      ] },
    }), { status: 200 });
  }) as unknown as typeof fetch;
  const outcome = await runWebSearch("aim training", fetchImpl);
  assert.equal(outcome.status, "succeeded");
  assert.equal(outcome.provider, "wikipedia");
  assert.match(outcome.message ?? "", /回退 Wikipedia/);
  assert.equal(outcome.items[0]?.title, "Aim (video game)");
  assert.equal(outcome.items[0]?.url, "https://en.wikipedia.org/wiki/Aim_(video_game)");
  assert.equal(outcome.items[0]?.snippet, "Aim training methods");
  assert.ok(urls.some((url) => url.includes("en.wikipedia.org/w/api.php")));
});

test("runWebSearch returns a readable failure when both providers fail", async () => {
  const fetchImpl = (async () => new Response("down", { status: 503 })) as unknown as typeof fetch;
  const outcome = await runWebSearch("anything", fetchImpl);
  assert.equal(outcome.status, "failed");
  assert.deepEqual(outcome.items, []);
  assert.match(outcome.message ?? "", /联网搜索失败/);
});

test("runWebSearch rejects an empty query instead of issuing a request", async () => {
  let called = false;
  const fetchImpl = (async () => {
    called = true;
    return new Response(DDG_HTML, { status: 200 });
  }) as unknown as typeof fetch;
  await assert.rejects(() => runWebSearch("   ", fetchImpl), /非空/);
  assert.equal(called, false);
});

test("runFetchPage reads through r.jina.ai and truncates", async () => {
  const urls: string[] = [];
  const fetchImpl = (async (url: string | URL) => {
    urls.push(String(url));
    return new Response("# Title\n\nbody", { status: 200 });
  }) as unknown as typeof fetch;
  const outcome = await runFetchPage("https://example.com/page", fetchImpl);
  assert.equal(outcome.status, "succeeded");
  assert.equal(outcome.text, "# Title\n\nbody");
  assert.equal(urls[0], "https://r.jina.ai/https://example.com/page");
});

test("runFetchPage falls back to the direct page when r.jina.ai is blocked", async () => {
  const urls: string[] = [];
  const fetchImpl = (async (url: string | URL) => {
    urls.push(String(url));
    const target = String(url);
    if (target.startsWith("https://r.jina.ai/")) {
      return new Response("blocked", { status: 403 });
    }
    return new Response(
      "<html><head><style>p{color:red}</style><script>x()</script></head><body><h1>Real &amp; Page</h1><p>Readable text</p></body></html>",
      { status: 200, headers: { "content-type": "text/html; charset=utf-8" } },
    );
  }) as unknown as typeof fetch;
  const outcome = await runFetchPage("https://example.com/page", fetchImpl);
  assert.equal(outcome.status, "succeeded");
  assert.match(outcome.text, /Real & Page/);
  assert.match(outcome.text, /Readable text/);
  assert.doesNotMatch(outcome.text, /x\(\)|color:red/);
  // Reader first, then the direct target.
  assert.equal(urls[0], "https://r.jina.ai/https://example.com/page");
  assert.equal(urls[1], "https://example.com/page");
});

test("runFetchPage reports both failures readably and validates the URL", async () => {
  const fetchImpl = (async () => new Response("nope", { status: 429 })) as unknown as typeof fetch;
  const failed = await runFetchPage("https://example.com", fetchImpl);
  assert.equal(failed.status, "failed");
  assert.match(failed.message ?? "", /HTTP 429/);

  await assert.rejects(() => runFetchPage("file:///etc/passwd", fetchImpl), /http 或 https/);
  await assert.rejects(() => runFetchPage("not-a-url", fetchImpl), /http 或 https/);
});

test("htmlToText strips scripts, styles and tags", () => {
  const text = htmlToText("<div><script>bad()</script><style>.x{}</style><p>one</p><p>two</p></div>");
  assert.doesNotMatch(text, /bad\(\)|\.x/);
  assert.match(text, /one/);
  assert.match(text, /two/);
});

test("parseWikipediaSearch handles malformed payloads", () => {
  assert.deepEqual(parseWikipediaSearch(null), []);
  assert.deepEqual(parseWikipediaSearch({ query: {} }), []);
  assert.deepEqual(parseWikipediaSearch({ query: { search: ["bad"] } }), []);
});

test("createWebSearchTools registers web_search and fetch_page", async () => {
  const tools = await createWebSearchTools();
  assert.deepEqual(tools.map((tool) => tool.name), [...WEB_SEARCH_TOOL_NAMES]);
  for (const tool of tools) {
    assert.ok(tool.description.length > 0);
    assert.ok(tool.parameters);
  }
});
