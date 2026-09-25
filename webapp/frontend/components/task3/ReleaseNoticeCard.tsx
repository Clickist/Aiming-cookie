"use client";

import { openExternalUrl } from "@/lib/desktop";
import { useT, type MessageKey } from "@/lib/i18n";
import { Button } from "@/ui/primitives";
import {
  CHANGELOG_URL,
  splitReleaseItemText,
  visibleReleaseItems,
  type ReleaseNoticeEntry,
  type ReleaseNoticeItemType,
} from "@/lib/release-notice";

// 更新公告右下通知卡（0924 拍板线框 v2，形态照
// .zcode/wireframes/release-announce-2026-09-24.html 视图 A）：kicker + 标题 +
// 标签条目列表 + 「知道了」主按钮 + 「查看完整更新日志」文字按钮。
// 条目内容来自共享 changelog（只消费 zh 字段），最多 3 条，超出部分经外链查看。
const TYPE_LABEL_KEYS: Record<ReleaseNoticeItemType, MessageKey> = {
  new: "release.notice.tagNew",
  imp: "release.notice.tagImp",
  fix: "release.notice.tagFix",
};

export function ReleaseNoticeCard({
  entry,
  onDismiss,
}: {
  entry: ReleaseNoticeEntry;
  onDismiss: () => void;
}) {
  const t = useT();
  const items = visibleReleaseItems(entry.items);
  return (
    <div aria-label={t("release.notice.ariaLabel")} className="task3-release-notice" role="dialog">
      <div className="task3-release-notice-head">
        <p className="task3-release-notice-kicker">{t("release.notice.kicker", { version: entry.version })}</p>
        <p className="task3-release-notice-title">{t("release.notice.title")}</p>
      </div>
      <ul className="task3-release-notice-list">
        {items.map((item, index) => {
          const text = splitReleaseItemText(item.zh);
          return (
            <li className="task3-release-notice-item" key={`${item.type}-${index}`}>
              <span className="task3-release-notice-tag" data-type={item.type}>
                {t(TYPE_LABEL_KEYS[item.type])}
              </span>
              <p>
                {text.main}
                {text.sub ? <small>{text.sub}</small> : null}
              </p>
            </li>
          );
        })}
      </ul>
      <div className="task3-release-notice-foot">
        <Button onClick={onDismiss} variant="primary">
          {t("release.notice.dismiss")}
        </Button>
        <button
          className="task3-release-notice-link"
          onClick={() => void openExternalUrl(CHANGELOG_URL)}
          type="button"
        >
          {t("release.notice.fullLog")}
        </button>
      </div>
    </div>
  );
}
