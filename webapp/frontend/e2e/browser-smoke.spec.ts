import { expect, test } from "@playwright/test";

import {
  PRODUCT_STATE,
  READY_PROVIDER_STATUS,
  RUN_MULTIMODAL,
  RUN_NATIVE,
  RUN_PENDING_MULTIMODAL,
  RUN_PENDING_NATIVE,
  TASKS,
  KOVAAK_SCORES_AVAILABLE,
  analysisSession,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
  registryBackedAnalysisSession,
} from "../fixtures/task7-fixtures";

const CUSTOM_PROVIDER_OPTION = "自定义 Provider 填写 URL 和 API key 后自动识别接口";

test.describe("Task 7 browser smoke", () => {
  test("conditional startup routes to onboarding", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      productState: { ...PRODUCT_STATE, onboarding_completed: false, onboarding_completion_kind: null },
    }));
    await page.goto("/");
    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("heading", { name: "连接模型服务" })).toBeVisible();
  });

  test("onboarding exposes accessible dropdowns, custom Provider fields, and a skip explanation", async ({ page }) => {
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
    await skip.hover();
    await expect(page.getByRole("tooltip")).toBeVisible();
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

  test("built-in Provider reveals catalog models after its API key is supplied", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
    }));
    await page.goto("/onboarding");
    await page.getByRole("button", { name: "选择 Provider" }).click();
    await page.getByRole("option", { name: "OpenAI API Key / OAuth / 设备码" }).click();

    await expect(page.getByText("Model", { exact: true })).toHaveCount(0);
    await page.locator('input[type="password"]').fill("builtin-secret");
    await expect(page.getByText("Model", { exact: true })).toBeVisible();
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

  test("desktop Run discovery selects one Run and requires a choice for multiple Runs", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL] }));
    await page.goto("/analyze");
    await expect(page.getByText("自动采集：采集中", { exact: false })).toBeVisible();
    await expect(page.locator('input[name="run"]')).toBeChecked();
    await expect(page.locator('input[name="input-mode"]')).toHaveCount(3);
    await expect(page.getByText("预览 / 实验")).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL, RUN_PENDING_NATIVE] }));
    await page.reload();
    await expect(page.getByText("2 条待分析", { exact: true })).toBeVisible();
    await expect(page.locator('input[name="run"]:checked')).toHaveCount(0);
  });

  test("manual fallback requires both MP4 and Stats", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
    }));
    await page.goto("/analyze");
    const start = page.getByRole("button", { name: "开始分析", exact: true });
    await expect(start).toBeDisabled();
    await page.locator('.task3-analyze-manual-cards input[accept="video/mp4"]').setInputFiles({ name: "fixture.mp4", mimeType: "video/mp4", buffer: Buffer.from("fixture") });
    await expect(start).toBeDisabled();
    await page.locator('.task3-analyze-manual-cards input[accept*="text/csv"]').setInputFiles({ name: "fixture.csv", mimeType: "text/csv", buffer: Buffer.from("FOV,103\n") });
    await expect(start).toBeEnabled();
  });

  test("Tasks shows every lifecycle state, partial outcome, and retry attempt", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ tasks: TASKS }));
    await page.goto("/tasks");
    for (const label of ["正在导入", "排队中", "运行中", "已完成", "正在重试"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText("部分可用", { exact: true })).toBeVisible();
    await expect(page.getByText("第 2 次尝试 · 可离开本页", { exact: true })).toBeVisible();
    await expect(page.getByText("失败 · 输入对齐", { exact: true })).toBeVisible();
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

  test("Analysis workspace keeps Diagnosis, Video, and Data in one workspace", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: analysisSession() }));
    await page.goto("/analysis/42");
    await expect(page.getByRole("heading", { name: "1wall 6targets small" })).toBeVisible();
    await expect(page.getByText("本轮最值得关注：减速阶段偏长", { exact: true })).toBeVisible();
    await expect(page.getByText("训练方向", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "查看证据" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "查看指标" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "问 Coach" }).first()).toBeVisible();

    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.getByText("没有可用视觉证据", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.getByRole("heading", { name: "实验性或受限指标" })).toBeVisible();
    await expect(page.getByText("时序分布", { exact: true })).toBeVisible();
  });

  test("a ready completed Analysis soft-starts Coach once without sending a user message", async ({ page }) => {
    let softStartCount = 0;
    let requestBody: unknown = null;
    let softStartCompleted = false;
    await installApiFixtures(page, apiScenario({ analysis: analysisSession() }));
    await page.route("**/api/coach/primary", async (route) => {
      const completedAtRequest = softStartCompleted;
      if (!completedAtRequest) await new Promise((resolve) => setTimeout(resolve, 150));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread: { id: 1, user_id: "dev", kind: "primary", created_at: "2026-08-06T00:00:00Z", updated_at: "2026-08-06T00:00:00Z" },
          messages: completedAtRequest ? [{
            id: 2,
            role: "assistant",
            content: "软启动首轮诊断已生成",
            created_at: "2026-08-06T00:00:00Z",
            legacy_session_id: null,
            context_refs: [],
          }] : [],
          refs: [],
        }),
      });
    });
    await page.route("**/api/coach/analysis-soft-start", async (route) => {
      softStartCount += 1;
      requestBody = route.request().postDataJSON();
      softStartCompleted = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ schema_version: "coach_agent_run.v1", run_ref: "coach-run:soft-start", parent_run_ref: null, attempt: 1, status: "succeeded", phase: "completed", partial_text: null, error: null, contexts: [], events: [], created_at: "2026-08-06T00:00:00Z", started_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:00:00Z" }),
      });
    });

    await page.goto(`${process.env.AIMING_COOKIE_E2E_BASE_URL ?? ""}/analysis/42`);

    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect.poll(() => softStartCount).toBe(1);
    await expect(page.getByText("软启动首轮诊断已生成", { exact: true })).toBeVisible();
    expect(requestBody).toEqual({ schema_version: "coach_analysis_soft_start_request.v1", analysis_session_id: 42 });
  });

  for (const candidate of [
    {
      name: "Provider is not ready",
      path: "/analysis/42",
      scenario: apiScenario({
        providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
      }),
    },
    {
      name: "Analysis is not done",
      path: "/analysis/42",
      scenario: apiScenario({ analysis: analysisSession({ status: "queued" }) }),
    },
    { name: "page is not an Analysis", path: "/history", scenario: apiScenario() },
    { name: "Analysis id is invalid", path: "/analysis/not-a-number", scenario: apiScenario() },
  ]) {
    test(`Analysis soft start stays off when ${candidate.name}`, async ({ page }) => {
      let softStartCount = 0;
      await installApiFixtures(page, candidate.scenario);
      await page.route("**/api/coach/analysis-soft-start", async (route) => {
        softStartCount += 1;
        await route.fulfill({ status: 409, body: "not available" });
      });

      await page.goto(`${process.env.AIMING_COOKIE_E2E_BASE_URL ?? ""}${candidate.path}`);
      await page.waitForTimeout(250);

      expect(softStartCount).toBe(0);
    });
  }

  test("a failed Analysis soft start leaves manual Coach input available", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: analysisSession() }));
    await page.route("**/api/coach/analysis-soft-start", async (route) => {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "provider_unavailable" }) });
    });

    await page.goto(`${process.env.AIMING_COOKIE_E2E_BASE_URL ?? ""}/analysis/42`);

    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.locator("#coach-draft")).toBeEnabled();
  });

  test("registry-backed Analysis labels candidate explanations without restoring legacy prescriptions", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: registryBackedAnalysisSession() }));
    await page.goto("/analysis/42");
    await expect(page.getByText("本轮最值得关注：减速阶段偏长", { exact: true })).toBeVisible();
    await expect(page.getByText("规则化观察", { exact: true })).toBeVisible();
    await expect(page.getByText("候选解释", { exact: true })).toBeVisible();
    await expect(page.getByText("规则化练习建议", { exact: true })).toBeVisible();
    await expect(page.getByText("历史候选说明", { exact: true })).toHaveCount(0);
  });

  test("Coach supports a primary conversation without a session binding", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/history");
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.getByText("先稳定接近目标时的减速节奏，再复测同一场景。", { exact: true })).toBeVisible();
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
