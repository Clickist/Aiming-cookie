import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// getConfigDir 在首次调用时缓存 DATA_ROOT，必须在任何断言前设置隔离根。
const dataRoot = mkdtempSync(join(tmpdir(), "ac-purchase-links-"));
mkdirSync(join(dataRoot, "config"), { recursive: true });
process.env.DATA_ROOT = dataRoot;

import { executeNativeAffiliate, isNativeAffiliateCommand } from "../src/affiliate-native.ts";

const CONFIG_PATH = join(dataRoot, "config", "affiliate-service.json");

function writeConfig(body: unknown): void {
  writeFileSync(CONFIG_PATH, JSON.stringify(body), "utf-8");
}

const ORIGINAL_FETCH = globalThis.fetch;

test.afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
});

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("官方直签平台结果映射为 direct，优先于淘宝", async () => {
  writeConfig({ service_url: "https://affiliate.example.com", token: "tok" });
  globalThis.fetch = (async (_url: any, init: any) => {
    assert.equal(init.headers["x-ac-token"], "tok");
    return {
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            q: "gwolves htx 4k",
            direct: { platform: "direct", title: "HTX無綫滑鼠4K版本", url: "https://www.g-wolves.cn/products/htx4k-wireless-gaming-mouse?ref=bzkvvtdy" },
            taobao: null,
            pdd: null,
          },
        ],
      }),
    };
  }) as typeof fetch;

  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "G-Wolves", model: "HTX 4K" }],
  });
  assert.equal(result.status, "succeeded");
  const links = (result.result as any).links;
  assert.equal(links[0].direct.url, "https://www.g-wolves.cn/products/htx4k-wireless-gaming-mouse?ref=bzkvvtdy");
  assert.equal(links[0].taobao, null);
});

test("isNativeAffiliateCommand 只认 purchase_links.lookup", () => {
  assert.equal(isNativeAffiliateCommand("purchase_links.lookup"), true);
  assert.equal(isNativeAffiliateCommand("eloshapes.query"), false);
});

test("参数契约：非法 items 与未知顶层字段被拒绝", async () => {
  const noItems = await executeNativeAffiliate("purchase_links.lookup", {});
  assert.equal(noItems.status, "failed");
  assert.equal(noItems.warning_or_error?.code, "invalid_parameters");

  const emptyItems = await executeNativeAffiliate("purchase_links.lookup", { items: [] });
  assert.equal(emptyItems.status, "failed");

  const badItem = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Logitech", price: "599" }],
  });
  assert.equal(badItem.status, "failed");

  const blankItem = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ variant: "SE" }],
  });
  assert.equal(blankItem.status, "failed");

  const unknownTop = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Logitech", model: "G304" }],
    force: true,
  });
  assert.equal(unknownTop.status, "failed");

  const tooMany = await executeNativeAffiliate("purchase_links.lookup", {
    items: Array.from({ length: 7 }, () => ({ brand: "Razer", model: "Viper" })),
  });
  assert.equal(tooMany.status, "failed");
});

test("服务未配置时返回 unavailable（Coach 走降级链）", async () => {
  rmSync(CONFIG_PATH, { force: true });
  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Logitech", model: "G304" }],
  });
  assert.equal(result.status, "unavailable");
  assert.equal(result.warning_or_error?.code, "purchase_links_unavailable");
});

test("https 之外的服务地址按未配置处理", async () => {
  writeConfig({ service_url: "http://affiliate.example.com", token: "t" });
  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Logitech", model: "G304" }],
  });
  assert.equal(result.status, "unavailable");
});

test("命中结果被规范化：淘宝投影、pdd 未命中为 null", async () => {
  writeConfig({ service_url: "https://affiliate.example.com", token: "tok" });
  globalThis.fetch = (async (_url: any, init: any) => {
    assert.equal(init.headers["x-ac-token"], "tok");
    assert.equal(new URL(String(_url)).pathname, "/links");
    return {
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            q: "logitech 罗技 G304",
            taobao: { platform: "taobao", title: "罗技 G304无线鼠标", price: "179", sales: "6万+", url: "https://s.click.taobao.com/t?e=x" },
            pdd: { platform: "pdd", miss: true, reason: "no_match" },
          },
        ],
      }),
    };
  }) as typeof fetch;

  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Logitech", model: "G304" }],
  });
  assert.equal(result.status, "succeeded");
  const links = (result.result as any).links;
  assert.equal(links.length, 1);
  assert.equal(links[0].taobao.url, "https://s.click.taobao.com/t?e=x");
  assert.equal(links[0].taobao.price, "179");
  assert.equal(links[0].pdd, null);
  assert.deepEqual(links[0].request, { brand: "Logitech", model: "G304" });
});

test("服务 5xx 返回 unavailable 而不是抛错", async () => {
  writeConfig({ service_url: "https://affiliate.example.com", token: "tok" });
  globalThis.fetch = (async () => ({ ok: false, status: 503, json: async () => ({}) })) as typeof fetch;
  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ brand: "Razer", model: "Viper V3 Pro" }],
  });
  assert.equal(result.status, "unavailable");
  assert.equal(result.warning_or_error?.code, "purchase_links_unavailable");
});

test("服务返回缺 results 的响应判为 failed", async () => {
  writeConfig({ service_url: "https://affiliate.example.com", token: "tok" });
  globalThis.fetch = (async () => ({ ok: true, status: 200, json: async () => ({ error: "bad" }) })) as typeof fetch;
  const result = await executeNativeAffiliate("purchase_links.lookup", {
    items: [{ q: "罗技 G304" }],
  });
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "purchase_links_invalid_response");
});
