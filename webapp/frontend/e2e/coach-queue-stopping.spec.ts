import { expect, test, type Page, type Route } from "@playwright/test";

import { apiScenario, installApiFixtures, installDesktopBridge } from "../fixtures/task7-fixtures";

// 钉住 zh-CN，断言不随运行环境系统语言漂移（同 trial-verification.spec）。
test.use({ locale: "zh-CN" });

// Coach 队列跨会话隔离 + 停止状态 UI 的真实组件级行为回归
//（.zcode/system-review-20261016/REPORT.md 第 2/4 条）。
// 整条链路走真实 AppShell + CoachPanel + SessionRail：route 层模拟 agent-runs
// 合同（创建/查询/stop、状态翻转、run_stop_requested 事件），SSE 流 abort 让
// 前端走既有轮询兜底。全部隔离在本页 route 内：无真实 sidecar、无真实模型、
// 无外呼。断言面向用户可见行为与实际发出的 API 报文，不做源码正则。

type FixtureEvent = {
  schema_version: "coach_agent_run_event.v1";
  event_ref: string;
  sequence: number;
  type: "status";
  phase: string;
  code: string;
  message: string;
  payload: null;
  created_at: string;
};

type FixtureRun = {
  schema_version: "coach_agent_run.v1";
  run_ref: string;
  session_id: number;
  parent_run_ref: null;
  attempt: number;
  status: "queued" | "running" | "succeeded" | "failed" | "stopped";
  phase: string;
  partial_text: string | null;
  error: null;
  analysis_refs: string[];
  events: FixtureEvent[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

const NOW = "2026-10-17T10:00:00Z";
const STOP_REQUESTED_CODE = "run_stop_requested";

/**
 * 在 installApiFixtures 之上叠加 agent-runs 行为模拟（后注册者优先，未覆盖的
 * 请求 fallback 回既有 fixtures）。stop 按 2026-10-17 runtime 合同模拟：
 * 受理即追加 type=status/code=run_stop_requested 事件，status 保持
 * queued/running，真终态由测试显式翻转（或 stopTerminatesImmediately 立即收）。
 */
async function installAgentRunFixture(
  page: Page,
  opts: { stopTerminatesImmediately?: boolean; stopDelayMs?: number } = {},
) {
  const runs = new Map<string, FixtureRun>();
  const posts: Array<{ content: string; sessionId: number | null }> = [];
  let runSeq = 0;
  let eventSeq = 0;

  const fulfill = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const runByRoute = (route: Route): FixtureRun | undefined => {
    const segments = new URL(route.request().url()).pathname.split("/").filter(Boolean);
    return runs.get(decodeURIComponent(segments[2] ?? ""));
  };

  // SSE 不模拟：abort 让前端走轮询兜底（1s 节奏），行为等价且确定性强。
  await page.route(/\/v1\/agent-runs\/[^/]+\/stream/, (route) => route.abort());

  // POST /v1/agent-runs：创建 run，记录报文（content + session_id）。
  await page.route(/\/v1\/agent-runs$/, async (route) => {
    const request = route.request();
    if (request.method() !== "POST") return route.fallback();
    const body = request.postDataJSON() as { content?: string; session_id?: number };
    runSeq += 1;
    const run: FixtureRun = {
      schema_version: "coach_agent_run.v1",
      run_ref: `run:e2e${runSeq}`,
      session_id: body.session_id ?? 1,
      parent_run_ref: null,
      attempt: 1,
      status: "running",
      phase: "text_generation",
      partial_text: "正在生成……",
      error: null,
      analysis_refs: [],
      events: [],
      created_at: NOW,
      started_at: new Date().toISOString(),
      finished_at: null,
    };
    runs.set(run.run_ref, run);
    posts.push({ content: body.content ?? "", sessionId: body.session_id ?? null });
    await fulfill(route, run);
  });

  // POST /v1/agent-runs/:ref/stop：追加 stop 事件；status 按合同保持不变。
  await page.route(/\/v1\/agent-runs\/[^/]+\/stop/, async (route) => {
    const run = runByRoute(route);
    if (!run) return fulfill(route, { detail: "Not found" }, 404);
    if (run.status === "queued" || run.status === "running") {
      eventSeq += 1;
      run.events = [...run.events, {
        schema_version: "coach_agent_run_event.v1",
        event_ref: `evt:${eventSeq}`,
        sequence: eventSeq,
        type: "status",
        phase: run.phase,
        code: STOP_REQUESTED_CODE,
        message: "stop requested",
        payload: null,
        created_at: NOW,
      }];
      if (opts.stopTerminatesImmediately) {
        run.status = "stopped";
        run.phase = "completed";
        run.finished_at = NOW;
      }
    }
    if (opts.stopDelayMs) await new Promise((resolve) => setTimeout(resolve, opts.stopDelayMs));
    await fulfill(route, run);
  });

  // GET /v1/agent-runs/:ref：返回当前可变状态（轮询据此观察翻转）。
  // 注册在最后＝最先命中；非本形状的请求 fallback 给上面的具体处理器。
  await page.route(/\/v1\/agent-runs\/[^/]+/, async (route) => {
    const request = route.request();
    const segments = new URL(request.url()).pathname.split("/").filter(Boolean);
    if (request.method() === "GET" && segments.length === 3) {
      const run = runs.get(decodeURIComponent(segments[2] ?? ""));
      if (!run) return fulfill(route, { detail: "Not found" }, 404);
      return fulfill(route, run);
    }
    return route.fallback();
  });

  const mutate = (ref: string, status: FixtureRun["status"]) => {
    const run = runs.get(ref);
    if (!run) throw new Error(`fixture run missing: ${ref}`);
    run.status = status;
    run.phase = "completed";
    run.finished_at = NOW;
  };

  return {
    posts,
    runs,
    lastRunRef: () => `run:e2e${runSeq}`,
    finishSucceeded: (ref: string) => mutate(ref, "succeeded"),
    settleStopped: (ref: string) => mutate(ref, "stopped"),
    postsWithContent: (content: string) => posts.filter((post) => post.content === content),
  };
}

async function sendComposerMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
  await composer.fill(text);
  await composer.press("Enter");
}

/** rail 会话项（排除同名「归档/删除」动作按钮的歧义）。 */
function railSession(page: Page, title: string) {
  return page.locator("button.task7-session-rail__session").filter({ hasText: title });
}

test.describe("Coach queue session isolation（REPORT #4）", () => {
  test("A 排队的消息不被 B 消费，回 A 仍能找回并继续", async ({ page }) => {
    // 注册顺序＝命中优先级（Playwright 后注册先命中）：fixtures 先铺底，
    // agent-runs 模拟最后注册、优先命中，未覆盖请求 fallback 回铺底。
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario());
    const agentRuns = await installAgentRunFixture(page, { stopTerminatesImmediately: true });
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
    await expect(composer).toBeVisible();

    // ── A（primary，id 1）生成中排队第二条 ────────────────────────────────
    await sendComposerMessage(page, "A 的第一条");
    await expect.poll(() => agentRuns.posts.length, { timeout: 10_000 }).toBe(1);
    expect(agentRuns.posts[0]).toMatchObject({ content: "A 的第一条", sessionId: 1 });
    // A 的回合进入生成态：终止键出现。
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();

    await sendComposerMessage(page, "A 的第二条");
    // 运行中发送＝进可见队列 chips，不发起第二次 POST。
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);
    await expect(page.getByLabel("待发送队列")).toContainText("A 的第二条");
    await expect.poll(() => agentRuns.posts.length).toBe(1);

    // ── 切到 B（id 2）──组件保持挂载（rail 客户端路由，非整页刷新）────────
    await railSession(page, "1wall 6targets 复盘").click();
    await expect(page).toHaveURL(/sessionId=2/);
    await expect(railSession(page, "1wall 6targets 复盘")).toHaveAttribute("aria-current", "page");
    await expect(composer).toBeVisible();

    // ── B 发送并完成一个回合 ─────────────────────────────────────────────
    await sendComposerMessage(page, "B 的第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(2);
    expect(agentRuns.posts[1]).toMatchObject({ content: "B 的第一条", sessionId: 2 });
    agentRuns.finishSucceeded("run:e2e2");

    // 终态后轮询收敛；给自动放行足够窗口（轮询 1s + 放行链路）。
    await page.waitForTimeout(3_000);

    // 核心缺陷断言：A 排队的内容绝不因 B 的回合成功而被发送进 B。
    expect(agentRuns.postsWithContent("A 的第二条")).toEqual([]);
    expect(agentRuns.posts).toHaveLength(2);

    // B 里也绝不显示 A 的排队消息（隔离的可见面）。
    await expect(page.getByLabel("待发送队列")).toHaveCount(0);
    await expect(page.locator(".task6-queue-chip")).toHaveCount(0);

    // ── 回 A：排队消息仍在，可继续（立即发送进 A）────────────────────────
    await railSession(page, "长期训练").click();
    await expect(page).toHaveURL(/sessionId=1/);
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);
    await expect(page.getByLabel("待发送队列")).toContainText("A 的第二条");

    // 立即打断发送：A 的在途回合停止（立即终态）后，这条内容恰发一次、目标 A。
    await page.getByRole("button", { name: "立即打断发送" }).click();
    await expect
      .poll(() => agentRuns.postsWithContent("A 的第二条"), { timeout: 10_000 })
      .toHaveLength(1);
    expect(agentRuns.postsWithContent("A 的第二条")[0]).toMatchObject({ sessionId: 1 });
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(0);
  });

  test("自动放行接位的新回合不被上一回合的 settle 掐掉", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario());
    const agentRuns = await installAgentRunFixture(page);
    // 消息拉取加 400ms 延迟：上一回合 settle 的 refresh await 窗口里，自动
    // 放行的新回合已经接位——settle 不得把它清成孤儿（无 SSE 无停止键）。
    // 真实请求路径是 /v1/sessions/N（fixtures 内部才归一化成 /api/coach/…）。
    await page.route(/\/v1\/sessions\/\d+$/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 400));
      return route.fallback();
    });
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
    await expect(composer).toBeVisible();

    await sendComposerMessage(page, "第一问");
    await expect.poll(() => agentRuns.posts.length).toBe(1);
    await sendComposerMessage(page, "排队续问");
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);

    // 上一回合成功 → 自动放行消费队首 → 新回合接位。
    agentRuns.finishSucceeded("run:e2e1");
    await expect
      .poll(() => agentRuns.postsWithContent("排队续问"), { timeout: 10_000 })
      .toHaveLength(1);

    // settle 窗口（≥400ms 的 refresh 在途）过去后，新回合仍在位：终止键可见。
    await page.waitForTimeout(3_000);
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(0);

    // 收尾：放行回合正常终结，不悬置。
    agentRuns.finishSucceeded(agentRuns.lastRunRef());
    await expect(page.getByRole("button", { name: "停止生成" })).toBeHidden({ timeout: 10_000 });
  });

  test("A 的乐观气泡不跨会话拼进 B 的消息区", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario());
    const agentRuns = await installAgentRunFixture(page);
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
    await expect(composer).toBeVisible();

    // A 仍在生成时切到 B（fixture 对 B 返回空消息列表，POST 不持久化）：
    // A 的乐观气泡只存在于内存。
    await sendComposerMessage(page, "A 的第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(1);
    await railSession(page, "1wall 6targets 复盘").click();
    await expect(page).toHaveURL(/sessionId=2/);
    await expect(composer).toBeVisible();

    await sendComposerMessage(page, "B 的第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(2);
    agentRuns.finishSucceeded("run:e2e2");

    // B 终态 settle + refresh 落定后：B 消息区只有 B 自己的气泡，A 的乐观
    // 气泡绝不跨会话拼接（显示残留）。
    await page.waitForTimeout(3_000);
    await expect(page.getByText("B 的第一条")).toBeVisible();
    await expect(page.getByText("A 的第一条")).toHaveCount(0);
  });

  test("stop 回包在途时切会话：延迟回包不覆盖 B 的消息区", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario());
    const agentRuns = await installAgentRunFixture(page, { stopTerminatesImmediately: true, stopDelayMs: 800 });
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
    await expect(composer).toBeVisible();

    await sendComposerMessage(page, "A 的第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(1);
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();

    // stop 回包在途（800ms）时切到 B 并完成一个回合。
    await page.getByRole("button", { name: "停止生成" }).click();
    await railSession(page, "1wall 6targets 复盘").click();
    await expect(page).toHaveURL(/sessionId=2/);
    await expect(composer).toBeVisible();
    await sendComposerMessage(page, "B 的第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(2);
    agentRuns.finishSucceeded("run:e2e2");

    // 覆盖 stop 回包落地 + B 终态 settle + 全部刷新窗口：B 消息区仍是 B 的
    // 内容，A 会话的存量消息不得被旧闭包的 setRun/refresh 写进当前视图。
    // 断言圈定对话面板：rail 的会话预览（fixture 摘要＝同一句）不算串写。
    const conversation = page.locator(".task6-coach-panel");
    await page.waitForTimeout(4_000);
    await expect(conversation.getByText("B 的第一条")).toBeVisible();
    await expect(conversation.getByText("先稳定接近目标时的减速节奏，再复测同一场景。")).toHaveCount(0);
  });
});

