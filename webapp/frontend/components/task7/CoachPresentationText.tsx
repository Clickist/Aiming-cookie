"use client";

/**
 * Coach 讲解展示消费端（B 块）：
 * - 优先消费 sidecar 的 coach_presentation.v1 segments；
 * - 兼容期把原始回复文本按冻结 fixtures 同语义本地切分（sidecar 投影就绪后
 *   调用方直接传 presentation，本层无需改动）；
 * - text 段走既有 CoachMessageText 受限富文本；
 * - openui 段走受限 library 的 Renderer，并维护 rendering/ready/degraded/failed
 *   局部渲染状态与 per-block error boundary。
 */

import { Renderer, type ActionEvent, type OpenUIError, type ParseResult } from "@openuidev/react-lang";
import { Component, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CoachMessageText } from "@/components/task7/CoachMessageText";
import { useT, type MessageKey } from "@/lib/i18n";
import {
  canUseOpenUIActions,
  createCoachUiAction,
  deriveOpenUIRenderState,
  isStalePresentation,
  looksLikeOpenUIDSL,
  MAX_OPENUI_SEGMENTS,
  presentationFromText,
  toAssistantMessageFromEvent,
  type CoachGenerationStatus,
  type CoachPresentation,
  type CoachPresentationCause,
  type CoachPresentationOpenUiSegment,
  type CoachUiActionV1,
} from "@/lib/openui/presentation";
import { CoachOpenUIActionsEnabledContext, coachOpenUILibrary } from "@/lib/openui/library";
import { logOpenUIDiagnostic, openUIDiagnosticLine, type OpenUIDiagnosticContext } from "@/lib/openui/diagnostics";

type PresentationTextProps = {
  /** sidecar 投影就绪后的权威 segments。 */
  presentation?: CoachPresentation;
  /** 兼容期原始文本（含 openui-lang fence）。 */
  text?: string;
  messageId?: string | number | null;
  conversationId?: number | null;
  runId?: string | null;
  generationStatus?: CoachGenerationStatus;
  analysisRef?: string | null;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
  /** @ToAssistant 手势落点：由 CoachPanel 复用既有消息发送流程。 */
  onToAssistant?: (action: CoachUiActionV1) => void;
  actionsDisabled?: boolean;
  /** 流式光标等行内尾巴。 */
  tail?: ReactNode;
};

type SegmentBoundaryProps = {
  children: ReactNode;
  fallback: ReactNode;
  resetKey: string;
  onError: () => void;
};

class OpenUISegmentBoundary extends Component<
  SegmentBoundaryProps,
  { failed: boolean }
