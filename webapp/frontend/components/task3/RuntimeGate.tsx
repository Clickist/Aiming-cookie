"use client";

import { type ReactNode, useEffect, useState } from "react";

import {
  awaitDesktopRuntimeConnection,
  exportDesktopCaptureDiagnostics,
  isDesktopRuntime,
  RUNTIME_FAILED_CODE,
} from "@/lib/desktop";
import { useT } from "@/lib/i18n";
import { Button, ErrorState } from "@/ui/primitives";

type GateState = "starting" | "ready" | "failed";

// 启动闸门（2026-10-06 启动闪退案的结构性修复前端侧）：runtime 由 Tauri 在
// 后台线程拉起、窗口秒开，就绪前应用不挂载——避免启动期请求全部打在
// 「runtime 未就绪」上。启动屏 = logo + 流动进度条（进度不可知，不承诺
// 百分比，点点 1006 拍板 A 型）+ 慢启动提示；起不来（runtime.failed）时
// 窗口必须活着并给出出口：文案指路重启应用，附一键导出诊断包（导出走
// Tauri 命令，不依赖 backend，正是 backend 起不来的第一现场取证）。
// hydration 合同：首帧不得依赖 window（SSR 与客户端首帧必须同形），所以
// 初始恒为 starting，挂载后再分流——浏览器预览放行，桌面开始轮询。
export function RuntimeGate({ children }: { children: ReactNode }) {
  const t = useT();
  const [state, setState] = useState<GateState>("starting");
  const [slowStarted, setSlowStarted] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);

  useEffect(() => {
    if (!isDesktopRuntime()) {
      setState("ready");
      return undefined;
    }
    let cancelled = false;
    void (async () => {
      try {
        await awaitDesktopRuntimeConnection();
        if (!cancelled) setState("ready");
      } catch (error) {
        if (!cancelled && error === RUNTIME_FAILED_CODE) setState("failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 启动超过 15s 提示可能被杀毒软件扫描：只描述「还没好」，不断言原因。
  useEffect(() => {
    if (state !== "starting") return undefined;
    const timer = window.setTimeout(() => setSlowStarted(true), 15_000);
    return () => window.clearTimeout(timer);
  }, [state]);

  if (state === "ready") return <>{children}</>;

  if (state === "failed") {
    const handleExport = () => {
      setExportNote(null);
      exportDesktopCaptureDiagnostics()
        .then((path) => setExportNote(path ? t("desktop.runtime.exportDone") : null))
        .catch(() => setExportNote(t("desktop.runtime.exportFailed")));
    };
    return (
      <div className="task3-runtime-gate">
        <ErrorState
          title={t("desktop.runtime.failedTitle")}
          style={{ maxWidth: 480, padding: "var(--space-6)" }}
        >
          <img
            className="task3-runtime-gate__mark"
            style={{ height: 52, marginBottom: "var(--space-3)", width: 52 }}
            src="/logo-mark.png"
            alt=""
          />
          <p style={{ lineHeight: 1.7, margin: "0 0 var(--space-4)" }}>
            {t("desktop.runtime.failedBody")}
          </p>
          <Button onClick={handleExport} variant="secondary">
            {t("desktop.runtime.exportDiagnostics")}
          </Button>
          {exportNote ? (
            <p style={{ margin: "var(--space-4) 0 0" }}>{exportNote}</p>
          ) : null}
        </ErrorState>
      </div>
    );
  }

  return (
    <div className="task3-runtime-gate">
      <div className="task3-runtime-gate__col">
        <img className="task3-runtime-gate__mark" src="/logo-mark.png" alt="" />
        <div className="task3-runtime-gate__brand">Aiming Cookie</div>
        <div className="task3-runtime-gate__track">
          <div className="task3-runtime-gate__thumb" />
        </div>
        <div className="task3-runtime-gate__status">{t("desktop.runtime.starting")}</div>
        {slowStarted ? (
          <div className="task3-runtime-gate__hint">{t("desktop.runtime.slowHint")}</div>
        ) : null}
      </div>
    </div>
  );
}
