/**
 * coach_presentation.v1 —— Coach 讲解富展示的展示投影（ARCHITECTURE §5.3）。
 *
 * 展示边界只在渲染层消费；模型原始输出照旧整体落 JSONL，投影从原文现算，
 * 历史消息零迁移。本文件的形状是 sidecar 与前端共用的冻结合同，不要为了
 * 单个调用点增删字段。
 */

export const COACH_PRESENTATION_SCHEMA = "coach_presentation.v1" as const;
export type CoachPresentationSchema = typeof COACH_PRESENTATION_SCHEMA;

/**
 * 生成状态，不等于前端渲染成功（渲染状态由前端维护）。
 * 进程重启打断的未完成内容必须用 interrupted，不得冒充 completed。
 */
export type CoachGenerationStatus = "streaming" | "completed" | "stopped" | "interrupted" | "failed";

/** §5.3 冻结的 cause 枚举；前端渲染侧的 parse_error/render_error 等另由前端产生。 */
export type CoachPresentationCause =
  | "parse_error"
  | "unknown_component"
  | "render_error"
  | "unsupported_library"
  | "budget_exceeded"
  | "process_interrupted"
  | "stopped";

export type CoachPresentationTextSegment = {
  id: string;
  kind: "text";
  text: string;
};

export type CoachPresentationOpenUiSegment = {
  id: string;
  kind: "openui";
  /** 与 library_version 一起驱动前端 Renderer 与提示词生成（单一来源）。 */
  library_id: string;
  library_version: string;
  source: string;
};

export type CoachPresentationSegment = CoachPresentationTextSegment | CoachPresentationOpenUiSegment;

export type CoachPresentation = {
  schema_version: CoachPresentationSchema;
  /**
   * live 路径在 assistant 消息落库前还没有消息主键：run 是稳定替身；历史
   * 重放传会话消息 id（当前为数字）。未知时为 null，字段恒带。
   */
  message_id: string | number | null;
  run_id: string | null;
  /** 单调递增；旧 revision 不得覆盖新状态。 */
  revision: number;
  generation_status: CoachGenerationStatus;
  cause?: CoachPresentationCause;
  segments: CoachPresentationSegment[];
};

/**
 * UI 组件库身份。前端 Renderer 注册与提示词生成必须使用同一对值
 * （ARCHITECTURE §5.3 library 合同）。改值等于换库，两侧必须同步升级。
 */
export const COACH_OPENUI_LIBRARY_ID = "coach-openui";
export const COACH_OPENUI_LIBRARY_VERSION = "1.0.0";

/** OpenUI 官方 inlineMode 的语言标识，精确匹配（大小写敏感）。 */
export const OPENUI_FENCE_LANG = "openui-lang";

/** 单 UI 区段预算 ≤64 KiB（§5.3）；超限降级为 text 并带 cause。 */
export const MAX_OPENUI_SEGMENT_BYTES = 64 * 1024;

/** 单回复默认 1 个 openui 区段（§5.3）；第 2 个起降级为 text 并带 cause。 */
export const MAX_OPENUI_SEGMENTS = 1;

/**
 * 流式展示管道既有的 text 截断预算。分段投影后只作用于 text 段，
 * openui 段走独立预算——合法区段不会再被拦腰截断。
 */
export const TEXT_SEGMENT_TRANSPORT_LIMIT = 12_000;

// ── coach_ui_action.v1（§5.3 动作权限）─────────────────────────────────
//
// @ToAssistant 是「用户手势发起新回合」：前端把按钮 message 作为普通用户
// 输入发送，ui_action 只作来源标记随请求透传。sidecar 校验形状后仅落审计
// 事件，不改变回合输入语义——它不是动作总线，不直接执行产品命令。

export type CoachUiActionV1 = {
  schema_version: "coach_ui_action.v1";
  kind: "to_assistant";
  /** 渲染层实例键（messageKey:offset），由前端绑定，不信任模型自报。 */
  interaction_id: string;
  message_id: string | number | null;
  segment_id: string;
  /** 按钮携带的追问文本，作为用户输入进入既有 turn 流程。 */
  message: string;
};

const UI_ACTION_MAX_MESSAGE_CHARS = 2_000;
const UI_ACTION_MAX_ID_CHARS = 256;

/** HTTP 边界校验：形状与上限不符一律返回 null（调用方回 400）。 */
export function parseCoachUiActionV1(value: unknown): CoachUiActionV1 | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== "coach_ui_action.v1") return null;
  if (raw.kind !== "to_assistant") return null;
  const interactionId = raw.interaction_id;
  const messageId = raw.message_id;
  const segmentId = raw.segment_id;
  const message = raw.message;
  if (typeof interactionId !== "string" || !interactionId || interactionId.length > UI_ACTION_MAX_ID_CHARS) return null;
  if (
    messageId !== null &&
    !(typeof messageId === "string" && messageId.length > 0 && messageId.length <= UI_ACTION_MAX_ID_CHARS) &&
    !(typeof messageId === "number" && Number.isSafeInteger(messageId) && messageId > 0)
  ) return null;
  if (typeof segmentId !== "string" || !segmentId || segmentId.length > UI_ACTION_MAX_ID_CHARS) return null;
  if (typeof message !== "string" || !message.trim() || message.length > UI_ACTION_MAX_MESSAGE_CHARS) return null;
  return {
    schema_version: "coach_ui_action.v1",
    kind: "to_assistant",
    interaction_id: interactionId,
    message_id: messageId,
    segment_id: segmentId,
    message,
  };
}
