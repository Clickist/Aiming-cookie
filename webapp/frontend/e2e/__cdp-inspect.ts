import { chromium } from "@playwright/test";

// CDP 连上实机 WebView，dump 右侧边缘的可疑"拖拽条"元素。
async function main() {
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
  const ctx = browser.contexts()[0];
  const page = ctx.pages()[0];
  await page.goto("http://localhost:3000/history");
  await page.waitForTimeout(2500);

  const report = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("*")) {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      const r = el.getBoundingClientRect();
      // 视口右缘附近（±24px）或超出右缘、窄条状（宽 ≤ 12px 且高 ≥ 200px，或宽 ≤4px）
      const nearRight = r.right > vw - 24;
      const stripish = (r.width <= 12 && r.height >= 200) || r.width <= 4;
      if (nearRight && stripish) {
        out.push(`${el.tagName}.${(el.className || "").toString().slice(0, 60)} rect=${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.width)}x${Math.round(r.height)} vw=${vw} cursor=${cs.cursor} bg=${cs.backgroundColor}`);
      }
    }
    return out.slice(0, 12);
  });
  console.log("STRIPS:", JSON.stringify(report, null, 1));
  await page.screenshot({ path: "E:\\DevCache\\temp\\history-right-edge.png" });
  console.log("screenshot saved");
  await browser.close();
}

void main();
