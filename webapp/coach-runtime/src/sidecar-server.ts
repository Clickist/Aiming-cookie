import http from "node:http";

import { failureResponse, makeError, type CoachRuntimeTurnSchema, isRecord } from "./contracts.ts";
import {
  ProviderAuthOperationManager,
  ProviderAuthRequestError,
} from "./provider-auth.ts";
import { getProviderProfileStatus, testProviderConnection } from "./provider-profile.ts";
import { listBuiltinProviderCatalog } from "./provider-models.ts";
import {
  runCoachTurn,
  stopCoachTurn,
  type CoachPartialRevision,
  type CoachTurnTiming,
} from "./turn.ts";
import type { CoachRuntimeTurnResponse } from "./contracts.ts";

export const DEFAULT_SIDECAR_HOST = "127.0.0.1";
export const DEFAULT_SIDECAR_PORT = 8765;

const defaultAuthOperations = new ProviderAuthOperationManager();

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
    onComplete?: (timing: CoachTurnTiming) => Promise<void> | void;
  },
) => Promise<CoachRuntimeTurnResponse>;

function writeNdjsonFrame(res: http.ServerResponse, frame: unknown): void {
  res.write(`${JSON.stringify(frame)}\n`);
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

function schemaForPath(pathname: string): CoachRuntimeTurnSchema {
  return pathname === "/v0/turn" ? "coach_runtime_turn.v0" : "coach_runtime_turn.v1";
}

function turnStatusCode(response: { ok: boolean; error: { code?: string } | null }): number {
  if (response.ok) return 200;
  if (
    response.error?.code === "invalid_profile" ||
    response.error?.code === "unknown_provider" ||
    response.error?.code === "unknown_model" ||
    response.error?.code === "unknown_model_capabilities"
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

  if (req.method === "GET" && url.pathname === "/healthz") {
    writeJson(res, 200, { ok: true });
    return;
  }

  if (req.method === "GET" && url.pathname === "/v1/auth/capabilities") {
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
    (url.pathname === "/v1/catalog" || url.pathname === "/v0/providers/catalog")
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

  if (req.method === "POST" && (url.pathname === "/v0/turn" || url.pathname === "/v1/turn")) {
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

    if (url.pathname !== "/v1/turn" || !acceptsNdjson(req)) {
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
    const response = await turnRunner(parsed, {
      onPartial: async (partial) => {
        if (
          partial.revision !== lastRevision + 1 ||
          !partial.text ||
          partial.text.length > 12_000
        ) {
          throw new Error("invalid Coach partial revision");
        }
        lastRevision = partial.revision;
        writeNdjsonFrame(res, {
          schema_version: COACH_RUNTIME_STREAM_SCHEMA,
          type: "partial",
          revision: partial.revision,
          text: partial.text,
          elapsed_ms: partial.elapsed_ms,
          provider_rounds: partial.provider_rounds,
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

  writeJson(res, 404, { ok: false, error: "not_found" });
}

export function createSidecarServer(options: {
  authOperations?: ProviderAuthOperationManager;
  turnRunner?: TurnRunner;
} = {}): http.Server {
  const authOperations = options.authOperations ?? new ProviderAuthOperationManager();
  const ownsAuthOperations = options.authOperations === undefined;
  const server = http.createServer((req, res) => {
    handleSidecarRequest(req, res, authOperations, options.turnRunner ?? runCoachTurn).catch(() => {
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

export function startSidecarServer(options: {
  host?: string;
  port?: number;
  authOperations?: ProviderAuthOperationManager;
} = {}): http.Server {
  const host = options.host ?? DEFAULT_SIDECAR_HOST;
  const port = options.port ?? DEFAULT_SIDECAR_PORT;
  const server = createSidecarServer({ authOperations: options.authOperations });
  server.listen(port, host);
  return server;
}
