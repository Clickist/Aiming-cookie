/**
 * Sidecar 进程级兜底 handler（鲁棒性审计 P1-12）。
 *
 * 此前 src/ 零 process.on：Bun/Node 对 unhandled rejection 默认即时杀进程，
 * kill 落在会话 JSONL appendFile 中途正是撕裂行（P0-1）的直接放大器。这里把
 * 「隐形自杀」变「有痕降级」：
 *   - unhandledRejection：结构化 cause 码落 coach-error.log，进程存活——
 *     单个浮动 promise 的失败不再放大成数据损坏；
 *   - uncaughtException：先落日志再退出（V8 文档口径：uncaughtException 后
 *     继续运行不可信）；sync 关 stdin 守卫不需要——进程退出本身即 EOF。
 * 依赖全部可注入，node:test 直测（不触发真实 process 事件）。
 */

import { appendFileSync } from "node:fs";
import { join } from "node:path";

import { getDataRoot } from "./app-data.ts";

export type ProcessGuardDeps = {
  /** 默认追加 coach-error.log 一行；测试注入收集器。 */
  writeLog?: (line: string) => void;
  /** 默认 process.on；测试注入捕获 handler。 */
  register?: (signal: "unhandledRejection" | "uncaughtException", handler: (error: unknown) => void) => void;
  /** 默认 process.exit；测试注入捕获退出码。 */
  exit?: (code: number) => void;
};

function describeError(error: unknown): string {
  if (error instanceof Error) {
    return `${error.message}${error.stack ? `\n${error.stack}` : ""}`;
  }
  return String(error);
}

export function installProcessGuards(deps: ProcessGuardDeps = {}): void {
  const writeLog = deps.writeLog ?? ((line: string) => {
    // 同步 append（全仓诊断日志同口径），best-effort：日志写不进去绝不二次
    // 崩溃，至少留在 stderr。
    try {
      appendFileSync(join(getDataRoot(), "coach-error.log"), `${line}\n`, "utf8");
    } catch {
      try {
        process.stderr.write(`${line}\n`);
      } catch {
        // 进程正在死亡，无可再做的观测。
      }
    }
  });
  const exit = deps.exit ?? ((code: number) => process.exit(code));
  const register = deps.register ?? ((signal, handler) => {
    process.on(signal, handler);
  });

  register("unhandledRejection", (error) => {
    // 有痕降级：记录后存活。rejection 不破坏进程状态的一致性假设——与
    // uncaughtException 不同，这里不退出（退出正是 P0-1 撕裂写的触发器）。
    writeLog(
      `${new Date().toISOString()} [process] unhandledRejection cause=unhandled_rejection`
      + ` error=${describeError(error)}`,
    );
  });
  register("uncaughtException", (error) => {
    // 记录后退出：V8 明确 uncaughtException 后进程状态不可信，继续运行可能
    // 造成更大损坏；exit 让桌面宿主按既定协议感知退出。
    writeLog(
      `${new Date().toISOString()} [process] uncaughtException cause=uncaught_exception`
      + ` error=${describeError(error)}`,
    );
    exit(1);
  });
}
