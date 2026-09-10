import { test, expect } from "@playwright/test";

import { installApiFixtures } from "../fixtures/task7-fixtures";

// 临时视觉验证：「新对话首页」四态截图（点点 0910 拍板的空对话改版）。
// 产物写到 .zcode/，仅供本轮人工核对，不入快照库。

const out = "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/";

test("coach home renders, chips fill draft, mention opens, send settles", async ({ page }) => {
  await installApiFixtures(page);
  await page.goto("/");

  // 启动默认恢复上次会话（fixture 里有历史消息）——空首页出现在「新建对话」后
  await page.getByRole("button", { name: "新建对话" }).first().click();
  const hero = page.locator(".task6-empty-hero");
  await expect(hero).toBeVisible();
  await expect(page.locator(".task6-home-greet")).toBeVisible();
  await expect(page.locator(".task6-home-chips .task6-suggestion")).toHaveCount(3);
  await page.screenshot({ path: `${out}ac-home-1-initial.png` });

  // chip 点击＝填入草稿，不直发
  await page.locator(".task6-home-chips .task6-suggestion").first().click();
  await expect(page.locator("#coach-draft")).not.toHaveValue("");
  await page.screenshot({ path: `${out}ac-home-2-chip-fill.png` });
  await page.locator("#coach-draft").fill("");

  // @ 引用按钮拉起候选
  await page.locator(".task6-composer-mention").click();
  await expect(page.locator(".task6-mention-menu")).toBeVisible();
  await page.screenshot({ path: `${out}ac-home-3-mention.png` });
  await page.keyboard.press("Escape");

  // 发送：过渡后回到常规聊天布局，footer composer 接管
  await page.locator("#coach-draft").fill("帮我看看最近的训练状态。");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(180);
  await page.screenshot({ path: `${out}ac-home-4-transition.png` });
  await expect(page.locator("footer.task6-composer .task6-composer-input")).toBeVisible();
  await expect(page.locator('.task6-message-entry[data-role="user"]').first()).toBeVisible();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}ac-home-5-settled.png` });
  await expect(page.locator(".task6-empty-hero")).toHaveCount(0);
});
