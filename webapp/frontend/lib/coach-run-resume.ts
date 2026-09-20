/**
 * 刷新/重启后的「在途 run」复接钉扎（0919 P2 缺陷）。
 *
 * 缺陷现象：发送消息后刷新页面（停止按钮刚出现的窗口内），run 彻底从界面
 * 消失（无停止按钮、无流式、无"生成中"提示），而服务端回合照常跑完、回复
 * 已落库——用户必须切走再切回才能看到回复，体感"石沉大海"。
 *
 * 根因：run 状态只活在 CoachPanel 内存里，刷新后无人知道本会话还有 in-flight
 * 的 agent-run，SSE 不续订、终态无人收敛（连落库消息都没人拉）。
 * 这里把「会话 → 在途 run_ref」钉在 localStorage：受理发送时落盘，回合终态
 * 时清除；页面加载/会话绑定时回读，以 GET /v1/agent-runs/:ref 复接。
 *
 * 纯逻辑无 React 依赖（与 composer.ts 草稿信封同款），node:test 直测。
 * 持久化是增强不是功能依赖：存储不可用/数据损坏一律静默降级为"无钉扎"。
 */

/** localStorage 键：{ sessionId, runRef, pinnedAt } 数组，最新在后。 */
export const ACTIVE_RUN_STORAGE_KEY = "aiming-cookie.coach-active-runs";
/** 钉扎条数上限：只有"最近活跃过的少数会话"可能被刷新复接，防止无界增长。 */
const ACTIVE_RUN_PINS_MAX = 8;

export interface ActiveRunPin {
  sessionId: number;
  runRef: string;
  pinnedAt: number;
}

function storageAvailable(storage: Storage | null | undefined): storage is Storage {
  try {
    return Boolean(storage && typeof storage.getItem === "function");
  } catch {
    return false;
  }
}

function isActiveRunPin(value: unknown): value is ActiveRunPin {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { sessionId?: unknown; runRef?: unknown; pinnedAt?: unknown };
  return Number.isInteger(candidate.sessionId)
    && (candidate.sessionId as number) > 0
    && typeof candidate.runRef === "string"
    && candidate.runRef.startsWith("agent_run:")
    && typeof candidate.pinnedAt === "number";
}

/** 读取全部钉扎；损坏数据按空处理（单条损坏不牵连其余条目）。 */
export function readActiveRunPins(storage: Storage | null | undefined): ActiveRunPin[] {
  if (!storageAvailable(storage)) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(ACTIVE_RUN_STORAGE_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter(isActiveRunPin) : [];
  } catch {
    return [];
  }
}

function writeActiveRunPins(storage: Storage, pins: ActiveRunPin[]): void {
  try {
    if (pins.length === 0) {
      storage.removeItem(ACTIVE_RUN_STORAGE_KEY);
      return;
    }
    storage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify(pins.slice(-ACTIVE_RUN_PINS_MAX)));
  } catch {
    // 配额满/隐私模式等写失败必须静默：持久化是增强不是功能依赖。
  }
}

/** 本会话当前钉扎的在途 run_ref；无钉扎返回 null。 */
export function activeRunRefForSession(storage: Storage | null | undefined, sessionId: number): string | null {
  if (!Number.isInteger(sessionId) || sessionId <= 0) return null;
  const pins = readActiveRunPins(storage);
  for (let i = pins.length - 1; i >= 0; i -= 1) {
    const pin = pins[i];
    if (pin.sessionId === sessionId) return pin.runRef;
  }
  return null;
}

/** 落一条钉扎（同会话覆盖旧值，最新的排到末尾）。 */
export function pinActiveRunRef(
  storage: Storage | null | undefined,
  sessionId: number,
  runRef: string,
  now: number = Date.now(),
): void {
  if (!storageAvailable(storage)) return;
  if (!Number.isInteger(sessionId) || sessionId <= 0) return;
  if (typeof runRef !== "string" || !runRef.startsWith("agent_run:")) return;
  const kept = readActiveRunPins(storage).filter((pin) => pin.sessionId !== sessionId);
  writeActiveRunPins(storage, [...kept, { sessionId, runRef, pinnedAt: now }]);
}

/** 解除某会话的钉扎（回合终态、run 已丢失等）。 */
export function clearActiveRunRef(storage: Storage | null | undefined, sessionId: number): void {
  if (!storageAvailable(storage)) return;
  writeActiveRunPins(storage, readActiveRunPins(storage).filter((pin) => pin.sessionId !== sessionId));
}
