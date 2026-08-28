import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import {
  ANALYSIS_AUTO_TEACH_KEY,
  buildAnalysisAutoTeachContent,
  markAnalysisAutoTaught,
  readAutoTaughtAnalyses,
} from "../lib/contracts";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("auto-teach content references the analysis and asks for problems and direction", () => {
  assert.equal(
    buildAnalysisAutoTeachContent("analysis:12"),
    "请结合这份分析：analysis:12，讲讲主要问题和改进方向。",
  );
});

test("auto-teach dedup markers round-trip per analysis and survive malformed storage", () => {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: () => null,
    removeItem: (key: string) => { store.delete(key); },
    setItem: (key: string, value: string) => { store.set(key, value); },
  };

  assert.equal(readAutoTaughtAnalyses(storage).size, 0);
  markAnalysisAutoTaught(storage, "analysis:7");
  markAnalysisAutoTaught(storage, "analysis:8");
  markAnalysisAutoTaught(storage, "analysis:7");
  assert.deepEqual([...readAutoTaughtAnalyses(storage)].sort(), ["analysis:7", "analysis:8"]);
  assert.equal(readAutoTaughtAnalyses(null).size, 0);

  storage.setItem(ANALYSIS_AUTO_TEACH_KEY, "{not json");
  assert.equal(readAutoTaughtAnalyses(storage).size, 0);
  markAnalysisAutoTaught(undefined, "analysis:9");
  assert.equal(readAutoTaughtAnalyses(storage).size, 0);
});

test("AnalysisWorkspace dispatches auto-teach only on a live transition to done", async () => {
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  assert.match(workspace, /new CustomEvent\(ANALYSIS_AUTO_TEACH_EVENT/);
  assert.match(workspace, /previous\.id === analysisId/);
  assert.match(workspace, /previous\.status !== null && previous\.status !== "done"/);
});

test("AppShell auto-teaches once per analysis when the Provider is ready", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(shell, /addEventListener\(ANALYSIS_AUTO_TEACH_EVENT/);
  assert.match(shell, /if \(capability !== "ready"\) return;/);
  assert.match(shell, /seen\.has\(analysisRef\)/);
  assert.match(shell, /markAnalysisAutoTaught\(window\.localStorage, analysisRef\)/);
  // 开讲并入当前选中会话：有选中会话时带 session_id，不再每次新建会话。
  assert.match(shell, /createCoachAgentRun\(\s*buildAnalysisAutoTeachContent\(analysisRef\)/);
  assert.match(shell, /sessionId: selectedCoachSessionId/);
  assert.match(shell, /softStartRun=\{softStartRun\}/);
  // CoachPanel 承接开讲 run（已有 softStartRun 合同），不自动发送用户文案。
  assert.match(panel, /softStartRun/);
  assert.match(panel, /appliedSoftStartRef/);
});

test("auto-teach yields while a Coach run is active (analysis is narrated by that turn)", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const panel = await source("components/task6/CoachPanel.tsx");
  // 活跃回合期间跳过开讲且不打去重标记（重试等无回合场景仍可开讲）。
  assert.match(shell, /if \(activeCoachRunRef\.current\) return;/);
  assert.match(shell, /activeCoachRunRef\.current = active;/);
  assert.match(shell, /onActiveRunChange=\{handleCoachActiveRunChange\}/);
  // CoachPanel 以 run 状态驱动活跃上报。
  assert.match(
    panel,
    /onActiveRunChange\?\.\(run !== null && \["queued", "running"\]\.includes\(run\.status\)\)/,
  );
});
