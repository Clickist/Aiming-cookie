// Turbopack 不解析 frontend 项目根外的 import，仓库级单一事实源
// design/opendesign-landing/changelog.json 由 scripts/sync-changelog.mjs
// （predev / prebuild 钩子，或 `npm run sync-changelog`）同步成本地生成物。
import changelogJson from "./changelog.generated.json";

// 应用内更新公告（右下通知卡，0924 拍板线框 v2）的单一数据与判定来源。
// 内容契约：design/opendesign-landing/changelog.json（应用 + 落地页同源），
// 客户端 UI 只消费 zh 字段；zh 内的副行用「——」与主行分隔（共享 schema
// 只有 zh/en 两个字符串字段，不为副行扩列）。

export const RELEASE_NOTICE_SEEN_KEY = "releaseNotice.lastSeenVersion";
export const RELEASE_NOTICE_MAX_ITEMS = 3;
export const CHANGELOG_URL = "https://aimingcookie.com/#changelog";

export type ReleaseNoticeItemType = "new" | "imp" | "fix";

export interface ReleaseNoticeItem {
  type: ReleaseNoticeItemType;
  zh: string;
  en: string;
}

export interface ReleaseNoticeEntry {
  version: string;
  date: string;
  items: ReleaseNoticeItem[];
}

export interface ChangelogData {
  versions: ReleaseNoticeEntry[];
}

/** 与落地页共享的更新日志内容（design/opendesign-landing/changelog.json）。 */
export const changelogData = changelogJson as ChangelogData;

/**
 * lastSeen 与当前版本不同（含从未记录）→ 弹；拿不到当前版本 → 不弹
 * （fail-closed：纯浏览器预览无版本号时静默）。
 */
export function shouldShowReleaseNotice(lastSeen: string | null, currentVersion: string): boolean {
  if (!currentVersion) return false;
  return lastSeen !== currentVersion;
}

/** 当前版本在 changelog 里有对应块才返回该块；找不到返回 null（不弹）。 */
export function findReleaseEntryIn(data: ChangelogData, version: string): ReleaseNoticeEntry | null {
  if (!version) return null;
  return data.versions.find((entry) => entry.version === version) ?? null;
}

/** 组件直接用：读仓库内共享 changelog 找当前版本块。 */
export function findReleaseEntry(version: string): ReleaseNoticeEntry | null {
  return findReleaseEntryIn(changelogData, version);
}

/** 条目截断：卡片最多展示前 3 条，其余在「查看完整更新日志」里看。 */
export function visibleReleaseItems(
  items: ReleaseNoticeItem[],
  max: number = RELEASE_NOTICE_MAX_ITEMS,
): ReleaseNoticeItem[] {
  return items.slice(0, max);
}

/** zh 主行与「——」后的副行拆分；无副行时 sub 为 null。 */
export function splitReleaseItemText(zh: string): { main: string; sub: string | null } {
  const index = zh.indexOf("——");
  if (index === -1) return { main: zh, sub: null };
  return { main: zh.slice(0, index), sub: zh.slice(index + "——".length) || null };
}

export function readReleaseNoticeSeen(storage: Pick<Storage, "getItem">): string | null {
  return storage.getItem(RELEASE_NOTICE_SEEN_KEY);
}

/** 用户点「知道了」后把当前版本记为已看，之后启动不再弹。 */
export function writeReleaseNoticeSeen(storage: Pick<Storage, "setItem">, version: string): void {
  storage.setItem(RELEASE_NOTICE_SEEN_KEY, version);
}
