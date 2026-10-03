// B0 locale 管道测试：X-Locale 头的读取（sidecar HTTP 层）与转发
// （python-analysis 桥 → Python backend）。纯管道：默认 zh-CN 行为不变，
// 本波不接任何文案消费方。
import assert from "node:assert/strict";
import http from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// DATA_ROOT must be set before python-analysis.ts reads the config file.
process.env.DATA_ROOT = mkdtempSync(join(tmpdir(), "coach-locale-pipe-"));
process.env.AIMING_COOKIE_ANALYSIS_POLL_INTERVAL_MS = "10";

const { localeFromRequest } = await import("../src/sidecar-coach-data.ts");
const { executeNativePythonAnalysis } = await import("../src/python-analysis.ts");
const { createSidecarServer } = await import("../src/sidecar-server.ts");
const { DESKTOP_TEST_TOKEN } = await import("./desktop-token-env.ts");

function fakeIncomingMessage(headers: http.IncomingHttpHeaders): http.IncomingMessage {
  return { headers } as unknown as http.IncomingMessage;
}

test("localeFromRequest reads the x-locale header", () => {
  assert.equal(localeFromRequest(fakeIncomingMessage({ "x-locale": "en-US" })), "en-US");
  assert.equal(localeFromRequest(fakeIncomingMessage({ "x-locale": "zh-CN" })), "zh-CN");
});

test("localeFromRequest defaults to zh-CN when the header is missing or invalid", () => {
  assert.equal(localeFromRequest(fakeIncomingMessage({})), "zh-CN");
  assert.equal(localeFromRequest(fakeIncomingMessage({ "x-locale": "fr-FR" })), "zh-CN");
  assert.equal(localeFromRequest(fakeIncomingMessage({ "x-locale": "" })), "zh-CN");
});

type CapturedRequest = { method: string; url: string; headers: http.IncomingHttpHeaders };

function startMockPythonServer(captured: CapturedRequest[]): Promise<http.Server> {
  const server = http.createServer((req, res) => {
    captured.push({ method: req.method ?? "GET", url: req.url ?? "/", headers: req.headers });
    if ((req.url ?? "").includes("/analyze")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ session_id: 31 }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "done" }));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

function writePythonConfig(baseUrl: string): void {
  mkdirSync(process.env.DATA_ROOT!, { recursive: true });
  writeFileSync(
    join(process.env.DATA_ROOT!, "desktop-runtime.json"),
    JSON.stringify({ python_base_url: baseUrl, python_token: "desktop-token" }),
    "utf-8",
  );
  // analysis.create_from_run 完成后等待 overview.json；预创建避免等待。
  const dir = join(process.env.DATA_ROOT!, "analyses", "31");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), JSON.stringify({ status: "done" }), "utf-8");
}

test("python-analysis bridge forwards X-Locale to the Python backend", async () => {
  const captured: CapturedRequest[] = [];
  const server = await startMockPythonServer(captured);
  writePythonConfig(`http://127.0.0.1:${(server.address() as { port: number }).port}`);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run",
      { run_ref: "run:7" },
      "owner-locale",
      "idem-key",
      undefined,
      "en-US",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(captured.length, 2); // POST analyze + GET session status
    assert.equal(captured[0].headers["x-locale"], "en-US");
    assert.equal(captured[1].headers["x-locale"], "en-US");
  } finally {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
    rmSync(join(process.env.DATA_ROOT!, "desktop-runtime.json"), { force: true });
  }
});

test("python-analysis bridge defaults X-Locale to zh-CN", async () => {
  const captured: CapturedRequest[] = [];
  const server = await startMockPythonServer(captured);
  writePythonConfig(`http://127.0.0.1:${(server.address() as { port: number }).port}`);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run",
      { run_ref: "run:8" },
      "owner-locale",
      "idem-key",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(captured[0].headers["x-locale"], "zh-CN");
  } finally {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
    rmSync(join(process.env.DATA_ROOT!, "desktop-runtime.json"), { force: true });
  }
});

test("sidecar accepts x-locale on agent-run creation without behavior change", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const address = server.address() as { port: number };
    const res = await new Promise<{ statusCode: number; json: unknown }>((resolve, reject) => {
      const req = http.request(
        {
          host: "127.0.0.1",
          port: address.port,
          method: "POST",
          path: "/v1/agent-runs",
          headers: {
            "Content-Type": "application/json",
            "X-User-Id": "locale-owner",
            "X-Locale": "en-US",
            "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
          },
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk: Buffer) => chunks.push(chunk));
          res.on("end", () => {
            const raw = Buffer.concat(chunks).toString("utf8");
            resolve({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
          });
        },
      );
      req.on("error", reject);
      req.write(JSON.stringify({ content: "locale pipe smoke" }));
      req.end();
    });
    // 创建本身不因带 x-locale 头而失败（turn 会因无 Provider 排队等待，属既有行为）。
    assert.equal(res.statusCode, 202);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
});
