import { expect, test, type Locator } from "@playwright/test";

import {
  ANALYSIS_DATA_TRACKING,
  ANALYSIS_FAMILY_FLICKING,
  ANALYSIS_FAMILY_FLICKING_UNAVAILABLE,
  ANALYSIS_FAMILY_SWITCHING,
  ANALYSIS_FAMILY_TRACKING,
  analysisSession,
  apiScenario,
  installApiFixtures,
} from "../fixtures/task7-fixtures";

function familyAnalysis(
  family: "static_clicking" | "continuous_tracking" | "target_switching",
  inputMode: "input_native" | "multimodal" | "video_fallback",
) {
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") throw new Error("family fixture requires v2");
  return analysisSession({
    result: {
      ...base.result,
      analysis_type: family === "target_switching" ? "target_switching" : family === "continuous_tracking" ? "tracking" : "flicking",
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

async function expectSvgToKeepViewBoxRatio(svg: Locator) {
  const ratios = await svg.evaluate((node) => {
    const element = node as SVGSVGElement;
    const rect = element.getBoundingClientRect();
    const viewBox = element.viewBox.baseVal;
    return {
      rendered: rect.width / rect.height,
      source: viewBox.width / viewBox.height,
    };
  });
  expect(ratios.rendered).toBeCloseTo(ratios.source, 1);
}

test("Analysis Data renders its bounded input-native projection", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await installApiFixtures(page, apiScenario());
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();

  await expect(page.getByText("运动平滑度（SPARC）").first()).toBeVisible();
  await expect(page.getByText("sparc", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "逐次 Flick" })).toBeVisible();
  await expect(page.getByText("analysis:42:source", { exact: false })).toHaveCount(0);
});

test("Analysis Data labels partial deterministic metrics as descriptive", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") {
    throw new Error("Analysis Data fixture requires AnalysisResultV2");
  }
  await installApiFixtures(page, apiScenario({
    analysis: analysisSession({
      result: {
        ...base.result,
        deterministic: {
          ...base.result.deterministic,
          support_status: "partial",
        },
      },
    }),
  }));

  await page.goto("/analysis/42");
  await expect(page.getByText(/描述性结果/)).toBeVisible();
  await page.getByRole("tab", { name: "数据" }).click();
  await expect(page.getByRole("heading", { name: "实验性或受限指标" })).toBeVisible();
  await expect(page.getByText("运动平滑度（SPARC）", { exact: true }).first()).toBeVisible();
});

test("Analysis Data translates and deduplicates shared limitations", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  const base = analysisSession();
  if (!base.result || base.result.schema_version !== "analysis_result.v2") {
    throw new Error("Analysis Data fixture requires AnalysisResultV2");
  }
  const sparc = base.result.deterministic.metrics?.sparc;
  if (!sparc || typeof sparc !== "object") {
    throw new Error("Analysis Data fixture requires a structured SPARC metric");
  }
  await installApiFixtures(page, apiScenario({
    analysis: analysisSession({
      result: {
        ...base.result,
        deterministic: {
          ...base.result.deterministic,
          limitations: [
            "Exact scenario hash, 1920x1080 resolution and one target bot only.",
            "Unknown or multi-target scenarios remain fail-closed.",
          ],
          metrics: {
            ...base.result.deterministic.metrics,
            sparc: {
              ...sparc,
              limitations: [
                "Exact scenario hash, 1920x1080 resolution and one target bot only.",
                "alignment_latency_reported_separately",
              ],
            },
          },
        },
        input_snapshot: {
          ...base.result.input_snapshot,
          scenario_resolution: {
            ...base.result.input_snapshot.scenario_resolution!,
            limitations: [
              "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.",
              "Unknown hashes and concurrent target layouts are not classified by this entry.",
            ],
          },
        },
      },
    }),
  }));

  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();
  await expect(page.getByText("仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。")).toHaveCount(1);
  await expect(page.getByText("未知场景或多目标布局不生成此类结论。")).toHaveCount(1);
  await expect(page.getByText("对齐延迟单独报告，不等同于跟随滞后。")).toBeVisible();
  await expect(page.getByText("Exact scenario hash", { exact: false })).toBeHidden();
});

test("Tracking Data renders real windows and keeps response timing observational", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await installApiFixtures(page, apiScenario({
    analysis: familyAnalysis("continuous_tracking", "multimodal"),
    analysisData: ANALYSIS_DATA_TRACKING,
    analysisFamilyData: ANALYSIS_FAMILY_TRACKING,
  }));
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();
  await expect(page.getByRole("heading", { name: "跟踪分段" })).toBeVisible();
  await expect(page.getByText("无法分离具体响应来源", { exact: false })).toBeVisible();
  await expect(page.getByText(/人的反应(?:时间|延迟)/)).toHaveCount(0);
});

