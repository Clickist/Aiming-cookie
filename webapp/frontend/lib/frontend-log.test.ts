import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

// frontend-log 在模块加载时读 window 挂全局兜底、并缓存环形内存；
// 这里先装一个最小 window（记录监听器 + 假 Tauri invoke），再动态加载。
type InvokeCall = { command: string; args: Record<string, unknown> };

const listeners: Record<string, Array<(event: unknown) => void>> = {};
const invokeCalls: InvokeCall[] = [];
let invokeImpl: (command: string, args: Record<string, unknown>) => Promise<unknown> = async () =>
  undefined;

const fakeWindow = {
  addEventListener: (type: string, handler: (event: unknown) => void) => {
    (listeners[type] ??= []).push(handler);
  },
  __TAURI_INTERNALS__: {
    invoke: (command: string, args: Record<string, unknown>) => {
      invokeCalls.push({ command, args });
      return invokeImpl(command, args);
    },
  },
};

Reflect.set(globalThis, "window", fakeWindow);

// 模块只加载一次（module cache）；window 在顶层已就位，全局兜底能挂上。
let modulePromise: Promise<typeof import("./frontend-log")> | null = null;
function loadModule(): Promise<typeof import("./frontend-log")> {
  modulePromise ??= import("./frontend-log");
  return modulePromise;
}

function setDesktop(desktop: boolean): void {
  if (desktop) {
    Reflect.set(globalThis, "isTauri", true);
  } else {
    Reflect.deleteProperty(globalThis, "isTauri");
  }
}

afterEach(async () => {
  (await loadModule()).resetFrontendLog();
  invokeCalls.length = 0;
  invokeImpl = async () => undefined;
  setDesktop(false);
});

test("ring buffer keeps at most 100 entries and truncates messages to 2000 chars", async () => {
  const { logFrontendError, readFrontendLog } = await loadModule();
  setDesktop(false);
  for (let index = 0; index < 105; index += 1) {
    logFrontendError("unit", `entry-${index}`);
  }
  const entries = readFrontendLog();
  assert.equal(entries.length, 100);
  assert.equal(entries[0]?.message, "entry-5");
  assert.equal(entries[99]?.message, "entry-104");
  assert.equal(typeof entries[0]?.ts, "number");

  logFrontendError("unit", "x".repeat(3000));
  assert.equal(readFrontendLog().at(-1)?.message.length, 2000);
  assert.equal(invokeCalls.length, 0, "non-desktop never invokes");
});

test("desktop runtime appends one line via invoke with the expected format", async () => {
  const { logFrontendError } = await loadModule();
  setDesktop(true);
  logFrontendError("error-boundary", "boom");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(invokeCalls.length, 1);
  assert.equal(invokeCalls[0]?.command, "desktop_append_frontend_log");
  const entry = invokeCalls[0]?.args.entry;
  assert.equal(typeof entry, "string");
  assert.match(entry as string, /^\d+ \[error-boundary\] boom$/);
});

test("desktop runtime flattens newlines and tabs into a single log line", async () => {
  const { logFrontendError } = await loadModule();
  setDesktop(true);
  logFrontendError("error-boundary", "line1\nline2\tline3");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(invokeCalls[0]?.args.entry as string, /^\d+ \[error-boundary\] line1 line2 line3$/);
});

test("invoke rejection is swallowed without throwing", async () => {
  const { logFrontendError, readFrontendLog } = await loadModule();
  setDesktop(true);
  invokeImpl = async () => {
    throw new Error("disk full");
  };
  assert.doesNotThrow(() => logFrontendError("unit", "still recorded"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(invokeCalls.length, 1);
  assert.equal(readFrontendLog().length, 1);
});

test("global window error and unhandledrejection handlers record entries", async () => {
  const { readFrontendLog } = await loadModule();
  setDesktop(false);
  assert.ok(listeners.error?.length, "error listener installed");
  assert.ok(listeners.unhandledrejection?.length, "unhandledrejection listener installed");

  listeners.error[0]!({ error: new Error("render failed"), message: "" });
  listeners.unhandledrejection[0]!({ reason: "async failed" });

  const entries = readFrontendLog();
  assert.equal(entries[0]?.source, "window.onerror");
  assert.match(entries[0]?.message ?? "", /render failed/);
  assert.equal(entries[1]?.source, "unhandledrejection");
  assert.equal(entries[1]?.message, "async failed");
});
