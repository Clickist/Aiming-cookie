import { expect, test } from "@playwright/test";

import {
  apiScenario,
  analysisSession,
  installApiFixtures,
} from "../fixtures/task7-fixtures";

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
    await page.goto("/");
    await expect(page.getByRole("banner")).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("navigation")).toHaveCount(1);

    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "跳到主要内容" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    const historyButton = page.getByRole("button", { name: "训练历史" });
    await historyButton.focus();
    const outline = await historyButton.evaluate((element) => {
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

  test("Coach workspace keeps the composer and session actions keyboard-operable", async ({ page }) => {
    await page.setViewportSize({ width: 1180, height: 720 });
    await installApiFixtures(page);
    await page.goto("/");
    const composer = page.locator("#coach-draft");
    await composer.focus();
    await expect(composer).toBeFocused();
    await composer.fill("帮我解释这次训练");
    await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
    const newConversation = page.getByRole("button", { name: "新建对话" });
    await newConversation.focus();
    await expect(newConversation).toBeFocused();
  });

  test("History sends selected Analysis context to Coach without opening an Analysis page", async ({ page }) => {
    await page.setViewportSize({ width: 1180, height: 720 });
    await installApiFixtures(page);
    await page.goto("/history");
    await page.getByRole("checkbox", { name: /选择分析/ }).check();
    await page.getByRole("button", { name: /引用所选分析/ }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("#coach-draft")).toBeVisible();
  });

  test("live regions, reduced motion, and the supported desktop width remain usable", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await installApiFixtures(page);
    await page.setViewportSize({ width: 1180, height: 720 });
    await page.goto("/");
    await expect(page.locator('[aria-live="polite"]')).not.toHaveCount(0);
    const transitionDurations = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>(".ac-button, .task7-session-rail button, .task6-composer-send"))
        .filter((element) => element.getBoundingClientRect().width > 0)
        .map((element) => getComputedStyle(element).transitionDuration),
    );
    expect(transitionDurations.every((duration) => duration === "0s")).toBe(true);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(page.getByText("Aiming Coach", { exact: true })).toBeVisible();
  });

  test("onboarding dropdowns expose listbox state at the supported desktop width", async ({ page }) => {
    await page.setViewportSize({ width: 1180, height: 720 });
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

  test("Coach chart alternatives and video controls are exposed", async ({ page }) => {
    const keyWarnings: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && message.text().includes('unique "key" prop')) {
        keyWarnings.push(message.text());
      }
    });
    await installApiFixtures(page, apiScenario({ analysis: seekableAnalysis() }));
    await page.route("**/api/coach/primary*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread: { id: 1, user_id: "dev", kind: "primary", created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z" },
          messages: [{
            id: 1,
            role: "assistant",
            content: "结合时间线和视频看这次减速。",
            created_at: "2026-08-10T00:00:00Z",
            legacy_session_id: null,
            cards: [
              { schema_version: "coach_message_card.v1", kind: "timeline", analysis_ref: "analysis:42", target_ref: null, time_range_ms: null },
              { schema_version: "coach_message_card.v1", kind: "evidence", analysis_ref: "analysis:42", target_ref: null, time_range_ms: [1200, 1800] },
            ],
          }],
          refs: [],
        }),
      });
    });
    await page.goto("/");
    await expect(page.getByRole("img", { name: /事件时间线/ })).toBeVisible();
    await page.getByRole("button", { name: "在视频中查看" }).click();
    await expect(page.getByRole("region", { name: "视频证据播放器" })).toBeVisible();
    await expect(page.getByRole("button", { name: "▶" })).toBeVisible();
    const timeline = page.getByRole("slider", { name: "分析时间轴" });
    await timeline.focus();
    await expect(timeline).toBeFocused();
    await timeline.press("Home");
    const before = Number(await timeline.inputValue());
    await timeline.press("ArrowRight");
    expect(Number(await timeline.inputValue())).toBeGreaterThan(before);
    expect(keyWarnings).toEqual([]);
  });

  test("Settings keeps an explicit keyboard-operable exit at the supported width", async ({ page }) => {
    await page.setViewportSize({ width: 1180, height: 720 });
    await installApiFixtures(page);
    await page.goto("/settings");
    const exit = page.getByRole("button", { name: "退出设置" });
    await exit.focus();
    await expect(exit).toBeFocused();
    await exit.click();
    await expect(page).toHaveURL(/\/$/);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
