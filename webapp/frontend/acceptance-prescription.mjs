// Acceptance: new Coach conversation, ask about overflick, capture the reply.
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve("node_modules/playwright-core/index.js"));

const OUT = path.resolve("C:/Users/袜子/Desktop/Aiming-cookie/.zcode/ac-prescription-acceptance");
fs.mkdirSync(OUT, { recursive: true });

const QUESTION = process.argv[2] || "我静态点击收尾控制差，该练什么？";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? (await ctx.newPage());
await page.bringToFront();

// Start a fresh conversation.
await page.getByRole("button", { name: "新建对话" }).first().click();
await page.waitForTimeout(1200);

const composer = page.locator("textarea[placeholder^='向 Coach 提问']").first();
await composer.waitFor({ state: "visible", timeout: 15000 });
await composer.click();
await composer.fill(QUESTION);

// Count assistant turns before submit so we can detect the new reply.
const before = await page.locator(".coach-message-assistant, [data-role='assistant']").count();

await page.getByRole("button", { name: "发送" }).first().click();

// Poll until a new assistant message appears and stops changing.
const SEL = ".coach-message-assistant, [data-role='assistant']";
let last = "";
let stable = 0;
const deadline = Date.now() + 150000;
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

const reply = (await page.locator(SEL).last().innerText()).trim();
const shot = path.join(OUT, "reply.png");
await page.screenshot({ path: shot, fullPage: false });
fs.writeFileSync(path.join(OUT, "reply.txt"), `${QUESTION}\n\n----\n\n${reply}\n`, "utf-8");
console.log(JSON.stringify({ question: QUESTION, chars: reply.length, reply, screenshot: shot }, null, 2));
await browser.close();
