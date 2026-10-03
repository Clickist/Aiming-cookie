/**
 * 「未检测到 KovaaK's」引导判定（1002 死循环善后，方案 A）。
 *
 * 背景：onboarding 不检测 KovaaK 安装，无 KovaaK 的新用户落主界面后能聊
 * 天但永远产生不了训练数据 → 永远完成不了试用验证（跑一局）→ 卡付费墙，
 * 全程无提示。本模块给两处引导卡（Coach 首页空态、History 空态）提供统一
 * 的「是否显示」判定与取数。
 *
 * 信号口径（与 webapp/backend/routes.py 核对过）：
 * - `/api/kovaak-connection` 的 `{connected}` 是用户是否绑定 Steam 档案
 *   （评分同步），**不是**「已安装」，不用；
 * - `/api/kovaak-local-directories`（desktop-token settings API）的
 *   `stats.source` 才是安装信号：environment（环境变量覆盖）/confirmed
 *   （设置页手动确认）/automatic（自动发现）任一命中 = 装了；仅
 *   `unavailable` = 数据目录未被发现。
 *
 * fail-open：接口失败或形状不对一律「不显示」——误报会把老用户也拦在
 * 假提示前，漏报只损失一张卡，宁可漏报。
 */

import { useEffect, useState } from "react";

import { getKovaaKLocalDirectories } from "./api";
import { isDesktopRuntime } from "./desktop";
import type { KovaaKLocalDirectoriesV1 } from "./types";

/** KovaaK's Steam 商店页（引导卡外链，新窗口打开）。 */
export const KOVAAK_STEAM_URL = "https://store.steampowered.com/app/824270/KovaaKs/";

/**
 * 是否显示引导卡：stats 目录（KovaaK 训练 CSV 的落盘位置）发现不了才显示。
 * performance 目录单独 unavailable 不算——stats 在即视为已安装，只是排行榜
 * 数据目录没配，不该喊「未检测到」。
 */
export function shouldShowKovaakInstallGuide(
  directories: KovaaKLocalDirectoriesV1 | null | undefined,
): boolean {
  return directories?.stats?.source === "unavailable";
}

/**
 * 引导卡取数 hook：桌面版挂载即查一次；窗口重获焦点/切回前台时复查
 * （用户从 Steam 装完回来的自然时机）；目录仍缺失时每 30s 轮询，让
 * 「设置 → KovaaK 手动确认目录」后卡片自动消失。已检测到即停轮询——
 * 后端目录解析对大 stats 目录是阻塞型扫描，不白发。浏览器预览拿不到
 * desktop-token API，不判定（false）。
 */
export function useKovaakInstallGuide(): boolean {
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    let cancelled = false;
    const check = async (): Promise<void> => {
      try {
        const directories = await getKovaaKLocalDirectories();
        if (!cancelled) setMissing(shouldShowKovaakInstallGuide(directories));
      } catch {
        if (!cancelled) setMissing(false);
      }
    };
    void check();
    const refresh = (): void => {
      void check();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    const timer = missing ? window.setInterval(check, 30_000) : undefined;
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [missing]);

  return missing;
}
