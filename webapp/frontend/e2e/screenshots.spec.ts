import { expect, test, type Page } from "@playwright/test";

import {
  ANALYSIS_FAMILY_FLICKING,
  ANALYSIS_FAMILY_SWITCHING,
  ANALYSIS_DATA,
  PRODUCT_STATE,
  RUN_MULTIMODAL,
  TASKS,
  apiScenario,
  analysisSession,
  installApiFixtures,
  installDesktopBridge,
  partialAnalysisSession,
  registryBackedAnalysisSession,
  setThemePreference,
  UNAVAILABLE_EVIDENCE_SEGMENTS,
} from "../fixtures/task7-fixtures";

function familyAnalysis(
  family: "static_clicking" | "target_switching",
  inputMode: "input_native" | "multimodal",
) {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") {
    throw new Error("family screenshot fixture requires v2");
  }
  return analysisSession({
    result: {
      ...base.result,
      analysis_type: family === "target_switching" ? "target_switching" : "flicking",
      input_mode: inputMode,
      input_snapshot: {
        ...base.result.input_snapshot,
        scenario_resolution: {
          ...base.result.input_snapshot.scenario_resolution!,
          aim_family: family,
        },
      },
    },
  });
}

function familySummaryData(family: "switching" | "flicking") {
  if (family === "switching") {
    return {
      ...ANALYSIS_DATA,
      event_markers: [
        { event_ref: "event:switch:1", kind: "switch_chain", relative_ms: 1200 },
        { event_ref: "event:switch:2", kind: "settle", relative_ms: 1430 },
      ],
      event_distribution: [
        { kind: "switch_chain", count: 2 },
        { kind: "settle", count: 2 },
      ],
    };
  }
  return {
    ...ANALYSIS_DATA,
    event_markers: [
      { event_ref: "event:flick:1", kind: "peak", relative_ms: 2478 },
      { event_ref: "event:flick:2", kind: "corrective", relative_ms: 2520 },
    ],
    event_distribution: [
      { kind: "peak", count: 1 },
      { kind: "corrective", count: 2 },
    ],
    target_relative_error_radius: {
      availability: "unavailable" as const,
      reason: "target_relative_samples_unavailable",
      points: [],
    },
  };
}

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

async function closeCoachOverlay(page: Page): Promise<void> {
  const backdrop = page.locator(".task6-coach-sidebar-wrap[data-state='open'] .task6-coach-scrim");
  await backdrop.waitFor({ state: "visible", timeout: 1_500 }).catch(() => undefined);
  if (await backdrop.isVisible()) {
    await backdrop.click({ position: { x: 4, y: 4 } });
    await expect(backdrop).toBeHidden();
  }
}

test.describe("Task 7 screenshot baselines", () => {
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

  test("analyze 1280 dark desktop", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1280, height: 820, scenario: apiScenario({ runs: [RUN_MULTIMODAL] }) });
    await page.goto("/analyze");
    await expect(page.getByText("自动采集：采集中", { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("analyze-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("tasks partial 1280 dark", async ({ page }) => {
    await prepare(page, { theme: "dark", width: 1280, height: 820, scenario: apiScenario({ tasks: [TASKS[2], TASKS[6]] }) });
    await page.goto("/tasks");
    await expect(page.getByText("部分可用", { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("tasks-partial-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("history 1280 light", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "light", width: 1280, height: 820 });
    await page.goto("/history");
    await expect(page.locator(".task4-page-title", { hasText: "历史" })).toBeVisible();
    await expect(page).toHaveScreenshot("history-1280-light.png", { animations: "disabled", fullPage: true });
  });

  for (const [tabName, fileName] of [
    ["诊断", "analysis-diagnosis-1280-dark.png"],
    ["视频", "analysis-video-1280-dark.png"],
    ["数据", "analysis-data-1280-dark.png"],
  ] as const) {
    test(`analysis ${tabName} 1280 dark`, async ({ page }) => {
      await prepare(page, {
        theme: "dark",
        width: 1280,
        height: 820,
        scenario: tabName === "诊断" ? apiScenario({ analysis: registryBackedAnalysisSession() }) : undefined,
      });
      await page.goto("/analysis/42");
      await expect(page.getByRole("heading", { name: "1wall 6targets small" })).toBeVisible();
      if (tabName !== "诊断") await page.getByRole("tab", { name: tabName }).click();
      await expect(page).toHaveScreenshot(fileName, { animations: "disabled", fullPage: true });
    });
  }

  test("analysis Switching data 1280 dark", async ({ page }) => {
    await prepare(page, {
      theme: "dark",
      width: 1280,
      height: 820,
      scenario: apiScenario({
        analysis: familyAnalysis("target_switching", "multimodal"),
        analysisData: familySummaryData("switching"),
        analysisFamilyData: ANALYSIS_FAMILY_SWITCHING,
      }),
    });
    await page.goto("/analysis/42");
    await closeCoachOverlay(page);
    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.locator("#family-detail-title")).toBeVisible();
    await expect(page).toHaveScreenshot("analysis-data-switching-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("analysis Flicking data 960 light has no horizontal overflow", async ({ page }) => {
    await prepare(page, {
      theme: "light",
      width: 960,
      height: 640,
      scenario: apiScenario({
        analysis: familyAnalysis("static_clicking", "input_native"),
        analysisData: familySummaryData("flicking"),
        analysisFamilyData: ANALYSIS_FAMILY_FLICKING,
      }),
    });
    await page.goto("/analysis/42");
    await closeCoachOverlay(page);
    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.locator("#family-detail-title")).toBeVisible();
    await expect(page.getByRole("heading", { name: "逐次 Flick" })).toBeVisible();
    await expect(page.getByText("路径质量分布", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(960);
    await expect(page).toHaveScreenshot("analysis-data-flicking-960-light.png", { animations: "disabled", fullPage: true });
  });

  test("settings 1280 dark desktop", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "dark", width: 1280, height: 820 });
    await page.goto("/settings");
    await expect(page.locator(".task6-settings-nav-title", { hasText: "设置" })).toBeVisible();
    await expect(page).toHaveScreenshot("settings-1280-dark.png", { animations: "disabled", fullPage: true });
  });

  test("history 960 system", async ({ page }) => {
    await prepare(page, { desktop: true, theme: "system", width: 960, height: 640 });
    await page.goto("/history");
    await expect(page.locator(".task4-page-title", { hasText: "历史" })).toBeVisible();
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

  test("analysis registry-backed 960 light", async ({ page }) => {
    await prepare(page, {
      theme: "light",
      width: 960,
      height: 640,
      scenario: apiScenario({ analysis: registryBackedAnalysisSession() }),
    });
    await page.goto("/analysis/42");
    await expect(page.getByText("候选解释", { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("analysis-registry-960-light.png", { animations: "disabled", fullPage: true });
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
