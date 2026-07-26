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
import type { ProviderProfileState } from "@/lib/types";
import { Drawer } from "@/ui/primitives";

import { CoachPanel } from "./CoachPanel";

const SIDE_BY_SIDE_BREAKPOINT = 1160;
const OVERLAY_BREAKPOINT = 840;

export function CoachSidebar({
  capability,
  open,
  onClose,
  onWidthChange,
  pathname,
  width,
}: {
  capability: "loading" | ProviderProfileState | "unavailable";
  open: boolean;
  onClose: () => void;
  onWidthChange: (width: number) => void;
  pathname: string;
  width: number;
}) {
  const [availableWidth, setAvailableWidth] = useState(0);
  const dragRef = useRef<{ pointerId: number; startWidth: number; startX: number } | null>(null);

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
  const currentAnalysisRef = pathname.startsWith("/analysis/")
    ? `analysis:${pathname.split("/")[2]}`
    : null;

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
    await attachCoachContext({ kind: "analysis", analysis_ref: analysisRef });
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-context-updated"));
  };

  const panel = (
    <CoachPanel
      capability={capability}
      currentAnalysisRef={currentAnalysisRef}
      onRequestContext={attachCurrent}
    />
  );

  if (layout.mode !== "side-by-side") {
    return (
      <div className="task6-coach-drawer" data-mode={layout.mode === "full" || availableWidth < OVERLAY_BREAKPOINT ? "full" : "overlay"}>
        <Drawer onClose={onClose} open={open} title="Coach">
          {layout.mode === "full" ? <button className="task6-back-workspace" onClick={onClose} type="button">← 返回主工作区</button> : null}
          {panel}
        </Drawer>
      </div>
    );
  }

  if (!open) return null;

  return (
    <aside
      aria-label="Coach"
      className="task6-coach-sidebar"
      data-mode="side-by-side"
      style={{ "--task6-coach-width": `${layout.width}px` } as CSSProperties}
    >
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
      <header className="task6-coach-header">
        <div><strong>Coach</strong><small>长期训练关系</small></div>
        <div className="task6-width-presets" aria-label="Coach 宽度预设">
          <button onClick={() => onWidthChange(COACH_MIN_WIDTH)} type="button">窄</button>
          <button onClick={() => onWidthChange(COACH_DEFAULT_WIDTH)} type="button">默认</button>
          <button onClick={() => onWidthChange(COACH_MAX_WIDTH)} type="button">宽</button>
        </div>
        <button aria-label="收起 Coach" onClick={onClose} type="button">×</button>
      </header>
      {panel}
    </aside>
  );
}
