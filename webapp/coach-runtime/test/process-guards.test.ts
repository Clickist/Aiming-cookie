import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { installProcessGuards } from "../src/process-guards.ts";

test("process guards log unhandled rejections and keep the process alive (P1-12)", () => {
  const lines: string[] = [];
  const handlers: Record<string, (error: unknown) => void> = {};
  let exitCode: number | null = null;
  installProcessGuards({
    writeLog: (line) => lines.push(line),
    register: (signal, handler) => { handlers[signal] = handler; },
    exit: (code) => { exitCode = code; },
  });

  handlers.unhandledRejection!(new Error("floating rejection"));
  // 记录而非吞：cause 码 + 错误文本落日志；进程存活（不退出）。
  assert.match(lines[0]!, /\[process\] unhandledRejection cause=unhandled_rejection/);
  assert.match(lines[0]!, /floating rejection/);
  assert.equal(exitCode, null);
});

test("process guards log uncaught exceptions and then exit(1) (P1-12)", () => {
  const lines: string[] = [];
  const handlers: Record<string, (error: unknown) => void> = {};
  let exitCode: number | null = null;
  installProcessGuards({
    writeLog: (line) => lines.push(line),
    register: (signal, handler) => { handlers[signal] = handler; },
    exit: (code) => { exitCode = code; },
  });

  handlers.uncaughtException!(new Error("unrecoverable state"));
  assert.match(lines[0]!, /\[process\] uncaughtException cause=uncaught_exception/);
  assert.match(lines[0]!, /unrecoverable state/);
  // uncaughtException 后进程状态不可信：记录后必须退出。
  assert.equal(exitCode, 1);
});

test("start-sidecar wires the guards before the server starts (P1-12)", async () => {
  const entry = await readFile(path.resolve(import.meta.dirname, "../start-sidecar.ts"), "utf8");
  assert.match(entry, /import \{ installProcessGuards \} from "\.\/src\/process-guards\.ts";/);
  // 守卫必须先于服务启动安装（覆盖启动早期窗口）。
  assert.ok(
    entry.indexOf("installProcessGuards()") < entry.indexOf("startSidecarServer({"),
    "installProcessGuards() must run before startSidecarServer",
  );
});
