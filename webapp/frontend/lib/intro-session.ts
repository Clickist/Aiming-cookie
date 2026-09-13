/**
 * 首启「开场分析」触发（Intro Session，PRD §6.1.1）。
 *
 * 触发时序：onboarding 已完成 + 首进 Coach 工作区 → GET /coach/intro-session
 * 查 created；created=false → POST 幂等创建并打开该会话（标题由 sidecar 定，
 * 前端不重复命名）。POST 同时由 sidecar 发出首条开场消息（kickoff run），
 * 前端不注入任何提示词。整个链路请求失败一律静默降级（不打扰用户，仅经前端
 * 错误通道留痕），且「一生只弹一次」由 sidecar 的 flag 保证、前端 ref 守卫
 * 只兜住同一次挂载内的重复调用（StrictMode 双跑等）。
 *
 * 这里把「查询 + 判断 + 创建」抽成可注入依赖的纯逻辑，便于 node:test 直测；
 * React 侧只负责 ref 守卫与打开会话的路由复用。
 */

export type IntroSessionStatus = { created: boolean; session_id: number | null };
export type IntroSessionCreated = { session_id: number };

export type IntroSessionDeps = {
  /** GET /coach/intro-session。 */
  getStatus: (signal?: AbortSignal) => Promise<IntroSessionStatus>;
  /** POST /coach/intro-session（幂等，返回既有或新建会话 id；sidecar 同时发首条开场消息）。 */
  create: (signal?: AbortSignal) => Promise<IntroSessionCreated>;
  /** 触发的可观测痕迹（失败静默降级用）。 */
  onError?: (error: unknown) => void;
};

/**
 * 执行一次首启触发。返回会话 id：created 已 true 时返回 `existing`（可能为
 * null，表示 flag 已置但 id 缺失，前端不猜测、不开会话）；需要创建时返回
 * POST 结果。任何失败都吞掉并回报 null。
 */
export async function triggerIntroSession(
  deps: IntroSessionDeps,
  signal?: AbortSignal,
): Promise<{ created: boolean; sessionId: number | null } | null> {
  try {
    const status = await deps.getStatus(signal);
    if (status.created) {
      return { created: false, sessionId: status.session_id ?? null };
    }
    const created = await deps.create(signal);
    const sessionId = created.session_id;
    if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
      throw new Error("intro session response missing a valid session_id");
    }
    return { created: true, sessionId };
  } catch (error) {
    if (isAbortError(error)) return null;
    deps.onError?.(error);
    return null;
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
