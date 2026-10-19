import assert from "node:assert/strict";
import test from "node:test";

import { installProxyFetch, resolveProxyForUrl } from "../src/proxy-fetch.ts";

const PROXY = "http://proxy.internal:7897";

test("resolveProxyForUrl honors per-scheme proxy env (curl semantics)", () => {
  const env = { HTTPS_PROXY: PROXY, HTTP_PROXY: "http://http-only:8080" };
  assert.equal(resolveProxyForUrl("https://auth.x.ai/oauth2/device/code", env), PROXY);
  assert.equal(resolveProxyForUrl("http://example.com/api", env), "http://http-only:8080");
});

test("resolveProxyForUrl reads lowercase env and falls back to ALL_PROXY", () => {
  assert.equal(
    resolveProxyForUrl("https://auth.x.ai", { https_proxy: "http://lower:1" }),
    "http://lower:1",
  );
  assert.equal(resolveProxyForUrl("https://auth.x.ai", { ALL_PROXY: PROXY }), PROXY);
  assert.equal(resolveProxyForUrl("http://example.com", { all_proxy: PROXY }), PROXY);
});

test("resolveProxyForUrl appends scheme to scheme-less proxy values", () => {
  assert.equal(resolveProxyForUrl("https://auth.x.ai", { HTTPS_PROXY: "127.0.0.1:7897" }), "http://127.0.0.1:7897");
});

test("loopback targets are never proxied regardless of env", () => {
  const env = { HTTPS_PROXY: PROXY, HTTP_PROXY: PROXY };
  for (const url of [
    "http://127.0.0.1:8000/api/health",
    "https://127.200.1.2:9/x",
    "http://localhost:3000/",
    "https://localhost/x",
    "http://[::1]:8080/v1",
    "http://0.0.0.0/x",
  ]) {
    assert.equal(resolveProxyForUrl(url, env), null, url);
  }
});

test("NO_PROXY entries: wildcard, exact host, dot suffix", () => {
  const env = { HTTPS_PROXY: PROXY };
  assert.equal(resolveProxyForUrl("https://auth.x.ai", { HTTPS_PROXY: PROXY, NO_PROXY: "*" }), null);
  assert.equal(resolveProxyForUrl("https://x.ai", { HTTPS_PROXY: PROXY, NO_PROXY: "x.ai" }), null);
  assert.equal(resolveProxyForUrl("https://api.x.ai", { HTTPS_PROXY: PROXY, NO_PROXY: ".x.ai" }), null);
  assert.equal(resolveProxyForUrl("https://api.x.ai", { HTTPS_PROXY: PROXY, NO_PROXY: "*.x.ai" }), null);
  // 后缀匹配不吞不同域
  assert.equal(resolveProxyForUrl("https://notx.ai", { HTTPS_PROXY: PROXY, NO_PROXY: "x.ai" }), PROXY);
  // 大小写不敏感
  assert.equal(resolveProxyForUrl("https://API.X.AI", { HTTPS_PROXY: PROXY, NO_PROXY: "x.ai" }), null);
});

test("no proxy env means direct connection", () => {
  assert.equal(resolveProxyForUrl("https://auth.x.ai", {}), null);
  assert.equal(resolveProxyForUrl("not a url", { HTTPS_PROXY: PROXY }), null);
  // 非 http(s) 协议不走代理
  assert.equal(resolveProxyForUrl("ftp://example.com", { HTTPS_PROXY: PROXY }), null);
});

test("installed wrapper passes init through verbatim on node runtime (proxy injection is bun-only)", async () => {
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
  const spy = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init });
    return new Response("ok");
  }) as typeof fetch;

  installProxyFetch({
    fetchImpl: spy,
    env: { HTTPS_PROXY: PROXY },
    warn: () => {},
  });

  await fetch("https://outbound.test/device/code", { method: "POST" });
  await fetch("http://127.0.0.1:8000/api/health");
  await fetch(new URL("https://outbound.test/via-url-object"));

  assert.equal(calls.length, 3);
  // node 运行时透传：不出网（fetchImpl 是 spy）、init 无 proxy 键。
  // https 出站的 proxy 注入行为由 proxy-fetch.bun.e2e.ts 在 Bun 下用
  // 假代理 CONNECT 断言覆盖。
  for (const call of calls) {
    assert.equal((call.init as RequestInit & { proxy?: string })?.proxy, undefined);
  }
  assert.equal((calls[0]!.init as RequestInit)?.method, "POST");
});
