import assert from "node:assert/strict";
import http from "node:http";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-sidecar-error-log-"));
process.env.DATA_ROOT = dataRoot;

const { createSidecarServer } = await import("../src/sidecar-server.ts");

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      {
        host: "127.0.0.1",
        port: address.port,
        method,
        path,
        headers: body
          ? {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            }
          : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({
            statusCode: res.statusCode ?? 0,
            json: raw ? JSON.parse(raw) : null,
          });
        });
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

test("an unexpected error keeps the unhandled contract and lands in coach-error.log", async () => {
  const server = createSidecarServer({
    turnRunner: async () => {
      throw new Error("sidecar boom sentinel");
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v1/turn", "{}");
    // (a) The response contract is unchanged: still the top-level catch-all.
    assert.equal(res.statusCode, 500);
    const error = (
      res.json as { error: { category: string; code: string; message: string; retryable: boolean } }
    ).error;
    assert.equal(error.category, "coach_runtime");
    assert.equal(error.code, "unhandled");
    assert.equal(error.message, "Unhandled sidecar error");
    assert.equal(error.retryable, false);

    // (b) The original error is appended to coach-error.log for diagnosis.
    assert.ok(existsSync(join(dataRoot, "coach-error.log")), "coach-error.log must exist after an unhandled error");
    const log = readFileSync(join(dataRoot, "coach-error.log"), "utf8");
    assert.match(log, /\[sidecar\]/);
    assert.ok(log.includes("sidecar boom sentinel"), `log should contain the raw message, got: ${JSON.stringify(log)}`);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});
