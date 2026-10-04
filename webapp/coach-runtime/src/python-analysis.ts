/**
 * Native Python analysis trigger commands.
 *
 * analysis.create_from_run — freezes a persisted local Run through the Python
 * backend's desktop API, then waits for the analysis worker to finish.
 *
 * The Python backend is reached directly over HTTP (same routes the frontend
 * uses): POST /api/kovaak-runs/{run_id}/analyze returns {session_id}, then
 * GET /api/sessions/{session_id} is polled until status is done/failed. The
 * Python base_url and desktop token come from the desktop runtime config file
 * (see python-backend.ts). This replaces the removed tool_bridge round trip.
 *
 * [fix 2026-10-04] D：done 但有残缺（limitations/error 非空）的 run，复用结果
 * 里带 rerun_available 指引教练可用 force: true 显式重跑（force 会跳过 Python
 * 侧 done 复用门产出新 session，旧 done 保留为历史）。
 */

import { randomUUID } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { getPythonBackendConfig } from "./python-backend.ts";
import { getAnalysesDir, getSessionsDir } from "./app-data.ts";
import { reportAnalysisRead } from "./fs-tools.ts";
import type { NativeWriteResult } from "./product-commands-write.ts";

type AnyDict = Record<string, any>;

const DESKTOP_USER_ID = "desktop-local";
// Poll interval is env-overridable so tests can exercise the running→done
// transition without waiting two seconds. 读取时求值而非模块加载时固化：
// node --test 全进程共享 env，先跑的测试文件设置 env 后，固化的常量会让
// 后跑文件的用例拿到错误预算（1004 全量污染实锤）。
const analyzePollIntervalMs = () => {
  const value = Number(process.env.AIMING_COOKIE_ANALYSIS_POLL_INTERVAL_MS);
  return Number.isFinite(value) && value > 0 ? value : 2_000;
};
// [fix 2026-10-04] 2 分钟即停等：Python 侧有任务级总预算与阶段僵尸清扫，
// 分析不会无声卡死；等待超时改为返回 pending（分析仍在后台继续），由教练
// 如实转告用户，桥本身不再长时间占住对话。Env 覆盖仅供测试注入短预算，
// 与 ANALYZE_POLL_INTERVAL_MS 同一模式。
const analyzeTimeoutMs = () => {
  const value = Number(process.env.AIMING_COOKIE_ANALYSIS_TIMEOUT_MS);
  return Number.isFinite(value) && value > 0 ? value : 120_000;
};
const REQUEST_TIMEOUT_MS = 15_000;
// The Python worker marks the session done before writing analyses/{id}/overview.json;
// wait a bounded time for the file so the returned analysis_ref is immediately readable.
const OVERVIEW_WAIT_TIMEOUT_MS = 10_000;
const OVERVIEW_WAIT_INTERVAL_MS = 250;

const FORWARDED_BODY_FIELDS = [
  "allow_parallel",
  // [fix 2026-10-04] 场景类型修正后的显式重跑：跳过 Python 侧 done 复用门。
  "force",
  "video_path",
  "cm_per_360",
  "fov",
  "profile_default",
  "manual_override",
  // [2026-10-04] Coach 判断制第二段：显式场景家族判断（四家族白名单由
  // Python 侧校验）与人读依据，随创建请求原样转发。
  "aim_family",
  "classification_basis",
] as const;

class PythonAnalysisError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

// ── done 但有残缺 ⇒ force 重跑入口（[fix 2026-10-04] FNamePool 案 D）──────
//
// 后端 create_from_run 的 force 已端到端打通（commit 0c271c3：跳过 done 复用
// 门、产出新 session、旧 done 保留为历史；run_active 在途互斥不放松）。这里
// 补 Coach 面的暴露：仅当该 run 已有 done 分析且其 deterministic.limitations /
// error 非空（“done 但有残缺”，典型如遥测旁车晚到导致动作层缺失）时，复用
// 结果里带 rerun_available 指引——教练征得用户同意后可发起带 force 的显式
// 重跑（A 修复后旁车晚到可被 ingest 补冻，重跑即可吃到新数据）。不满足时
// 维持现状文案。force 参数本身仍照传（场景类型修正的既有语义不受影响）。

interface RunDoneAnalysis {
  sessionId: number;
  /** limitations/error 非空 ⇒ 该 done 分析“有残缺”，可提供 force 重跑入口。 */
  qualifies: boolean;
  limitations: string[];
}

