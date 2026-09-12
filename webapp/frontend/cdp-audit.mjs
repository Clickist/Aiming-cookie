// CDP audit helper — connect to the running desktop app's WebView2.
// Usage: node cdp-audit.mjs <command> [args...]
// Commands:
//   shot <name>              -> viewport screenshot to .zcode/ac-uiux-audit-0911/<name>.png
//   eval "<js>"              -> evaluate JS in page, print JSON result
//   click <x> <y>            -> real input click at client coords
//   move <x> <y>             -> mouse move (hover)
//   wheel <dx> <dy> <x> <y>  -> scroll at point
//   type "<text>"            -> keyboard type (no focus change)
//   key "<key>"              -> press key, e.g. Escape, Enter
//   frames <n> <intervalMs> "<selectorless-region?>" -> burst screenshots for motion sampling
//   console                  -> dump recent console errors (needs listener session; see watch)
//   watch <seconds>          -> listen console/pageerror for N seconds while I interact
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require(
  path.join(path.resolve("node_modules/playwright-core"), "index.js"),
);

const OUT_DIR = path.resolve("C:/Users/袜子/Desktop/Aiming-cookie/.zcode/ac-uiux-audit-0911");
fs.mkdirSync(OUT_DIR, { recursive: true });

async function connect() {
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
  const ctx = browser.contexts()[0];
  if (!ctx) throw new Error("no context");
  const page = ctx.pages()[0] ?? (await ctx.newPage());
  return { browser, page };
}

const [, , cmd, ...args] = process.argv;
const { browser, page } = await connect();

try {
  if (cmd === "shot") {
    const buf = await page.screenshot();
    const p = path.join(OUT_DIR, `${args[0]}.png`);
    fs.writeFileSync(p, buf);
    console.log("saved", p);
  } else if (cmd === "eval") {
    const result = await page.evaluate(args[0]);
    console.log(JSON.stringify(result, null, 1));
  } else if (cmd === "click") {
    await page.mouse.click(Number(args[0]), Number(args[1]));
    console.log("clicked", args[0], args[1]);
  } else if (cmd === "move") {
    await page.mouse.move(Number(args[0]), Number(args[1]));
    console.log("moved", args[0], args[1]);
  } else if (cmd === "wheel") {
    await page.mouse.move(Number(args[2]), Number(args[3]));
    await page.mouse.wheel(Number(args[0]), Number(args[1]));
    console.log("wheeled");
  } else if (cmd === "type") {
    await page.keyboard.type(args[0], { delay: 15 });
    console.log("typed");
  } else if (cmd === "key") {
    await page.keyboard.press(args[0]);
    console.log("pressed", args[0]);
  } else if (cmd === "frames") {
    const n = Number(args[0]) || 8;
    const gap = Number(args[1]) || 120;
    for (let i = 0; i < n; i++) {
      const buf = await page.screenshot();
      fs.writeFileSync(path.join(OUT_DIR, `burst-${String(i).padStart(2, "0")}.png`), buf);
      if (i < n - 1) await new Promise((r) => setTimeout(r, gap));
    }
    console.log("burst saved", n);
  } else if (cmd === "watch") {
    const secs = Number(args[0]) || 30;
    const logs = [];
    page.on("console", (m) => {
      if (["error", "warning"].includes(m.type())) logs.push([m.type(), m.text()]);
    });
    page.on("pageerror", (e) => logs.push(["pageerror", String(e)]));
    await new Promise((r) => setTimeout(r, secs * 1000));
    console.log(JSON.stringify(logs, null, 1));
  } else {
    console.log("unknown command");
  }
} finally {
  await browser.close().catch(() => {});
}
