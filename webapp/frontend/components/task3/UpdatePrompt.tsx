"use client";

import { useCallback, useState } from "react";

import { useT } from "@/lib/i18n";
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
  const t = useT();
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
    <div aria-label={t("update.prompt.ariaLabel")} className="task3-update-prompt" role="alertdialog">
      <p className="task3-update-prompt-title">{t("update.prompt.title", { version: update.version })}</p>
      <p className="task3-update-prompt-note">
        {phase === "installing"
          ? t("update.prompt.noteInstalling")
          : phase === "failed"
            ? t("update.prompt.noteFailed")
            : t("update.prompt.noteReady")}
      </p>
      <div className="task3-update-prompt-actions">
        {phase === "installing" ? (
          <span aria-live="polite" className="task3-update-prompt-busy">
            {t("update.prompt.busy")}
          </span>
        ) : (
          <>
            <Button onClick={() => void install()} size="compact">
              {phase === "failed" ? t("update.prompt.retry") : t("update.prompt.installNow")}
            </Button>
            <Button onClick={onDismiss} size="compact" variant="secondary">
              {t("update.prompt.later")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
