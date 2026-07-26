import { chromium, expect, test, type Page } from "@playwright/test";

import {
  RUN_MULTIMODAL,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
}

test.describe("release interaction polish", () => {
  test("Coach width presets, pointer drag, and keyboard resize share the workspace width", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await installApiFixtures(page);
    await page.goto("/history");

    const sidebar = page.getByRole("complementary", { name: "Coach" });
    const separator = page.getByRole("separator", { name: "调整 Coach 宽度" });
    await expect(sidebar).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await sidebar.getByRole("button", { name: "宽", exact: true }).click();
    await expect(sidebar).toHaveCSS("width", "480px");
    await expect(separator).toHaveAttribute("aria-valuenow", "480");
    await expectNoHorizontalOverflow(page);

    await sidebar.getByRole("button", { name: "默认", exact: true }).click();
    const handle = await separator.boundingBox();
    expect(handle).not.toBeNull();
    await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + 100);
    await page.mouse.down();
    await page.mouse.move(handle!.x + handle!.width / 2 - 48, handle!.y + 100, { steps: 4 });
    await page.mouse.up();
    await expect(separator).toHaveAttribute("aria-valuenow", "408");
    await expect(sidebar).toHaveCSS("width", "408px");

    await separator.press("ArrowLeft");
    await expect(separator).toHaveAttribute("aria-valuenow", "392");
    await expect(sidebar).toHaveCSS("width", "392px");
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 960, height: 640 });
    await page.getByRole("button", { name: "Coach" }).click();
    const overlayDialog = page.getByRole("dialog", { name: "Coach" });
    const overlay = page.locator(".ac-drawer");
    await expect(overlayDialog).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expect(overlay).toHaveAttribute("data-state", "open");
    await expect(overlay).toHaveCSS("transition-duration", "0.2s");

    await overlayDialog.getByRole("button", { name: "Close" }).click();
    await expect(overlay).toHaveAttribute("data-state", "closed");
    await expect(overlay).toHaveCount(0, { timeout: 500 });
  });

  test("History overlays use focus-safe Dialog and Drawer primitives", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_MULTIMODAL] }));
    await page.goto("/history");

    const confirmTrigger = page.getByRole("button", { name: "确认并分析" });
    await confirmTrigger.click();
    const confirmDialog = page.getByRole("dialog", { name: "确认这条 Run" });
    await expect(confirmDialog.getByRole("button", { name: "Close" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(confirmDialog).toBeHidden();
    await expect(confirmTrigger).toBeFocused();

    const inspectTrigger = page.getByRole("button", { name: "查看 Run" });
    await inspectTrigger.click();
    const inspector = page.getByRole("dialog", { name: /Run 详情/ });
    await expect(inspector.getByRole("button", { name: "Close" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(inspector).toBeHidden();
    await expect(inspectTrigger).toBeFocused();

    const detailTrigger = page.getByRole("button", { name: "查看摘要" });
    await detailTrigger.click();
    const detailDialog = page.getByRole("dialog", { name: "分析摘要" });
    await expect(detailDialog.getByRole("button", { name: "Close" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(detailDialog).toBeHidden();
    await expect(detailTrigger).toBeFocused();
  });

  test("Analysis Tabs implement roving focus and tabpanel relationships without decorative motion", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/analysis/42");

    const diagnosis = page.getByRole("tab", { name: "诊断" });
    const video = page.getByRole("tab", { name: "视频" });
    const data = page.getByRole("tab", { name: "数据" });
    const panel = page.getByRole("tabpanel");

    await expect(diagnosis).toHaveAttribute("tabindex", "0");
    await expect(video).toHaveAttribute("tabindex", "-1");
    await expect(data).toHaveAttribute("tabindex", "-1");
    await expect(diagnosis).toHaveAttribute("aria-controls", "analysis-view-panel");

    await diagnosis.focus();
    await diagnosis.press("ArrowRight");
    await expect(video).toBeFocused();
    await expect(video).toHaveAttribute("aria-selected", "true");
    await expect(panel).toHaveAttribute("aria-labelledby", "analysis-view-tabs-video-tab");

    await video.press("End");
    await expect(data).toBeFocused();
    await data.press("Home");
    await expect(diagnosis).toBeFocused();
    await diagnosis.press("ArrowLeft");
    await expect(data).toBeFocused();
  });

  test("Toast has a keyboard close action, auto-dismisses, and reduced motion is immediate", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await installApiFixtures(page);
    await page.goto("/history");

    const removeContext = page.locator('[aria-label^="移除 "]');
    await removeContext.click();
    const toast = page.getByRole("status").filter({ has: page.getByRole("button", { name: "关闭通知" }) });
    await expect(toast).toBeVisible();
    const closeToast = toast.getByRole("button", { name: "关闭通知" });
    await closeToast.focus();
    await closeToast.press("Enter");
    await expect(toast).toBeHidden();

    await removeContext.click();
    await expect(toast).toBeVisible();
    await expect(toast).toBeHidden({ timeout: 6_000 });

    await page.goto("/onboarding");
    const step = page.locator(".task3-onboarding-step");
    await expect(step).toHaveCSS("animation-duration", "0.18s");
    await step.evaluate((element) => element.setAttribute("data-test-step-instance", "provider"));
    await page.getByRole("button", { name: "继续", exact: true }).click();
    await expect(step).not.toHaveAttribute("data-test-step-instance", "provider");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.reload();
    await expect(page.locator(".task3-onboarding-step")).toHaveCSS("animation-duration", "0s");
    const primary = page.locator('.ac-button[data-variant="primary"]').first();
    await primary.hover();
    const box = await primary.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await expect(primary).toHaveCSS("transform", "none");
    await page.mouse.up();
  });
});

test("real Tauri WebView preserves Coach resizing and all three responsive modes", async () => {
  const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
  test.skip(!cdpUrl, "requires an isolated Tauri smoke instance and its CDP endpoint");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.unrouteAll({ behavior: "wait" });
  await installApiFixtures(page!);
  await page!.addInitScript(() => {
    localStorage.setItem("aiming-cookie.ui.coach-open", "open");
    localStorage.setItem("aiming-cookie.ui.coach-width", "360");
  });

  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.goto("http://localhost:3105/history");
  const sidebar = page!.getByRole("complementary", { name: "Coach" });
  const separator = page!.getByRole("separator", { name: "调整 Coach 宽度" });
  await expect(sidebar).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  const handle = await separator.boundingBox();
  expect(handle).not.toBeNull();
  await page!.mouse.move(handle!.x + handle!.width / 2, handle!.y + 100);
  await page!.mouse.down();
  await page!.mouse.move(handle!.x + handle!.width / 2 - 48, handle!.y + 100, { steps: 4 });
  await page!.mouse.up();
  await expect(separator).toHaveAttribute("aria-valuenow", "408");
  await expect(sidebar).toHaveCSS("width", "408px");

  await sidebar.getByRole("button", { name: "宽", exact: true }).click();
  await separator.press("ArrowLeft");
  await expect(separator).toHaveAttribute("aria-valuenow", "464");
  await expect(sidebar).toHaveCSS("width", "464px");
  await expectNoHorizontalOverflow(page!);

  await page!.setViewportSize({ width: 960, height: 640 });
  const responsiveDrawer = page!.locator(".task6-coach-drawer");
  await expect(responsiveDrawer).toHaveAttribute("data-mode", "overlay");
  await expect(responsiveDrawer.locator(".ac-drawer")).toHaveAttribute("data-state", "open");
  await expect(responsiveDrawer.locator(".ac-drawer")).toHaveCSS("transition-duration", "0.2s");
  await expectNoHorizontalOverflow(page!);

  await page!.setViewportSize({ width: 720, height: 640 });
  await expect(responsiveDrawer).toHaveAttribute("data-mode", "full");
  await expect(responsiveDrawer.getByRole("button", { name: "← 返回主工作区" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  await page!.emulateMedia({ reducedMotion: "reduce" });
  await expect(responsiveDrawer.locator(".ac-drawer")).toHaveCSS("transition-duration", "0s");
  const screenshotPath = process.env.AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT;
  if (screenshotPath) await page!.screenshot({ path: screenshotPath });
});
