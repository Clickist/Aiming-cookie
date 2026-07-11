import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createPythonAnalysisClient } from "../src/python-analysis-client.ts";

function assertSpikeError(error: unknown, code: string) {
  assert.equal(typeof error, "object");
  assert.ok(error);
  assert.equal((error as { schema_version?: unknown }).schema_version, "error.v1");
  assert.equal((error as { category?: unknown }).category, "local_cv_runtime");
  assert.equal((error as { code?: unknown }).code, code);
  assert.equal((error as { details?: unknown }).details, null);
  return true;
}

async function withPythonScript(
  source: string,
  run: (adapterPath: string) => Promise<void>,
) {
  const directory = await mkdtemp(join(tmpdir(), "aiming-cookie-python-adapter-"));
  const adapterPath = join(directory, "adapter.py");
  await writeFile(adapterPath, source, "utf8");
  try {
    await run(adapterPath);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("Python adapter returns one progress event and deterministic analysis_result.v1 summary", async () => {
  const client = createPythonAnalysisClient();
  const progress: Array<{ stage: string; message: string }> = [];
  const summary = await client.getAnalysisSummary({
    requestId: "req-1",
    analysisId: "analysis-fixture-1",
    onProgress: (entry) => progress.push(entry),
  });

  assert.deepEqual(progress, [{ stage: "loading_fixture", message: "Loading analysis fixture" }]);
  assert.deepEqual(summary, {
    analysis_id: "analysis-fixture-1",
    schema_version: "analysis_result.v1",
    summary_type: "flicking",
    diagnosis: { summary: { fixture_signal: "stable" } },
    notes: ["fixture-only"],
  });
});

test("Python adapter returns analysis_not_found for an unknown fixture id", async () => {
  const client = createPythonAnalysisClient();
  await assert.rejects(
    client.getAnalysisSummary({ requestId: "req-unknown", analysisId: "unknown-fixture" }),
    (error) => assertSpikeError(error, "analysis_not_found"),
  );
});

test("Node client maps malformed stdout and nonzero exit to analysis_adapter_failed", async () => {
  await withPythonScript(
    'import sys\nsys.stdin.readline()\nsys.stdout.write("not-json\\n")\nsys.stdout.flush()\n',
    async (adapterPath) => {
      const client = createPythonAnalysisClient({ adapterPath });
      await assert.rejects(
        client.getAnalysisSummary({ requestId: "req-malformed", analysisId: "analysis-fixture-1" }),
        (error) => assertSpikeError(error, "analysis_adapter_failed"),
      );
    },
  );

  await withPythonScript(
    'import sys\nsys.stdin.readline()\nsys.exit(9)\n',
    async (adapterPath) => {
      const client = createPythonAnalysisClient({ adapterPath });
      await assert.rejects(
        client.getAnalysisSummary({ requestId: "req-nonzero", analysisId: "analysis-fixture-1" }),
        (error) => assertSpikeError(error, "analysis_adapter_failed"),
      );
    },
  );
});

test("Node client terminates the child when AbortSignal is aborted", async () => {
  await withPythonScript(
    [
      "import signal",
      "import sys",
      "sys.stdin.readline()",
      "signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))",
      "signal.pause()",
      "",
    ].join("\n"),
    async (adapterPath) => {
      const controller = new AbortController();
      const client = createPythonAnalysisClient({ adapterPath });
      const result = client.getAnalysisSummary({
        requestId: "req-abort",
        analysisId: "analysis-fixture-1",
        signal: controller.signal,
      });
      controller.abort();
      await assert.rejects(result, (error) => assertSpikeError(error, "analysis_adapter_failed"));
    },
  );
});
