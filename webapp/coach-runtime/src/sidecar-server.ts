import { appendFileSync } from "node:fs";
import http from "node:http";
import { join } from "node:path";

import { getDataRoot } from "./app-data.ts";
import { failureResponse, makeError, type CoachRuntimeTurnSchema, isRecord } from "./contracts.ts";
import {
  activePackDisplayName,
  lastActiveKnowledgeFallbackReason,
  loadActiveKnowledgeRegistry,
  resolveActiveKnowledge,
} from "./knowledge-active.ts";
import { materializeKnowledgeDir } from "./knowledge-materialize.ts";
import { validateKnowledgeRegistry, REGISTRY_SCHEMA_VERSION_V3 } from "./knowledge-registry.ts";
import {
  CoachDataError,
  createCoachSession,
  deleteCoachSession,
  getCoachSessionDetail,
  listCoachSessions,
  ownerIdFromRequest,
  truncateCoachSession,
  updateCoachSession,
} from "./sidecar-coach-data.ts";
import {
  ProviderAuthOperationManager,
  ProviderAuthRequestError,
} from "./provider-auth.ts";
import { getProviderProfileStatus, testProviderConnection } from "./provider-profile.ts";
import { listBuiltinProviderCatalog } from "./provider-models.ts";
import { handleProviderProfileRequest } from "./provider-profiles.ts";
import { loadProfile } from "./provider-store.ts";
import { readSessionStats, readSessionMessages } from "./session-repo.ts";
import { ensureIntroSession, readIntroSessionFlag } from "./intro-session.ts";
import { INTRO_KICKOFF_PROMPT } from "./intro-kickoff.ts";
import {
  runCoachTurn,
  stopCoachTurn,
  type CoachActivityUpdate,
  type CoachPartialRevision,
  type CoachTurnTiming,
} from "./turn.ts";
import type { CoachRuntimeTurnResponse } from "./contracts.ts";
import {
  AgentRunError,
  createAgentRun,
  getAgentRun,
  steerAgentRun,
  stopAgentRun,
  retryAgentRun,
  decideConfirmation,
  hasActiveAgentRunForSession,
  resumeWaitingRuns,
  subscribeAgentRun,
} from "./agent-runs.ts";

export const DEFAULT_SIDECAR_HOST = "127.0.0.1";
export const DEFAULT_SIDECAR_PORT = 8765;

const defaultAuthOperations = new ProviderAuthOperationManager();

// 开场分析首条消息的自动开讲：最贴近现有「分析完成自动开讲」的机制——
// 由 sidecar 合成一条内部 kickoff 指令创建一次 Agent run（该指令在 UI 读取
// 时被过滤，用户不会看到假 user 消息）。并发/重复 POST 由 in-flight 守卫 +
// agent-runs 的活跃 run 检查双重去重；会话已有消息则视为开讲已完成。
//
// 发 kickoff 前必须先确认「当前档凭据此刻真的解析得出来」：runAgentTurn 只检查
// 档存在，凭据缺失时会静默挂起为 provider_waiting，等应用重启后内存 run 丢失，
// 一次性 flag 已置位就会让开场分析永远空白。凭据判定复用 provider-profile 的
// 状态投影（getProviderProfileStatus → Models.getAuth），与设置页「可用」同语义。
let introKickoffInFlight: Promise<{ runRef: string | null; providerReady: boolean }> | null = null;

async function isActiveProviderReady(): Promise<boolean> {
  const profile = loadProfile();
  if (!profile) return false;
  const status = await getProviderProfileStatus(profile);
  return status.status === "ready";
}

function ensureIntroKickoffRun(
  ownerId: string,
  sessionId: number,
): Promise<{ runRef: string | null; providerReady: boolean }> {
  if (introKickoffInFlight) return introKickoffInFlight;
  introKickoffInFlight = (async () => {
    // 凭据此刻不可用：不创建 run（否则只会挂起 provider_waiting 后随重启丢失），
    // 交由前端在下次挂载复查时凭 provider_ready=false 重试（自愈）。
    const providerReady = await isActiveProviderReady();
    if (!providerReady) return { runRef: null, providerReady: false };
    if (hasActiveAgentRunForSession(sessionId)) return { runRef: null, providerReady };
    const messages = await readSessionMessages(sessionId);
    if (messages.length > 0) return { runRef: null, providerReady };
    const run = createAgentRun(ownerId, INTRO_KICKOFF_PROMPT, { sessionId });
    return { runRef: run.run_ref, providerReady };
  })().finally(() => {
    // 守卫只覆盖单次创建调用；完成后清空让后续（如 sidecar 重启后）可恢复。
    introKickoffInFlight = null;
  });
  return introKickoffInFlight;
}

function readRequestBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => {
      chunks.push(chunk);
    });
    req.on("end", () => {
      resolve(Buffer.concat(chunks).toString("utf8"));
    });
    req.on("error", reject);
  });
}

function writeJson(res: http.ServerResponse, statusCode: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    "Access-Control-Allow-Headers": "content-type,x-user-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

const COACH_RUNTIME_STREAM_SCHEMA = "coach_runtime_stream.v1" as const;

type TurnRunner = (
  request: unknown,
  options?: {
    onPartial?: (partial: CoachPartialRevision) => Promise<void> | void;
    onActivity?: (activity: CoachActivityUpdate) => Promise<void> | void;
    onComplete?: (timing: CoachTurnTiming) => Promise<void> | void;
  },
) => Promise<CoachRuntimeTurnResponse>;

function writeNdjsonFrame(res: http.ServerResponse, frame: unknown): void {
  res.write(`${JSON.stringify(frame)}\n`);
}

const AGENT_RUN_STREAM_SCHEMA = "coach_agent_run_stream.v1" as const;

function writeSseEvent(res: http.ServerResponse, event: string, data: unknown): void {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function acceptsNdjson(req: http.IncomingMessage): boolean {
  const accept = req.headers.accept;
  if (typeof accept !== "string") return false;
  return accept.split(",").some((value) => {
    const [mediaType, ...parameters] = value.trim().split(";");
    if (mediaType.trim().toLowerCase() !== "application/x-ndjson") return false;
    return !parameters.some((parameter) => /^\s*q\s*=\s*0(?:\.0*)?\s*$/i.test(parameter));
  });
}

function schemaForPath(_pathname: string): CoachRuntimeTurnSchema {
  return "coach_runtime_turn.v1";
}

function turnStatusCode(response: { ok: boolean; error: { code?: string } | null }): number {
  if (response.ok) return 200;
  if (
    response.error?.code === "invalid_profile" ||
    response.error?.code === "unknown_provider" ||
    response.error?.code === "unknown_model"
  ) {
    return 400;
  }
  return 500;
}

async function parseJsonBody(req: http.IncomingMessage): Promise<unknown> {
  const rawBody = await readRequestBody(req);
  try {
    return rawBody.trim() ? JSON.parse(rawBody) : null;
  } catch {
    throw new Error("request body is not valid JSON");
  }
}

function writeCoachDataError(res: http.ServerResponse, error: unknown): void {
  if (error instanceof CoachDataError) {
    writeJson(res, error.statusCode, { detail: error.message });
    return;
  }
  writeJson(res, 500, {
    detail: error instanceof Error ? error.message : "Coach data operation failed",
  });
}

function writeAuthError(res: http.ServerResponse, error: unknown): void {
  if (error instanceof ProviderAuthRequestError) {
    writeJson(res, error.statusCode, {
      ok: false,
      error: {
        code: error.code,
        message: error.message,
      },
    });
    return;
  }
  writeJson(res, 500, {
    ok: false,
    error: {
      code: "auth_operation_failed",
      message: "Authentication operation failed",
    },
  });
}

function operationRoute(pathname: string):
  | { operationId: string; action: "status" | "input" | "take_result" }
  | undefined {
  const match = pathname.match(/^\/v1\/auth\/operations\/([^/]+)(?:\/(input|take-result))?$/);
  if (!match) return undefined;
  return {
    operationId: decodeURIComponent(match[1]),
    action: match[2] === "input" ? "input" : match[2] === "take-result" ? "take_result" : "status",
  };
}

export async function handleSidecarRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  authOperations: ProviderAuthOperationManager = defaultAuthOperations,
  turnRunner: TurnRunner = runCoachTurn,
): Promise<void> {
  const host = req.headers.host ?? "127.0.0.1";
  const url = new URL(req.url ?? "/", `http://${host}`);

  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Headers": "content-type,x-user-id",
      "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "Access-Control-Allow-Origin": "*",
    });
    res.end();
    return;
  }

  if (req.method === "GET" && url.pathname === "/healthz") {
    writeJson(res, 200, { ok: true });
    return;
  }

  // Loopback parity endpoint (kb-sdk C6): the backend pack-import flow POSTs a
  // candidate pack's registry JSON here and treats the TS validator as the
  // parity oracle. A failed validation is a 200 payload, not an error status;
  // only a malformed request body is a client error.
  if (req.method === "POST" && url.pathname === "/knowledge/validate") {
    let parsed: unknown;
    try {
      parsed = await parseJsonBody(req);
    } catch (error) {
      writeJson(res, 400, {
        ok: false,
        error: {
          code: "invalid_json",
          message: error instanceof Error ? error.message : String(error),
        },
      });
      return;
    }
    try {
      // Packs are v3-only (parity with Python validate_pack): the shared
      // validator still dispatches v1/v2 for historical official assets, so
      // a pack registry must be gated to v3 before validation.
      if (
        !isRecord(parsed)
        || parsed.schema_version !== REGISTRY_SCHEMA_VERSION_V3
      ) {
        writeJson(res, 200, {
          valid: false,
          errors: [
            "knowledge/registry.json must declare schema_version "
              + `'${REGISTRY_SCHEMA_VERSION_V3}'; packs cannot ship legacy v1/v2 `
              + "registries (see sdk/knowledge-pack/SPEC.md)",
          ],
        });
        return;
      }
      validateKnowledgeRegistry(parsed);
      writeJson(res, 200, { valid: true });
    } catch (error) {
      writeJson(res, 200, {
        valid: false,
        errors: [error instanceof Error ? error.message : String(error)],
      });
    }
    return;
  }

  // Backend-triggered knowledge rematerialization (kb-sdk C3): the activate
  // endpoint POSTs here after writing config/knowledge.json so a pack switch
  // takes effect immediately, without restarting the sidecar process. A
  // fallback to official is a 200 payload (mode/fallbackReason), not an error.
  if (req.method === "POST" && url.pathname === "/knowledge/rematerialize") {
    try {
      writeJson(res, 200, { ok: true, ...rematerializeActiveKnowledge() });
    } catch (error) {
      writeJson(res, 500, {
        ok: false,
        error: {
          code: "rematerialize_failed",
          message: error instanceof Error ? error.message : String(error),
        },
      });
    }
    return;
  }

  if (
    req.method === "GET" &&
    (url.pathname === "/v1/auth/capabilities" || url.pathname === "/v1/provider-auth/capabilities")
  ) {
    try {
      writeJson(res, 200, await authOperations.capabilities());
    } catch (error) {
      writeAuthError(res, error);
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/auth/operations") {
    try {
      writeJson(res, 202, await authOperations.start(await parseJsonBody(req)));
    } catch (error) {
      if (error instanceof Error && error.message === "request body is not valid JSON") {
        writeJson(res, 400, { ok: false, error: { code: "invalid_json", message: error.message } });
      } else {
        writeAuthError(res, error);
      }
    }
    return;
  }

  const authRoute = operationRoute(url.pathname);
  if (authRoute) {
    try {
      if (req.method === "GET" && authRoute.action === "status") {
        writeJson(res, 200, authOperations.get(authRoute.operationId));
        return;
      }
      if (req.method === "POST" && authRoute.action === "input") {
        writeJson(
          res,
          200,
          authOperations.submitInput(authRoute.operationId, await parseJsonBody(req)),
        );
        return;
      }
      if (req.method === "DELETE" && authRoute.action === "status") {
        writeJson(res, 200, authOperations.cancel(authRoute.operationId));
        return;
      }
      if (req.method === "POST" && authRoute.action === "take_result") {
        writeJson(res, 200, authOperations.takeResult(authRoute.operationId));
        return;
      }
    } catch (error) {
      if (error instanceof Error && error.message === "request body is not valid JSON") {
        writeJson(res, 400, { ok: false, error: { code: "invalid_json", message: error.message } });
      } else {
        writeAuthError(res, error);
      }
      return;
    }
  }

  if (
    req.method === "GET" &&
    (url.pathname === "/v1/catalog" || url.pathname === "/v0/providers/catalog" || url.pathname === "/v1/providers/catalog")
  ) {
    try {
      writeJson(res, 200, await listBuiltinProviderCatalog());
    } catch (error) {
      writeJson(
        res,
        500,
        failureResponse(
          makeError({
            category: "provider_catalog",
            code: "catalog_failed",
            message: error instanceof Error ? error.message : String(error),
            retryable: false,
          }),
        ),
      );
    }
    return;
  }

  if (
    req.method === "POST" &&
    (url.pathname === "/v1/profile/status" || url.pathname === "/v0/providers/test")
  ) {
    try {
      const parsed = await parseJsonBody(req);
      const profile = isRecord(parsed) && "profile" in parsed ? parsed.profile : parsed;
      const response =
        url.pathname === "/v0/providers/test"
          ? await testProviderConnection(profile, {
              timeoutMs: isRecord(parsed) && typeof parsed.timeout_ms === "number"
                ? parsed.timeout_ms
                : undefined,
            })
          : await getProviderProfileStatus(profile);
      const statusCode = url.pathname === "/v0/providers/test" ? 200 : response.ok ? 200 : 400;
      writeJson(res, statusCode, response);
    } catch (error) {
      writeJson(res, 400, {
        ok: false,
        error: {
          code: "invalid_json",
          message: error instanceof Error ? error.message : String(error),
        },
      });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/turn") {
    let parsed: unknown;
    try {
      parsed = await parseJsonBody(req);
    } catch (error) {
      writeJson(
        res,
        400,
        failureResponse(
          makeError({
            category: "coach_runtime",
            code: "invalid_json",
            message: error instanceof Error ? error.message : String(error),
            retryable: false,
          }),
          [],
          schemaForPath(url.pathname),
        ),
      );
      return;
    }

    if (!acceptsNdjson(req)) {
      const response = await turnRunner(parsed);
      writeJson(res, turnStatusCode(response), response);
      return;
    }

    res.writeHead(200, {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    let timing: CoachTurnTiming | null = null;
    let lastRevision = 0;
    let lastActivitySequence = 0;
    const response = await turnRunner(parsed, {
      onPartial: async (partial) => {
        // A partial is valid when it carries answer text or live thinking
        // (thinking-only revisions stream with text: null before the first
        // text delta).
        if (
          partial.revision !== lastRevision + 1 ||
          !(partial.text || partial.thinking_text)
        ) {
          throw new Error("invalid Coach partial revision");
        }
        lastRevision = partial.revision;
        // Match the agent-runs SSE path: a long partial is truncated instead
        // of failing the whole turn.
        const safeText = partial.text ? partial.text.slice(0, 12_000) : null;
        writeNdjsonFrame(res, {
          schema_version: COACH_RUNTIME_STREAM_SCHEMA,
          type: "partial",
          revision: partial.revision,
          text: safeText,
          thinking_text: partial.thinking_text ? partial.thinking_text.slice(0, 12_000) : null,
          elapsed_ms: partial.elapsed_ms,
          provider_rounds: partial.provider_rounds,
        });
      },
      onActivity: async (activity) => {
        if (
          activity.sequence !== lastActivitySequence + 1 ||
          !["thinking", "tool"].includes(activity.kind) ||
          !["started", "completed", "failed"].includes(activity.state) ||
          (activity.tool_call_id !== undefined && typeof activity.tool_call_id !== "string") ||
          (activity.tool_name !== undefined && typeof activity.tool_name !== "string") ||
          (activity.command_name !== undefined && typeof activity.command_name !== "string")
        ) {
          throw new Error("invalid Coach activity update");
        }
        lastActivitySequence = activity.sequence;
        writeNdjsonFrame(res, {
          schema_version: COACH_RUNTIME_STREAM_SCHEMA,
          type: "activity",
          activity,
        });
      },
      onComplete: async (completedTiming) => {
        timing = completedTiming;
      },
    });
    writeNdjsonFrame(res, {
      schema_version: COACH_RUNTIME_STREAM_SCHEMA,
      type: "final",
      response,
      timing,
    });
    res.end();
    return;
  }

  const stopMatch = url.pathname.match(/^\/v1\/turn\/([^/]+)\/stop$/);
  if (req.method === "POST" && stopMatch) {
    const runId = decodeURIComponent(stopMatch[1]);
    writeJson(res, 200, {
      schema_version: "coach_runtime_stop.v1",
      stopped: stopCoachTurn(runId),
    });
    return;
  }

  // ---------------------------------------------------------------------------
  // Agent run lifecycle routes
  // ---------------------------------------------------------------------------

  if (req.method === "POST" && url.pathname === "/v1/agent-runs") {
    try {
      const ownerId = ownerIdFromRequest(req);
      const body = await parseJsonBody(req);
      if (!isRecord(body)) {
        writeJson(res, 400, { detail: "Request body must be a JSON object" });
        return;
      }
      const content = typeof body.content === "string" ? body.content : "";
      const sessionIdValue = body.session_id;
      const sessionId = typeof sessionIdValue === "number" && Number.isInteger(sessionIdValue)
        ? sessionIdValue
        : undefined;
      // 结构化分析引用（前端引用菜单）：字符串数组原样透传，合法性由
      // createAgentRun 过滤（只收 analysis:N，封顶 10 条）。
      const contextRefs = Array.isArray(body.context_refs)
        ? body.context_refs.filter((ref): ref is string => typeof ref === "string")
        : undefined;
      const result = createAgentRun(ownerId, content, { sessionId, contextRefs });
      writeJson(res, 202, result);
    } catch (error) {
      if (error instanceof AgentRunError) {
        writeJson(res, 400, { detail: error.code });
      } else if (error instanceof Error && error.message === "request body is not valid JSON") {
        writeJson(res, 400, { detail: error.message });
      } else {
        writeJson(res, 500, { detail: error instanceof Error ? error.message : "Agent run creation failed" });
      }
    }
    return;
  }

  const agentRunStopMatch = url.pathname.match(/^\/v1\/agent-runs\/([^/]+)\/stop$/);
  if (req.method === "POST" && agentRunStopMatch) {
    try {
      const ownerId = ownerIdFromRequest(req);
      const runRef = decodeURIComponent(agentRunStopMatch[1]);
      const result = await stopAgentRun(ownerId, runRef);
      if (result === null) {
        writeJson(res, 404, { detail: "Coach agent run is unavailable" });
      } else {
        writeJson(res, 200, result);
      }
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  // Composer 排队/转向透传（P3）：steer 运行中注入，follow-up 停止前排入，
  // next-turn 排进下一轮开头（审计#13；同 run 连续 turn，run_id 不变）。
  // 纯转发到 pi AgentHarness 对应入口，零持久化；无运行中会话时用显式
  // 409（run_not_steerable）/ 404 语义，绝不落 500。
  const agentRunQueueMatch = url.pathname.match(/^\/v1\/agent-runs\/([^/]+)\/(steer|follow-up|next-turn)$/);
  if (req.method === "POST" && agentRunQueueMatch) {
    const runRef = decodeURIComponent(agentRunQueueMatch[1]);
    const queueVerb = agentRunQueueMatch[2];
    const kind = queueVerb === "follow-up"
      ? "follow_up" as const
      : queueVerb === "next-turn"
        ? "next_turn" as const
        : "steer" as const;
    try {
      let body: unknown;
      try {
        body = await parseJsonBody(req);
      } catch (error) {
        writeJson(res, 400, { detail: error instanceof Error ? error.message : "Invalid request body" });
        return;
      }
      if (!isRecord(body)) {
        writeJson(res, 400, { detail: "Request body must be a JSON object" });
        return;
      }
      if (typeof body.text !== "string") {
        writeJson(res, 400, { detail: "text is required" });
        return;
      }
      const text = body.text.trim().slice(0, 12_000);
      if (!text) {
        writeJson(res, 400, { detail: "text must be a non-empty string" });
        return;
      }
      let drainMode: "all" | "one-at-a-time";
      if (body.drain_mode !== undefined) {
        if (body.drain_mode !== "all" && body.drain_mode !== "one-at-a-time") {
          writeJson(res, 400, { detail: 'drain_mode must be "all" or "one-at-a-time"' });
          return;
        }
        drainMode = body.drain_mode;
      }
      const ownerId = ownerIdFromRequest(req);
      const result = await steerAgentRun(
        ownerId,
        runRef,
        { kind, text, ...(drainMode !== undefined ? { drain_mode: drainMode } : {}) },
      );
      if (result === null) {
        writeJson(res, 404, { detail: "Coach agent run is unavailable" });
      } else {
        writeJson(res, 200, {
          schema_version: "coach_agent_run_steer.v1",
          run_ref: runRef,
          kind,
          queued: true,
        });
      }
    } catch (error) {
      if (error instanceof AgentRunError) {
        writeJson(res, 409, { detail: error.code });
      } else {
        writeCoachDataError(res, error);
      }
    }
    return;
  }

  const agentRunRetryMatch = url.pathname.match(/^\/v1\/agent-runs\/([^/]+)\/retry$/);
  if (req.method === "POST" && agentRunRetryMatch) {
    try {
      const ownerId = ownerIdFromRequest(req);
      const runRef = decodeURIComponent(agentRunRetryMatch[1]);
      const result = retryAgentRun(ownerId, runRef);
      if (result === null) {
        writeJson(res, 404, { detail: "Coach agent run is unavailable" });
      } else {
        writeJson(res, 202, result);
      }
    } catch (error) {
      if (error instanceof AgentRunError) {
        writeJson(res, 409, { detail: error.code });
      } else {
        writeCoachDataError(res, error);
      }
    }
    return;
  }

  const confirmationDecisionMatch = url.pathname.match(/^\/v1\/confirmations\/([^/]+)\/decision$/);
  if (req.method === "POST" && confirmationDecisionMatch) {
    try {
      const ownerId = ownerIdFromRequest(req);
      const confirmationRef = decodeURIComponent(confirmationDecisionMatch[1]);
      const body = await parseJsonBody(req);
      const decision = isRecord(body) && body.decision === "confirm" ? "confirm"
        : isRecord(body) && body.decision === "reject" ? "reject"
        : undefined;
      if (!decision) {
        writeJson(res, 400, { detail: "decision must be confirm or reject" });
        return;
      }
      const result = decideConfirmation(ownerId, confirmationRef, decision);
      if (result === null) {
        writeJson(res, 404, { detail: "Coach confirmation is unavailable" });
      } else {
        writeJson(res, 200, result);
      }
    } catch (error) {
      if (error instanceof AgentRunError) {
        writeJson(res, 409, { detail: error.code });
      } else {
        writeCoachDataError(res, error);
      }
    }
    return;
  }

  const agentRunStreamMatch = url.pathname.match(/^\/v1\/agent-runs\/([^/]+)\/stream$/);
  if (req.method === "GET" && agentRunStreamMatch) {
    const ownerId = ownerIdFromRequest(req);
    const runRef = decodeURIComponent(agentRunStreamMatch[1]);
    const initial = getAgentRun(ownerId, runRef);
    if (initial === null) {
      writeJson(res, 404, { detail: "Coach agent run is unavailable" });
      return;
    }
    // A stream subscriber replaces the GET poll that used to drive
    // provider-recovery requeue; resume any waiting run before subscribing.
    resumeWaitingRuns(ownerId);
    res.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-cache",
      "Content-Type": "text/event-stream; charset=utf-8",
      Connection: "keep-alive",
      "X-Content-Type-Options": "nosniff",
    });
    res.flushHeaders();

    // Catch a late subscriber up with the current partial text (and live
    // thinking) before new revisions stream in.
    if (initial.partial_text || initial.partial_thinking) {
      writeSseEvent(res, "partial", {
        schema_version: AGENT_RUN_STREAM_SCHEMA,
        type: "partial",
        text: initial.partial_text ?? "",
        thinking_text: initial.partial_thinking,
      });
    }

    let closed = false;
    let unsubscribe: () => void = () => {};
    const close = () => {
      if (closed) return;
      closed = true;
      unsubscribe();
      res.end();
    };

    const subscribed = subscribeAgentRun(ownerId, runRef, {
      onPartial: (text, thinking) => {
        if (closed) return;
        writeSseEvent(res, "partial", {
          schema_version: AGENT_RUN_STREAM_SCHEMA,
          type: "partial",
          text,
          thinking_text: thinking ?? null,
        });
      },
      onActivity: (event) => {
        if (closed) return;
        writeSseEvent(res, "activity", {
          schema_version: AGENT_RUN_STREAM_SCHEMA,
          type: "activity",
          event,
        });
      },
      onDone: (state) => {
        if (closed) return;
        writeSseEvent(res, "done", {
          schema_version: AGENT_RUN_STREAM_SCHEMA,
          type: "done",
          status: state.status,
          run: state,
        });
        close();
      },
    });
    if (subscribed === null) {
      close();
      return;
    }
    unsubscribe = subscribed;
    res.on("close", close);
    req.on("close", close);
    req.on("aborted", close);
    return;
  }

  const agentRunMatch = url.pathname.match(/^\/v1\/agent-runs\/([^/]+)$/);
  if (req.method === "GET" && agentRunMatch) {
    try {
      const ownerId = ownerIdFromRequest(req);
      const runRef = decodeURIComponent(agentRunMatch[1]);
      resumeWaitingRuns(ownerId);
      const result = getAgentRun(ownerId, runRef);
      if (result === null) {
        writeJson(res, 404, { detail: "Coach agent run is unavailable" });
      } else {
        writeJson(res, 200, result);
      }
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  // ---------------------------------------------------------------------------
  // Intro Session routes (one-time 开场分析)
  // ---------------------------------------------------------------------------

  // 幂等创建：首次调用建会话并持久化 flag，随后自动发出首条 Coach 消息；
  // 之后每次调用都返回同一个 session_id，不再新建、不再重发首条。
  // 当前档凭据不可用时只建会话、不建 kickoff run（run_ref=null、
  // provider_ready=false），前端下次挂载复查时再 POST 补发。
  if (req.method === "POST" && url.pathname === "/coach/intro-session") {
    try {
      const ownerId = ownerIdFromRequest(req);
      const ensured = await ensureIntroSession();
      const kickoff = await ensureIntroKickoffRun(ownerId, ensured.session_id);
      writeJson(res, 200, {
        session_id: ensured.session_id,
        created: ensured.created,
        run_ref: kickoff.runRef,
        provider_ready: kickoff.providerReady,
      });
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  if (req.method === "GET" && url.pathname === "/coach/intro-session") {
    const flag = readIntroSessionFlag();
    // has_messages=false → 开场分析尚未开讲（含 flag 已置但被凭据闸门拦下的
    // 受害现场），前端据此在 Provider 恢复后补发一次幂等 POST（自愈）。
    // 判空口径与 kickoff 守卫一致：readSessionMessages 已过滤内部 kickoff 指令。
    const hasMessages = flag.created && flag.session_id !== null
      ? (await readSessionMessages(flag.session_id)).length > 0
      : false;
    writeJson(res, 200, {
      created: flag.created,
      session_id: flag.session_id,
      has_messages: hasMessages,
    });
    return;
  }

  // ---------------------------------------------------------------------------
  // Coach session routes
  // ---------------------------------------------------------------------------

  if (req.method === "GET" && url.pathname === "/v1/sessions") {
    try {
      const ownerId = ownerIdFromRequest(req);
      const includeArchived = url.searchParams.get("include_archived") === "true";
      writeJson(res, 200, await listCoachSessions(ownerId, { includeArchived }));
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/sessions") {
    try {
      const ownerId = ownerIdFromRequest(req);
      const body = await parseJsonBody(req);
      const title = isRecord(body) && typeof body.title === "string" ? body.title : undefined;
      writeJson(res, 201, await createCoachSession(ownerId, title));
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  // 会话级 token/费用统计（审计#20）：pi Session.getSessionStats 的透传，
  // 展示型数据——会话不存在回 404，底层取数失败回 null 字段而非 500。
  const sessionStatsMatch = url.pathname.match(/^\/v1\/sessions\/([^/]+)\/stats$/);
  if (req.method === "GET" && sessionStatsMatch) {
    try {
      const sessionId = Number(decodeURIComponent(sessionStatsMatch[1]));
      if (!Number.isInteger(sessionId) || sessionId <= 0) {
        writeJson(res, 400, { detail: "Coach session id is invalid" });
        return;
      }
      const stats = await readSessionStats(sessionId);
      if (stats === null) {
        writeJson(res, 404, { detail: "Coach session is unavailable" });
        return;
      }
      writeJson(res, 200, { session_id: sessionId, stats });
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  const sessionMatch = url.pathname.match(/^\/v1\/sessions\/([^/]+)$/);
  if (sessionMatch && (req.method === "GET" || req.method === "PATCH" || req.method === "DELETE")) {
    try {
      const ownerId = ownerIdFromRequest(req);
      const sessionId = Number(decodeURIComponent(sessionMatch[1]));
      if (!Number.isInteger(sessionId) || sessionId <= 0) {
        writeJson(res, 400, { detail: "Coach session id is invalid" });
        return;
      }
      if (req.method === "GET") {
        writeJson(res, 200, await getCoachSessionDetail(ownerId, sessionId));
      } else if (req.method === "DELETE") {
        writeJson(res, 200, await deleteCoachSession(ownerId, sessionId));
      } else {
        const body = await parseJsonBody(req);
        const update: { title?: string; status?: "archived" } = {};
        if (isRecord(body)) {
          if (typeof body.title === "string") update.title = body.title;
          if (body.status === "archived") update.status = "archived";
        }
        writeJson(res, 200, await updateCoachSession(ownerId, sessionId, update));
      }
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  // 编辑重发截断（digests §11 item 7）：keep_messages = 保留前 N 条可见消息。
  const sessionTruncateMatch = url.pathname.match(/^\/v1\/sessions\/([^/]+)\/truncate$/);
  if (req.method === "POST" && sessionTruncateMatch) {
    try {
      let body: unknown;
      try {
        body = await parseJsonBody(req);
      } catch (error) {
        writeJson(res, 400, { detail: error instanceof Error ? error.message : "Invalid request body" });
        return;
      }
      if (!isRecord(body)) {
        writeJson(res, 400, { detail: "Request body must be a JSON object" });
        return;
      }
      const sessionId = Number(decodeURIComponent(sessionTruncateMatch[1]));
      const ownerId = ownerIdFromRequest(req);
      writeJson(res, 200, await truncateCoachSession(ownerId, sessionId, body.keep_messages as number));
    } catch (error) {
      writeCoachDataError(res, error);
    }
    return;
  }

  if (await handleProviderProfileRequest(req, res, url, authOperations)) return;

  writeJson(res, 404, { ok: false, error: "not_found" });
}

export function createSidecarServer(options: {
  authOperations?: ProviderAuthOperationManager;
  turnRunner?: TurnRunner;
} = {}): http.Server {
  const authOperations = options.authOperations ?? new ProviderAuthOperationManager();
  const ownsAuthOperations = options.authOperations === undefined;
  const server = http.createServer((req, res) => {
    handleSidecarRequest(req, res, authOperations, options.turnRunner ?? runCoachTurn).catch((error) => {
      console.error("[sidecar] request failed:", error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : error);
      try {
        appendFileSync(
          join(getDataRoot(), "coach-error.log"),
          `${new Date().toISOString()} [sidecar] ${error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error)}\n`,
          "utf8",
        );
      } catch {
        // Best-effort error capture; never mask the original failure.
      }
      if (res.writableEnded) return;
      const failure = failureResponse(
        makeError({
          category: "coach_runtime",
          code: "unhandled",
          message: "Unhandled sidecar error",
          retryable: false,
        }),
      );
      if (res.headersSent) {
        writeNdjsonFrame(res, {
          schema_version: COACH_RUNTIME_STREAM_SCHEMA,
          type: "final",
          response: failure,
          timing: null,
        });
        res.end();
      } else {
        writeJson(res, 500, failure);
      }
    });
  });
  if (ownsAuthOperations) server.on("close", () => authOperations.dispose());
  return server;
}

export interface KnowledgeRematerializeResult {
  mode: "official" | "pack";
  packId?: string;
  fallbackReason?: string;
}

/**
 * Resolve the active knowledge base and re-materialize it into app-data.
 * Shared by sidecar startup and POST /knowledge/rematerialize so an activate
 * call takes effect immediately instead of at the next process start. A
 * failure must not throw past the caller's error handling: startup treats it
 * as a log, the route answers 5xx.
 */
function rematerializeActiveKnowledge(): KnowledgeRematerializeResult {
  const active = resolveActiveKnowledge();
  const registry = loadActiveKnowledgeRegistry();
  const fallbackReason = lastActiveKnowledgeFallbackReason();
  if (active.mode === "official" && fallbackReason) {
    // Config/pointer-level degradation; bad-pack load failures already
    // logged their own reason inside loadActiveKnowledgeRegistry.
    console.error(`[coach] knowledge active state fell back to official: ${fallbackReason}`);
  }
  materializeKnowledgeDir(undefined, {
    registry,
    packDisplayName: active.mode === "pack" && fallbackReason === null
      ? activePackDisplayName(active.packId)
      : undefined,
  });
  return active.mode === "pack" && fallbackReason === null
    ? { mode: "pack", packId: active.packId }
    : fallbackReason
    ? { mode: "official", fallbackReason }
    : { mode: "official" };
}

export function startSidecarServer(options: {
  host?: string;
  port?: number;
  authOperations?: ProviderAuthOperationManager;
} = {}): http.Server {
  const host = options.host ?? DEFAULT_SIDECAR_HOST;
  const port = options.port ?? DEFAULT_SIDECAR_PORT;
  // Materialize the active knowledge REGISTRY into app-data so the Coach's
  // plain file tools can browse it (knowledge/index.json). Resolution walks
  // DATA_ROOT/config/knowledge.json (kb-sdk C3): a pack whose registry fails
  // to load falls back to the official base, and the fallback reason lands in
  // the startup log. Idempotent and bound to registry_version; a failure must
  // not keep the sidecar from starting. The backend activate endpoint can
  // re-trigger the same routine via POST /knowledge/rematerialize.
  try {
    rematerializeActiveKnowledge();
  } catch (error) {
    console.error("knowledge materialization failed:", error);
  }
  const server = createSidecarServer({ authOperations: options.authOperations });
  server.listen(port, host);
  return server;
}
