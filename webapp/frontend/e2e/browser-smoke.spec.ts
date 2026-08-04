import { expect, test } from "@playwright/test";

import {
  PRODUCT_STATE,
  READY_PROVIDER_STATUS,
  RUN_MULTIMODAL,
  RUN_NATIVE,
  TASKS,
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
    await expect(page.getByRole("heading", { name: "连接 KovaaK 成绩" })).toBeVisible();
    await page.getByRole("button", { name: "稍后再说" }).click();
    await expect(page.getByRole("heading", { name: "训练后自动整理证据" })).toBeVisible();

    await page.goto("/settings");
    await expect(page.getByText("KovaaK 成绩", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Control Tracking/ })).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(0);
  });

  test("KovaaK connection remains manageable when only the score read fails", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "GET /api/kovaak-scores": 503 } }));
    await page.goto("/settings");
    await expect(page.getByText("已连接", { exact: true })).toBeVisible();
    await expect(page.getByText("KovaaK 成绩暂时无法读取，请稍后重试。")).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新成绩" })).toBeEnabled();
  });

  test("desktop Run discovery selects one Run and requires a choice for multiple Runs", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_MULTIMODAL] }));
    await page.goto("/analyze");
    await expect(page.getByRole("heading", { name: "桌面采集状态" })).toBeVisible();
    await expect(page.locator('input[name="run"]')).toBeChecked();
    await expect(page.locator('input[name="input-mode"]')).toHaveCount(3);
    await expect(page.getByText("预览 / 实验")).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await installApiFixtures(page, apiScenario({ runs: [RUN_MULTIMODAL, RUN_NATIVE] }));
    await page.reload();
    await expect(page.getByText("2 条待确认 · 必须选择一条")).toBeVisible();
    await expect(page.locator('input[name="run"]:checked')).toHaveCount(0);
  });

  test("manual fallback requires both MP4 and Stats", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
    }));
    await page.goto("/analyze");
    const start = page.getByRole("button", { name: "开始 video fallback" });
    await expect(start).toBeDisabled();
    await page.getByLabel("选择 MP4 录像").setInputFiles({ name: "fixture.mp4", mimeType: "video/mp4", buffer: Buffer.from("fixture") });
    await expect(start).toBeDisabled();
    await page.getByLabel("选择 Stats CSV").setInputFiles({ name: "fixture.csv", mimeType: "text/csv", buffer: Buffer.from("FOV,103\n") });
    await expect(start).toBeEnabled();
  });

  test("Tasks shows every lifecycle state, partial outcome, and retry attempt", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ tasks: TASKS }));
    await page.goto("/tasks");
    for (const label of ["正在导入", "等待分析", "分析中", "已完成", "失败", "正在重试"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText("部分结果可用")).toBeVisible();
    await expect(page.getByText("2 次 attempt")).toBeVisible();
    await expect(page.getByText("输入对齐失败")).toBeVisible();
  });

  test("History keeps pending Runs, Run records, and Analysis records separate", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      runs: [RUN_MULTIMODAL, { ...RUN_NATIVE, readiness_state: "analyzed", analysis_count: 1 }],
    }));
    await page.goto("/history");
    await expect(page.getByRole("heading", { name: "待分析训练" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "训练记录" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "分析记录" })).toBeVisible();
    await expect(page.getByText("1 条", { exact: true })).toHaveCount(3);
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
    await expect(page.getByText("重点观察：停枪控制不稳", { exact: true })).toBeVisible();
    await expect(page.getByText("历史候选说明", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看证据" })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看指标" })).toBeVisible();
    await expect(page.getByRole("button", { name: "问 Coach" })).toBeVisible();

    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.getByRole("slider", { name: "分析时间轴" })).toBeVisible();

    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.getByRole("heading", { name: "正式指标" })).toBeVisible();
    await expect(page.getByRole("group", { name: "按已验证事件种类统计的分布图" })).toBeVisible();
  });

  test("registry-backed Analysis labels candidate explanations without restoring legacy prescriptions", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: registryBackedAnalysisSession() }));
    await page.goto("/analysis/42");
    await expect(page.getByText("重点观察：停枪控制不稳", { exact: true })).toBeVisible();
    await expect(page.getByText("证据等级：规则化观察", { exact: true })).toBeVisible();
    await expect(page.getByText("候选解释", { exact: true })).toBeVisible();
    await expect(page.getByText("规则化练习建议", { exact: true })).toBeVisible();
    await expect(page.getByText("历史候选说明", { exact: true })).toHaveCount(0);
  });

  test("Coach supports a primary conversation without a session binding", async ({ page }) => {
    await installApiFixtures(page);
    await page.goto("/history");
    await expect(page.getByRole("complementary", { name: "Coach" })).toBeVisible();
    await expect(page.getByText("先稳定接近目标时的减速节奏，再复测同一场景。", { exact: true })).toBeVisible();
    await expect(page.getByLabel("发送上下文")).toBeVisible();
  });

  test("Settings exposes Provider, calibration, capture, theme, and storage without secrets", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page);
    await page.goto("/settings");
    for (const label of ["Provider", "配置档", "自动采集 / Raw Input", "主题", "存储"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText("Stats 自动读取优先", { exact: false })).toBeVisible();
    await expect(page.getByText("Run 录像", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/task7-fixture-token|C:\\Task7Fixture/)).toHaveCount(0);
  });
});
