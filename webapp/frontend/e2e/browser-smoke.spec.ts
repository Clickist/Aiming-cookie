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
} from "../fixtures/task7-fixtures";

test.describe("Task 7 browser smoke", () => {
  test("conditional startup routes to onboarding", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      productState: { ...PRODUCT_STATE, onboarding_completed: false, onboarding_completion_kind: null },
    }));
    await page.goto("/");
    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("heading", { name: "连接你自己的 AI Provider" })).toBeVisible();
  });

  test("onboarding explains provider cost, data boundary, skip, and capture opt-in", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      providerStatus: { ...READY_PROVIDER_STATUS, configured: false, profile_id: null, status: "unconfigured" },
    }));
    await page.goto("/onboarding");
    await expect(page.getByText("Aiming Cookie 本身开源免费。", { exact: false })).toBeVisible();
    await expect(page.getByText("本地视频、Raw Input、文件路径和密钥不会进入 Coach 对话。", { exact: false })).toBeVisible();
    const skip = page.getByRole("button", { name: "暂不连接，使用本地模式" });
    await expect(skip).toHaveAttribute("title", /没有任何 Coach 功能/);
    await expect(page.getByRole("tab", { name: "Provider 目录" })).toBeVisible();
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

  test("Analysis workspace keeps Diagnosis, Video, and Data in one workspace", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ analysis: analysisSession() }));
    await page.goto("/analysis/42");
    await expect(page.getByRole("heading", { name: "1wall 6targets small" })).toBeVisible();
    await expect(page.getByText("最需要处理：停枪控制不稳", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.getByRole("slider", { name: "分析时间轴" })).toBeVisible();

    await page.getByRole("tab", { name: "数据" }).click();
    await expect(page.getByRole("heading", { name: "正式指标" })).toBeVisible();
    await expect(page.getByRole("img", { name: "按事件类型统计的分布图" })).toBeVisible();
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
