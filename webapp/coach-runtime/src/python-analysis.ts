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
 */

import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { getPythonBackendConfig } from "./python-backend.ts";
import { getAnalysesDir } from "./app-data.ts";
import { reportAnalysisRead } from "./fs-tools.ts";
import type { NativeWriteResult } from "./product-commands-write.ts";

type AnyDict = Record<string, any>;

const DESKTOP_USER_ID = "desktop-local";
// Poll interval is env-overridable so tests can exercise the running→done
// transition without waiting two seconds.
const ANALYZE_POLL_INTERVAL_MS = (() => {
  const value = Number(process.env.AIMING_COOKIE_ANALYSIS_POLL_INTERVAL_MS);
  return Number.isFinite(value) && value > 0 ? value : 2_000;
})();
// [fix 2026-10-04] 2 分钟即停等：Python 侧有任务级总预算与阶段僵尸清扫，
// 分析不会无声卡死；等待超时改为返回 pending（分析仍在后台继续），由教练
// 如实转告用户，桥本身不再长时间占住对话。Env 覆盖仅供测试注入短预算，
// 与 ANALYZE_POLL_INTERVAL_MS 同一模式。
const ANALYZE_TIMEOUT_MS = (() => {
  const value = Number(process.env.AIMING_COOKIE_ANALYSIS_TIMEOUT_MS);
  return Number.isFinite(value) && value > 0 ? value : 120_000;
})();
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
] as const;

class PythonAnalysisError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
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
  const deadline = Date.now() + ANALYZE_TIMEOUT_MS;
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
        : Math.round(ANALYZE_TIMEOUT_MS / 1000);
      return {
        status: "pending",
        task_phase: typeof body.task_phase === "string" ? body.task_phase : null,
        started_at: startedAt,
        attempts: typeof body.attempts === "number" ? body.attempts : undefined,
        elapsed_seconds: elapsedSeconds,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, ANALYZE_POLL_INTERVAL_MS));
  }
}

export function isNativePythonAnalysisCommand(commandName: string): boolean {
  return commandName === "analysis.create_from_run";
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
    return {
      status: "succeeded",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: `analysis:${sessionId}`,
      result: {
        session_id: sessionId,
        analysis_ref: `analysis:${sessionId}`,
        status: "done",
      },
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
