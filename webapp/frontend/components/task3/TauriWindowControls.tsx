"use client";

import type { MouseEvent } from "react";

type WindowControlAction = "minimize" | "toggleMaximize" | "close";

async function getTauriWindow() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  return getCurrentWindow();
}

async function runWindowControl(action: WindowControlAction) {
  const appWindow = await getTauriWindow();
  if (!appWindow) return;
  if (action === "minimize") {
    await appWindow.minimize();
    return;
  }
  if (action === "toggleMaximize") {
    await appWindow.toggleMaximize();
    return;
  }
  await appWindow.close();
}

export async function startWindowDragging() {
  const appWindow = await getTauriWindow();
  if (!appWindow) return;
  await appWindow.startDragging();
}

function stopTitleBarDrag(event: MouseEvent<HTMLDivElement>) {
  event.stopPropagation();
}

export function TauriWindowControls() {
  return (
    <div aria-label="窗口控制" className="task3-window-controls" onMouseDown={stopTitleBarDrag}>
      <button
        aria-label="最小化"
        className="task3-window-control"
        onClick={() => void runWindowControl("minimize")}
        title="最小化"
        type="button"
      >
        <span aria-hidden="true">-</span>
      </button>
      <button
        aria-label="最大化或还原"
        className="task3-window-control"
        onClick={() => void runWindowControl("toggleMaximize")}
        title="最大化或还原"
        type="button"
      >
        <span aria-hidden="true">□</span>
      </button>
      <button
        aria-label="关闭"
        className="task3-window-control task3-window-control--close"
        onClick={() => void runWindowControl("close")}
        title="关闭"
        type="button"
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}
