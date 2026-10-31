/**
 * coach_presentation.v1（ARCHITECTURE §5.3）的 sidecar 实现入口。
 * 展示链的四条路径（partial 流、SSE/轮询事件、完成响应、历史重放）
 * 都从这里取同一份投影语义。
 */

export {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  COACH_PRESENTATION_SCHEMA,
  MAX_OPENUI_SEGMENT_BYTES,
  MAX_OPENUI_SEGMENTS,
  OPENUI_FENCE_LANG,
  TEXT_SEGMENT_TRANSPORT_LIMIT,
  parseCoachUiActionV1,
  type CoachGenerationStatus,
  type CoachPresentation,
  type CoachPresentationCause,
  type CoachPresentationOpenUiSegment,
  type CoachPresentationSegment,
  type CoachPresentationTextSegment,
  type CoachUiActionV1,
} from "./types.ts";

export { normalizeUserFacingText } from "./normalize-text.ts";

export {
  PresentationSplitter,
  applyPresentationTransportBudget,
  projectGenerationStatus,
  splitPresentation,
  type SplitPresentationOptions,
} from "./split.ts";
