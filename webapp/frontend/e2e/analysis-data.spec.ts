import { expect, test } from "@playwright/test";

import { analysisSession, apiScenario, installApiFixtures } from "../fixtures/task7-fixtures";

test("Analysis Data renders its bounded projection and seeks Video from an event", async ({ page }) => {
  await installApiFixtures(page, apiScenario());
  await page.goto("/analysis/42");
  await page.getByRole("tab", { name: "数据" }).click();

  await expect(page.getByText("运动平滑度（SPARC）")).toBeVisible();
  await expect(page.getByText("sparc", { exact: true })).toHaveCount(0);
  const unavailable = page.getByText(/另有 1 项指标暂不可用/);
  await expect(unavailable).toBeVisible();
  await expect(page.getByText("视觉验证状态")).toBeHidden();
  await unavailable.click();
  await expect(page.getByText("视觉验证状态")).toBeVisible();
  await expect(page.getByText("analysis:42:source", { exact: false })).toHaveCount(0);

  const errorSeries = page.getByRole("img", { name: /目标相对误差序列，共 3 个样本，峰值 0.8/ });
  await expect(errorSeries).toBeVisible();
  await expect(page.getByText("共 3 个样本，峰值 0.8", { exact: false })).toBeVisible();
  await expect(errorSeries.getByRole("button")).toHaveCount(0);

  await page.getByRole("button", { name: "目标变向" }).click();
  await expect(page.getByRole("tab", { name: "视频" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("slider", { name: "分析时间轴" })).toHaveValue("800");
});

test("Analysis Data labels partial deterministic metrics as descriptive", async ({ page }) => {
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
  await expect(page.getByText("没有可正式展示的指标", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "实验性或受限指标" })).toBeVisible();
  await expect(page.getByText("运动平滑度（SPARC）", { exact: true })).toBeVisible();
});

test("Analysis Data translates and deduplicates shared limitations", async ({ page }) => {
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
