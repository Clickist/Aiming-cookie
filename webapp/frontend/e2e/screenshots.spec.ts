import { expect, test, type Page } from "@playwright/test";

import {
  PRODUCT_STATE,
  RUN_MULTIMODAL,
  TASKS,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
  partialAnalysisSession,
  setThemePreference,
  UNAVAILABLE_EVIDENCE_SEGMENTS,
} from "../fixtures/task7-fixtures";

async function prepare(
  page: Page,
  options: {
    desktop?: boolean;
    theme: "system" | "light" | "dark";
    width: number;
    height: number;
    scenario?: ReturnType<typeof apiScenario>;
  },
): Promise<void> {
  await page.setViewportSize({ width: options.width, height: options.height });
  await page.emulateMedia({ colorScheme: options.theme === "light" ? "light" : "dark", reducedMotion: "reduce" });
  await setThemePreference(page, options.theme);
  if (options.desktop) await installDesktopBridge(page);
  await installApiFixtures(page, options.scenario ?? apiScenario());
}

test.describe("Task 7 screenshot baselines", () => {
  test("onboarding 1280 light", async ({ page }) => {
    await prepare(page, { theme: "light", width: 1280, height: 820 });
    await page.goto("/onboarding");
    await expect(page.getByRole("heading", { name: "连接你自己的 AI Provider" })).toBeVisible();
    await expect(page).toHaveScreenshot("onboarding-1280-light.png", { animations: "disabled" });
  });

  test("analyze 1280 dark desktop", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1280, height: 820, scenario: apiScenario({ runs: [RUN_MULTIMODAL] }) });
    await page.goto("/analyze");
    await expect(page.getByRole("heading", { name: "桌面采集状态" })).toBeVisible();
    await expect(page).toHaveScreenshot("analyze-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("tasks partial 1280 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1280, height: 820, scenario: apiScenario({ tasks: [TASKS[2], TASKS[6]] }) });
    await page.goto("/tasks");
    await expect(page.getByText("部分结果可用")).toBeVisible();
    await expect(page).toHaveScreenshot("tasks-partial-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("history 1280 light", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "light", width: 1280, height: 820 });
    await page.goto("/history");
    await expect(page.getByRole("heading", { name: "历史" })).toBeVisible();
    await expect(page).toHaveScreenshot("history-1280-light.png", { animations: "disabled", fullPage: true });
  });

  for (const [tabName, fileName] of [
    ["诊断", "analysis-diagnosis-1280-dark.png"],
    ["视频", "analysis-video-1280-dark.png"],
    ["数据", "analysis-data-1280-dark.png"],
  ] as const) {
    test(`analysis ${tabName} 1280 dark`, async ({ page }) => {
      await prepare(page, { theme: "dark", width: 1280, height: 820 });
      await page.goto("/analysis/42");
      await expect(page.getByRole("heading", { name: "1wall 6targets small" })).toBeVisible();
      if (tabName !== "诊断") await page.getByRole("tab", { name: tabName }).click();
      await expect(page).toHaveScreenshot(fileName, { animations: "disabled", fullPage: true });
    });
  }

  test("settings 1280 dark desktop", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1280, height: 820 });
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
    await expect(page).toHaveScreenshot("settings-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("history 960 system", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "system", width: 960, height: 640 });
    await page.goto("/history");
    await expect(page.getByRole("heading", { name: "历史" })).toBeVisible();
    await expect(page).toHaveScreenshot("history-960-system.png", { animations: "disabled", fullPage: true });
  });

  test("analysis partial 960 light", async ({ page }) => {
    await prepare(page, {
      theme: "light",
      width: 960,
      height: 640,
      scenario: apiScenario({ analysis: partialAnalysisSession(), evidenceSegments: UNAVAILABLE_EVIDENCE_SEGMENTS }),
    });
    await page.goto("/analysis/42");
    await expect(page.getByText("视觉结果部分不可用")).toBeVisible();
    await expect(page).toHaveScreenshot("analysis-partial-960-light.png", { animations: "disabled", fullPage: true });
  });

  test("analyze narrow dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 720, height: 820 });
    await page.goto("/analyze");
    await expect(page.getByRole("heading", { name: "新建分析" })).toBeVisible();
    await expect(page).toHaveScreenshot("analyze-720-dark.png", { animations: "disabled", fullPage: true });
  });

  test("Coach narrow full mode", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 720, height: 820 });
    await page.goto("/history");
    await page.getByRole("button", { name: "Coach" }).click();
    await expect(page.getByRole("dialog", { name: "Coach" })).toBeVisible();
    await expect(page).toHaveScreenshot("coach-720-dark.png", { animations: "disabled", fullPage: true });
  });

  test("startup failure 960 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 960, height: 640, scenario: apiScenario({ failures: { "/api/product-state": 503 } }) });
    await page.goto("/");
    await expect(page.locator('.ac-state[role="alert"]')).toBeVisible();
    await expect(page).toHaveScreenshot("startup-failure-960-dark.png", { animations: "disabled" });
  });

  test("tasks empty 960 light", async ({ page }) => {
    await prepare(page, { theme: "light", width: 960, height: 640, scenario: apiScenario({ tasks: [] }) });
    await page.goto("/tasks");
    await expect(page.getByText("还没有分析任务")).toBeVisible();
    await expect(page).toHaveScreenshot("tasks-empty-960-light.png", { animations: "disabled" });
  });

  test("startup routes to history when records exist", async ({ page }) => {
    await prepare(page, { theme: "system", width: 960, height: 640, scenario: apiScenario({ productState: PRODUCT_STATE }) });
    await page.goto("/");
    await expect(page).toHaveURL(/\/history$/);
  });
});
