import { expect, test } from "@playwright/test";

import {
  READY_PROVIDER_STATUS,
  apiScenario,
  installApiFixtures,
  memberTrialMe,
} from "../fixtures/task7-fixtures";

// AC 客户端试用态（验证闸）：免费「一局分析 + 两问」先证明 AC 能用，再放行订阅。
// 全程浏览器 fixture（mock /me 带试用态 + trial-events 记账），不依赖真实会员服务。
test.describe("AC 试用态（验证闸）", () => {
  // Playwright 上下文默认 en-US（无存储偏好时应用按系统语言预选），这里显式
  // 钉住 zh-CN，断言不随运行环境系统语言漂移。
  test.use({ locale: "zh-CN" });
  test("剩余次数可见 → analysis_done 上报并置已验证 → 两问烧完呈现付费墙两出口", async ({ page }) => {
    const scenario = apiScenario({
      // 试用用户没有任何 Provider 档（BYOK 未配置），付费墙才能呈现。
      profiles: { profiles: [] },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: null, configured: false, status: "unconfigured" },
      memberMe: memberTrialMe(1, 2),
    });
    await installApiFixtures(page, scenario);

    // 1. 用户中心显示试用卡：剩余 1 次分析 / 2 次提问（数字来自 /me）。
    await page.goto("/account");
    await expect(page.getByText("免费验证", { exact: true })).toBeVisible();
    await expect(page.getByText("剩余 1 次分析 / 2 次提问")).toBeVisible();
    await expect(page.getByText("已验证 ✓ 可订阅")).toHaveCount(0);

    // 2. 模拟分析终态 done：AppShell 监听既有分析完成事件上报 analysis_done
    //    （fixture 镜像服务端扣减 analyses_remaining→0、置 verified），上报成功
    //    会广播会员态刷新，用户中心的「已验证 ✓ 可订阅」随即出现。
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("aiming-cookie:analysis-auto-teach", {
        detail: { analysis_ref: "analysis:42" },
      }));
    });
    await expect.poll(() => scenario.trialEvents.map((event) => event.type)).toContain("analysis_done");
    await expect(page.getByText("已验证 ✓ 可订阅")).toBeVisible();

    // 3. 模拟两轮教练回复成功落地（CoachPanel run succeeded 时派发的内部事件，
    //    run_ref 作去重键）——两问烧完。
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("aiming-cookie:trial-question-answered", {
        detail: { run_ref: "coach-run:1" },
      }));
    });
    await expect.poll(() => scenario.trialEvents.length).toBe(2);
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("aiming-cookie:trial-question-answered", {
        detail: { run_ref: "coach-run:2" },
      }));
    });
    await expect.poll(() => scenario.trialEvents.length).toBe(3);

    // 4. 两项余量皆 0 且无订阅、没配 BYOK：Coach 输入区呈现付费墙，正向收尾，
    //    两出口按钮=订阅（外链）/ BYOK（Button href → 链接角色，进设置）。
    await page.goto("/");
    await expect(page.getByText("验证完成——AC 已在你的电脑上正常运行")).toBeVisible();
    await expect(page.getByRole("button", { name: "订阅 AC 会员" })).toBeVisible();
    await expect(page.getByRole("link", { name: "使用自己的 API Key（BYOK）" })).toBeVisible();
  });

  test("重复完成事件只上报一次（本地去重），服务端幂等由 accounts 兜底", async ({ page }) => {
    const scenario = apiScenario({
      profiles: { profiles: [] },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: null, configured: false, status: "unconfigured" },
      memberMe: memberTrialMe(1, 2),
    });
    await installApiFixtures(page, scenario);
    await page.goto("/");
    await expect(page.getByText("验证完成——AC 已在你的电脑上正常运行")).toHaveCount(0);
    await page.evaluate(() => {
      for (let i = 0; i < 3; i += 1) {
        window.dispatchEvent(new CustomEvent("aiming-cookie:analysis-auto-teach", {
          detail: { analysis_ref: "analysis:42" },
        }));
      }
    });
    await expect.poll(() => scenario.trialEvents.length).toBe(1);
    await expect.poll(() => scenario.memberMe && extractTrial(scenario.memberMe).analyses_remaining).toBe(0);
  });

  // 登录完成即告知（点点 0926 实测：不知道免费额度是什么、为什么有）：/me 首次
  // 带回满额试用态时右下 Toast 说清「一局 + 两问」的验证闸语义；localStorage
  // 去重键保证只弹一次，重载不再打扰。先等试用卡出现（证明 /me 已解析）再断言，
  // 避免把「还没加载完」误判成「没弹」。
  test("满额试用首次加载弹欢迎提示，重载不再弹（localStorage 去重）", async ({ page }) => {
    const scenario = apiScenario({
      runs: [],
      tasks: [],
      sessions: [],
      coachSessions: [],
      profiles: { profiles: [] },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: null, configured: false, status: "unconfigured" },
      memberMe: memberTrialMe(1, 2),
    });
    await installApiFixtures(page, scenario);

    await page.goto("/account");
    await expect(page.getByText("免费验证", { exact: true })).toBeVisible();
    await expect(page.getByText("已到账新用户免费验证额度")).toBeVisible();

    await page.reload();
    await expect(page.getByText("免费验证", { exact: true })).toBeVisible();
    await expect(page.getByText("已到账新用户免费验证额度")).toHaveCount(0);
  });

  // 登录成功且未订阅（点点 0926 验收：登录完不知道去哪看额度）：device exchange
  // 成功事件驱动 AppShell 直达 /settings?provider=official#llm-provider，官方档
  // 详情的试用块自然呈现（剩余次数+说明+去跑一局）。浏览器形态不走真实
  // deep-link（isDesktopRuntime=false），按本文件既有 CustomEvent 模式模拟
  // lib/member-deeplink 在 exchange 成功时派发的内部事件。
  test("登录成功且未订阅：直达设置页官方档详情并显示试用块", async ({ page }) => {
    const scenario = apiScenario({
      runs: [],
      tasks: [],
      sessions: [],
      coachSessions: [],
      profiles: { profiles: [] },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: null, configured: false, status: "unconfigured" },
      memberMe: memberTrialMe(1, 2),
    });
    await installApiFixtures(page, scenario);

    // 等应用水合完成（侧栏「新建对话」可见）再派发，避免事件落在监听挂载前。
    await page.goto("/");
    await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("aiming-cookie:member-exchanged", {
        detail: { member: false },
      }));
    });
    await expect(page).toHaveURL(/\/settings\?provider=official#llm-provider$/);
    await expect(page.getByText("免费验证", { exact: true })).toBeVisible();
    await expect(page.getByText("剩余 1 次分析 / 2 次提问")).toBeVisible();
    await expect(page.getByText("去训练历史跑一局分析")).toBeVisible();

    // 已订阅登录不抢导航：保持现有落点（仍在 Coach 首页）。
    await page.goto("/");
    await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("aiming-cookie:member-exchanged", {
        detail: { member: true },
      }));
    });
    await page.waitForTimeout(300);
    await expect(page).toHaveURL(/\/$/);
  });

  // 0926 串测实锤的回归锁：会员登录自动创建的官方 relay 托管档不算 BYOK——
  // 只有 relay 档时付费墙必须照常出现（否则纯新用户试用烧完看不到任何出口）。
  // 场景用空 runs/sessions：fixture 默认带待分析记录会触发启动路由跳 History
  // （那里不挂 CoachPanel），把断言变成路由竞态；真实用户的付费墙出现在任何
  // 挂 CoachPanel 的路由上，这里钉在 Coach 首页验。
  test("仅存官方 relay 托管档时付费墙照常呈现（relay 档不豁免）", async ({ page }) => {
    const scenario = apiScenario({
      runs: [],
      tasks: [],
      sessions: [],
      coachSessions: [],
      profiles: {
        profiles: [{
          id: 1,
          name: "Aiming Cookie",
          provider_id: "aiming-cookie-relay",
          kind: "builtin",
          base_url: null,
          model_id: "deepseek-v4-flash",
          is_default: true,
          configured: true,
          credential_configured: true,
          has_api_key: true,
          status: "ready",
        }],
      },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: 1 },
      memberMe: memberTrialMe(0, 0),
    });
    await installApiFixtures(page, scenario);
    await page.goto("/");
    await expect(page.getByText("验证完成——AC 已在你的电脑上正常运行")).toBeVisible();
    await expect(page.getByRole("button", { name: "订阅 AC 会员" })).toBeVisible();
  });

  // 反向：用户真配过自己的档（provider_id 非 relay），即使试用烧完也不出付费墙。
  test("已配自己的 Provider（BYOK）时试用烧完不出付费墙", async ({ page }) => {
    const scenario = apiScenario({
      runs: [],
      tasks: [],
      sessions: [],
      coachSessions: [],
      profiles: {
        profiles: [{
          id: 1,
          name: "我的中转",
          provider_id: "custom",
          kind: "custom_openai_compatible",
          base_url: "https://api.example.com",
          model_id: "deepseek-v4-flash",
          is_default: true,
          configured: true,
          credential_configured: true,
          has_api_key: true,
          status: "ready",
        }],
      },
      providerStatus: { ...READY_PROVIDER_STATUS, profile_id: 1 },
      memberMe: memberTrialMe(0, 0),
    });
    await installApiFixtures(page, scenario);
    await page.goto("/");
    await expect(page.getByText("验证完成——AC 已在你的电脑上正常运行")).toHaveCount(0);
  });
});

function extractTrial(memberMe: unknown): { analyses_remaining: number; questions_remaining: number } {
  const me = memberMe as { me: { trial: { analyses_remaining: number; questions_remaining: number } } };
  return me.me.trial;
}
