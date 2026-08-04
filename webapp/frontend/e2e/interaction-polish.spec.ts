import { chromium, expect, test, type Page } from "@playwright/test";

import {
  ANALYSIS_DATA,
  ANALYSIS_FAMILY_FLICKING,
  ANALYSIS_FAMILY_SWITCHING,
  ANALYSIS_FAMILY_TRACKING,
  CURRENT_TRAINING_NO_PLAN,
  CURRENT_TRAINING_PAUSED,
  COACH_CONTEXTS,
  RUN_MULTIMODAL,
  RUN_NATIVE,
  TASKS,
  apiScenario,
  analysisSession,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
}

function familyAnalysis(
  family: "static_clicking" | "target_switching",
  inputMode: "input_native" | "multimodal",
) {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") {
    throw new Error("family WebView fixture requires v2");
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
  const kinds = family === "switching"
    ? [{ kind: "switch_chain", count: 2 }, { kind: "settle", count: 2 }]
    : [{ kind: "peak", count: 1 }, { kind: "corrective", count: 2 }];
  return {
    ...ANALYSIS_DATA,
    event_markers: kinds.map(({ kind }, index) => ({
      event_ref: `event:${family}:${index + 1}`,
      kind,
      relative_ms: family === "switching" ? 1200 + index * 230 : 2478 + index * 42,
    })),
    event_distribution: kinds,
    target_relative_error_radius: {
      availability: "unavailable" as const,
      reason: "target_relative_samples_unavailable",
      points: [],
    },
  };
}

