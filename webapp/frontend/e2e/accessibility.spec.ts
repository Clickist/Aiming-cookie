import { expect, test } from "@playwright/test";

import {
  apiScenario,
  installApiFixtures,
  partialAnalysisSession,
  registryBackedAnalysisSession,
} from "../fixtures/task7-fixtures";

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
    const close = dialog.getByRole("button", { name: "Close" });
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
    await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible();
  });

  test("video controls and chart text alternatives are exposed", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: partialAnalysisSession() }));
    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.getByRole("group", { name: "按已验证事件种类统计的分布图" })).toBeVisible();
    await expect(page.getByText("文本摘要", { exact: false }).first()).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario());
    await page.reload();
    await page.getByRole("tab", { name: "视频" }).click();
    const video = page.locator("video[controls]");
    await expect(video).toBeVisible();
    await video.focus();
    await expect(video).toBeFocused();
    const timeline = page.getByRole("slider", { name: "分析时间轴" });
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
    await expect(page.getByRole("button", { name: "查看证据" })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看指标" })).toBeVisible();
    await expect(page.getByRole("button", { name: "问 Coach" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
