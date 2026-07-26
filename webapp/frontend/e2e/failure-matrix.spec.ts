import { expect, test } from "@playwright/test";

import {
  CAPTURE_STATUS,
  RUN_MULTIMODAL,
  analysisSession,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
  partialAnalysisSession,
  UNAVAILABLE_EVIDENCE_SEGMENTS,
} from "../fixtures/task7-fixtures";

test.describe("Task 7 failure matrix", () => {
  test("product state service failure is not rendered as first-use empty state", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "/api/product-state": 503 } }));
    await page.goto("/");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("暂时无法读取本地产品状态");
    await expect(page.getByText("没有把读取失败当成空数据。", { exact: false })).toBeVisible();
  });

  test("Tasks unavailable differs from an empty task list", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "/api/tasks": 503 } }));
    await page.goto("/tasks");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("任务状态暂时不可用");
    await expect(page.getByText("还没有分析任务")).toHaveCount(0);
  });

  test("History failure does not become no records", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "/api/sessions": 503 } }));
    await page.goto("/history");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("历史暂时不可用");
    await expect(page.getByText("读取失败没有被显示成没有记录。", { exact: false })).toBeVisible();
  });

  test("capture permission and alignment failures stay adjacent to their source", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      capture: { ...CAPTURE_STATUS, raw_input_permission: "denied", runtime_health: "degraded" },
      runs: [{
        ...RUN_MULTIMODAL,
        supported_input_modes: ["video_fallback"],
        evidence_availability: { ...RUN_MULTIMODAL.evidence_availability, raw: "permission_denied" },
        alignment: { status: "failed" },
        limitations: ["permission_denied", "alignment_failed"],
      }],
    }));
    await page.goto("/analyze");
    await expect(page.locator(".task3-run-issue")).toContainText("permission_denied、alignment_failed");
    await expect(page.locator(".task3-mode-card").filter({ hasText: "Input-native" }).locator("input")).toBeDisabled();
  });

  for (const [status, copy] of [
    ["queued", "Analysis 正在等待处理"],
    ["running", "Analysis 正在生成确定性结果"],
  ] as const) {
    test(`Analysis ${status} uses an explicit processing state`, async ({ page }) => {
      await installApiFixtures(page, apiScenario({ analysis: analysisSession({ status, result: null }) }));
      await page.goto("/analysis/42");
      await expect(page.getByText(copy)).toBeVisible();
      await expect(page.getByText("这里不显示推测百分比。", { exact: false })).toBeVisible();
    });
  }

  test("retryable Analysis failure remains actionable", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      analysis: analysisSession({
        status: "failed",
        result: null,
        error: {
          schema_version: "error.v1",
          category: "local_cv_runtime",
          code: "video_failed",
          message: "视频分析失败，native 结果未受影响。",
          retryable: true,
          trace_id: null,
          details: null,
        },
      }),
    }));
    await page.goto("/analysis/42");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("Analysis 没有完成");
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  });

  test("partial Analysis preserves native results and marks video unavailable", async ({ page }) => {
    await installApiFixtures(page, apiScenario({
      analysis: partialAnalysisSession(),
      evidenceSegments: UNAVAILABLE_EVIDENCE_SEGMENTS,
    }));
    await page.goto("/analysis/42");
    await expect(page.getByText("视觉结果部分不可用")).toBeVisible();
    await expect(page.getByText("输入原生结果仍然保留", { exact: false })).toBeVisible();
    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.getByText("视觉证据当前不可用")).toBeVisible();
  });

  test("Desktop managed video preserves the handler route segments", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ analysis: analysisSession() }));
    let requestedUrl = "";
    await page.route("http://aiming-cookie-media.localhost/**", async (route) => {
      requestedUrl = route.request().url();
      await route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "managed_video_unavailable.v1",
          availability: "unavailable",
          reason: "managed_video_unavailable",
        }),
      });
    });

    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "视频" }).click();
    await expect.poll(() => requestedUrl).not.toBe("");

    expect(new URL(requestedUrl).pathname).toBe("/analysis/42");
    expect(requestedUrl).not.toMatch(/%2F/i);
  });

  test("deleted Analysis reference is unavailable rather than empty", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "/api/sessions/42": 404 } }));
    await page.goto("/analysis/42");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("这条 Analysis 已删除或不可用");
  });
});
