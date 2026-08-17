/**
 * File system tools for the Coach agent.
 *
 * Provides read/write/ls tools that resolve relative paths against a given
 * cwd (the app-data directory). These are local implementations equivalent
 * to Pi coding-agent's tools, kept here to avoid importing the heavy
 * coding-agent package (which has TUI dependencies).
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve as resolvePath, sep } from "node:path";

import { loadPiAi } from "./pi-source.ts";

type TypeBuilder = {
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  Optional(schema: unknown): unknown;
  String(options?: Record<string, unknown>): unknown;
};

const { Type } = (await loadPiAi()) as unknown as { Type: TypeBuilder };

function resolveToCwd(path: string, cwd: string): string {
  return isAbsolute(path) ? path : resolvePath(cwd, path);
}

// ── Coach-managed product state guard ──────────────────────────────────
//
// These files are the on-disk state of the native write commands in
// product-commands-write.ts. Hand-writing them via the raw `write` tool
// bypasses every contract (plan_id/status/version, phase transitions,
// override schema) and desyncs the product commands (Bug 4 of the 2026-08-16
// deep test). The write tool refuses them and points at the product command.
const PROTECTED_STATE_FILES: ReadonlyMap<string, string> = new Map([
  ["training/plan.json", "training_plan.*"],
  ["training/history.jsonl", "training_plan.execution.record / training_plan.retest.record"],
  ["teaching/session.json", "teaching_session.update"],
  ["config/scenario-overrides.json", "scenario_memory.set"],
  ["config/calibration.json", "calibration.save / calibration.delete"],
  ["config/peripheral.json", "peripheral_profile.update"],
  ["config/kovaak-connection.json", "kovaak.connection.disconnect"],
]);

function protectedStateFile(absolutePath: string, cwd: string): { relative: string; command: string } | null {
  const relativePath = relative(cwd, absolutePath).split(sep).join("/");
  const command = PROTECTED_STATE_FILES.get(relativePath);
  return command ? { relative: relativePath, command } : null;
}

// ── Analysis read tracking ──────────────────────────────────────────────
//
// When the Coach reads (or lists) a file under `analyses/{id}/`, the id is
// reported through module-level listeners so the enclosing turn can attach the
// analysis to the run/session state. The frontend uses that analysis_ref to
// turn `@3.4s` time links into video seeks.

type AnalysisReadListener = (analysisId: number) => void;

const analysisReadListeners = new Set<AnalysisReadListener>();

// Per-turn read scope: while a turn body runs inside runScopedAnalysisReads,
// analysis reads are dispatched only to that turn's listener. This prevents
// concurrent turns from cross-reporting analysis ids into each other's refs.
const analysisReadScope = new AsyncLocalStorage<Set<AnalysisReadListener>>();

export function subscribeAnalysisReads(listener: AnalysisReadListener): () => void {
  analysisReadListeners.add(listener);
  return () => {
    analysisReadListeners.delete(listener);
  };
}

/** Run a turn body so analysis reads are reported only to `listener`. */
export function runScopedAnalysisReads<T>(
  listener: AnalysisReadListener,
  body: () => Promise<T>,
): Promise<T> {
  return analysisReadScope.run(new Set([listener]), body);
}

function dispatchAnalysisRead(analysisId: number): void {
  if (!Number.isSafeInteger(analysisId) || analysisId <= 0) return;
  const scoped = analysisReadScope.getStore();
  if (scoped) {
    for (const listener of scoped) listener(analysisId);
    return;
  }
  for (const listener of analysisReadListeners) listener(analysisId);
}

/** Report an analysis id directly (used by native product commands). */
export function reportAnalysisRead(analysisId: number): void {
  dispatchAnalysisRead(analysisId);
}

/**
 * Analysis ids the user referenced explicitly ("analysis:7"). These pin the
 * discussion subject: the auto-teach opener and direct questions carry the
 * ref in the message text.
 */
