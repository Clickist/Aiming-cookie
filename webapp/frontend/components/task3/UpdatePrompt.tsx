"use client";

import { useCallback, useState } from "react";

import type { DesktopUpdate } from "@/lib/updater";
import { Button } from "@/ui/primitives";

type UpdatePhase = "ready" | "installing" | "failed";

// 启动静默检查命中新版本后的右下角提示卡：安装（下载 → 被动安装 → 自动重启）
// 在桌面壳内完成，不打断当前会话内容；关闭只是本次启动内不再打扰。
export function UpdatePrompt({
  update,
  onDismiss,
}: {
  update: DesktopUpdate;
  onDismiss: () => void;
}) {
  const [phase, setPhase] = useState<UpdatePhase>("ready");
  const install = useCallback(async () => {
    setPhase("installing");
    try {
      await update.install();
      // 安装成功时进程会重启；走到这里说明重启未发生，保持安装态由用户自行处理。
    } catch {
      setPhase("failed");
    }
  }, [update]);
  return (
    <div aria-label="应用更新提示" className="task3-update-prompt" role="alertdialog">
      <p className="task3-update-prompt-title">发现新版本 {update.version}</p>
      <p className="task3-update-prompt-note">
        {phase === "installing"
          ? "正在下载并安装，完成后应用会自动重启…"
          : phase === "failed"
            ? "更新失败，请确认网络后在设置的「应用更新」里重试。"
            : "下载官方安装包并自动重启完成升级。"}
      </p>
      <div className="task3-update-prompt-actions">
        {phase === "installing" ? (
          <span aria-live="polite" className="task3-update-prompt-busy">
            处理中…
          </span>
        ) : (
          <>
            <Button onClick={() => void install()} size="compact">
              {phase === "failed" ? "重试" : "立即更新"}
            </Button>
            <Button onClick={onDismiss} size="compact" variant="secondary">
              稍后再说
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