test("Switching Data renders each complete chain and its four bounded metrics", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await installApiFixtures(page, apiScenario({
    analysis: familyAnalysis("target_switching", "multimodal"),
    analysisFamilyData: ANALYSIS_FAMILY_SWITCHING,
  }));
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();
  await expect(page.getByRole("heading", { name: "切换链" })).toBeVisible();
  await expect(page.getByRole("button", { name: /完整切换 #1.*切换到新目标耗时.*切换位移.*路径效率.*到达后稳定耗时/ })).toBeVisible();
});

test("native Flicking keeps real rows while video seek remains unavailable", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await installApiFixtures(page, apiScenario({
    analysis: familyAnalysis("static_clicking", "input_native"),
    analysisFamilyData: ANALYSIS_FAMILY_FLICKING,
  }));
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();
  await expect(page.getByRole("heading", { name: "逐次 Flick" })).toBeVisible();
  await expect(page.getByText("加速阶段", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("减速阶段", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/^稳定 0 ms$/).first()).toBeVisible();
  await page.getByRole("tab", { name: "视频" }).click();
  await expect(page.getByText("没有可用视觉证据", { exact: true })).toBeVisible();
});

test("Data charts preserve their geometry while Coach resizes the workspace", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await page.setViewportSize({ width: 1280, height: 820 });
  await installApiFixtures(page, apiScenario());
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();

  const coach = page.getByRole("complementary", { name: "Coach" });
  const phaseChart = page.locator('svg[viewBox="0 0 360 90"]');
  const pathChart = page.locator('svg[viewBox="0 0 360 110"]');
  await expect(coach).toBeVisible();
  await expectSvgToKeepViewBoxRatio(phaseChart);
  await expectSvgToKeepViewBoxRatio(pathChart);

  const initialWidth = (await phaseChart.boundingBox())?.width ?? 0;
  await page.getByRole("separator", { name: "调整 Coach 宽度" }).press("ArrowRight");
  await expect.poll(async () => (await phaseChart.boundingBox())?.width ?? 0).toBeLessThan(initialWidth);
  await expectSvgToKeepViewBoxRatio(phaseChart);
  await expectSvgToKeepViewBoxRatio(pathChart);

  for (const width of [960, 720]) {
    await page.setViewportSize({ width, height: 820 });
    const dialog = page.getByRole("dialog", { name: "Coach" });
    if (await dialog.isVisible()) await page.keyboard.press("Escape");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expectSvgToKeepViewBoxRatio(phaseChart);
    await expectSvgToKeepViewBoxRatio(pathChart);
  }
});

test("Tracking charts use the full card width when formal metrics are absent", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await page.setViewportSize({ width: 1280, height: 820 });
  await installApiFixtures(page, apiScenario({
    analysis: familyAnalysis("continuous_tracking", "multimodal"),
    analysisData: ANALYSIS_DATA_TRACKING,
    analysisFamilyData: ANALYSIS_FAMILY_TRACKING,
  }));
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();

  const layout = page.locator('[data-family="tracking"]');
  await expect(layout).toHaveAttribute("data-metrics", "empty");
  const widthShare = await layout.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    const detail = node.lastElementChild?.getBoundingClientRect();
    return detail ? detail.width / rect.width : 0;
  });
  expect(widthShare).toBeGreaterThan(0.95);
  await expectSvgToKeepViewBoxRatio(page.locator('svg[viewBox="0 0 360 100"]'));

  await page.setViewportSize({ width: 720, height: 820 });
  const dialog = page.getByRole("dialog", { name: "Coach" });
  if (await dialog.isVisible()) await page.keyboard.press("Escape");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(720);
  await expectSvgToKeepViewBoxRatio(page.locator('svg[viewBox="0 0 360 100"]'));
});

test("video-fallback Flicking stays unavailable without replacing generic metrics", async ({ page }) => {
    test.skip(true, "独立 Analysis 页面已随 2026-08-10 Coach-first IA 下线（/analysis/:id 仅 redirect → /history）；等价断言待 Coach 卡片/History 详情稳定后按新 IA 重写");
  await installApiFixtures(page, apiScenario({
    analysis: familyAnalysis("static_clicking", "video_fallback"),
    analysisFamilyData: ANALYSIS_FAMILY_FLICKING_UNAVAILABLE,
  }));
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();

  await expect(page.getByRole("note").filter({ hasText: "Flick" })).toBeVisible();
  await expect(page.getByText("运动平滑度（SPARC）", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Flick #/)).toHaveCount(0);
});