export function explicitAnalysisRefsFromText(text: string): number[] {
  const ids: number[] = [];
  for (const match of text.matchAll(/analysis:([1-9][0-9]*)/g)) {
    const id = Number(match[1]);
    if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

// Deep reads of an analysis directory also count as engagement: the model
// lectures by reading analyses/N/ with the read/ls tools, and without this
// report the discussion bar and @time links lose their analysis ref.
const ANALYSIS_DIR_PATTERN = /(?:^|[\\/])analyses[\\/](\d+)(?:[\\/]|$)/i;

function notifyAnalysisRead(path: string): void {
  const match = ANALYSIS_DIR_PATTERN.exec(path);
  if (!match) return;
  dispatchAnalysisRead(Number(match[1]));
}

export function createReadTool(cwd: string) {
  return {
    name: "read",
    label: "read",
    description:
      "Read the contents of a file. Relative paths resolve against the app-data directory.",
    parameters: Type.Object({
      path: Type.String({ description: "Path to the file to read (relative or absolute)" }),
    }),
    async execute(
      _id: string,
      { path }: { path: string },
      signal?: AbortSignal,
    ) {
      if (signal?.aborted) throw new Error("Operation aborted");
      const absolutePath = resolveToCwd(path, cwd);
      try {
        const content = await readFile(absolutePath, "utf8");
        notifyAnalysisRead(absolutePath);
        return { content: [{ type: "text" as const, text: content }] };
      } catch (error) {
        throw new Error(
          `Failed to read ${path}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    },
  };
}

export function createWriteTool(cwd: string) {
  return {
    name: "write",
    label: "write",
    description:
      "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.",
    parameters: Type.Object({
      path: Type.String({ description: "Path to the file to write (relative or absolute)" }),
      content: Type.String({ description: "Content to write to the file" }),
    }),
    async execute(
      _id: string,
      { path, content }: { path: string; content: string },
      signal?: AbortSignal,
    ) {
      if (signal?.aborted) throw new Error("Operation aborted");
      const absolutePath = resolveToCwd(path, cwd);
      const protectedFile = protectedStateFile(absolutePath, cwd);
      if (protectedFile) {
        throw new Error(
          `Refusing to write ${protectedFile.relative}: it is product state managed by ${protectedFile.command}. ` +
          `Use run_product_command with that command instead of the write tool.`,
        );
      }
      try {
        const { mkdir } = await import("node:fs/promises");
        await mkdir(dirname(absolutePath), { recursive: true });
        if (signal?.aborted) throw new Error("Operation aborted");
        await writeFile(absolutePath, content, "utf8");
        return {
          content: [
            { type: "text" as const, text: `Successfully wrote ${content.length} bytes to ${path}` },
          ],
        };
      } catch (error) {
        throw new Error(
          `Failed to write ${path}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    },
  };
}

export function createLsTool(cwd: string) {
  return {
    name: "ls",
    label: "ls",
    description:
      "List directory contents. Returns entries sorted alphabetically, with '/' suffix for directories.",
    parameters: Type.Object({
      path: Type.Optional(
        Type.String({ description: "Directory to list (default: app-data root)" }),
      ),
    }),
    async execute(
      _id: string,
      { path }: { path?: string },
      signal?: AbortSignal,
    ) {
      if (signal?.aborted) throw new Error("Operation aborted");
      const dirPath = resolveToCwd(path || ".", cwd);
      try {
        const entries = await readdir(dirPath, { withFileTypes: true });
        notifyAnalysisRead(dirPath);
        entries.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
        const lines = entries.map((entry) => entry.name + (entry.isDirectory() ? "/" : ""));
        const output = lines.length > 0 ? lines.join("\n") : "(empty directory)";
        return { content: [{ type: "text" as const, text: output }] };
      } catch (error) {
        throw new Error(
          `Failed to list ${path || "."}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    },
  };
}