test.describe("release interaction polish", () => {
  test("navigation active state, disabled Coach tooltip, and task dot preserve toolbar geometry", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await installApiFixtures(page, apiScenario({ tasks: [TASKS[2]] }));
    await page.goto("/settings");

    const coach = page.locator(".task3-toolbar-action");
    await expect(coach).toBeDisabled();
    await expect(coach.locator("..")).toHaveAttribute("title", "当前页面不支持 Coach");
    await expect(page.getByRole("link", { name: "设置" })).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".task3-task-nav-dot")).toHaveCount(1);

    const toolbarActions = page.locator(".task3-tool-nav > *");
    const before = await toolbarActions.evaluateAll((nodes) => nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return [rect.left, rect.top, rect.width, rect.height];
    }));
    await page.goto("/tasks");
    const after = await toolbarActions.evaluateAll((nodes) => nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return [rect.left, rect.top, rect.width, rect.height];
    }));
    expect(after).toEqual(before);
    await expect(page.locator('a[href="/tasks"]')).toHaveAttribute("aria-current", "page");
  });

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

  test("History query Run ref selects the matching pending Run in Analyze", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_MULTIMODAL, RUN_NATIVE] }));
    await page.goto(`/analyze?run=${encodeURIComponent(RUN_NATIVE.run_ref)}`);

    await expect(page.locator('input[name="run"]').nth(1)).toBeChecked();
    await expect(page.locator(".task3-run-item[data-selected]")).toContainText(
      RUN_NATIVE.scenario ?? "未知场景",
    );

    await page.goto("/analyze?run=run%3Amissing");
    await expect(page.locator('input[name="run"]:checked')).toHaveCount(0);
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

  test("Coach locator is acknowledged only by the active Analysis workspace", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/history");
    const contextButton = page.locator(".task6-context-chip > button").first();
    await expect(contextButton).toHaveText(COACH_CONTEXTS.contexts[0]!.label);
    await contextButton.click();
    const feedback = page.locator(".ac-toast__body");
    await expect(feedback).toHaveText("未能定位，请重试。");
    await expect(feedback).not.toHaveText("已定位");

    await page.goto("/analysis/42");
    await expect(page.getByRole("tab", { name: "诊断" })).toBeVisible();
    await expect(contextButton).toHaveText(COACH_CONTEXTS.contexts[0]!.label);
    await contextButton.click();
    await expect(feedback).toHaveText("已定位");
    const acknowledged = await page.evaluate(() => !window.dispatchEvent(new CustomEvent(
      "aiming-cookie:coach-locate",
      { cancelable: true, detail: { view: "video", relative_start_ms: 500 } },
    )));
    expect(acknowledged).toBe(true);
    await expect(page.getByRole("tab", { name: "视频" })).toHaveAttribute("aria-selected", "true");

    const invalid = await page.evaluate(() => window.dispatchEvent(new CustomEvent(
      "aiming-cookie:coach-locate",
      { cancelable: true, detail: { view: "unknown", relative_start_ms: -1 } },
    )));
    expect(invalid).toBe(true);

    await page.goto("/history");
    const afterUnmount = await page.evaluate(() => window.dispatchEvent(new CustomEvent(
      "aiming-cookie:coach-locate",
      { cancelable: true, detail: { view: "video", relative_start_ms: 500 } },
    )));
    expect(afterUnmount).toBe(true);
  });

  test("Coach keeps current training readable and turns safe shortcuts into drafts", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await page.addInitScript(() => localStorage.setItem("aiming-cookie.ui.coach-open", "open"));
    await installApiFixtures(page);
    await page.goto("/history");

    const training = page.getByRole("region", { name: "当前训练" });
    await expect(training).toContainText("1wall 6targets small");
    await expect(training).toContainText("练什么");
    await expect(training).toContainText("复测");
    await training.getByRole("button", { name: /查看训练项目/ }).click();
    await expect(training.locator(".task6-training-item")).toHaveCount(3);

    await training.getByRole("button", { name: "问 Coach" }).first().click();
    const draft = page.locator("#coach-draft");
    await expect(draft).toHaveValue(/1wall 6targets small/);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("aiming-cookie:coach-kovaak-intent", {
      detail: { kind: "kovaak_score_item", item_name: "controlsphere" },
    })));
    await expect(draft).toHaveValue(/controlsphere/);

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ currentTraining: CURRENT_TRAINING_PAUSED }));
    await page.reload();
    await expect(page.getByRole("region", { name: "当前训练" })).toContainText("已暂停");

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ currentTraining: CURRENT_TRAINING_NO_PLAN }));
    await page.reload();
    await expect(page.getByRole("region", { name: "当前训练" })).toContainText("还没有当前训练安排");

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({
      providerStatus: { profile_id: 1, configured: true, status: "connection_failed", message: "Provider unavailable" },
    }));
    await page.reload();
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeHidden();
    await page.getByRole("button", { name: "Coach" }).click();
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.getByRole("region", { name: "当前训练" })).toContainText("1wall 6targets small");
    await page.getByRole("region", { name: "当前训练" }).getByRole("button", { name: /查看训练项目/ }).click();
    await expect(page.getByRole("region", { name: "当前训练" }).getByRole("button", { name: "问 Coach" }).first()).toBeDisabled();

    await page.setViewportSize({ width: 960, height: 640 });
    await expect(page.getByRole("dialog", { name: "Coach" })).toBeHidden();
    await page.getByRole("button", { name: "Coach" }).click();
    await expect(page.getByRole("dialog", { name: "Coach" })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Coach" }).getByRole("region", { name: "当前训练" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("KovaaK score actions carry a safe item name into a Coach draft", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await installApiFixtures(page);
    let coachRunPosts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/coach/agent-runs") {
        coachRunPosts += 1;
      }
    });
    await page.goto("/settings");

    const scoreRow = page.locator(".kovaak-score-row").filter({ hasText: "controlsphere" }).first();
    await scoreRow.getByRole("button", { name: "让 Coach 看看" }).click();
    await expect(page).toHaveURL(/\/history$/);
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.locator("#coach-draft")).toHaveValue(/controlsphere/);
    expect(coachRunPosts).toBe(0);
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
  const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";
  test.skip(!cdpUrl, "requires an isolated Tauri smoke instance and its CDP endpoint");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().startsWith("http://localhost:")) ?? pages[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.unrouteAll({ behavior: "wait" });
  await installApiFixtures(page!);
  await page!.addInitScript(() => {
    localStorage.setItem("aiming-cookie.ui.coach-open", "open");
    localStorage.setItem("aiming-cookie.ui.coach-width", "360");
  });

  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.goto(new URL("/history", appUrl).toString());
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

test("real Tauri WebView renders the approved frontend realization matrix", async () => {
  const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
  const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";
  test.skip(!cdpUrl, "requires an isolated Tauri smoke instance and its CDP endpoint");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().startsWith("http://localhost:")) ?? pages[0];
  expect(page, "Tauri WebView page").toBeDefined();

  const useScenario = async (scenario = apiScenario()) => {
    await page!.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page!, scenario);
  };
  const setTheme = async (theme: "light" | "dark") => {
    await page!.evaluate((value) => localStorage.setItem("aiming-cookie.ui.theme", value), theme);
  };
  const closeCoach = async () => {
    await page!.evaluate(() => localStorage.setItem("aiming-cookie.ui.coach-open", "closed"));
  };

  await page!.setViewportSize({ width: 1280, height: 820 });
  await useScenario();
  await setTheme("light");
  await page!.goto(new URL("/onboarding", appUrl).toString());
  await expect(page!.getByRole("heading", { name: "连接模型服务" })).toBeVisible();
  await page!.getByRole("button", { name: "继续", exact: true }).click();
  await expect(page!.getByRole("heading", { name: "连接 KovaaK 成绩" })).toBeVisible();
  await expect(page!.getByRole("region", { name: "KovaaK 成绩连接" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  await setTheme("dark");
  await page!.goto(new URL("/settings", appUrl).toString());
  await expect(page!.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page!.getByRole("heading", { name: "KovaaK 成绩" })).toBeVisible();
  await expect(page!.locator(".kovaak-stage-summary")).toContainText("Easier");
  await expect(page!.locator(".kovaak-stage-summary")).toContainText("Medium");
  await expectNoHorizontalOverflow(page!);

  await closeCoach();
  await useScenario(apiScenario({ analysisFamilyData: ANALYSIS_FAMILY_TRACKING }));
  await page!.goto(new URL("/analysis/42", appUrl).toString());
  const analysisTabs = page!.getByRole("tab");
  await expect(analysisTabs).toHaveCount(3);
  await expect(analysisTabs.nth(0)).toHaveText("诊断");
  await expect(analysisTabs.nth(1)).toHaveText("视频");
  await expect(analysisTabs.nth(2)).toHaveText("数据");
  await analysisTabs.nth(2).click();
  await expect(page!.getByRole("heading", { name: "跟踪分段" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  await page!.evaluate(() => localStorage.setItem("aiming-cookie.ui.coach-open", "open"));
  await useScenario();
  await page!.goto(new URL("/history", appUrl).toString());
  const currentTraining = page!.getByRole("region", { name: "当前训练" });
  await expect(currentTraining).toBeVisible();
  for (const label of ["练什么", "练多少", "注意", "观察", "复测"]) {
    await expect(currentTraining.getByText(label, { exact: true })).toBeVisible();
  }

  await page!.setViewportSize({ width: 960, height: 640 });
  await setTheme("light");
  await closeCoach();
  await useScenario(apiScenario({
    analysis: familyAnalysis("target_switching", "multimodal"),
    analysisData: familySummaryData("switching"),
    analysisFamilyData: ANALYSIS_FAMILY_SWITCHING,
  }));
  await page!.goto(new URL("/analysis/42", appUrl).toString());
  await page!.getByRole("tab", { name: "数据" }).click();
  await expect(page!.getByRole("heading", { name: "切换链" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  await useScenario(apiScenario({
    analysis: familyAnalysis("static_clicking", "input_native"),
    analysisData: familySummaryData("flicking"),
    analysisFamilyData: ANALYSIS_FAMILY_FLICKING,
  }));
  await page!.goto(new URL("/analysis/42", appUrl).toString());
  await page!.getByRole("tab", { name: "数据" }).click();
  await expect(page!.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page!.getByRole("heading", { name: "逐次 Flick" })).toBeVisible();
  await expect(page!.getByText("速度峰值", { exact: true })).toBeVisible();
  await expect(page!.getByText("修正动作", { exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page!);
});
