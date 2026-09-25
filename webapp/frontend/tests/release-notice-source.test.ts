import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("AppShell 挂载更新公告卡：版本取桌面壳 getVersion，changelog 无块或已看过则不弹", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 版本来源与设置页「应用更新」同一通道（@tauri-apps/api/app getVersion），
  // 且只在桌面运行时查询——纯浏览器预览拿不到版本，静默不弹。
  assert.match(shell, /import \{ getVersion \} from "@tauri-apps\/api\/app"/);
  assert.match(shell, /shellHidden \|\| !isDesktopRuntime\(\)\) return undefined/);
  assert.match(shell, /void getVersion\(\)/);
  // fail-closed：changelog 里没有当前版本块就不弹。
  assert.match(shell, /const entry = findReleaseEntry\(version\);/);
  assert.match(shell, /if \(!entry\) return;/);
  // 弹出判定与 lastSeen 持久化走 lib/release-notice 的同一组纯函数。
  assert.match(shell, /shouldShowReleaseNotice\(readReleaseNoticeSeen\(window\.localStorage\), version\)/);
  assert.match(shell, /writeReleaseNoticeSeen\(window\.localStorage, releaseNotice\.version\)/);
  assert.match(shell, /<ReleaseNoticeCard entry=\{releaseNotice\.entry\} onDismiss=\{dismissReleaseNotice\} \/>/);
});

test("更新公告卡文案经字典键（无裸中文字面量）且外链走受控 openExternalUrl", async () => {
  const component = await source("components/task3/ReleaseNoticeCard.tsx");
  const withoutComments = component
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(withoutComments, /[\u4e00-\u9fff]/, "ReleaseNoticeCard 存在裸中文（应进 lib/i18n/dict/task3.zh.ts 分片）");
  assert.match(component, /useT\(\)/);
  // 「查看完整更新日志」必须经受控外链出口（tauri-plugin-opener），不许 <a target=_blank>。
  assert.match(component, /openExternalUrl\(CHANGELOG_URL\)/);
  assert.doesNotMatch(component, /target="_blank"/);
});

test("更新公告卡样式：only semantic tokens（无裸色值），reduced-motion 下关进场动画", async () => {
  const styles = await source("components/task3/task3.css");
  const notice = styles.slice(styles.indexOf(".task3-release-notice"));
  assert.match(notice, /\.task3-release-notice \{[\s\S]*?position:\s*fixed/);
  assert.match(notice, /animation: task3-release-notice-in var\(--duration-surface\) var\(--ease-out\)/);
  assert.match(styles, /@keyframes task3-release-notice-in[\s\S]*?opacity:\s*0;[\s\S]*?translateY\(12px\)/);
  const reduced = styles.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/);
  assert.ok(reduced);
  assert.match(reduced[0], /\.task3-release-notice \{\s*animation: none;/);
});
