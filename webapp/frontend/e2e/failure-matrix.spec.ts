import { expect, test } from "@playwright/test";

import {
  CAPTURE_STATUS,
  EVIDENCE_SEGMENTS,
  RUN_MULTIMODAL,
  RUN_PENDING_MULTIMODAL,
  analysisSession,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
  partialAnalysisSession,
  UNAVAILABLE_EVIDENCE_SEGMENTS,
} from "../fixtures/task7-fixtures";

function seekableMp4Analysis() {
  const base = analysisSession();
  const result = base.result;
  if (!result || result.schema_version !== "analysis_result.v2") {
    throw new Error("seekable MP4 fixture requires AnalysisResultV2");
  }
  return analysisSession({
    input_mode: "video_fallback",
    result: { ...result, input_mode: "video_fallback" },
    history: {
      ...base.history!,
      input_mode: "video_fallback",
      source_availability: { ...base.history!.source_availability, mp4: "available" },
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

test.describe("Task 7 failure matrix", () => {
  test("product state service failure is not rendered as first-use empty state", async ({ page }) => {
    await installApiFixtures(page, apiScenario({ failures: { "/api/product-state": 503 } }));
    await page.goto("/");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("暂时无法读取本地产品状态");
    await expect(page.getByText("没有把读取失败当成空数据。", { exact: false })).toBeVisible();
  });

  test("Tasks unavailable differs from an empty task list", async ({ page }) => {
    test.skip(true, "Tasks 页面已下线（legacy URL redirect → /history）；任务失败态无对应页面");
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
    test.skip(true, "/analyze 页面已移除且不保留 redirect（browser-smoke「legacy Tasks and Analysis URLs redirect」已注明）；Run 来源状态 UI 待新 IA 稳定后重写");
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      capture: { ...CAPTURE_STATUS, raw_input_permission: "denied", runtime_health: "degraded" },
      runs: [{
        ...RUN_PENDING_MULTIMODAL,
        supported_input_modes: ["video_fallback"],
        evidence_availability: { ...RUN_MULTIMODAL.evidence_availability, raw: "permission_denied" },
        alignment: { status: "failed" },
        limitations: ["permission_denied", "alignment_failed"],
      }],
    }));
    await page.goto("/analyze");
    await expect(page.getByText("权限被拒绝 · 覆盖率 100%", { exact: true })).toBeVisible();
    await expect(page.getByText("对齐失败", { exact: true })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^输入原生/ })).toBeDisabled();
  });

  for (const [status, copy] of [
    ["queued", "Analysis 正在等待处理"],
    ["running", "Analysis 正在生成确定性结果"],
  ] as const) {
    test(`Analysis ${status} uses an explicit processing state`, async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
      await installApiFixtures(page, apiScenario({ analysis: analysisSession({ status, result: null }) }));
      await page.goto("/analysis/42");
      await expect(page.getByText(copy)).toBeVisible();
      await expect(page.getByText("这里不显示推测百分比。", { exact: false })).toBeVisible();
    });
  }

  test("retryable Analysis failure remains actionable", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
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
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
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

  test("video fallback requests managed playback when EvidenceSegment overlays are unavailable", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
    await installApiFixtures(page, apiScenario({
      analysis: seekableMp4Analysis(),
      failures: { "/api/sessions/42/evidence-segments": 404 },
    }));
    let requestedUrl = "";
    let requestedOwner = "";
    page.on("request", (request) => {
      if (new URL(request.url()).pathname !== "/api/sessions/42/video") return;
      requestedUrl = request.url();
      requestedOwner = request.headers()["x-user-id"] ?? "";
    });

    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "视频" }).click();
    await expect.poll(() => requestedUrl).not.toBe("");
    expect(new URL(requestedUrl).pathname).toBe("/api/sessions/42/video");
    expect(requestedOwner).toBe(process.env.NEXT_PUBLIC_USER_ID ?? "dev");
  });

  test("EvidenceSegment failure stays local to the timeline and retry preserves the player", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
    await installApiFixtures(page, apiScenario({ analysis: seekableMp4Analysis() }));
    let segmentRequests = 0;
    await page.route("**/api/sessions/42/evidence-segments", async (route) => {
      segmentRequests += 1;
      if (segmentRequests === 1) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "unavailable" }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EVIDENCE_SEGMENTS) });
    });

    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "视频" }).click();
    const player = page.locator("video");
    await expect(player).toBeVisible();
    await page.getByRole("button", { name: /^证据片段 \d+$/ }).click();
    await expect(page.getByText("证据片段暂时不可用")).toBeVisible();
    await player.evaluate((element) => element.dataset.testPlayer = "retained");

    await page.getByRole("button", { name: "重试证据片段" }).click();
    await expect.poll(() => segmentRequests).toBe(2);
    await expect(page.getByText("证据片段暂时不可用")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /证据片段/ }).first()).toBeVisible();
    await expect(player).toHaveAttribute("data-test-player", "retained");
  });

  test("an empty EvidenceSegment response remains an empty timeline state", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
    await installApiFixtures(page, apiScenario({ analysis: seekableMp4Analysis() }));
    await page.route("**/api/sessions/42/evidence-segments", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...EVIDENCE_SEGMENTS, segments: [] }),
      });
    });

    await page.goto("/analysis/42");
    await page.getByRole("tab", { name: "视频" }).click();
    await expect(page.locator("video")).toBeVisible();
    await page.getByRole("button", { name: /^证据片段 \d+$/ }).click();
    await expect(page.getByText("没有可用证据片段")).toBeVisible();
    await expect(page.getByText("证据片段暂时不可用")).toHaveCount(0);
  });

  test("Desktop managed video preserves the handler route segments", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ analysis: seekableMp4Analysis() }));
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
    await expect(page.getByText("视觉证据当前不可用")).toBeVisible();

    expect(new URL(requestedUrl).pathname).toBe("/analysis/42");
    expect(requestedUrl).not.toMatch(/%2F/i);
  });

  test("deleted Analysis reference is unavailable rather than empty", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
    await installApiFixtures(page, apiScenario({ failures: { "/api/sessions/42": 404 } }));
    await page.goto("/analysis/42");
    await expect(page.locator('.ac-state[role="alert"]')).toContainText("这条 Analysis 已删除或不可用");
  });
});
