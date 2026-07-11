import http from "node:http";

import { failureResponse, makeError } from "./contracts.ts";
import { runCoachTurn } from "./turn.ts";

export const DEFAULT_SIDECAR_HOST = "127.0.0.1";
export const DEFAULT_SIDECAR_PORT = 8765;

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

export async function handleSidecarRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
): Promise<void> {
  const host = req.headers.host ?? "127.0.0.1";
  const url = new URL(req.url ?? "/", `http://${host}`);

  if (req.method === "GET" && url.pathname === "/healthz") {
    writeJson(res, 200, { ok: true });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v0/turn") {
    let rawBody: string;
    try {
      rawBody = await readRequestBody(req);
    } catch (error) {
      writeJson(
        res,
        400,
        failureResponse(
          makeError({
            category: "coach_runtime",
            code: "body_read_failed",
            message: error instanceof Error ? error.message : String(error),
            retryable: false,
          }),
        ),
      );
      return;
    }

    let parsed: unknown;
    try {
      parsed = rawBody.trim() ? JSON.parse(rawBody) : null;
    } catch {
      writeJson(
        res,
        400,
        failureResponse(
          makeError({
            category: "coach_runtime",
            code: "invalid_json",
            message: "request body is not valid JSON",
            retryable: false,
          }),
        ),
      );
      return;
    }

    const response = await runCoachTurn(parsed);
    writeJson(res, response.ok ? 200 : 500, response);
    return;
  }

  writeJson(res, 404, { ok: false, error: "not_found" });
}

export function createSidecarServer(): http.Server {
  return http.createServer((req, res) => {
    handleSidecarRequest(req, res).catch((error) => {
      writeJson(
        res,
        500,
        failureResponse(
          makeError({
            category: "coach_runtime",
            code: "unhandled",
            message: error instanceof Error ? error.message : String(error),
            retryable: false,
          }),
        ),
      );
    });
  });
}

export function startSidecarServer(options: {
  host?: string;
  port?: number;
} = {}): http.Server {
  const host = options.host ?? DEFAULT_SIDECAR_HOST;
  const port = options.port ?? DEFAULT_SIDECAR_PORT;
  const server = createSidecarServer();
  server.listen(port, host);
  return server;
}