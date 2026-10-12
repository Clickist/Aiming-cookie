/**
 * Coach 回合活跃的全局播报（进程内单源，不落盘）。
 *
 * CoachPanel 的 run 进入/离开 queued|running 时经 AppShell 上报
 * （handleCoachActiveRunChange 落点）；安装更新、切换存储位置都会重启应用
 * 打断在途回合，相关入口据此守卫：UpdatePrompt 安装按钮禁用、
 * SettingsWorkspace 存储位置变更直接劝返。
 *
 * 模块级单例 + useSyncExternalStore 订阅（与 i18n locale 同款）：值恒为
 * 布尔、set 时同值早退，订阅方只在真变化时重渲染。
 */

import { useSyncExternalStore } from "react";

let coachRunActive = false;
const listeners = new Set<() => void>();

/** 回合活跃变化上报（run 进入/离开 queued|running）；同值幂等不广播。 */
export function setCoachRunActive(active: boolean): void {
  if (coachRunActive === active) return;
  coachRunActive = active;
  for (const listener of listeners) listener();
}

export function getCoachRunActive(): boolean {
  return coachRunActive;
}

export function subscribeCoachRunActive(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React 侧读取：回合活跃状态变化时重渲染。 */
export function useCoachRunActive(): boolean {
  return useSyncExternalStore(subscribeCoachRunActive, getCoachRunActive);
}
