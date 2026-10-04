import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHANGELOG_URL,
  RELEASE_NOTICE_MAX_ITEMS,
  RELEASE_NOTICE_SEEN_KEY,
  changelogData,
  findReleaseEntryIn,
  readReleaseNoticeSeen,
  shouldShowReleaseNotice,
  splitReleaseItemText,
  visibleReleaseItems,
  writeReleaseNoticeSeen,
  type ChangelogData,
  type ReleaseNoticeItem,
} from "./release-notice";

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => (map.has(key) ? (map.get(key) as string) : null),
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  };
}

const item = (type: ReleaseNoticeItem["type"], zh: string): ReleaseNoticeItem => ({
  type,
  zh,
  en: zh,
});

test("版本对比：lastSeen 缺失或与当前版本不同 → 弹；相同 → 不弹", () => {
  assert.equal(shouldShowReleaseNotice(null, "1.3.0"), true);
  assert.equal(shouldShowReleaseNotice("1.2.4", "1.3.0"), true);
  assert.equal(shouldShowReleaseNotice("1.3.0", "1.3.0"), false);
});

test("fail-closed：拿不到当前版本（空串）一律不弹，即使 lastSeen 也为空", () => {
  assert.equal(shouldShowReleaseNotice(null, ""), false);
  assert.equal(shouldShowReleaseNotice("1.3.0", ""), false);
});

test("点「知道了」后写入 lastSeen 键，读回一致；未写入时读到 null", () => {
  const storage = fakeStorage();
  assert.equal(readReleaseNoticeSeen(storage), null);
  writeReleaseNoticeSeen(storage, "1.3.0");
  assert.equal(storage.getItem(RELEASE_NOTICE_SEEN_KEY), "1.3.0");
  assert.equal(readReleaseNoticeSeen(storage), "1.3.0");
  assert.equal(RELEASE_NOTICE_SEEN_KEY, "releaseNotice.lastSeenVersion");
});

test("条目截断：卡片最多展示前 3 条", () => {
  assert.equal(RELEASE_NOTICE_MAX_ITEMS, 3);
  const items = [
    item("new", "一"),
    item("new", "二"),
    item("imp", "三"),
    item("fix", "四"),
  ];
  const visible = visibleReleaseItems(items);
  assert.equal(visible.length, 3);
  assert.deepEqual(visible.map((entry) => entry.zh), ["一", "二", "三"]);
});

test("changelog 没有当前版本块 → 不弹（返回 null，fail-closed）", () => {
  const data: ChangelogData = {
    versions: [{ version: "1.2.2", date: "2026-09-18", items: [item("fix", "x")] }],
  };
  assert.equal(findReleaseEntryIn(data, "1.3.0"), null);
  assert.equal(findReleaseEntryIn(data, ""), null);
  assert.equal(findReleaseEntryIn(data, "1.2.2")?.version, "1.2.2");
});

test("zh 主行与「——」副行拆分；无分隔符时副行为 null", () => {
  assert.deepEqual(splitReleaseItemText("自定义知识库——导入第三方知识包，训练讲解随包更新"), {
    main: "自定义知识库",
    sub: "导入第三方知识包，训练讲解随包更新",
  });
  assert.deepEqual(splitReleaseItemText("若干稳定性问题"), { main: "若干稳定性问题", sub: null });
});

test("共享 changelog 内容：当前打包版本（package.json 版本）的块存在且可被应用卡消费（否则升级后永远不弹）", async () => {
  const pkg = (await import("../package.json", { with: { type: "json" } })).default;
  const entry = findReleaseEntryIn(changelogData, pkg.version);
  assert.ok(entry, `changelog.json 缺少 v${pkg.version} 块`);
  assert.ok(entry.date, "版本块缺 date");
  // 条目数不设下限（1003 点点拍板：修一个大 bug 也值得发一版，1 条热修是合法形态）；
  // 只锁条目类型枚举合法，防 changelog 写错 type 导致卡片徽标渲染异常。
  assert.ok(entry.items.length >= 1);
  for (const releaseItem of entry.items) {
    assert.ok(
      releaseItem.type === "new" || releaseItem.type === "imp" || releaseItem.type === "fix",
      `未知条目类型: ${String(releaseItem.type)}`,
    );
  }
});

test("完整更新日志外链指向落地页 changelog 锚点", () => {
  assert.equal(CHANGELOG_URL, "https://aimingcookie.com/#changelog");
});
