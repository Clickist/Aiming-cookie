import { useT } from "@/lib/i18n";
import { KOVAAK_STEAM_URL } from "@/lib/kovaak-install-guide";
import { Notice } from "@/ui/primitives";

/**
 * 「未检测到 KovaaK's」引导卡（1002 方案 A）：无 KovaaK 新用户的主界面
 * 泄漏点修复——提示分析依赖 KovaaK 训练数据，带 Steam 商店链接与手动
 * 指定目录的小字。显示/消失由 lib/kovaak-install-guide 的判定与 hook
 * 负责，本组件只管渲染。
 *
 * 两个变体跟宿主页的空态风格：
 * - `hero`（默认）：Coach 首页空态，task6-kovaak-guide 线框卡（task6.css）；
 * - `notice`：History 空态，复用全局 Notice 原语（与该页其他提示一致）。
 */
export function KovaakInstallGuideCard({ variant = "hero" }: { variant?: "hero" | "notice" }) {
  const t = useT();
  const body = (
    <>
      <p>
        {t("kovaak.guide.body")}{" "}
        <a href={KOVAAK_STEAM_URL} rel="noreferrer" target="_blank">
          {t("kovaak.guide.storeLink")}
        </a>
      </p>
      <p className="kovaak-install-guide-hint">{t("kovaak.guide.hint")}</p>
    </>
  );
  if (variant === "notice") {
    return (
      <Notice title={t("kovaak.guide.title")} tone="info">
        {body}
      </Notice>
    );
  }
  return (
    <aside className="task6-kovaak-guide">
      <strong>{t("kovaak.guide.title")}</strong>
      {body}
    </aside>
  );
}
