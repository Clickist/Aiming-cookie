// CDP acceptance for the three changes (scenario.open / scenario.list / no-source / index split).
// Usage: node acceptance-scenario.mjs
// Operates on the running desktop app via CDP 9223. Light on the machine: one
// conversation, three short prompts.
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve("node_modules/playwright-core/index.js"));

const OUT = path.resolve("C:/Users/袜子/Desktop/Aiming-cookie/.zcode/ac-scenario-acceptance");
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? (await ctx.newPage());
await page.bringToFront();

const log = (step, value) => console.log(JSON.stringify({ step, ...value }));

// Record scenario_open invocations from the Tauri bridge.
await page.evaluate(() => {
  const w = window;
  w.__scenarioOpenCalls = w.__scenarioOpenCalls || [];
  const internals = w.__TAURI_INTERNALS__;
  if (internals && !internals.__wrappedScenario) {
    const original = internals.invoke;
    internals.invoke = (cmd, args, opts) => {
      if (String(cmd).includes("scenario")) w.__scenarioOpenCalls.push({ cmd, args });
      return original(cmd, args, opts);
    };
    internals.__wrappedScenario = true;
  }
});

await page.getByRole("button", { name: "新建对话" }).first().click();
await page.waitForTimeout(1200);

const composer = page.locator("textarea[placeholder^='向 Coach 提问']").first();
await composer.waitFor({ state: "visible", timeout: 15000 });
const SEL = ".coach-message-assistant, [data-role='assistant']";

async function ask(question) {
  const before = await page.locator(SEL).count();
  await composer.click();
  await composer.fill(question);
  await page.getByRole("button", { name: "发送" }).first().click();
  let last = "";
  let stable = 0;
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(3000);
    const count = await page.locator(SEL).count();
    if (count <= before) continue;
    const text = (await page.locator(SEL).last().innerText()).trim();
    if (text && text === last) {
      stable += 1;
      if (stable >= 3) break;
    } else {
      stable = 0;
      last = text;
    }
  }
  return (await page.locator(SEL).last().innerText()).trim();
}

const results = {};

// 1) overflick recommendation
results.recommendation = await ask("我 overflick 该练什么？");
log("recommendation", { chars: results.recommendation.length, text: results.recommendation });
await page.screenshot({ path: path.join(OUT, "1-recommendation.png") });

// 2) consent to open a locally installed scenario
results.openReply = await ask("好，帮我打开 1wall 6targets small");
log("openReply", { chars: results.openReply.length, text: results.openReply });
await page.waitForTimeout(6000);
const calls = await page.evaluate(() => window.__scenarioOpenCalls || []);
results.scenarioOpenCalls = calls;
log("scenarioOpenCalls", { calls });
await page.screenshot({ path: path.join(OUT, "2-open.png") });

// 3) local scenario list
results.listReply = await ask("本机有哪些场景？");
log("listReply", { chars: results.listReply.length, pretext: results.listReply.slice(0, 400) });
await page.screenshot({ path: path.join(OUT, "3-list.png") });

// 4) no video-source wording in recommendation replies
const sourceMarkers = ["出处：", "《", "视频标题", "BV1", "bilibili"];
const found = sourceMarkers.filter((m) => results.recommendation.includes(m) || results.openReply.includes(m));
results.sourceMarkersFound = found;
log("sourceMarkers", { found });

fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2), "utf-8");
console.log(JSON.stringify({ done: true, out: OUT }));
await browser.close();
