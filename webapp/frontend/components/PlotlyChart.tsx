"use client";

/**
 * Reusable Plotly wrapper for backend-provided figures.
 *
 * The backend (kovaak_tracker/coach/visualization.py) returns plotly
 * graph_objects.Figure objects; worker.py serializes them via `.to_dict()`,
 * so on the wire each figure is `{ data: [...], layout: {...} }`. We feed that
 * straight to react-plotly.js — no transform layer needed.
 *
 * Loaded with next/dynamic { ssr: false } because react-plotly.js's default
 * import touches `window` at module-eval time, which breaks RSC prerender.
 */

import dynamic from "next/dynamic";
import type { PlotlyFigure } from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full min-h-[200px] text-label-sm text-on-surface-variant">
      加载图表中…
    </div>
  ),
});

export interface PlotlyChartProps {
  figure: PlotlyFigure;
  className?: string;
  /** Override layout with dark-theme defaults (paper/plot bg, font color). */
  darkTheme?: boolean;
}

/**
 * Default dark-theme layout overrides applied on top of the figure's own
 * layout (paper_bgcolor / plot_bgcolor / font.color). Pass `darkTheme={false}`
 * to render the figure with its original layout untouched.
 */
const DARK_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#e6e5e0", family: "Inter, ui-sans-serif, system-ui" },
  margin: { l: 40, r: 24, t: 40, b: 40 },
} as const;

export default function PlotlyChart({
  figure,
  className,
  darkTheme = true,
}: PlotlyChartProps) {
  const layout = darkTheme
    ? { ...DARK_LAYOUT, ...(figure.layout as object) }
    : (figure.layout as object);

  return (
    <Plot
      data={figure.data as never[]}
      layout={layout}
      config={{
        displaylogo: false,
        responsive: true,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
      }}
      useResizeHandler
      className={className ?? "w-full h-full"}
    />
  );
}
