import { expect, test, type Page } from "@playwright/test";

import {
  PRODUCT_STATE,
  analysisSession,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
  setThemePreference,
} from "../fixtures/task7-fixtures";

function seekableAnalysis() {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2" || !base.history) {
    throw new Error("seekable fixture requires v2 history");
  }
  return analysisSession({
    input_mode: "multimodal",
    result: { ...base.result, input_mode: "multimodal" },
    history: {
      ...base.history,
      input_mode: "multimodal",
      source_availability: { ...base.history.source_availability, mp4: "available" },
      visual_replay: {
        kind: "seekable_mp4",
        available: true,
        seekable: true,
        endpoint: "/api/sessions/42/video",
        artifact_ref: "analysis:42:video",
        reason: null,
      },
    },
  });
}

async function prepare(
  page: Page,
  options: {
    desktop?: boolean;
    theme: "system" | "light" | "dark";
    width: 1280 | 1920;
    height: 820 | 1080;
    scenario?: ReturnType<typeof apiScenario>;
  },
): Promise<void> {
  await page.setViewportSize({ width: options.width, height: options.height });
  await page.emulateMedia({ colorScheme: options.theme === "light" ? "light" : "dark", reducedMotion: "reduce" });
  await setThemePreference(page, options.theme);
  if (options.desktop) await installDesktopBridge(page);
  await installApiFixtures(page, options.scenario ?? apiScenario());
}

async function installCoachCards(page: Page): Promise<void> {
  await page.route("**/api/coach/primary*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread: { id: 1, user_id: "dev", kind: "primary", created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z" },
        messages: [{
          id: 1,
          role: "assistant",
          content: "减速阶段偏长，而且末段出现了反向修正。先看关键数据，再结合视频确认发生的位置。",
          created_at: "2026-08-10T00:00:00Z",
          legacy_session_id: null,
          context_refs: [],
          cards: [
            { schema_version: "coach_message_card.v1", kind: "metrics", analysis_ref: "analysis:42", target_ref: null, time_range_ms: null },
            { schema_version: "coach_message_card.v1", kind: "timeline", analysis_ref: "analysis:42", target_ref: null, time_range_ms: null },
            { schema_version: "coach_message_card.v1", kind: "evidence", analysis_ref: "analysis:42", target_ref: null, time_range_ms: [1200, 1800] },
          ],
        }],
        refs: [{ id: 1, analysis_session_id: 42, status: "active", attached_at: "2026-08-10T00:00:00Z", deleted_at: null }],
      }),
    });
  });
}

test.describe("Coach-first desktop screenshot baselines", () => {
  test("onboarding 1280 light", async ({ page }) => {
    await prepare(page, { theme: "light", width: 1280, height: 820 });
    await page.goto("/onboarding");
    await expect(page.getByRole("heading", { name: "连接模型服务" })).toBeVisible();
    await expect(page).toHaveScreenshot("onboarding-1280-light.png", { animations: "disabled" });
  });

  test("onboarding 1280 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1280, height: 820 });
    await page.goto("/onboarding");
    await expect(page.getByRole("heading", { name: "连接模型服务" })).toBeVisible();
    await expect(page).toHaveScreenshot("onboarding-1280-dark.png", { animations: "disabled" });
  });

  test("Coach conversation 1280 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1280, height: 820 });
    await page.goto("/");
    await expect(page.getByText("Aiming Coach", { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("coach-conversation-1280-dark.png", { animations: "disabled" });
  });

  test("Coach conversation 1920 light", async ({ page }) => {
    await prepare(page, { theme: "light", width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.locator(".task3-coach-view")).toBeVisible();
    await expect(page).toHaveScreenshot("coach-conversation-1920-light.png", { animations: "disabled" });
  });

  test("Coach cards 1280 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1280, height: 820, scenario: apiScenario({ analysis: seekableAnalysis() }) });
    await installCoachCards(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "关键数据" })).toBeVisible();
    await expect(page).toHaveScreenshot("coach-cards-1280-dark.png", { animations: "disabled" });
  });

  test("Coach video workspace 1920 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1920, height: 1080, scenario: apiScenario({ analysis: seekableAnalysis() }) });
    await installCoachCards(page);
    await page.goto("/");
    await page.getByRole("button", { name: "在视频中查看" }).click();
    await expect(page.getByRole("region", { name: "Coach 视频讲解" })).toBeVisible();
    await expect(page).toHaveScreenshot("coach-video-1920-dark.png", { animations: "disabled" });
  });

  test("History 1280 light", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "light", width: 1280, height: 820 });
    await page.goto("/history");
    await expect(page.locator(".task4-page-title", { hasText: "历史" })).toBeVisible();
    await expect(page).toHaveScreenshot("history-1280-light.png", { animations: "disabled", fullPage: true });
  });

  test("History 1920 dark", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1920, height: 1080 });
    await page.goto("/history");
    await expect(page.locator(".task4-page")).toBeVisible();
    await expect(page).toHaveScreenshot("history-1920-dark.png", { animations: "disabled", fullPage: true });
  });

  test("Settings 1280 dark", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1280, height: 820 });
    await page.goto("/settings");
    await expect(page.locator(".task6-settings-nav-title", { hasText: "设置" })).toBeVisible();
    await expect(page.getByRole("button", { name: "退出设置" })).toBeVisible();
    await expect(page).toHaveScreenshot("settings-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("Settings 1920 light", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "light", width: 1920, height: 1080 });
    await page.goto("/settings");
    await expect(page.locator(".task6-settings-layout")).toBeVisible();
    await expect(page).toHaveScreenshot("settings-1920-light.png", { animations: "disabled", fullPage: true });
  });

  test("startup keeps the Coach workspace when records exist", async ({ page }) => {
    await prepare(page, { theme: "system", width: 1280, height: 820, scenario: apiScenario({ productState: PRODUCT_STATE }) });
    await page.goto("/");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("Aiming Coach", { exact: true })).toBeVisible();
  });
});
