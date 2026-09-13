/**
 * 首启「开场分析」触发（Intro Session，PRD §6.1.1）。
 *
 * 触发时序：onboarding 已完成 + 首进 Coach 工作区 → GET /coach/intro-session
 * 查 created/has_messages；flag 未置（created=false）→ POST 幂等创建并打开该
 * 会话（标题由 sidecar 定，前端不重复命名）。POST 同时由 sidecar 发出首条开场
 * 消息（kickoff run），前端不注入任何提示词。
 *
 * 自愈：created=true 但 has_messages=false 表示 flag 已置而开场分析仍空白
 * （当时 Provider 凭据不可用，sidecar 闸门拦下了 kickoff run）。这时同样走幂等
 * POST，sidecar 在 Provider 恢复后补发 kickoff——「一生只弹一次」仍由 flag 保证，
 * POST 幂等且 sidecar 有「已有消息就不重发」守卫，不会重复开讲。
 *
 * 整个链路请求失败一律静默降级（不打扰用户，仅经前端错误通道留痕），且
 * 「一生只弹一次」由 sidecar 的 flag 保证、前端 ref 守卫只兜住同一次挂载内的
 * 重复调用（StrictMode 双跑等）。
 *
 * 这里把「查询 + 判断 + 创建」抽成可注入依赖的纯逻辑，便于 node:test 直测；
 * React 侧只负责 ref 守卫与打开会话的路由复用。
 */

export type IntroSessionStatus = {
  created: boolean;
  session_id: number | null;
  /** 开场分析会话是否已有可见消息；flag 已置但为 false = 开场分析仍空白（待自愈）。 */
  has_messages: boolean;
};
export type IntroSessionCreated = {
  session_id: number;
  /** 本次 POST 是否新建了 kickoff run；null = 未新建（未就绪/已有消息/已有活跃 run）。 */
  run_ref: string | null;
  /** 当前档凭据此刻是否解析得出；false = 未创建 kickoff run，待 Provider 恢复。 */
  provider_ready: boolean;
};

export type IntroSessionDeps = {
  /** GET /coach/intro-session。 */
  getStatus: (signal?: AbortSignal) => Promise<IntroSessionStatus>;
  /** POST /coach/intro-session（幂等，返回既有或新建会话 id；sidecar 在 Provider 可用时发首条开场消息）。 */
  create: (signal?: AbortSignal) => Promise<IntroSessionCreated>;
  /** 触发的可观测痕迹（失败静默降级用）。 */
  onError?: (error: unknown) => void;
};

/**
 * 执行一次首启触发。返回会话 id：已有可见消息（has_messages=true）时返回
 * `existing`（可能为 null，表示 flag 已置但 id 缺失，前端不猜测、不开会话）；
 * created=false 或 created=true 但空白（has_messages=false）时都发幂等 POST，
 * 让 sidecar 在 Provider 可用时补跑开场分析。`created` 仅在本次真的新建会话
 * 时为 true（空白自愈时会话早已存在，返回 false，不重复打开）。任何失败都
 * 吞掉并回报 null。
 */
export async function triggerIntroSession(
  deps: IntroSessionDeps,
  signal?: AbortSignal,
): Promise<{ created: boolean; sessionId: number | null } | null> {
  try {
    const status = await deps.getStatus(signal);
    if (status.created && status.has_messages) {
      return { created: false, sessionId: status.session_id ?? null };
    }
    const created = await deps.create(signal);
    const sessionId = created.session_id;
    if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
      throw new Error("intro session response missing a valid session_id");
    }
    return { created: !status.created, sessionId };
  } catch (error) {
    if (isAbortError(error)) return null;
    deps.onError?.(error);
    return null;
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
