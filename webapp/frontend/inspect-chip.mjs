import pw from "playwright";

const browser = await pw.chromium.connectOverCDP("http://127.0.0.1:9223");
const page = browser.contexts()[0].pages().find(p => p.url().includes("tauri.localhost"));

// 找训练 chip
const found = await page.evaluate(() => !!document.querySelector(".task6-training-chip"));
if (!found) { console.log("当前会话没有训练 chip"); browser.close(); process.exit(0); }

await page.locator(".task6-training-chip").first().click({ timeout: 3000 }).catch(e => console.log("click:", String(e).slice(0, 40)));
await page.waitForTimeout(500);
// 展开态截图（chip 区域）
const box = await page.evaluate(() => {
  const f = document.querySelector(".task6-coach-floating");
  const r = f.getBoundingClientRect();
  return { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), w: Math.min(460, r.width + 16), h: Math.min(500, r.height + 16) };
});
await page.screenshot({ path: "E:/DevCache/temp/ac-cdp/chip-expanded.png", clip: { x: box.x, y: box.y, width: box.w, height: box.h } });
// 收起态
await page.locator(".task6-training-chip").first().click().catch(() => {});
await page.waitForTimeout(400);
await page.screenshot({ path: "E:/DevCache/temp/ac-cdp/chip-collapsed.png", clip: { x: box.x, y: box.y, width: box.w, height: box.h } });
// DOM 结构
const dom = await page.evaluate(() => {
  const f = document.querySelector(".task6-coach-floating");
  return { html: f?.outerHTML.slice(0, 600), revealH: document.querySelector(".task6-training-reveal")?.getBoundingClientRect().height };
});
console.log(JSON.stringify(dom, null, 1));
browser.close();
console.log("done");