function readRunDoneAnalyses(runId: number): RunDoneAnalysis[] {
  const sessionsDir = getSessionsDir();
  let entries: string[];
  try {
    entries = readdirSync(sessionsDir);
  } catch {
    return [];
  }
  const out: RunDoneAnalysis[] = [];
  for (const name of entries) {
    const match = name.match(/^(\d+)\.json$/);
    if (!match) continue;
    let session: AnyDict | null = null;
    try {
      session = JSON.parse(readFileSync(join(sessionsDir, name), "utf-8")) as AnyDict;
    } catch {
      continue;
    }
    if (!session || session.status !== "done" || session.kovaak_run_id !== runId) continue;
    const result = session.result && typeof session.result === "object"
      ? session.result as AnyDict
      : null;
    const deterministic = result?.deterministic && typeof result.deterministic === "object"
      ? result.deterministic as AnyDict
      : null;
    const limitations = Array.isArray(deterministic?.limitations)
      ? (deterministic.limitations as unknown[]).filter(
          (item): item is string => typeof item === "string" && item.length > 0,
        )
      : [];
    const error = session.error && typeof session.error === "object" ? session.error : null;
    out.push({
      sessionId: Number(match[1]),
      qualifies: limitations.length > 0 || error !== null,
      limitations,
    });
  }
  return out;
}

function newCommandId(): string {
  return `command:${randomUUID().replace(/-/g, "")}`;
}

function newAuditRef(): string {
  return `audit:${randomUUID().replace(/-/g, "")}`;
}

function parseRunRef(value: unknown): number {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) return value;
  if (typeof value === "string") {
    const match = value.match(/^run:(\d+)$/);
    if (match) return parseInt(match[1], 10);
    const parsed = parseInt(value, 10);
    if (Number.isInteger(parsed) && parsed > 0) return parsed;
  }
  throw new PythonAnalysisError("invalid_parameters", "run_ref is required");
}

function requestSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

