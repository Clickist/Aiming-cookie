import assert from "node:assert/strict";
import http from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// DATA_ROOT must be set before python-analysis.ts reads the config file.
process.env.DATA_ROOT = mkdtempSync(join(tmpdir(), "coach-python-analysis-"));
process.env.AIMING_COOKIE_ANALYSIS_POLL_INTERVAL_MS = "10";
// [fix 2026-10-04] pending 语义用例需要短等待预算（默认 120s 不可测）。
process.env.AIMING_COOKIE_ANALYSIS_TIMEOUT_MS = "120";

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

// Backend session records (sessions/{id}.json) hold kovaak_run_id/status/result —
// the [fix 2026-10-04] D rerun-entry gate reads them locally.
function writeSession(sessionId: number, data: Record<string, unknown>): void {
  const dir = join(process.env.DATA_ROOT!, "sessions");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${sessionId}.json`), JSON.stringify(data), "utf-8");
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

// [fix 2026-10-04] 等待超时 ≠ 失败：返回 succeeded + result.status="pending"，
// 带阶段事实与转告指引，分析仍在 Python 侧后台继续。
test("analysis.create_from_run returns pending with phase facts when the wait times out", async () => {
  const startedAt = new Date(Date.now() - 90_000).toISOString();
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 46 }));
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/46",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          status: "running",
          task_phase: "analyzing_video",
          started_at: startedAt,
          attempts: 1,
        }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7" }, "owner-a", "idem-key-pending",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result_ref, "analysis:46");
    assert.equal(result.result?.status, "pending");
    assert.equal(result.result?.task_phase, "analyzing_video");
    assert.equal(result.result?.attempts, 1);
    assert.ok((result.result?.elapsed_seconds ?? 0) >= 90);
    assert.match(result.result?.guidance ?? "", /analyzing_video/);
    assert.match(result.result?.guidance ?? "", /不要继续阻塞等待/);
  } finally {
    await closeServer(server);
  }
});

// [fix 2026-10-04] 场景类型修正后的显式重跑：force 必须透传到 Python 侧。
test("analysis.create_from_run forwards force to the Python backend", async () => {
  const bodies: unknown[] = [];
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (req, res) => {
        let raw = "";
        req.on("data", (chunk: Buffer) => (raw += chunk.toString("utf8")));
        req.on("end", () => {
          bodies.push(raw ? JSON.parse(raw) : {});
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ session_id: 47 }));
        });
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/47",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(47);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:7", force: true }, "owner-a", "idem-key-force",
    );
    assert.equal(result.status, "succeeded");
    assert.deepEqual(bodies, [{ force: true }]);
  } finally {
    await closeServer(server);
  }
});

// [fix 2026-10-04] D：done 但有残缺（deterministic.limitations 非空）的 run，
// 复用既有 done 分析时结果里暴露 force 重跑入口。[2026-10-07] 返回面收敛：
// 机器码 limitations 只进 [analysis-diagnostics] 诊断日志，payload 只带纯
// 指令式 guidance（不提残缺/limitations/旁车）。
test("analysis.create_from_run surfaces a force rerun entry when reusing a degraded done analysis", async () => {
  writeSession(61, {
    id: 61,
    status: "done",
    kovaak_run_id: 9,
    result: { deterministic: { limitations: ["telemetry_alignment_missing"] } },
  });
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/9/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 61 }));   // 复用既有 done
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/61",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(61);
  const diagnosticLines: string[] = [];
  const originalConsoleError = console.error;
  console.error = (...args: unknown[]) => {
    diagnosticLines.push(args.map((item) => (typeof item === "string" ? item : String(item))).join(" "));
  };
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:9" }, "owner-a", "idem-key-rerun",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result?.status, "done");
    assert.equal(result.result?.rerun_available, true);
    // 机器码不再进入工具返回 payload，等量信息走诊断日志。
    assert.equal(result.result?.limitations, undefined);
    assert.deepEqual(Object.keys(result.result ?? {}).sort(), [
      "analysis_ref",
      "guidance",
      "rerun_available",
      "session_id",
      "status",
    ]);
    // guidance 是纯指令式：只讲操作，不描述数据完整度状态。
    assert.match(result.result?.guidance ?? "", /直接按现有结果讲解/);
    assert.match(result.result?.guidance ?? "", /force: true/);
    assert.match(result.result?.guidance ?? "", /不要向用户解释/);
    assert.doesNotMatch(result.result?.guidance ?? "", /残缺|limitations|旁车/);
    const diagnostics = diagnosticLines.filter((line) => line.includes("[analysis-diagnostics]"));
    assert.equal(diagnostics.length, 1);
    assert.match(diagnostics[0], /run=9/);
    assert.match(diagnostics[0], /session=61/);
    assert.match(diagnostics[0], /telemetry_alignment_missing/);
  } finally {
    console.error = originalConsoleError;
    await closeServer(server);
  }
});

// [fix 2026-10-04] D：done 分析残缺为空 ⇒ 不提供 force 入口，维持现状文案。
test("analysis.create_from_run keeps the plain done result when the reused analysis has no limitations", async () => {
  writeSession(62, {
    id: 62,
    status: "done",
    kovaak_run_id: 9,
    result: { deterministic: { limitations: [] } },
  });
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/9/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 62 }));
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/62",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(62);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:9" }, "owner-a", "idem-key-plain",
    );
    assert.equal(result.status, "succeeded");
    assert.deepEqual(result.result, {
      session_id: 62,
      analysis_ref: "analysis:62",
      status: "done",
    });
  } finally {
    await closeServer(server);
  }
});

// [fix 2026-10-04] D：新建 session（非复用）时不出现重跑入口——即便该 run
// 另有带残缺的旧 done 分析。
test("analysis.create_from_run omits the rerun entry when a new session is created", async () => {
  writeSession(63, {
    id: 63,
    status: "done",
    kovaak_run_id: 9,
    result: { deterministic: { limitations: ["visual_artifact_commit_failed"] } },
  });
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/9/analyze",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: 64 }));   // 全新 session
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/64",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(64);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run", { run_ref: "run:9" }, "owner-a", "idem-key-new",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result?.rerun_available, undefined);
    assert.deepEqual(result.result, {
      session_id: 64,
      analysis_ref: "analysis:64",
      status: "done",
    });
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

// ── [2026-10-04] Coach 判断制 ──────────────────────────────────────────────

const {
  executeNativeScenarioEvidence,
  isNativeScenarioEvidenceCommand,
} = await import("../src/python-analysis.ts");

// 第二段：显式家族判断与依据原样转发到 Python 创建接口。
test("analysis.create_from_run forwards aim_family and classification_basis", async () => {
  const bodies: unknown[] = [];
  const server = await startMockServer([
    {
      match: (method, url) => method === "POST" && url === "/api/kovaak-runs/7/analyze",
      handler: (req, res) => {
        let raw = "";
        req.on("data", (chunk: Buffer) => (raw += chunk.toString("utf8")));
        req.on("end", () => {
          bodies.push(raw ? JSON.parse(raw) : {});
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ session_id: 71 }));
        });
      },
    },
    {
      match: (method, url) => method === "GET" && url === "/api/sessions/71",
      handler: (_req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "done" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  writeOverview(71);
  try {
    const result = await executeNativePythonAnalysis(
      "analysis.create_from_run",
      {
        run_ref: "run:7",
        aim_family: "continuous_tracking",
        classification_basis: "hold_frac 0.98 且官方杀率 1.41/s",
      },
      "owner-a", "idem-key-aim",
    );
    assert.equal(result.status, "succeeded");
    assert.deepEqual(bodies, [{
      aim_family: "continuous_tracking",
      classification_basis: "hold_frac 0.98 且官方杀率 1.41/s",
    }]);
  } finally {
    await closeServer(server);
  }
});

// 第一段：只读证据包命令（GET，不入队不开跑）。
test("analysis.scenario_evidence fetches the read-only evidence bundle", async () => {
  let evidenceRequested = false;
  const server = await startMockServer([
    {
      match: (method, url) =>
        method === "GET" && url === "/api/kovaak-runs/7/scenario-evidence",
      handler: (_req, res) => {
        evidenceRequested = true;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          schema_version: "scenario_evidence.v1",
          run_ref: "run:7",
          name_evidence: { matched_keywords: ["track"], candidate_family: "continuous_tracking" },
          telemetry_evidence: null,
          shape_evidence: null,
          memory: null,
          current_resolution: { classification_source: "name_heuristic" },
          field_legend: { how_to_judge: "..." },
        }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  try {
    assert.equal(isNativeScenarioEvidenceCommand("analysis.scenario_evidence"), true);
    assert.equal(isNativeScenarioEvidenceCommand("analysis.create_from_run"), false);
    const result = await executeNativeScenarioEvidence(
      "analysis.scenario_evidence", { run_ref: "run:7" }, "owner-a",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(result.result_ref, "run:7");
    assert.equal(result.result?.schema_version, "scenario_evidence.v1");
    assert.ok(evidenceRequested);
  } finally {
    await closeServer(server);
  }
});

test("analysis.scenario_evidence surfaces backend failures", async () => {
  const server = await startMockServer([
    {
      match: (method, url) => url === "/api/kovaak-runs/7/scenario-evidence",
      handler: (_req, res) => {
        res.writeHead(409, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ detail: "source_unavailable: stats identity missing" }));
      },
    },
  ]);
  writeConfig(serverBaseUrl(server));
  try {
    const result = await executeNativeScenarioEvidence(
      "analysis.scenario_evidence", { run_ref: "run:7" }, "owner-a",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "scenario_evidence_failed");
    assert.match(result.warning_or_error?.message ?? "", /source_unavailable/);
  } finally {
    await closeServer(server);
  }
});
