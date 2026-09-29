// CF Pages 构建期落地页域名注入（0929 修复线上登录按钮占位域名）：
// 源码里 accounts 域名是 example.invalid 占位（公开仓零真实域名红线），
// 云端构建时用 Pages 环境变量 ACCOUNTS_BASE_URL 替换为真域名。
// 缺环境变量时静默跳过（本地/PR 预览保持占位，构建不失败）。
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLACEHOLDER = "https://accounts.example.invalid";
const landingRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "design",
  "opendesign-landing",
);
const target = process.env.ACCOUNTS_BASE_URL?.trim();
if (!target || !/^https:\/\/[a-z0-9.-]+$/i.test(target)) {
  console.log("[landing-inject] ACCOUNTS_BASE_URL 未配置或非法，跳过注入（占位域名保留）");
  process.exit(0);
}
let replaced = 0;
for (const file of ["index.html", "en/index.html"]) {
  const path = join(landingRoot, file);
  const source = readFileSync(path, "utf8");
  const count = source.split(PLACEHOLDER).length - 1;
  if (!count) continue;
  writeFileSync(path, source.split(PLACEHOLDER).join(target));
  replaced += count;
  console.log(`[landing-inject] ${file}: ${count} 处 → ${target}`);
}
console.log(`[landing-inject] 完成，共 ${replaced} 处`);
