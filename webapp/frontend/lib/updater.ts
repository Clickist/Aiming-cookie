import { relaunch } from "@tauri-apps/plugin-process";
import { check } from "@tauri-apps/plugin-updater";

import { isDesktopRuntime } from "./desktop";

export interface DesktopUpdateProgress {
  /** 更新包总字节数；远端未提供 Content-Length 时为 null（进度条走不确定态）。 */
  contentLength: number | null;
  /** 已下载字节数。 */
  downloadedBytes: number;
  /** EMA 平滑后的下载速度（字节/秒；首块前为 0）。 */
  bytesPerSecond: number;
  /** Finished 事件后为 true：包已下完，插件在验签安装，随后自动重启。 */
  finished: boolean;
}

export interface DesktopUpdate {
  /** 远端 latest.json 提供的新版本号（如 "0.1.14"）。 */
  version: string;
  /** 下载并安装更新包，成功后自动重启；onProgress 在下载期间持续回报进度。 */
  install: (onProgress?: (progress: DesktopUpdateProgress) => void) => Promise<void>;
}

// 进度元信息的显示格式（视觉稿口径）：固定 1 位小数 MB，配 tabular-nums 数字稳定不跳。
const BYTES_PER_MB = 1048576;

export function formatDownloadedMb(bytes: number): string {
  return `${(bytes / BYTES_PER_MB).toFixed(1)} MB`;
}

export function formatDownloadSpeed(bytesPerSecond: number): string {
  return `${(bytesPerSecond / BYTES_PER_MB).toFixed(1)} MB/s`;
}

// check() 只能在 Tauri 上下文调用，浏览器会话在此短路返回 null；
// 检查端点与验签公钥都来自 tauri.conf.json 的 plugins.updater 配置。
export async function checkForDesktopUpdate(): Promise<DesktopUpdate | null> {
  if (!isDesktopRuntime()) return null;
  const update = await check();
  if (!update) return null;
  return {
    version: update.version,
    install: async (onProgress) => {
      let contentLength: number | null = null;
      let downloadedBytes = 0;
      let bytesPerSecond = 0;
      let lastChunkAt: number | null = null;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          contentLength = event.data.contentLength ?? null;
        } else if (event.event === "Progress") {
          const now = Date.now();
          downloadedBytes += event.data.chunkLength;
          if (lastChunkAt != null) {
            const seconds = (now - lastChunkAt) / 1000;
            if (seconds > 0) {
              const instant = event.data.chunkLength / seconds;
              // 显示速度用 EMA 平滑（0.82/0.18，视觉稿系数），首块直接取瞬时值起步；
              // 瞬时网速抖动由后续块抹平，避免数字乱跳。
              bytesPerSecond = bytesPerSecond === 0 ? instant : bytesPerSecond * 0.82 + instant * 0.18;
            }
          }
          lastChunkAt = now;
        }
        onProgress?.({
          contentLength,
          downloadedBytes,
          bytesPerSecond: event.event === "Finished" ? 0 : bytesPerSecond,
          finished: event.event === "Finished",
        });
      });
      await relaunch();
    },
  };
}
