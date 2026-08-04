import { expect, test } from "@playwright/test";

import {
  ANALYSIS_DATA_TRACKING,
  ANALYSIS_FAMILY_TRACKING,
  apiScenario,
  analysisSession,
  installApiFixtures,
  registryBackedAnalysisSession,
} from "../fixtures/task7-fixtures";

function trackingAnalysis() {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") throw new Error("tracking fixture requires v2");
  return analysisSession({
    analysis_type: "tracking",
    input_mode: "multimodal",
    result: {
      ...base.result,
      analysis_type: "tracking",
      input_mode: "multimodal",
      input_snapshot: {
        ...base.result.input_snapshot,
        scenario_resolution: {
          ...base.result.input_snapshot.scenario_resolution!,
          aim_family: "continuous_tracking",
        },
      },
    },
  });
}

function seekableAnalysis() {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2" || !base.history) throw new Error("seekable fixture requires v2 history");
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

test.describe("Task 7 accessibility", () => {
  test("landmarks, skip link, focus order, and visible focus are keyboard-operable", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/analyze");
    await expect(page.getByRole("banner")).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("navigation")).toHaveCount(2);

    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "跳到主要内容" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    const toolbarLink = page.getByRole("link", { name: "历史" });
    await toolbarLink.focus();
    const outline = await toolbarLink.evaluate((element) => {
      const style = getComputedStyle(element);
      return { width: Number.parseFloat(style.outlineWidth), style: style.outlineStyle };
    });
    expect(outline.width).toBeGreaterThanOrEqual(2);
    expect(outline.style).not.toBe("none");
  });

  test("desktop pointer target sizes follow the approved 40/36/32 contract", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/settings");
    const failures = await page.evaluate(() => {
      const checks = [
        { selector: ".ac-button:not([data-size='compact'])", minimum: 40 },
        { selector: ".ac-button[data-size='compact']", minimum: 36 },
        { selector: ".task3-toolbar a, .task3-toolbar button", minimum: 36 },
        { selector: ".ac-icon-button", minimum: 32 },
      ];
      return checks.flatMap(({ selector, minimum }) =>
        Array.from(document.querySelectorAll<HTMLElement>(selector)).flatMap((element) => {
          const rect = element.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0 || rect.height >= minimum) return [];
          return [{ selector, minimum, height: rect.height, label: element.getAttribute("aria-label") ?? element.textContent?.trim() ?? "" }];
        }),
      );
    });
    expect(failures).toEqual([]);
  });

  test("Coach overlay traps focus and closes with Escape", async ({ page }) => {
    await page.setViewportSize({ width: 960, height: 640 });
    await installApiFixtures(page);
    await page.goto("/history");
    await page.getByRole("button", { name: "Coach" }).click();
    const dialog = page.getByRole("dialog", { name: "Coach" });
    await expect(dialog).toBeVisible();
    const close = dialog.getByRole("button", { name: "关闭 Coach" });
    await expect(close).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "Coach" })).toBeFocused();
  });

  test("live regions, reduced motion, and 200 percent equivalent layout remain usable", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await installApiFixtures(page);
    await page.goto("/tasks");
    await expect(page.locator('[aria-live="polite"]')).not.toHaveCount(0);
    const transitionDurations = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>(".ac-button, .task3-task-row"))
        .filter((element) => element.getBoundingClientRect().width > 0)
        .map((element) => getComputedStyle(element).transitionDuration),
    );
    expect(transitionDurations.every((duration) => duration === "0s")).toBe(true);

    await page.setViewportSize({ width: 640, height: 410 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(page.getByRole("heading", { name: "任务状态" })).toBeVisible();
  });

  test("onboarding dropdowns expose listbox state and remain contained at the narrow desktop size", async ({ page }) => {
    await page.setViewportSize({ width: 960, height: 640 });
    await installApiFixtures(page, apiScenario({
      providerStatus: { profile_id: null, configured: false, status: "unconfigured", message: "No provider configured" },
    }));
    await page.goto("/onboarding");

    const provider = page.getByRole("button", { name: "选择 Provider" });
    await provider.focus();
    await page.keyboard.press("ArrowDown");
    await expect(provider).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("listbox", { name: "Provider 选项" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(provider).toHaveAttribute("aria-expanded", "false");

    await provider.click();
    await page.getByRole("listbox", { name: "Provider 选项" }).getByRole("option", { name: /自定义 Provider/ }).click();
    await page.getByPlaceholder("https://provider.example/v1").fill("https://provider.example/v1");
    await page.locator('input[type="password"]').fill("custom-secret");
    const model = page.getByRole("button", { name: "选择 Model" });
    await expect(model).toBeEnabled();
    await model.click();
    await expect(page.getByRole("listbox", { name: "Model 选项" })).toBeVisible();
    await expect(page.locator(".task3-onboarding-status")).toHaveAttribute("aria-live", "polite");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });

  test("video controls and chart text alternatives are exposed", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      analysis: trackingAnalysis(),
      analysisData: ANALYSIS_DATA_TRACKING,
      analysisFamilyData: ANALYSIS_FAMILY_TRACKING,
    }));
    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.getByRole("img", { name: /目标相对误差半径分布，共 3 个样本，峰值 0.8/ })).toBeVisible();
    await expect(page.getByText(/共 3 个样本，峰值 0.8/)).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ analysis: seekableAnalysis() }));
    await page.reload();
    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.getByRole("region", { name: "视频证据播放器" })).toBeVisible();
    await expect(page.getByRole("button", { name: "▶" })).toBeVisible();
    const timeline = page.getByRole("slider", { name: "分析时间轴" });
    await timeline.focus();
    await expect(timeline).toBeFocused();
    const before = Number(await timeline.inputValue());
    await timeline.press("ArrowRight");
    expect(Number(await timeline.inputValue())).toBeGreaterThan(before);
  });

  test("registry-backed diagnosis remains operable without compact-layout overflow", async ({ page }) => {
    await page.setViewportSize({ width: 960, height: 640 });
    await installApiFixtures(page, apiScenario({ analysis: registryBackedAnalysisSession() }));
    await page.goto("/analysis/42");
    await expect(page.getByText("候选解释", { exact: true })).toBeVisible();
    await expect(page.getByText("规则化练习建议", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看证据" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "查看指标" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "问 Coach" }).first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