test.describe("Coach stopping state（REPORT #2 前端面）", () => {
  test("停止受理到真终态之间：显示正在停止、不放行发送、立即钮不受理新意图", async ({ page }) => {
    // 按 runtime 新合同：stop 只挂事件不落地，status 保持 running 直到真终态。
    // 同上：fixtures 铺底在前，agent-runs 模拟最后注册、优先命中。
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario());
    const agentRuns = await installAgentRunFixture(page, { stopTerminatesImmediately: false });
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: "向 Coach 提问" });
    await expect(composer).toBeVisible();

    await sendComposerMessage(page, "停止测试第一条");
    await expect.poll(() => agentRuns.posts.length).toBe(1);
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();

    // 排队一条消息（停止后验证「立即」不受理）。
    await sendComposerMessage(page, "排队中的消息");
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);
    await expect.poll(() => agentRuns.posts.length).toBe(1);

    // 点击停止（composer 为空时终止键在位）：服务端受理（挂 run_stop_requested
    // 事件）但任务仍在生成。
    await page.getByRole("button", { name: "停止生成" }).click();
    await expect
      .poll(() => agentRuns.runs.get("run:e2e1")?.events.some((event) => event.code === STOP_REQUESTED_CODE) ?? false)
      .toBe(true);

    // 1) 停止窗口内「立即」不受理新意图：按钮禁用，不重复出站、不改待发
    //    登记、chip 保留（可取消/回填）。
    const sendNow = page.getByRole("button", { name: "立即打断发送" });
    await expect(sendNow).toBeDisabled();

    // 2) 停止窗口内草稿不被发送也不被转队：无新 POST、无新 chip、草稿保留。
    await composer.fill("停止期间的草稿");
    await composer.press("Enter");
    await page.waitForTimeout(1_500);
    expect(agentRuns.posts).toHaveLength(1);
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);
    await expect(composer).toHaveValue("停止期间的草稿");

    // 3) 用户可见的停止中表述：停止键让位为「正在停止」且不可再点。
    const stopping = page.getByRole("button", { name: "正在停止" });
    await expect(stopping).toBeVisible();
    await expect(stopping).toBeDisabled();

    // 4) 真终态（stopped）后解锁：partial 保留；停止窗口内「立即」未被受理，
    //    chip 保留，由用户手动「立即」恰发一次（本轮 UI 语义拍板）。
    agentRuns.settleStopped("run:e2e1");
    await expect(stopping).toBeHidden({ timeout: 10_000 });
    await expect(page.getByText("正在生成……")).toBeVisible();
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(1);
    await page.waitForTimeout(1_500);
    expect(agentRuns.postsWithContent("排队中的消息")).toEqual([]);

    await sendNow.click();
    await expect
      .poll(() => agentRuns.postsWithContent("排队中的消息"), { timeout: 10_000 })
      .toHaveLength(1);
    expect(agentRuns.postsWithContent("排队中的消息")[0]).toMatchObject({ sessionId: 1 });
    await expect(page.getByLabel("待发送队列").locator(".task6-queue-chip")).toHaveCount(0);

    // 放行产生的新回合收尾，composer 回到空闲，草稿可正常发送。
    agentRuns.finishSucceeded(agentRuns.lastRunRef());
    await page.waitForTimeout(2_500);
    await composer.fill("停止期间的草稿");
    await composer.press("Enter");
    await expect
      .poll(() => agentRuns.postsWithContent("停止期间的草稿"), { timeout: 10_000 })
      .toHaveLength(1);
  });
});