> {
  constructor(props: SegmentBoundaryProps) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(): void {
    this.props.onError();
  }

  componentDidUpdate(previous: SegmentBoundaryProps): void {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function noticeKey(
  state: "degraded" | "failed",
  cause: CoachPresentationCause | undefined,
  generationStatus: CoachGenerationStatus,
): MessageKey {
  if (state === "degraded") return "coach.presentation.partial";
  switch (cause) {
    case "unsupported_library":
      return "coach.presentation.unsupported";
    case "budget_exceeded":
      return "coach.presentation.budget";
    case "process_interrupted":
    case "stopped":
      return "coach.presentation.interrupted";
    default:
      return generationStatus === "failed"
        ? "coach.presentation.failed"
        : "coach.presentation.parse";
  }
}

function OpenUISegmentNotice({
  state,
  cause,
  generationStatus,
  diagnostic,
}: {
  state: "degraded" | "failed";
  cause?: CoachPresentationCause;
  generationStatus: CoachGenerationStatus;
  diagnostic?: OpenUIDiagnosticContext;
}) {
  const t = useT();
  const line = diagnostic ? openUIDiagnosticLine({ ...diagnostic, state, cause }) : null;
  useEffect(() => {
    if (line) logOpenUIDiagnostic(line);
  }, [line]);
  return (
    <div
      className="coach-openui-notice"
      data-cause={cause ?? state}
      data-state={state}
      role="note"
    >
      {t(noticeKey(state, cause, generationStatus))}
    </div>
  );
}

function CoachOpenUISegment({
  segment,
  generationStatus,
  messageId,
  diagnostic,
  actionsDisabled,
  onToAssistant,
}: {
  segment: CoachPresentationOpenUiSegment;
  generationStatus: CoachGenerationStatus;
  messageId: string | number | null;
  diagnostic: OpenUIDiagnosticContext;
  actionsDisabled: boolean;
  onToAssistant?: (action: CoachUiActionV1) => void;
}) {
  const [parsed, setParsed] = useState<{
    source: string;
    streaming: boolean;
    result: ParseResult | null;
  } | null>(null);
  const [renderErrors, setRenderErrors] = useState<{ source: string; errors: OpenUIError[] } | null>(null);
  const [boundaryFailedSource, setBoundaryFailedSource] = useState<string | null>(null);
  const streaming = generationStatus === "streaming";
  // 子 Renderer 在 effect 中回传解析结果；父级 effect 重置会覆盖同一帧的回传。
  const hasParsed = parsed?.source === segment.source && parsed.streaming === streaming;
  const parseResult = hasParsed ? parsed.result : null;
  const errors = renderErrors?.source === segment.source ? renderErrors.errors : [];
  const boundaryFailed = boundaryFailedSource === segment.source;

  const derived = deriveOpenUIRenderState({
    generationStatus,
    libraryId: segment.library_id,
    libraryVersion: segment.library_version,
    source: segment.source,
    hasParsed,
    rootPresent: parseResult?.root != null,
    parseErrors: parseResult?.meta.errors ?? [],
    renderErrors: errors,
    hasForbiddenCalls:
      (parseResult?.queryStatements.length ?? 0) > 0 ||
      (parseResult?.mutationStatements.length ?? 0) > 0,
  });

  const state = boundaryFailed ? "failed" : derived.state;
  const cause = boundaryFailed ? "render_error" : derived.cause;
  const actionsEnabled =
    !actionsDisabled &&
    !boundaryFailed &&
    Boolean(onToAssistant) &&
    canUseOpenUIActions(generationStatus, derived.state);
  const diagnosticLine = openUIDiagnosticLine({
    ...diagnostic,
    state,
    cause,
    library_id: segment.library_id,
    library_version: segment.library_version,
    source_length: segment.source.length,
    root_present: parseResult?.root != null,
    statement_count: parseResult?.meta.statementCount,
    errors: [
      ...(parseResult?.meta.errors ?? []),
      ...errors,
      ...(hasParsed && !parseResult?.root ? [{ code: "no-root" }] : []),
    ],
  });
  useEffect(() => {
    if (state === "rendering") return;
    logOpenUIDiagnostic(diagnosticLine);
  }, [state, diagnosticLine]);

  const handleAction = useCallback(
    (event: ActionEvent): void => {
      if (!actionsEnabled) return;
      const message = toAssistantMessageFromEvent(event);
      if (!message) return;
      onToAssistant?.(
        createCoachUiAction({
          messageId,
          segmentId: segment.id,
          message,
        }),
      );
    },
    [actionsEnabled, messageId, onToAssistant, segment.id],
  );

  const failureNotice = (
    <OpenUISegmentNotice
      cause={cause}
      generationStatus={generationStatus}
      state={state === "degraded" ? "degraded" : "failed"}
    />
  );

  // 解析/预算/库身份失败：不渲染 DSL，也不把客户端错误冒充生成失败。
  if (state === "failed") {
    return <div className="coach-openui-root">{failureNotice}</div>;
  }

  return (
    <div className="coach-openui-root" data-openui-state={state}>
      <CoachOpenUIActionsEnabledContext.Provider value={actionsEnabled}>
        <OpenUISegmentBoundary
          fallback={failureNotice}
          onError={() => setBoundaryFailedSource(segment.source)}
          resetKey={segment.source}
        >
          <Renderer
            isStreaming={generationStatus === "streaming"}
            library={coachOpenUILibrary}
            onAction={handleAction}
            onError={(nextErrors) => setRenderErrors({ source: segment.source, errors: nextErrors })}
            onParseResult={(result) => setParsed({ source: segment.source, streaming, result })}
            publishObservability={false}
            response={segment.source}
          />
        </OpenUISegmentBoundary>
      </CoachOpenUIActionsEnabledContext.Provider>
      {state === "degraded" ? failureNotice : null}
    </div>
  );
}

/** 同一 message 内旧 revision 不得覆盖新状态。 */
function useStablePresentation(
  presentation: CoachPresentation | undefined,
  fallback: CoachPresentation,
): CoachPresentation {
  const latestRef = useRef<CoachPresentation | null>(null);
  if (!presentation) return fallback;
  if (latestRef.current && isStalePresentation(latestRef.current, presentation)) {
    return latestRef.current;
  }
  latestRef.current = presentation;
  return presentation;
}

export function CoachPresentationText({
  presentation,
  text,
  messageId = null,
  conversationId = null,
  runId = null,
  generationStatus,
  analysisRef,
  onOpenVideo,
  onToAssistant,
  actionsDisabled = false,
  tail,
}: PresentationTextProps): ReactNode {
  const local = useMemo(
    () =>
      presentationFromText(text ?? "", {
        messageId,
        runId,
        generationStatus,
      }),
    [text, messageId, runId, generationStatus],
  );
  const resolved = useStablePresentation(presentation, local);
  const effectiveStatus = generationStatus && (
    resolved.generation_status === "streaming" ||
    generationStatus === "failed" || generationStatus === "stopped" || generationStatus === "interrupted"
  ) ? generationStatus : resolved.generation_status;
  const segments = resolved.segments;
  const lastIndex = segments.length - 1;
  const lastSegment = lastIndex >= 0 ? segments[lastIndex]! : null;
  let openuiCount = 0;

  return (
    <div className="coach-presentation">
      {segments.map((segment, index) => {
        const diagnostic: OpenUIDiagnosticContext = {
          conversation_id: conversationId,
          message_id: resolved.message_id ?? messageId,
          run_id: resolved.run_id ?? runId,
          segment_id: segment.id,
          revision: resolved.revision,
          generation_status: effectiveStatus,
        };
        if (segment.kind === "text") {
          return looksLikeOpenUIDSL(segment.text) ? (
            <OpenUISegmentNotice
              diagnostic={diagnostic}
              cause={
                resolved.cause === "budget_exceeded" ? "budget_exceeded" : "parse_error"
              }
              generationStatus={effectiveStatus}
              key={segment.id}
              state="failed"
            />
          ) : (
            <CoachMessageText
              analysisRef={analysisRef}
              key={segment.id}
              onOpenVideo={onOpenVideo}
              tail={index === lastIndex ? tail : undefined}
              text={segment.text}
            />
          );
        }
        openuiCount += 1;
        // 单回复默认 1 个 openui 区段（合同 §5.3）：sidecar 已做预算投影，
        // 前端对畸形/旧快照再兜一层，超限区段只呈现 cause，不静默展示。
        if (openuiCount > MAX_OPENUI_SEGMENTS) {
          return (
            <OpenUISegmentNotice
              diagnostic={diagnostic}
              cause="budget_exceeded"
              generationStatus={effectiveStatus}
              key={segment.id}
              state="failed"
            />
          );
        }
        return (
          <CoachOpenUISegment
            actionsDisabled={actionsDisabled}
            diagnostic={diagnostic}
            generationStatus={effectiveStatus}
            key={segment.id}
            messageId={resolved.message_id ?? messageId}
            onToAssistant={onToAssistant}
            segment={segment}
          />
        );
      })}
      {tail && lastSegment?.kind === "openui" ? <span>{tail}</span> : null}
    </div>
  );
}
