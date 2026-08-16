import { expect, test } from "@playwright/test";

import {
  RUN_MULTIMODAL,
  RUN_NATIVE,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";
import type { KovaaKRunListItem } from "../lib/types";

function pendingRun(id: number): KovaaKRunListItem {
  return {
    ...RUN_NATIVE,
    id,
    run_ref: `run:${id}`,
    source_key: `pending-run-${id}`,
    scenario: `Pending Scenario ${id}`,
    readiness_state: "pending_analysis",
    analysis_count: 0,
  };
}

test.beforeEach(async ({ page }) => {
  await installDesktopBridge(page);
});

test("multi-selected pending runs hand a draft request to the Coach composer", async ({ page }) => {
  // 会话列表为空 = 重启后场景：intent 跳转应落到新草稿。
  await installApiFixtures(page, apiScenario({
    coachSessions: [],
    runs: [
      { ...RUN_MULTIMODAL, readiness_state: "pending_analysis", analysis_count: 0 },
      { ...RUN_NATIVE, readiness_state: "pending_analysis", analysis_count: 0 },
    ],
  }));
  await page.goto("/history");

  const checkboxes = page.locator("section[aria-labelledby='pending-title'] input[type='checkbox']");
  await expect(checkboxes).toHaveCount(2);
  await checkboxes.nth(0).check();
  await checkboxes.nth(1).check();

  const coachButton = page.getByRole("button", { name: "让 Coach 分析（2）" }).first();
  await expect(coachButton).toBeVisible();

  // 交给 Coach 之前不得触发分析。
  let analyzePosted = false;
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/api\/kovaak-runs\/\d+\/analyze$/.test(new URL(request.url()).pathname)) {
      analyzePosted = true;
    }
  });
  await coachButton.click();

  await page.waitForURL((url) => url.pathname === "/");
  const composer = page.locator("#coach-draft");
  await expect(composer).toHaveValue(/请分析我这几局训练：[\s\S]*run:7[\s\S]*；[\s\S]*run:8[\s\S]*，讲讲主要问题和改进方向。/);
  expect(analyzePosted).toBe(false);
});

test("a single selected run hands a singular draft to the Coach composer", async ({ page }) => {
  await installApiFixtures(page, apiScenario({
    coachSessions: [],
    runs: [{ ...RUN_MULTIMODAL, readiness_state: "pending_analysis", analysis_count: 0 }],
  }));
  await page.goto("/history");

  await page.locator("section[aria-labelledby='pending-title'] input[type='checkbox']").first().check();
  await page.getByRole("button", { name: "让 Coach 分析", exact: true }).first().click();

  await page.waitForURL((url) => url.pathname === "/");
  await expect(page.locator("#coach-draft")).toHaveValue(/请分析我这局训练：[\s\S]*（run:7）[\s\S]*。$/);
});

test("selection is capped at five pending runs with an explicit notice", async ({ page }) => {
  await installApiFixtures(page, apiScenario({
    runs: Array.from({ length: 6 }, (_, index) => pendingRun(index + 1)),
  }));
  await page.goto("/history");

  const checkboxes = page.locator("section[aria-labelledby='pending-title'] input[type='checkbox']");
  await expect(checkboxes).toHaveCount(6);
  for (let index = 0; index < 5; index += 1) {
    await checkboxes.nth(index).check();
  }
  await expect(page.getByRole("button", { name: "让 Coach 分析（5）" }).first()).toBeVisible();

  // 第 6 条勾选被上限拦截：用 click（check() 会因状态未变而失败）。
  await checkboxes.nth(5).click();
  await expect(page.getByText("最多同时选 5 条一起交给 Coach。")).toBeVisible();
  await expect(checkboxes.nth(5)).not.toBeChecked();
  await expect(page.getByRole("button", { name: "让 Coach 分析（5）" }).first()).toBeVisible();
});

test("run records are selectable except when no input tier is available", async ({ page }) => {
  const noTierRun: KovaaKRunListItem = {
    ...RUN_NATIVE,
    id: 9,
    run_ref: "run:9",
    source_key: "no-tier-run",
    scenario: "No Tier Scenario",
    supported_input_modes: [],
    readiness_state: "analyzed",
    analysis_count: 0,
  };
  await installApiFixtures(page, apiScenario({
    coachSessions: [],
    runs: [RUN_NATIVE, noTierRun],
  }));
  await page.goto("/history");

  // 训练记录区也提供勾选；无任何可用 tier 的行禁用并说明原因。
  const checkboxes = page.locator("section[aria-labelledby='runs-title'] input[type='checkbox']");
  await expect(checkboxes).toHaveCount(2);
  await expect(checkboxes.nth(1)).toBeDisabled();
  await expect(checkboxes.nth(1)).toHaveAttribute("title", "证据不足以分析");

  // 可用的训练记录勾选后同样把话术交给 Coach。
  await checkboxes.nth(0).check();
  await page.getByRole("button", { name: "让 Coach 分析", exact: true }).first().click();
  await page.waitForURL((url) => url.pathname === "/");
  await expect(page.locator("#coach-draft")).toHaveValue(/请分析我这局训练：[\s\S]*（run:8）[\s\S]*。$/);
});

test("finished analysis records can be attached to a Coach draft by analysis ref", async ({ page }) => {
  await installApiFixtures(page, apiScenario({
    coachSessions: [],
    runs: [],
  }));
  await page.goto("/history");

  // 分析记录区：已完成的分析可勾选交给 Coach。
  const analysisBoxes = page.locator("section[aria-labelledby='analysis-title'] input[type='checkbox']");
  await expect(analysisBoxes.first()).toBeVisible();
  await analysisBoxes.first().check();
  await page.getByRole("button", { name: "让 Coach 分析", exact: true }).first().click();
  await page.waitForURL((url) => url.pathname === "/");
  // fixture 的默认分析记录是 analysis:42，话术以 analysis ref 引用。
  await expect(page.locator("#coach-draft")).toHaveValue(/请结合这份分析：[\s\S]*（analysis:42）[\s\S]*。$/);
});

test("history no longer exposes the run inspector or analysis summary entry points", async ({ page }) => {
  await installApiFixtures(page, apiScenario());
  await page.goto("/history");

  await expect(page.getByRole("button", { name: "查看 Run" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看摘要" })).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "分析摘要" })).toHaveCount(0);

  // 区块顺序：待分析训练 → 分析记录 → 训练记录。
  const pending = page.locator("#pending-title");
  const analysis = page.locator("#analysis-title");
  const runs = page.locator("#runs-title");
  await expect(pending).toBeVisible();
  await expect(analysis).toBeVisible();
  await expect(runs).toBeVisible();
  const pendingBox = await pending.boundingBox();
  const analysisBox = await analysis.boundingBox();
  const runsBox = await runs.boundingBox();
  expect(pendingBox?.y).toBeLessThan(analysisBox?.y ?? Number.POSITIVE_INFINITY);
  expect(analysisBox?.y).toBeLessThan(runsBox?.y ?? Number.POSITIVE_INFINITY);
});
