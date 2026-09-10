/**
 * File system + shell tools for the Coach agent.
 *
 * The heavy lifting (truncation, offset/limit paging, image handling, grep,
 * file-mutation queueing, shell execution) comes from pi coding-agent's
 * canonical tool implementations — we stopped hand-rolling those when we
 * adopted the upstream tools (2026-09-06). This layer only adds Coach product
 * guards: protected product-state files on write, and analysis-read
 * notifications that drive the @time video links.
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { constants } from "node:fs";
import { access as fsAccess, readFile } from "node:fs/promises";
import { isAbsolute, relative, resolve as resolvePath, sep } from "node:path";

import { loadPiCodingTools } from "./pi-source.ts";

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

function resolveToCwd(path: string, cwd: string): string {
  return isAbsolute(path) ? path : resolvePath(cwd, path);
}

// ── Analysis-read notifications (@time video links) ─────────────────────

const analysisReadListeners = new Set<AnalysisReadListener>();

// Per-turn read scope: while a turn body runs inside runScopedAnalysisReads,
// analysis reads are dispatched only to that turn's listener. This prevents
// concurrent turns from cross-reporting analysis ids into each other's refs.
const analysisReadScope = new AsyncLocalStorage<Set<AnalysisReadListener>>();

type AnalysisReadListener = (analysisId: number, subject: boolean) => void;

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

function dispatchAnalysisRead(analysisId: number, subject: boolean): void {
  if (!Number.isSafeInteger(analysisId) || analysisId <= 0) return;
  const scoped = analysisReadScope.getStore();
  if (scoped) {
    for (const listener of scoped) listener(analysisId, subject);
    return;
  }
  for (const listener of analysisReadListeners) listener(analysisId, subject);
}

/**
 * Report an analysis id directly (used by native product commands).
 *
 * `subject: true` marks discussion-subject engagement (the analysis the user
 * asked about, one the turn created, or one whose video evidence was opened);
 * only subject engagement joins the discussion bar. Reference reads such as
 * history comparison stay `subject: false` so they do not pollute 本次讨论.
 */
export function reportAnalysisRead(analysisId: number, subject = false): void {
  dispatchAnalysisRead(analysisId, subject);
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

// ── Skill 调用通知（与 analysis-read 同款管线）──────────────────────────
//
// 系统提示词的 available_skills 块给每个技能 SKILL.md 的绝对路径，模型
// "调用技能"的实际动作就是 read 该文件（Pi 无显式 skill 工具）。这里在
// read 包装层识别 skills/<name>/SKILL.md 路径并派发，turn 层转成
// tool_events 里的 {type:"skill"} 工作事件。
const SKILL_FILE_PATTERN = /(?:^|[\\/])skills[\\/]([^\\/]+)[\\/]SKILL\.md$/i;

const skillReadScope = new AsyncLocalStorage<Set<(skillName: string) => void>>();

/** Run a turn body so skill reads are reported only to `listener`. */
export function runScopedSkillReads<T>(
  listener: (skillName: string) => void,
  body: () => Promise<T>,
): Promise<T> {
  return skillReadScope.run(new Set([listener]), body);
}

function notifySkillRead(path: string): void {
  const match = SKILL_FILE_PATTERN.exec(path);
  if (!match) return;
  const scoped = skillReadScope.getStore();
  if (scoped) {
    for (const listener of scoped) listener(match[1]!);
  }
}

type CoachTool = {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (id: string, args: any, signal?: AbortSignal) => Promise<unknown>;
};

async function loadTools() {
  return (await loadPiCodingTools()) as unknown as {
    createReadTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createWriteTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createLsTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createEditTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createGrepTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createFindTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
    createBashTool: (cwd: string, options?: Record<string, unknown>) => CoachTool;
  };
}

/**
 * read: pi 原版（2000 行/50KB 截断、offset/limit 分页、图片支持）+ Coach 层
 * 注入 readFile 操作以触发 analysis-read 通知——原版 resolve 完路径后才读，
 * 这里拿到的一定是绝对路径。
 */
export async function createReadTool(cwd: string) {
  const { createReadTool: create } = await loadTools();
  // operations 是整体替换不是合并——必须把默认的 access/detectImageMimeType 一起带上。
  return create(cwd, {
    operations: {
      access: (absolutePath: string) => fsAccess(absolutePath, constants.R_OK),
      readFile: async (absolutePath: string) => {
        const buffer = await readFile(absolutePath);
        notifyAnalysisRead(absolutePath);
        notifySkillRead(absolutePath);
        return buffer;
      },
    },
  });
}

/**
 * write: pi 原版（自带同文件写入排队）+ 写前拦截产品状态文件。
 */
export async function createWriteTool(cwd: string) {
  const { createWriteTool: create } = await loadTools();
  const inner = create(cwd);
  return {
    ...inner,
    execute: async (id: string, args: { path: string; content: string }, signal?: AbortSignal) => {
      const absolutePath = resolveToCwd(args.path, cwd);
      const protectedFile = protectedStateFile(absolutePath, cwd);
      if (protectedFile) {
        throw new Error(
          `Refusing to write ${protectedFile.relative}: it is product state managed by ${protectedFile.command}. ` +
          `Use run_product_command with that command instead of the write tool.`,
        );
      }
      return inner.execute(id, args, signal);
    },
  };
}

// ls / edit / grep / find / bash：原版直通，Coach 层无附加逻辑。
// 各工具的截断/上限/排队语义全部来自 pi coding-agent 原版（如 ls 默认 500
// 条上限、edit 走同文件变更队列、bash 自带超时与 Windows shell 选择）。

export async function createLsTool(cwd: string) {
  const { createLsTool: create } = await loadTools();
  const inner = create(cwd);
  return {
    ...inner,
    async execute(id: string, args: { path?: string } | undefined, signal?: AbortSignal) {
      const result = await inner.execute(id, args, signal);
      // 旧版 Coach ls 在列目录成功后上报 analysis-read——Coach"列出
      // analyses/N 目录"也是一次分析读取，讨论列表与 @time 链依赖它；
      // pi 原版直通没有这一步，这里补回（2026-09-06 重构回归修复）。
      try {
        notifyAnalysisRead(resolveToCwd(args?.path || ".", cwd));
      } catch {
        // best-effort
      }
      return result;
    },
  };
}

export async function createEditTool(cwd: string) {
  const { createEditTool: create } = await loadTools();
  return create(cwd);
}

export async function createGrepTool(cwd: string) {
  const { createGrepTool: create } = await loadTools();
  return create(cwd);
}

export async function createFindTool(cwd: string) {
  const { createFindTool: create } = await loadTools();
  return create(cwd);
}

export async function createBashTool(cwd: string) {
  const { createBashTool: create } = await loadTools();
  return create(cwd);
}
