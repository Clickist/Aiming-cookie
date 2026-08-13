import { expect, test } from "@playwright/test";

import {
  PRODUCT_STATE,
  READY_PROVIDER_STATUS,
  RUN_NATIVE,
  RUN_PENDING_MULTIMODAL,
  KOVAAK_SCORES_AVAILABLE,
  analysisSession,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

const CUSTOM_PROVIDER_OPTION = "自定义 Provider 填写 URL 和 API key 后自动识别接口";

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

test.describe("Task 7 browser smoke", () => {
  test("startup redirects to mandatory onboarding before the Coach workspace", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      productState: { ...PRODUCT_STATE, onboarding_completed: false, onboarding_completion_kind: null },
    }));
    await page.goto("/");
    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("heading", { name: "连接模型服务", exact: true })).toBeVisible();
  });

  test("onboarding exposes accessible dropdowns and custom Provider fields without a skip path", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
    }));
    await page.goto("/onboarding");
    const provider = page.getByRole("button", { name: "选择 Provider" });
    await expect(provider).toHaveAttribute("aria-haspopup", "listbox");
    await expect(provider).toHaveAttribute("aria-expanded", "false");
    await provider.click();
    await expect(provider).toHaveAttribute("aria-expanded", "true");
    await page.getByRole("option", { name: CUSTOM_PROVIDER_OPTION }).click();
    await expect(page.getByText("Base URL", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "读取可用模型" })).toHaveCount(0);

    const skip = page.getByRole("button", { name: "暂时不连接" });
    await expect(skip).toHaveCount(0);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "测试连接" })).toBeDisabled();
  });

  test("custom Provider remains available when the built-in catalog is unavailable", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
      failures: { "GET /api/providers/catalog": 503 },
    }));
    await page.goto("/onboarding");
    let createdProfile: Record<string, unknown> | null = null;
    page.on("request", (request) => {
      if (request.method() !== "POST" || new URL(request.url()).pathname !== "/api/provider-profiles") return;
      createdProfile = request.postDataJSON() as Record<string, unknown>;
    });

    const provider = page.getByRole("button", { name: "选择 Provider" });
    await expect(provider).toBeEnabled();
    await provider.click();
    await page.getByRole("option", { name: CUSTOM_PROVIDER_OPTION }).click();
    await expect(page.getByText("Provider 目录暂时不可用，请稍后重试。", { exact: true })).toBeHidden();

    await page.getByPlaceholder("https://provider.example/v1").fill("https://provider.example/v1");
    await page.locator('input[type="password"]').fill("custom-secret");
    const model = page.getByRole("button", { name: "选择 Model" });
    await expect(model).toBeVisible();
    await expect(page.getByText("接口协议", { exact: true })).toHaveCount(0);
    await model.click();
    await page.getByRole("option", { name: "custom-model-a" }).click();
    await expect(page.getByRole("button", { name: "测试连接" })).toBeEnabled();
    await page.getByRole("button", { name: "测试连接" }).click();
    await expect.poll(() => createdProfile).not.toBeNull();
    expect(createdProfile).toMatchObject({
      model_id: "custom-model-a",
      context_window: 32768,
      max_tokens: 4096,
    });
  });

  test("custom Provider exposes Model ID only when its model list is unavailable", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
      failures: { "POST /api/provider-profiles/custom/models": 503 },
    }));
    await page.goto("/onboarding");
    await page.getByRole("button", { name: "选择 Provider" }).click();
    await page.getByRole("option", { name: CUSTOM_PROVIDER_OPTION }).click();

    await page.getByPlaceholder("https://provider.example/v1").fill("https://provider.example/v1");
    await page.locator('input[type="password"]').fill("custom-secret");

    await expect(page.getByText("无法自动识别接口协议或读取模型列表，请选择协议后手动填写 Model ID。", { exact: true })).toBeVisible();
    await expect(page.getByText("接口协议", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "测试连接" })).toBeDisabled();
    await expect(page.getByText("Model ID", { exact: true })).toBeVisible();
  });

  test("built-in Provider preserves its API key and reuses one profile when a test is retried", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
      providerTestStatuses: [
        { profile_id: 1, configured: true, status: "connection_failed", message: "Provider unavailable" },
        { ...READY_PROVIDER_STATUS, profile_id: 1 },
      ],
      profiles: { profiles: [] },
    }));
    const profileWrites: string[] = [];
    const testedProfileIds: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path === "/api/coach/provider-profiles" && request.method() === "POST") profileWrites.push(`POST ${path}`);
      if (/^\/api\/coach\/provider-profiles\/\d+$/.test(path) && request.method() === "PUT") profileWrites.push(`PUT ${path}`);
      const testPath = /^\/api\/coach\/provider-profiles\/(\d+)\/test$/.exec(path);
      if (testPath) testedProfileIds.push(testPath[1]);
    });
    await page.goto("/onboarding");
    await page.getByRole("button", { name: "选择 Provider" }).click();
    await page.getByRole("option", { name: "OpenAI API Key / OAuth / 设备码" }).click();

    const apiKey = page.locator('input[type="password"]');
    const model = page.getByRole("button", { name: "选择 Model" });
    await expect(page.getByText("Model", { exact: true })).toHaveCount(0);
    await apiKey.fill("builtin-secret");
    await expect(model).toBeVisible();

    await model.click();
    await page.getByRole("option", { name: "GPT-5.4" }).click();
    await expect(apiKey).toHaveValue("builtin-secret");

    const testConnection = page.getByRole("button", { name: "测试连接" });
    await expect(testConnection).toBeEnabled();
    await testConnection.click();
    await expect(page.getByText("Provider unavailable", { exact: true })).toBeVisible();
    await expect(apiKey).toHaveValue("builtin-secret");
    await expect(testConnection).toBeEnabled();

    await testConnection.click();
    await expect(page.getByRole("button", { name: "继续" })).toBeVisible();
    await expect(apiKey).toHaveValue("builtin-secret");
    await expect(page.getByRole("button", { name: "GPT-5.4" })).toBeVisible();
    expect(profileWrites).toEqual(["POST /api/coach/provider-profiles", "PUT /api/coach/provider-profiles/1"]);
    expect(testedProfileIds).toEqual(["1", "1"]);
  });

  test("Settings keeps custom Provider available when the built-in catalog is unavailable", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "GET /api/providers/catalog": 503 } }));
    await page.goto("/settings");

    await expect(page.getByRole("heading", { name: "添加 Provider" })).toBeVisible();
    const providerSelects = page.locator(".task6-provider-form select");
    await expect(providerSelects).toHaveCount(1);
    await providerSelects.nth(0).selectOption("custom");
    await expect(page.getByText("Base URL", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "读取可用模型" })).toHaveCount(0);
  });

  test("KovaaK scores are optional in onboarding and grouped in Settings without a stage tab", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/onboarding");
    await page.getByRole("button", { name: "继续" }).click();
    await expect(page.getByRole("heading", { name: "连接 KovaaK 成绩", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "跳过这一步" }).click();
    await expect(page.getByRole("heading", { name: "训练后自动整理证据" })).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ kovaakScores: KOVAAK_SCORES_AVAILABLE }));
    await page.goto("/settings");
    await expect(page.getByText("KovaaK 成绩", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Control Tracking/ })).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(0);
  });

  test("KovaaK connection remains manageable when only the score read fails", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "GET /api/kovaak-scores": 503 } }));
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "KovaaK 成绩连接" })).toBeVisible();
    await expect(page.getByText("KovaaK 成绩暂时无法读取，请稍后重试。")).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新成绩" })).toBeEnabled();
  });

  test("legacy Tasks and Analysis URLs redirect to History", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page);
    for (const path of ["/tasks", "/analyze", "/analysis", "/analysis/42", "/analysis?id=42"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/history$/);
      await expect(page.getByRole("heading", { name: "分析记录" })).toBeVisible();
    }
  });

  test("History keeps pending Runs, Run records, and Analysis records separate", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      runs: [RUN_PENDING_MULTIMODAL, RUN_NATIVE],
    }));
    await page.goto("/history");
    await expect(page.getByRole("heading", { name: "待分析训练" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "训练记录" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "分析记录" })).toBeVisible();
    await expect(page.locator(".task4-sec-count")).toHaveText(["1", "1", "1"]);
    await page.getByRole("button", { name: "查看摘要" }).click();
    await expect(page.getByRole("dialog", { name: "分析摘要" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Analysis/ })).toHaveCount(0);
  });

  test("History empty states remain framed and responsive", async ({ page }) => {
    await page.setViewportSize({ width: 899, height: 920 });
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [], sessions: [] }));
    await page.goto("/history");

    const statePanels = page.locator(".task4-sec > .ac-state.task4-panel.task4-state-panel");
    await expect(statePanels).toHaveCount(3);
    await expect(statePanels.nth(2).getByText("还没有分析记录", { exact: true })).toBeVisible();

    const layout = await statePanels.evaluateAll((panels) => panels.map((panel) => {
      const element = panel as HTMLElement;
      const section = element.parentElement as HTMLElement;
      const style = getComputedStyle(element);
      return {
        borderTopWidth: style.borderTopWidth,
        panelWidth: element.getBoundingClientRect().width,
        sectionWidth: section.getBoundingClientRect().width,
      };
    }));
    expect(layout.every(({ borderTopWidth }) => borderTopWidth === "1px")).toBe(true);
    expect(layout.every(({ panelWidth, sectionWidth }) => panelWidth <= sectionWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });

  test("Coach renders Analysis cards and opens video beside the conversation", async ({ page }) => {
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
            content: "我把这次减速问题和证据放在下面。",
            created_at: "2026-08-10T00:00:00Z",
            legacy_session_id: null,
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
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "关键数据" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "事件时间线" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "视频证据" })).toBeVisible();
    await page.getByRole("button", { name: "在视频中查看" }).click();
    await expect(page.getByRole("region", { name: "Coach 视频讲解" })).toBeVisible();
    await expect(page.getByRole("region", { name: "视频证据播放器" })).toBeVisible();
    await page.getByRole("button", { name: "关闭视频讲解" }).click();
    await expect(page.getByRole("region", { name: "Coach 视频讲解" })).toBeHidden();
  });

  test("Coach supports the primary conversation as the main workspace", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/");
    await expect(page.getByLabel("Coach 消息").getByText("先稳定接近目标时的减速节奏，再复测同一场景。", { exact: true })).toBeVisible();
    await expect(page.locator("#coach-draft")).toBeVisible();
    await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  });

  test("Settings exposes Provider, calibration, capture, theme, and storage without secrets", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page);
    await page.goto("/settings");
    for (const label of ["LLM Provider", "Profile", "自动采集与 Raw Input", "主题", "存储"]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByText("Stats 自动读取优先", { exact: false })).toBeVisible();
    await page.getByRole("link", { name: "存储", exact: true }).click();
    await expect(page.getByText("Run 录像", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/task7-fixture-token|C:\\Task7Fixture/)).toHaveCount(0);
  });

});
