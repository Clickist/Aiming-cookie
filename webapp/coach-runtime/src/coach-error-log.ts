import { appendFileSync } from "node:fs";
import { join } from "node:path";

import { getDataRoot } from "./app-data.ts";

/**
 * provider-auth 失败详情落 coach-error.log（随报障包经 coachErrorLogTail 带出）。
 * best-effort：诊断通道自身故障不得反伤登录主流程。
 */
export function appendCoachErrorLog(line: string): void {
  try {
    appendFileSync(join(getDataRoot(), "coach-error.log"), line + "\n", "utf8");
  } catch {
    // 诊断写入失败静默：与 net-resilience defaultWriteLog 同纪律
  }
}
