// 0911 滚动条位置审查：历史页+设置页，滚动中截图+量滚动条几何
import pw from "playwright";
const connectOverCDP = (a) => pw.chromium.connectOverCDP(a);

const browser = await connectOverCDP("http://127.0.0.1:9223");
const context = browser.contexts()[0];
const page = context.pages().find((p) => p.url().startsWith("http://tauri.localhost")) ?? context.pages()[0];

// ── 历史页 ──
await page.getByRole("button", { name: /训练历史|历史/ }).first().click();
await page.waitForURL("**/history**", { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1200);
await page.evaluate(() => {
  const sc = [...document.querySelectorAll("*")].find(e => e.scrollHeight > e.clientHeight + 200 && getComputedStyle(e).overflowY !== "visible");
  if (sc) sc.scrollTop = 300;
});
await page.waitForTimeout(300);
await page.screenshot({ path: "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/history-shots/30-hist-scrollbar.png" });
const hist = await page.evaluate(() => {
  const out = [];
  document.querySelectorAll("*").forEach((el) => {
    if (el.scrollHeight > el.clientHeight + 100) {
      const cs = getComputedStyle(el);
      if (cs.overflowY === "auto" || cs.overflowY === "scroll") {
        const r = el.getBoundingClientRect();
        // 滚动条宽度估算
        const sw = el.offsetWidth - el.clientWidth;
        out.push({ cls: (el.className||"").toString().slice(0,50), x: Math.round(r.x), w: Math.round(r.width), scrollBarW: sw, right: Math.round(r.right), winW: innerWidth });
      }
    }
  });
  return { out, winW: innerWidth, winH: innerHeight };
});
console.log("HISTORY:", JSON.stringify(hist, null, 1));

// ── 设置页 ──
await page.getByRole("button", { name: /系统设置/ }).first().click();
await page.waitForURL("**/settings**", { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1200);
await page.evaluate(() => {
  const sc = [...document.querySelectorAll("*")].find(e => e.scrollHeight > e.clientHeight + 200 && getComputedStyle(e).overflowY !== "visible");
  if (sc) sc.scrollTop = 300;
});
await page.waitForTimeout(300);
await page.screenshot({ path: "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/history-shots/31-settings-scrollbar.png" });
const set = await page.evaluate(() => {
  const out = [];
  document.querySelectorAll("*").forEach((el) => {
    if (el.scrollHeight > el.clientHeight + 100) {
      const cs = getComputedStyle(el);
      if (cs.overflowY === "auto" || cs.overflowY === "scroll") {
        const r = el.getBoundingClientRect();
        const sw = el.offsetWidth - el.clientWidth;
        out.push({ cls: (el.className||"").toString().slice(0,50), x: Math.round(r.x), w: Math.round(r.width), scrollBarW: sw, right: Math.round(r.right) });
      }
    }
  });
  return out;
});
console.log("SETTINGS:", JSON.stringify(set, null, 1));
await browser.close();
