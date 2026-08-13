import { chromium, expect, test } from "@playwright/test";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const providerReady = process.env.AIMING_COOKIE_TAURI_PROVIDER_READY === "1";
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

test("real Tauri WebView completes a Coach Provider run through the product UI", async () => {
  test.skip(!cdpUrl || !providerReady, "requires a Tauri session with Provider configured through the product UI");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  const agentRunRequests: Array<{ method: string; path: string }> = [];
  page!.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/agent-runs")) {
      agentRunRequests.push({ method: request.method(), path });
    }
  });
  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.goto(`${appUrl}/`);

  await expect(page!.getByText("Aiming Coach", { exact: true })).toBeVisible();
  const composer = page!.getByRole("textbox", { name: "向 Coach 提问" });
  await expect(composer).toBeVisible();
  const assistantMessages = page!.locator('.task6-message[data-role="assistant"] p').filter({ hasText: /\S/ });
  const assistantCountBeforeSend = await assistantMessages.count();

  const prompt = "E2E smoke: reply with one short sentence saying Coach is connected.";
  await composer.fill(prompt);
  await page!.getByRole("button", { name: "发送" }).click();

  await expect(page!.locator('.task6-message[data-role="user"]', { hasText: prompt })).toBeVisible();
  await expect.poll(() => agentRunRequests.some(({ method, path }) => method === "POST" && path === "/v1/agent-runs"), {
    timeout: 30_000,
  }).toBe(true);
  await expect.poll(() => agentRunRequests.some(({ method, path }) => method === "GET" && /^\/v1\/agent-runs\/[^/]+$/.test(path)), {
    timeout: 30_000,
  }).toBe(true);
  await expect(assistantMessages).toHaveCount(assistantCountBeforeSend + 1, { timeout: 120_000 });
  await expect(assistantMessages.last()).toBeVisible();
  await expect(page!.getByRole("button", { name: "发送" })).toBeVisible({ timeout: 120_000 });
  await expect(page!.locator(".task6-error-card")).toHaveCount(0);
});
