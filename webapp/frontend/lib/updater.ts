import { relaunch } from "@tauri-apps/plugin-process";
import { check } from "@tauri-apps/plugin-updater";

import { isDesktopRuntime } from "./desktop";

export interface DesktopUpdate {
  /** 远端 latest.json 提供的新版本号（如 "0.1.14"）。 */
  version: string;
  /** 下载并安装更新包，成功后自动重启应用。 */
  install: () => Promise<void>;
}

// check() 只能在 Tauri 上下文调用，浏览器会话在此短路返回 null；
// 检查端点与验签公钥都来自 tauri.conf.json 的 plugins.updater 配置。
export async function checkForDesktopUpdate(): Promise<DesktopUpdate | null> {
  if (!isDesktopRuntime()) return null;
  const update = await check();
  if (!update) return null;
  return {
    version: update.version,
    install: async () => {
      await update.downloadAndInstall();
      await relaunch();
    },
  };
}