async function extractErrorDetail(response: Response): Promise<string> {
  let detail = `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as AnyDict;
    if (typeof body?.detail === "string" && body.detail) detail = body.detail;
  } catch {
    // Non-JSON error body — keep the status text.
  }
  return detail;
}

async function triggerAnalysis(
  runId: number,
  config: { baseUrl: string; token: string },
  params: AnyDict,
  idempotencyKey: string,
  locale: "zh-CN" | "en-US",
  signal?: AbortSignal,
): Promise<number> {
  const body: AnyDict = {};
  for (const field of FORWARDED_BODY_FIELDS) {
    if (params[field] !== undefined) body[field] = params[field];
  }
  const response = await fetch(`${config.baseUrl}/api/kovaak-runs/${runId}/analyze`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Aiming-Cookie-Desktop-Token": config.token,
      // B0 locale 管道：sidecar → Python 桥原样转发请求 locale。
      "X-Locale": locale,
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
    signal: requestSignal(signal),
  });
  if (!response.ok) {
    throw new PythonAnalysisError(
      "analysis_trigger_failed",
      await extractErrorDetail(response),
    );
  }
  const parsed = (await response.json()) as AnyDict;
  const sessionId = parsed.session_id;
  if (typeof sessionId !== "number" || !Number.isInteger(sessionId) || sessionId <= 0) {
    throw new PythonAnalysisError("invalid_response", "analysis trigger returned an invalid session id");
  }
  return sessionId;
}

async function waitForOverviewFile(sessionId: number, signal?: AbortSignal): Promise<void> {
  const path = join(getAnalysesDir(), String(sessionId), "overview.json");
  const deadline = Date.now() + OVERVIEW_WAIT_TIMEOUT_MS;
  while (!existsSync(path)) {
    if (signal?.aborted) return;
    if (Date.now() >= deadline) return; // Not fatal — the analysis is done; reads can retry.
    await new Promise((resolve) => setTimeout(resolve, OVERVIEW_WAIT_INTERVAL_MS));
  }
}

interface AnalysisOutcome {
  status: "done" | "failed" | "pending";
  error?: AnyDict;
  // [fix 2026-10-04] pending 时随结果透出进度事实（来自 Python 会话读模型），
  // 供教练如实向用户汇报阶段与耗时，而不是笼统的"失败"。
  task_phase?: string | null;
  started_at?: string | null;
  attempts?: number;
  elapsed_seconds?: number;
}

async function pollAnalysisStatus(
  sessionId: number,
  config: { baseUrl: string; token: string },
  locale: "zh-CN" | "en-US",
  signal?: AbortSignal,
): Promise<AnalysisOutcome> {
  const deadline = Date.now() + analyzeTimeoutMs();
  for (;;) {
    if (signal?.aborted) {
      throw new PythonAnalysisError("aborted", "analysis wait was aborted");
    }
    const response = await fetch(`${config.baseUrl}/api/sessions/${sessionId}`, {
      headers: {
        "X-Aiming-Cookie-Desktop-Token": config.token,
        "X-User-Id": DESKTOP_USER_ID,
        "X-Locale": locale,
      },
      signal: requestSignal(signal),
    });
    if (!response.ok) {
      throw new PythonAnalysisError(
        "session_status_failed",
        await extractErrorDetail(response),
      );
    }
    const body = (await response.json()) as AnyDict;
    const status = body.status;
    if (status === "done" || status === "failed") {
      return { status, error: body.error && typeof body.error === "object" ? body.error : undefined };
    }
    if (Date.now() >= deadline) {
      // [fix 2026-10-04] 等待超时 ≠ 分析失败：Python 侧总预算/僵尸清扫仍在
      // 管理这个作业。返回 pending 并带阶段事实，由教练转告用户后结束本次
      // 等待，不再阻塞对话也不返回笼统 failed。
      const startedAt = typeof body.started_at === "string" ? body.started_at : null;
      const startedMs = startedAt ? Date.parse(startedAt) : NaN;
      const elapsedSeconds = Number.isFinite(startedMs)
        ? Math.max(0, Math.round((Date.now() - startedMs) / 1000))
        : Math.round(analyzeTimeoutMs() / 1000);
      return {
        status: "pending",
        task_phase: typeof body.task_phase === "string" ? body.task_phase : null,
        started_at: startedAt,
        attempts: typeof body.attempts === "number" ? body.attempts : undefined,
        elapsed_seconds: elapsedSeconds,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, analyzePollIntervalMs()));
  }
}

export function isNativePythonAnalysisCommand(commandName: string): boolean {
  return commandName === "analysis.create_from_run";
}

// ── [2026-10-04] Coach 判断制第一段：分类证据包（只读，不入队不开跑）────
//
// analysis.scenario_evidence — GET /api/kovaak-runs/{run_id}/scenario-evidence。
// 返回场景名字线索、冻结旁车遥测操作特征、Stats/Raw 挑战形状粗分类与字段
// 图例（自描述数据，知识跟数据走）。Coach 看完证据再决定是否带 aim_family
// 发起 analysis.create_from_run（第二段），并用 scenario_memory.set 记住该图。

export function isNativeScenarioEvidenceCommand(commandName: string): boolean {
  return commandName === "analysis.scenario_evidence";
}

export async function executeNativeScenarioEvidence(
  commandName: string,
  params: AnyDict,
  _ownerId: string,
  signal?: AbortSignal,
): Promise<NativeWriteResult> {
  const commandId = newCommandId();
  const auditRef = newAuditRef();
  try {
    if (commandName !== "analysis.scenario_evidence") {
      throw new PythonAnalysisError(
        "unknown_command",
        `${commandName} is not a native Python analysis command`,
      );
    }
    const runId = parseRunRef(params.run_ref);
    const config = getPythonBackendConfig();
    if (!config) {
      throw new PythonAnalysisError("python_backend_unavailable", "Python 分析后端未就绪，请稍后重试");
    }
    const response = await fetch(
      `${config.baseUrl}/api/kovaak-runs/${runId}/scenario-evidence`,
      {
        headers: { "X-Aiming-Cookie-Desktop-Token": config.token },
        signal: requestSignal(signal),
      },
    );
    if (!response.ok) {
      throw new PythonAnalysisError(
        "scenario_evidence_failed",
        await extractErrorDetail(response),
      );
    }
    const evidence = (await response.json()) as AnyDict;
    return {
      status: "succeeded",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: `run:${runId}`,
      result: evidence,
    };
  } catch (error) {
    if (error instanceof PythonAnalysisError) {
      return {
        status: "failed",
        command_id: commandId,
        audit_ref: auditRef,
        warning_or_error: { code: error.code, message: error.message },
      };
    }
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "internal_error", message: "scenario evidence could not be collected" },
    };
  }
}

export async function executeNativePythonAnalysis(
  commandName: string,
  params: AnyDict,
  _ownerId: string,
  idempotencyKey: string,
  signal?: AbortSignal,
  /** B0 locale 管道：桥接请求转发 X-Locale（默认 zh-CN）。调用链（turn →
      product-command-tools）目前无请求上下文可传，待 B3/B5 接线。 */
  locale: "zh-CN" | "en-US" = "zh-CN",
): Promise<NativeWriteResult> {
  const commandId = newCommandId();
  const auditRef = newAuditRef();

  try {
    if (commandName !== "analysis.create_from_run") {
      throw new PythonAnalysisError(
        "unknown_command",
        `${commandName} is not a native Python analysis command`,
      );
    }
    const runId = parseRunRef(params.run_ref);
    const config = getPythonBackendConfig();
    if (!config) {
      throw new PythonAnalysisError("python_backend_unavailable", "Python 分析后端未就绪，请稍后重试");
    }
    // [fix 2026-10-04] D：触发前记录该 run 既有 done 分析（复用识别 + force
    // 重跑资格判定）。读不到本地会话记录按无处理，不阻塞创建。
    const priorDone = readRunDoneAnalyses(runId);
    const sessionId = await triggerAnalysis(runId, config, params, idempotencyKey, locale, signal);
    const outcome = await pollAnalysisStatus(sessionId, config, locale, signal);
    if (outcome.status === "done") {
      await waitForOverviewFile(sessionId, signal);
    }

    if (outcome.status === "failed") {
      return {
        status: "failed",
        command_id: commandId,
        audit_ref: auditRef,
        result_ref: `analysis:${sessionId}`,
        warning_or_error: {
          code: typeof outcome.error?.code === "string" ? outcome.error.code : "analysis_failed",
          message: typeof outcome.error?.message === "string" ? outcome.error.message : "分析失败",
        },
      };
    }
    if (outcome.status === "pending") {
      // [fix 2026-10-04] 等待超时但分析仍在后台进行：按 pending 如实返回
      // （不是 failed），带阶段与耗时事实和转告指引；不 reportAnalysisRead
      // （overview.json 未就绪，分析还不是可讨论的主题）。
      const phaseLabel = outcome.task_phase ?? "unknown";
      const minutes = Math.max(1, Math.round((outcome.elapsed_seconds ?? 0) / 60));
      return {
        status: "succeeded",
        command_id: commandId,
        audit_ref: auditRef,
        result_ref: `analysis:${sessionId}`,
        result: {
          session_id: sessionId,
          analysis_ref: `analysis:${sessionId}`,
          status: "pending",
          task_phase: outcome.task_phase,
          started_at: outcome.started_at,
          attempts: outcome.attempts,
          elapsed_seconds: outcome.elapsed_seconds,
          guidance:
            `分析仍在后台进行（阶段：${phaseLabel}，已 ${minutes} 分钟）；` +
            "请把当前阶段与耗时告诉用户，不要继续阻塞等待。",
        },
      };
    }
    // 本讨论创建的分析即讨论主题：挂进「本次讨论」，并让讲课时文中的
    // @time 链接能解析到这份分析的视频。
    reportAnalysisRead(sessionId, true);
    const doneResult: AnyDict = {
      session_id: sessionId,
      analysis_ref: `analysis:${sessionId}`,
      status: "done",
    };
    // [fix 2026-10-04] D：返回的 session 是既有 done 分析的复用、且其
    // limitations/error 非空（“done 但有残缺”）时，暴露 force 重跑入口；
    // 新建 session 或残缺为空时维持现状文案。
    const reused = priorDone.find((item) => item.sessionId === sessionId);
    if (reused?.qualifies) {
      doneResult.rerun_available = true;
      doneResult.limitations = reused.limitations;
      doneResult.guidance =
        "该 Run 已有完成的分析但带残缺（limitations 非空，详见本分析的 " +
        "scenario_info.limitations）。若用户想把新到齐的数据（如晚到的遥测旁车）" +
        "补进分析，先征得用户同意，再用 analysis.create_from_run 传 " +
        "force: true 显式重跑：会产出新 session，旧结果保留为历史。";
    }
    return {
      status: "succeeded",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: `analysis:${sessionId}`,
      result: doneResult,
    };
  } catch (error) {
    if (error instanceof PythonAnalysisError) {
      return {
        status: "failed",
        command_id: commandId,
        audit_ref: auditRef,
        warning_or_error: { code: error.code, message: error.message },
      };
    }
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "internal_error", message: "analysis could not be completed" },
    };
  }
}
