"use client";

import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";

import { attachCoachContext } from "@/lib/api";
import { coachLayoutMode } from "@/lib/contracts";
import type { ProviderProfileState } from "@/lib/types";
import { Drawer } from "@/ui/primitives";

import { CoachPanel } from "./CoachPanel";

const WIDTH_KEY = "aiming-cookie.ui.coach-width";
const MIN_WIDTH = 320;
const DEFAULT_WIDTH = 360;
const MAX_WIDTH = 480;
const WIDTH_STEP = 16;
const SIDE_BY_SIDE_BREAKPOINT = 1160;
const OVERLAY_BREAKPOINT = 840;

function clampWidth(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value));
}

export function CoachSidebar({
  capability,
  open,
  onClose,
  pathname,
}: {
  capability: "loading" | ProviderProfileState | "unavailable";
  open: boolean;
  onClose: () => void;
  pathname: string;
}) {
  const [availableWidth, setAvailableWidth] = useState(0);
  const [width, setWidth] = useState(DEFAULT_WIDTH);

  useEffect(() => {
    const stored = Number(window.localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) setWidth(clampWidth(stored));
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

  const setCoachWidth = (next: number) => {
    const clamped = clampWidth(next);
    setWidth(clamped);
    window.localStorage.setItem(WIDTH_KEY, String(clamped));
  };

  const resizeKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    setCoachWidth(width + (event.key === "ArrowLeft" ? -WIDTH_STEP : WIDTH_STEP));
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

  if (!open) return null;
  if (layout.mode !== "side-by-side") {
    return (
      <div className="task6-coach-drawer" data-mode={layout.mode === "full" || availableWidth < OVERLAY_BREAKPOINT ? "full" : "overlay"}>
        <Drawer onClose={onClose} open title="Coach">
          {layout.mode === "full" ? <button className="task6-back-workspace" onClick={onClose} type="button">← 返回主工作区</button> : null}
          {panel}
        </Drawer>
      </div>
    );
  }

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
        aria-valuemax={MAX_WIDTH}
        aria-valuemin={MIN_WIDTH}
        aria-valuenow={layout.width}
        className="task6-resizer"
        onKeyDown={resizeKeys}
        role="separator"
        tabIndex={0}
      />
      <header className="task6-coach-header">
        <div><strong>Coach</strong><small>长期训练关系</small></div>
        <div className="task6-width-presets" aria-label="Coach 宽度预设">
          <button onClick={() => setCoachWidth(MIN_WIDTH)} type="button">窄</button>
          <button onClick={() => setCoachWidth(DEFAULT_WIDTH)} type="button">默认</button>
          <button onClick={() => setCoachWidth(MAX_WIDTH)} type="button">宽</button>
        </div>
        <button aria-label="收起 Coach" onClick={onClose} type="button">×</button>
      </header>
      {panel}
    </aside>
  );
}
