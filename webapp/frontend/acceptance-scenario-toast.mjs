// Focused proof that the Coach scenario ui_event reaches the frontend and
// openKovaakScenario dispatches the Steam deep link (toast shows Rust result).
import { createRequire } from "node:module";
import path from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve("node_modules/playwright-core/index.js"));
const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const page = browser.contexts()[0].pages()[0];
await page.bringToFront();
await page.getByRole("button", { name: "新建对话" }).first().click();
await page.waitForTimeout(1200);
const composer = page.locator("textarea[placeholder^='向 Coach 提问']").first();
await composer.waitFor({ state: "visible", timeout: 15000 });
await composer.click();
await composer.fill("直接帮我打开 1wall 6targets small，不用再确认。");
await page.getByRole("button", { name: "发送" }).first().click();
let toast = null;
const deadline = Date.now() + 180000;
while (Date.now() < deadline && !toast) {
  await page.waitForTimeout(1500);
  const text = await page.evaluate(() => document.body.innerText);
  if (text.includes("已请求打开 KovaaK")) toast = "已请求打开 KovaaK，请确认目标场景已加载。";
  if (text.includes("本机 KovaaK 没有这个场景")) toast = "本机 KovaaK 没有这个场景，需要先订阅/下载。";
}
console.log(JSON.stringify({ toast }));
await browser.close();
