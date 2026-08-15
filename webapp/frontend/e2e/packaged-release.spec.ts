import { chromium, expect, test, type Browser } from "@playwright/test";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;

test("packaged release WebView renders the product surface", async () => {
  test.skip(!cdpUrl, "requires an installed release instance with WebView2 CDP enabled");

  let browser: Browser | undefined;
  const deadline = Date.now() + 30_000;
  while (!browser && Date.now() < deadline) {
    try {
      browser = await chromium.connectOverCDP(cdpUrl!);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  expect(browser, "packaged WebView CDP connection").toBeDefined();
  const page = browser!.contexts().flatMap((context) => context.pages())[0];
  expect(page, "packaged Tauri WebView page").toBeDefined();

  await expect(page!.locator('[aria-label="Aiming Cookie"]')).toBeVisible({ timeout: 60_000 });
  const content = await page!.locator("body").innerText();
  expect(content.trim().length).toBeGreaterThan(20);
  expect(content).not.toMatch(/asset not found|failed to load|application error/i);

  const screenshotPath = process.env.AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT;
  if (screenshotPath) await page!.screenshot({ path: screenshotPath });
});
