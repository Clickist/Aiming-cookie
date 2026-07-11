import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { isRecord, isSpikeErrorV1, makeSpikeError, type SpikeErrorV1 } from "./contracts.ts";

const PROTOCOL = "analysis_tool_stdio.v0";
const SPIKE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_ADAPTER_PATH = resolve(SPIKE_ROOT, "python/analysis_adapter.py");

export type AnalysisSummary = {
  analysis_id: "analysis-fixture-1";
  schema_version: "analysis_result.v1";
  summary_type: "flicking";
  diagnosis: { summary: { fixture_signal: "stable" } };
  notes: ["fixture-only"];
};

export type PythonAnalysisClient = {
  getAnalysisSummary(request: {
    requestId: string;
    analysisId: string;
    signal?: AbortSignal;
    onProgress?: (progress: { stage: string; message: string }) => void;
  }): Promise<AnalysisSummary>;
};

type PythonAnalysisClientOptions = {
  executable?: string;
  adapterPath?: string;
};

function adapterFailure(): SpikeErrorV1 {
  return makeSpikeError({
    category: "local_cv_runtime",
    code: "analysis_adapter_failed",
    message: "Analysis adapter failed",
    retryable: false,
    trace_id: null,
    details: null,
  });
}

function isAnalysisSummary(value: unknown): value is AnalysisSummary {
  return (
    isRecord(value) &&
    value.analysis_id === "analysis-fixture-1" &&
    value.schema_version === "analysis_result.v1" &&
    value.summary_type === "flicking" &&
    isRecord(value.diagnosis) &&
    isRecord(value.diagnosis.summary) &&
    value.diagnosis.summary.fixture_signal === "stable" &&
    Array.isArray(value.notes) &&
    value.notes.length === 1 &&
    value.notes[0] === "fixture-only"
  );
}

export function createPythonAnalysisClient(options: PythonAnalysisClientOptions = {}): PythonAnalysisClient {
  const executable = options.executable ?? "python3";
  const adapterPath = options.adapterPath ?? DEFAULT_ADAPTER_PATH;

  return {
    async getAnalysisSummary({ requestId, analysisId, signal, onProgress }) {
      return new Promise<AnalysisSummary>((resolvePromise, reject) => {
        const child = spawn(executable, [adapterPath], {
          cwd: SPIKE_ROOT,
          stdio: ["pipe", "pipe", "pipe"],
        });
        let stdout = "";
        let terminal: { kind: "result"; summary: AnalysisSummary } | { kind: "error"; error: SpikeErrorV1 } | null = null;
        let protocolFailed = false;
        let aborted = false;
        let settled = false;

        const fail = () => {
          if (settled) return;
          settled = true;
          reject(adapterFailure());
        };
        const removeAbortListener = () => signal?.removeEventListener("abort", abortChild);
        const finish = () => {
          if (settled) return;
          removeAbortListener();
          if (aborted || protocolFailed || terminal === null) {
            fail();
            return;
          }
          if (terminal.kind === "error") {
            settled = true;
            reject(terminal.error);
            return;
          }
          settled = true;
          resolvePromise(terminal.summary);
        };
        const parseLine = (line: string) => {
          if (!line || protocolFailed) return;
          let entry: unknown;
          try {
            entry = JSON.parse(line);
          } catch {
            protocolFailed = true;
            return;
          }
          if (!isRecord(entry) || entry.protocol !== PROTOCOL || entry.request_id !== requestId || typeof entry.type !== "string") {
            protocolFailed = true;
            return;
          }
          if (entry.type === "progress") {
            if (terminal !== null || typeof entry.stage !== "string" || typeof entry.message !== "string") {
              protocolFailed = true;
              return;
            }
            onProgress?.({ stage: entry.stage, message: entry.message });
            return;
          }
          if (entry.type === "result") {
            if (terminal !== null || !isAnalysisSummary(entry.summary)) {
              protocolFailed = true;
              return;
            }
            terminal = { kind: "result", summary: entry.summary };
            return;
          }
          if (entry.type === "error") {
            if (terminal !== null || !isSpikeErrorV1(entry.error)) {
              protocolFailed = true;
              return;
            }
            terminal = { kind: "error", error: entry.error };
            return;
          }
          protocolFailed = true;
        };
        function abortChild() {
          if (aborted || settled) return;
          aborted = true;
          child.kill("SIGTERM");
        }

        if (signal?.aborted) abortChild();
        else signal?.addEventListener("abort", abortChild, { once: true });

        child.stdout.setEncoding("utf8");
        child.stdout.on("data", (chunk: string) => {
          stdout += chunk;
          const lines = stdout.split("\n");
          stdout = lines.pop() ?? "";
          for (const line of lines) parseLine(line.endsWith("\r") ? line.slice(0, -1) : line);
        });
        child.stderr.resume();
        child.on("error", () => {
          protocolFailed = true;
        });
        child.on("close", (code) => {
          if (stdout.length > 0) parseLine(stdout.endsWith("\r") ? stdout.slice(0, -1) : stdout);
          if (code !== 0) protocolFailed = true;
          finish();
        });

        child.stdin.end(`${JSON.stringify({
          protocol: PROTOCOL,
          request_id: requestId,
          operation: "get_analysis_summary",
          analysis_id: analysisId,
        })}\n`);
      });
    },
  };
}
