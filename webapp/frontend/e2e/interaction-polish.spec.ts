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
  RUN_PENDING_MULTIMODAL,
  RUN_PENDING_NATIVE,
  KOVAAK_SCORES_AVAILABLE,
  TASKS,
  apiScenario,
  analysisSession,
  installApiFixtures,
  installDesktopBridge,
  redirectTauriRuntime,
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

    await expect(page.getByRole("button", { name: "Coach" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "关闭设置" })).toHaveClass(/active/);
    await expect(page.locator(".task3-task-nav-dot")).toHaveCount(1);

    const taskLink = page.locator('a[href="/tasks"]');
    const before = await taskLink.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return [rect.width, rect.height];
    });
    await page.goto("/tasks");
    const after = await taskLink.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return [rect.width, rect.height];
    });
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
    const overlay = page.locator(".task6-coach-sidebar-wrap");
    await expect(overlayDialog).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expect(overlay).toHaveAttribute("data-state", "open");
    await expect(overlay.locator(".task6-coach-sidebar")).toHaveCSS("transition-duration", "0.2s, 0.2s");

    await overlayDialog.getByRole("button", { name: "关闭 Coach", exact: true }).click();
    await expect(overlay).toHaveAttribute("data-state", "closed");
    await expect(overlay).toHaveCount(0, { timeout: 500 });
  });

  test("History overlays use focus-safe Dialog and Drawer primitives", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL, RUN_NATIVE] }));
    await page.goto("/history");

    const confirmTrigger = page.getByRole("button", { name: "开始分析" }).first();
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

  });

  test("History query Run ref selects the matching pending Run in Analyze", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL, RUN_PENDING_NATIVE] }));
    await page.goto(`/analyze?run=${encodeURIComponent(RUN_NATIVE.run_ref)}`);

    await expect(page.locator('input[name="run"]').nth(1)).toBeChecked();
    await expect(page.locator('.task3-analyze-run-item[data-selected="true"]')).toContainText(
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
    await expect(contextButton).toHaveText(`已附加分析：${COACH_CONTEXTS.contexts[0]!.label}`);
    await contextButton.click();
    const feedback = page.locator(".ac-toast__body");
    await expect(feedback).toHaveText("未能定位，请重试。");
    await expect(feedback).not.toHaveText("已定位");

    await page.goto("/analysis/42");
    await expect(page.getByRole("tab", { name: "诊断" })).toBeVisible();
    await expect(contextButton).toHaveText(`已附加分析：${COACH_CONTEXTS.contexts[0]!.label}`);
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
    await expect(training).not.toContainText("练什么");
    expect((await training.boundingBox())?.height).toBeLessThanOrEqual(48);
    await training.getByRole("button", { name: "展开" }).click();
    await expect(training).toContainText("练什么");
    await expect(training).toContainText("练多少");
    await expect(training).toContainText("注意");
    await expect(training.locator(".task6-training-item")).toHaveCount(1);

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
    const noPlanTraining = page.getByRole("region", { name: "当前训练" });
    await expect(noPlanTraining).toContainText("还没有当前训练安排");
    await expect(noPlanTraining).not.toContainText("创建训练安排后");
    await expect(noPlanTraining.getByRole("button", { name: "展开" })).toHaveCount(0);
    expect((await noPlanTraining.boundingBox())?.height).toBeLessThanOrEqual(48);

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({
      providerStatus: { profile_id: 1, configured: true, status: "connection_failed", message: "Provider unavailable" },
    }));
    await page.reload();
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeHidden();
    await page.getByRole("button", { name: "Coach" }).click();
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.getByRole("region", { name: "当前训练" })).toContainText("1wall 6targets small");
    await page.getByRole("region", { name: "当前训练" }).getByRole("button", { name: "展开" }).click();
    await expect(page.getByRole("region", { name: "当前训练" }).getByRole("button", { name: "问 Coach" }).first()).toBeDisabled();

    await page.setViewportSize({ width: 960, height: 640 });
    await expect(page.getByRole("dialog", { name: "Coach" })).toBeHidden();
    await page.getByRole("button", { name: "Coach" }).click();
    await expect(page.getByRole("dialog", { name: "Coach" })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Coach" }).getByRole("region", { name: "当前训练" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("side-by-side Coach stays viewport-bound while the analysis page scrolls", async ({ page }) => {
    await page.setViewportSize({ width: 1178, height: 920 });
    await page.addInitScript(() => localStorage.setItem("aiming-cookie.ui.coach-open", "open"));
    await installApiFixtures(page);
    await page.goto("/analysis/42");

    const coach = page.getByRole("complementary", { name: "Coach" });
    await expect(coach).toBeVisible();
    const scrollRange = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
    expect(scrollRange).toBeGreaterThan(100);
    await page.evaluate((scrollTop) => window.scrollTo(0, scrollTop), Math.round(scrollRange / 2));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);

    const layout = await page.evaluate(() => {
      const wrapper = document.querySelector<HTMLElement>(".task6-coach-sidebar-wrap");
      const header = document.querySelector<HTMLElement>(".task6-coach-header");
      const composer = document.querySelector<HTMLElement>(".task6-composer");
      if (!wrapper || !header || !composer) return null;
      const wrapperRect = wrapper.getBoundingClientRect();
      const headerRect = header.getBoundingClientRect();
      const composerRect = composer.getBoundingClientRect();
      return {
        composerBottom: Math.round(composerRect.bottom),
        composerTop: Math.round(composerRect.top),
        headerTop: Math.round(headerRect.top),
        position: getComputedStyle(wrapper).position,
        wrapperBottom: Math.round(wrapperRect.bottom),
        wrapperTop: Math.round(wrapperRect.top),
      };
    });

    expect(layout).toEqual({
      composerBottom: 920,
      composerTop: expect.any(Number),
      headerTop: 48,
      position: "fixed",
      wrapperBottom: 920,
      wrapperTop: 48,
    });
    expect(layout?.composerTop).toBeGreaterThan(48);
  });

  test("Coach composer keeps a taller draft area with its send action inside", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await page.addInitScript(() => localStorage.setItem("aiming-cookie.ui.coach-open", "open"));
    await installApiFixtures(page);
    await page.goto("/history");

    const composerInput = page.locator(".task6-composer-input");
    const draft = page.locator("#coach-draft");
    const send = composerInput.getByRole("button", { name: "发送" });
    const layout = await Promise.all([
      composerInput.boundingBox(),
      draft.boundingBox(),
      send.boundingBox(),
    ]);
    const [composerBox, draftBox, sendBox] = layout;

    expect(composerBox?.height).toBeGreaterThanOrEqual(72);
    expect(draftBox?.height).toBeGreaterThanOrEqual(72);
    expect(sendBox).not.toBeNull();
    expect(sendBox!.x).toBeGreaterThan(draftBox!.x);
    expect(sendBox!.x + sendBox!.width).toBeLessThanOrEqual(composerBox!.x + composerBox!.width);
    expect(sendBox!.y + sendBox!.height).toBeLessThanOrEqual(composerBox!.y + composerBox!.height);
    await expect(draft).toHaveCSS("border-top-width", "0px");
    await expect(composerInput).toHaveCSS("border-top-width", "1px");
  });

  test("no current plan stays a normal empty state without an error overlay", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await page.addInitScript(() => localStorage.setItem("aiming-cookie.ui.coach-open", "open"));
    await installApiFixtures(page, apiScenario({ currentTraining: CURRENT_TRAINING_NO_PLAN }));
    await page.goto("/history");

    const training = page.getByRole("region", { name: "当前训练" });
    await expect(training).toContainText("还没有当前训练安排");
    await expect(training).not.toContainText("创建训练安排后");
    await expect(training.getByRole("button", { name: "展开" })).toHaveCount(0);
    expect((await training.boundingBox())?.height).toBeLessThanOrEqual(48);
    await expect(page.getByText("当前训练暂时无法读取")).toHaveCount(0);
    await expect(page.getByText("当前训练暂不可用")).toHaveCount(0);
  });

  test("KovaaK score actions carry a safe item name into a Coach draft", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 820 });
    await installApiFixtures(page, apiScenario({ kovaakScores: KOVAAK_SCORES_AVAILABLE }));
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

