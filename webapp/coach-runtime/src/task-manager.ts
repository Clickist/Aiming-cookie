/**
 * Background async task manager for Coach agent runs.
 *
 * Each run_ref maps to an in-flight async task with an AbortController.
 * The sidecar uses this to run turns asynchronously while returning 202
 * immediately, and to support cooperative cancellation when the user
 * requests a stop.
 */

type ManagedTask = {
  abort: () => void;
  promise: Promise<void>;
};

const tasks = new Map<string, ManagedTask>();

/**
 * Start an async function as a background task identified by `runRef`.
 * If a task with the same ref already exists, this is a no-op.
 */
export function startTask(runRef: string, fn: (signal: AbortSignal) => Promise<void>): void {
  if (tasks.has(runRef)) return;
  const controller = new AbortController();
  const promise = fn(controller.signal).catch(() => {
    // Errors are handled by the caller's try/catch; swallow unhandled rejections
    // here so they don't crash the process.
  }).finally(() => {
    tasks.delete(runRef);
  });
  tasks.set(runRef, { abort: () => controller.abort(), promise });
}

/**
 * Request cancellation of a running task. Returns false if the task is
 * not active (already completed or never started).
 */
export function stopTask(runRef: string): boolean {
  const task = tasks.get(runRef);
  if (!task) return false;
  task.abort();
  return true;
}

/** Check whether a task is still active. */
export function isTaskActive(runRef: string): boolean {
  return tasks.has(runRef);
}

/** Wait for a specific task to complete (used by stop to allow graceful shutdown). */
export async function waitForTask(runRef: string, timeoutMs?: number): Promise<void> {
  const task = tasks.get(runRef);
  if (!task) return;
  if (timeoutMs !== undefined) {
    await Promise.race([
      task.promise,
      new Promise<void>((resolve) => {
        const timer = setTimeout(() => resolve(), timeoutMs);
        timer.unref?.();
      }),
    ]);
  } else {
    await task.promise;
  }
}
