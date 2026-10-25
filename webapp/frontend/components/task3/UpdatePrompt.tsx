"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useT } from "@/lib/i18n";
import { formatDownloadedMb, formatDownloadSpeed, type DesktopUpdate, type DesktopUpdateProgress } from "@/lib/updater";
import { Button } from "@/ui/primitives";

type UpdatePhase = "ready" | "installing" | "failed";

// 启动静默检查命中新版本后的右下角提示卡：安装（下载 → 被动安装 → 自动重启）
// 在桌面壳内完成，不打断当前会话内容；关闭只是本次启动内不再打扰。
// blocked（Coach 回合活跃）时卡片照常出现，但安装按钮禁用并换说明文案：
// 安装会重启应用打断在途回合，回合结束后再装。
export function UpdatePrompt({
  update,
  onDismiss,
  blocked = false,
}: {
  update: DesktopUpdate;
  onDismiss: () => void;
  /** Coach 回合活跃：安装会重启应用打断回复，按钮禁用并换说明。 */
  blocked?: boolean;
}) {
  const [phase, setPhase] = useState<UpdatePhase>("ready");
  const [progress, setProgress] = useState<DesktopUpdateProgress | null>(null);
  // Progress 事件高频：最新值进 ref，rAF 合帧后再 setState，防渲染风暴；
  // Finished 是终态，直接落 state（安装随时可能触发重启，不等下一帧）。
  const progressRef = useRef<DesktopUpdateProgress | null>(null);
  const frameRef = useRef<number | null>(null);
  const t = useT();

  const flushProgress = useCallback(() => {
    frameRef.current = null;
    setProgress(progressRef.current);
  }, []);

  // 卸载时取消在途渲染帧，不对已卸载组件 setState。
  useEffect(
    () => () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  const install = useCallback(async () => {
    setPhase("installing");
    setProgress(null);
    progressRef.current = null;
    try {
      await update.install((p) => {
        progressRef.current = p;
        if (p.finished) {
          if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
          frameRef.current = null;
          setProgress(p);
          return;
        }
        if (frameRef.current == null) frameRef.current = requestAnimationFrame(flushProgress);
      });
      // 安装成功时进程会重启；走到这里说明重启未发生，保持安装态由用户自行处理。
    } catch {
      setProgress(null);
      progressRef.current = null;
      setPhase("failed");
    }
  }, [update, flushProgress]);

  // 下载中显示进度条 + 元信息（update-download-progress 视觉稿）：
  // 左 = 已下载/总量（总大小未知时只显已下载），右 = 百分比 · 速度（完成只显 100%）。
  const p = progress ?? { contentLength: null, downloadedBytes: 0, bytesPerSecond: 0, finished: false };
  const ratio = p.contentLength ? Math.min(1, p.downloadedBytes / p.contentLength) : null;
  const percent = ratio != null ? Math.floor(ratio * 100) : null;
  const bytesLabel =
    p.contentLength != null
      ? `${formatDownloadedMb(p.downloadedBytes)} / ${formatDownloadedMb(p.contentLength)}`
      : formatDownloadedMb(p.downloadedBytes);
  const rightLabel = p.finished
    ? "100%"
    : percent != null
      ? `${percent}% · ${formatDownloadSpeed(p.bytesPerSecond)}`
      : formatDownloadSpeed(p.bytesPerSecond);
  return (
    <div aria-label={t("update.prompt.ariaLabel")} className="task3-update-prompt" role="alertdialog">
      <p className="task3-update-prompt-title">{t("update.prompt.title", { version: update.version })}</p>
      <p className="task3-update-prompt-note">
        {phase === "installing"
          ? progress?.finished
            ? t("update.prompt.noteDownloadedInstalling")
            : t("update.prompt.noteDownloading")
          : phase === "failed"
            ? t("update.prompt.noteFailed")
            : blocked
              ? t("update.prompt.noteCoachBusy")
              : t("update.prompt.noteReady")}
      </p>
      {phase === "installing" ? (
        <>
          {/* contentLength 未知 → 不确定态滑动；已知 → 按比例出条，完成即满条。 */}
          <div className="task3-update-prompt-bar" data-indeterminate={ratio == null || undefined}>
            <i style={ratio != null ? { width: `${(ratio * 100).toFixed(1)}%` } : undefined} />
          </div>
          <div aria-live="polite" className="task3-update-prompt-meta">
            <span>{bytesLabel}</span>
            <span className="task3-update-prompt-meta-strong">{rightLabel}</span>
          </div>
        </>
      ) : null}
      <div className="task3-update-prompt-actions">
        {/* 安装中按钮保留但禁用（视觉稿）：插件无取消能力，界面不提供取消入口。 */}
        <Button disabled={phase === "installing" || blocked} onClick={() => void install()} size="compact">
          {phase === "failed" ? t("update.prompt.retry") : t("update.prompt.installNow")}
        </Button>
        <Button disabled={phase === "installing"} onClick={onDismiss} size="compact" variant="secondary">
          {t("update.prompt.later")}
        </Button>
      </div>
    </div>
  );
}