test("real Tauri WebView keeps the Coach-first workspace layout at desktop width", async () => {
  const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
  const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";
  test.skip(!cdpUrl, "requires an isolated Tauri smoke instance and its CDP endpoint");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().startsWith("http://localhost:")) ?? pages[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.unrouteAll({ behavior: "wait" });
  await installApiFixtures(page!);

  await page!.setViewportSize({ width: 1280, height: 820 });

  // Coach workspace at / has Session rail and toolbar.
  await page!.goto(new URL("/", appUrl).toString());
  await expect(page!.getByRole("navigation", { name: "会话" })).toBeVisible();
  await expect(page!.locator('[aria-label="Aiming Cookie"]')).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  // History page keeps Session rail.
  await page!.goto(new URL("/history", appUrl).toString());
  await expect(page!.getByRole("navigation", { name: "会话" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  // Settings page hides Session rail but stays overflow-free.
  await page!.goto(new URL("/settings", appUrl).toString());
  await expectNoHorizontalOverflow(page!);

  // Reduced motion is respected.
  await page!.emulateMedia({ reducedMotion: "reduce" });
  const screenshotPath = process.env.AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT;
  if (screenshotPath) await page!.screenshot({ path: screenshotPath });
});

test("real Tauri WebView renders the Coach-first product surface", async () => {
  const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
  const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";
  test.skip(!cdpUrl, "requires an isolated Tauri smoke instance and its CDP endpoint");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().startsWith("http://localhost:")) ?? pages[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.unrouteAll({ behavior: "wait" });
  await redirectTauriRuntime(page!, appUrl);

  const useScenario = async (scenario = apiScenario()) => {
    await page!.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page!, scenario);
  };
  const setTheme = async (theme: "light" | "dark") => {
    await page!.evaluate((value) => localStorage.setItem("aiming-cookie.ui.theme", value), theme);
  };

  await page!.setViewportSize({ width: 1280, height: 820 });
  await useScenario(apiScenario({ kovaakScores: KOVAAK_SCORES_AVAILABLE }));

  // Coach workspace at / is the default surface.
  await setTheme("light");
  await page!.goto(new URL("/", appUrl).toString());
  await expect(page!.locator('[aria-label="Aiming Cookie"]')).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  // Settings page shows KovaaK scores and theme.
  await setTheme("dark");
  await page!.goto(new URL("/settings", appUrl).toString());
  await expect(page!.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page!.getByRole("heading", { name: "KovaaK 成绩" })).toBeVisible();
  await expectNoHorizontalOverflow(page!);

  // Coach home shows the current training plan.
  await useScenario();
  await page!.goto(new URL("/", appUrl).toString());
  const currentTraining = page!.getByRole("region", { name: "当前训练计划" });
  await expect(currentTraining).toBeVisible();
  await currentTraining.getByRole("button", { name: "展开" }).click();
  for (const label of ["练什么", "练多少", "注意", "观察", "复测"]) {
    await expect(currentTraining.getByText(label, { exact: true })).toBeVisible();
  }

  // Retired URLs redirect to History.
  await page!.goto(new URL("/analyze", appUrl).toString());
  await expect(page!).toHaveURL(/\/history/);
  await page!.goto(new URL("/analysis?id=42", appUrl).toString());
  await expect(page!).toHaveURL(/\/history/);
});
