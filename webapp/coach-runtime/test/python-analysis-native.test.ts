import assert from "node:assert/strict";
import http from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// DATA_ROOT must be set before python-analysis.ts reads the config file.
process.env.DATA_ROOT = mkdtempSync(join(tmpdir(), "coach-python-analysis-"));
process.env.AIMING_COOKIE_ANALYSIS_POLL_INTERVAL_MS = "10";

const { executeNativePythonAnalysis, isNativePythonAnalysisCommand } = await import(
  "../src/python-analysis.ts"
);
const { createProductCommandTool } = await import("../src/product-command-tools.ts");

function writeConfig(baseUrl: string): void {
  mkdirSync(process.env.DATA_ROOT!, { recursive: true });
  writeFileSync(
    join(process.env.DATA_ROOT!, "desktop-runtime.json"),
    JSON.stringify({ python_base_url: baseUrl, python_token: "desktop-token" }),
    "utf-8",
  );
}

function removeConfig(): void {
  rmSync(join(process.env.DATA_ROOT!, "desktop-runtime.json"), { force: true });
}

// The Python worker writes analyses/{session_id}/overview.json; the command
// waits for it after the session reaches done, so tests pre-create it.
function writeOverview(sessionId: number): void {
  const dir = join(process.env.DATA_ROOT!, "analyses", String(sessionId));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), JSON.stringify({ status: "done" }), "utf-8");
}

type MockRoute = {
  match: (method: string, url: string) => boolean;
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => void;
};

function startMockServer(routes: MockRoute[]): Promise<http.Server> {
  const server = http.createServer((req, res) => {
    const url = req.url ?? "/";
    const route = routes.find((r) => r.match(req.method ?? "GET", url));
    if (!route) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "not found" }));
      return;
    }
    route.handler(req, res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

function serverBaseUrl(server: http.Server): string {
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("server is not listening");
  return `http://127.0.0.1:${address.port}`;
}

function closeServer(server: http.Server): Promise<void> {
  return new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
}

test("analysis.create_from_run is recognized as a native Python analysis command", () => {
  assert.equal(isNativePythonAnalysisCommand("analysis.create_from_run"), true);
  assert.equal(isNativePythonAnalysisCommand("analysis.retry"), false);
});

test("analysis.create_from_run triggers Python and returns the completed session", async () => {
  const requests: Array<{ url: string; headers: http.IncomingHttpHeaders; body?: unknown }> = [];
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (req, res) => {
        let body = "";
        req.on("data", (chunk: Buffer) => (body += chunk.toString("utf8")));
        req.on("end", () => {
          requests.push({ url: req.url ?? "", headers: req.headers, body: body ? JSON.parse(body) : undefined });
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ session_id: 42 }));
        });
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/42",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(42);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result_ref, "analysis:42");
    assert.deepEqual(result.result, {
      session_id: 42,
      analysis_ref: "analysis:42",
      status: "done",
    });
    assert.equal(requests.length, 1);
    const post = requests[0];
    assert.equal(post.headers["x-aiming-cookie-desktop-token"], "desktop-token");
    assert.equal(post.headers["idempotency-key"], "idem-key");
    assert.deepEqual(post.body, {});
  } finally {
    await closeServer(server);
  }
});

test("analysis.create_from_run polls until the worker finishes", async () => {
  let sessionPolls = 0;
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 43 }));
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/43",
      handler: (_req, res) => {
        sessionPolls += 1;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: sessionPolls >= 2 ? "done" : "running" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(43);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key-2",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result_ref, "analysis:43");
    assert.equal(sessionPolls, 2);
  } finally {
    await closeServer(server);
  }
});

test("analysis.create_from_run surfaces a rejected trigger", async () => {
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (_req, res) => {
        res.writeHead(429, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ detail: "已有 Analysis 正在进行" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key-3",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "analysis_trigger_failed");
    assert.match(result.warning_or_error?.message ?? "", /已有 Analysis 正在进行/);
  } finally {
    await closeServer(server);
  }
});

test("analysis.create_from_run surfaces a failed worker run", async () => {
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 44 }));
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/44",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          status: "failed",
          error: { code: "llm_provider", message: "provider quota exceeded", retryable: true },
        }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key-4",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "llm_provider");
    assert.match(result.warning_or_error?.message ?? "", /provider quota exceeded/);
    assert.equal(result.result_ref, "analysis:44");
  } finally {
    await closeServer(server);
  }
});

test("analysis.create_from_run reports when the Python backend is not ready", async () => {
  removeConfig();
  const result = await executeNativePythonAnalysis(
    "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key-5",
  );
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "python_backend_unavailable");
});

test("analysis.create_from_run rejects an invalid run_ref", async () => {
  const result = await executeNativePythonAnalysis(
    "analysis.create_from_run", { run_ref: "not-a-ref" }, "owner-a", "idem-key-6",
  );
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "invalid_parameters");
});

test("tool dispatches analysis.create_from_run natively when no bridge exists", async () => {
  removeConfig();
  const tool = createProductCommandTool(null);
  const result = await tool.execute("call", {
    command_name: "analysis.create_from_run",
    parameters: { run_ref: "run:7" },
  });
  const text = result.content[0]?.text ?? "";
  const parsed = JSON.parse(text) as { status: string; warning_or_error?: { code: string } };
  assert.equal(parsed.status, "failed");
  assert.equal(parsed.warning_or_error?.code, "python_backend_unavailable");
});
