"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

import { attachCoachContext } from "@/lib/api";
import {
  coachLayoutMode,
  COACH_DEFAULT_WIDTH,
  COACH_MAX_WIDTH,
  COACH_MIN_WIDTH,
  COACH_WIDTH_STEP,
} from "@/lib/contracts";
import type { CoachAgentRunV1, ProviderProfileState } from "@/lib/types";
import { CoachPanel } from "./CoachPanel";

const SIDE_BY_SIDE_BREAKPOINT = 1160;
const OVERLAY_BREAKPOINT = 840;

export function CoachSidebar({
  analysisId,
  sessionId = null,
  capability,
  open,
  onClose,
  onWidthChange,
  pathname,
  softStartRun,
  state,
  width,
}: {
  analysisId: number | null;
  sessionId?: number | null;
  capability: "loading" | ProviderProfileState | "unavailable";
  open: boolean;
  onClose: () => void;
  onWidthChange: (width: number) => void;
  pathname: string;
  softStartRun: CoachAgentRunV1 | null;
  state: "open" | "closed";
  width: number;
}) {
  const [availableWidth, setAvailableWidth] = useState(
    () => typeof document === "undefined" ? 0 : document.documentElement.clientWidth,
  );
  const dragRef = useRef<{ pointerId: number; startWidth: number; startX: number } | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const update = () => setAvailableWidth(document.documentElement.clientWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const layout = useMemo(
    () => coachLayoutMode(availableWidth || SIDE_BY_SIDE_BREAKPOINT, width),
    [availableWidth, width],
  );
  const currentAnalysisRef = analysisId === null ? null : `analysis:${analysisId}`;

  const closeAndRestoreFocus = () => {
    onClose();
    window.requestAnimationFrame(() => returnFocusRef.current?.focus());
  };

  useEffect(() => {
    if (!open || layout.mode === "side-by-side") return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      dialogRef.current?.querySelector<HTMLElement>("button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])")?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [layout.mode, open]);

  const keepOverlayFocus = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndRestoreFocus();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])",
    )).filter((element) => element.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const resizeKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    onWidthChange(width + (event.key === "ArrowLeft" ? -COACH_WIDTH_STEP : COACH_WIDTH_STEP));
  };

  const startResize = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    dragRef.current = { pointerId: event.pointerId, startWidth: width, startX: event.clientX };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveResize = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    onWidthChange(drag.startWidth + drag.startX - event.clientX);
  };

  const finishResize = (event: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const attachCurrent = async (analysisRef: string) => {
    await attachCoachContext(
      { kind: "analysis", analysis_ref: analysisRef },
      sessionId == null ? {} : { sessionId },
    );
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-context-updated"));
  };

  const panel = (
    <CoachPanel
      capability={capability}
      currentAnalysisRef={currentAnalysisRef}
      sessionId={sessionId}
      layoutMode={layout.mode !== "side-by-side" ? (layout.mode === "full" || availableWidth < OVERLAY_BREAKPOINT ? "full" : "overlay") : "side-by-side"}
      onClose={layout.mode !== "side-by-side" ? closeAndRestoreFocus : undefined}
      onRequestContext={attachCurrent}
      pathname={pathname}
      softStartRun={softStartRun}
    />
  );

  if (layout.mode !== "side-by-side") {
    const mode = layout.mode === "full" || availableWidth < OVERLAY_BREAKPOINT ? "full" : "overlay";
    return (
      <div
        className="task6-coach-sidebar-wrap"
        data-mode={mode}
        data-state={state}
        style={{ "--task6-coach-width": `${layout.width}px` } as CSSProperties}
      >
        <div aria-hidden="true" className="task6-coach-scrim" onClick={open ? closeAndRestoreFocus : undefined} />
        <aside aria-hidden={!open || undefined} aria-label="Coach" className="task6-coach-sidebar" onKeyDown={keepOverlayFocus} ref={dialogRef} role="dialog">
          {panel}
        </aside>
      </div>
    );
  }

  return (
    <div
      className="task6-coach-sidebar-wrap"
      data-mode="side-by-side"
      data-state={state}
      style={{ "--task6-coach-width": `${layout.width}px` } as CSSProperties}
    >
      <aside aria-hidden={!open || undefined} aria-label="Coach" className="task6-coach-sidebar">
        <div
          aria-label="调整 Coach 宽度"
          aria-orientation="vertical"
          aria-valuemax={COACH_MAX_WIDTH}
          aria-valuemin={COACH_MIN_WIDTH}
          aria-valuenow={layout.width}
          className="task6-resizer"
          onKeyDown={resizeKeys}
          onLostPointerCapture={() => { dragRef.current = null; }}
          onPointerCancel={finishResize}
          onPointerDown={startResize}
          onPointerMove={moveResize}
          onPointerUp={finishResize}
          role="separator"
          tabIndex={0}
        />
        {panel}
      </aside>
    </div>
  );
}
