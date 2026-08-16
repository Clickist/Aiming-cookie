"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getHistorySessions, listKovaakRuns } from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import {
  buildCoachAnalysisDraft,
  buildHistorySections,
  COACH_PENDING_INTENT_KEY,
  formatHistoryDate,
  getHistoryStatusText,
  presentRecordLabel,
} from "@/lib/contracts";
import type { KovaaKRunListItem, SessionListItem } from "@/lib/types";
import { Button, Empty, ErrorState, IconButton, Notice } from "@/ui/primitives";

type RefreshState = "idle" | "loading" | "unavailable";
type RunDiscoveryState = "loading" | "available" | "browser_unavailable" | "service_unavailable";

/** 「让 Coach 分析」一次最多引用的训练条数（分析逐条串行进行）。 */
const MAX_SELECTED_RUNS = 5;

function sessionTone(status: string): "neutral" | "info" | "success" | "warning" | "error" {
  if (status === "done") return "success";
  if (status === "failed") return "error";
  if (status === "running") return "info";
  return "neutral";
}

function sessionStatus(status: string): string {
  return {
    queued: "排队中",
    running: "分析中",
    done: "已完成",
    failed: "失败",
  }[status] ?? getHistoryStatusText(status);
}

function inputModeLabel(mode: string): string {
  return {
    input_native: "输入原生",
    multimodal: "多源模式",
    video_fallback: "视频兼容",
  }[mode] ?? mode;
}

function sourceState(run: KovaaKRunListItem, key: string): string | undefined {
  return run.evidence_availability[key] ?? run.source_availability[key];
}

function historyEvidenceState(state: string | undefined): string | undefined {
  if (!state) return undefined;
  return ["available", "attached", "partial", "missing", "unavailable", "not_present", "unsupported", "aligned", "failed"].includes(state)
    ? state
    : getHistoryStatusText(state);
}

function EvidenceChip({ label, state }: { label: string; state: string | undefined }) {
  const normalized = state ?? "missing";
  let chipState: "ok" | "part" | "miss" | "bad" = "miss";
  if (["available", "attached", "aligned"].includes(normalized)) chipState = "ok";
  else if (normalized === "partial") chipState = "part";
  else if (normalized === "failed") chipState = "bad";
  const icon = chipState === "ok" ? "✓" : chipState === "part" ? "◐" : chipState === "bad" ? "✕" : "−";
  return (
    <span className="task4-ev" data-state={chipState}>
      <i aria-hidden="true">{icon}</i>
      <span>{label}</span>
    </span>
  );
}

function runRecordBadge(run: KovaaKRunListItem) {
  if (run.analysis_count > 0 || run.readiness_state === "analyzed") {
    return <span className="task4-badge task4-badge-neu">已分析</span>;
  }
  if (run.finalization_state === "source_unavailable" || run.finalization_state === "unavailable") {
    return <span className="task4-badge task4-badge-warn">来源不可用</span>;
  }
  if (run.limitations.length > 0) {
    return <span className="task4-badge task4-badge-err">证据不完整</span>;
  }
  return null;
}

function RunRow({
  run,
  disabled,
  onToggle,
  selected,
}: {
  run: KovaaKRunListItem;
  disabled?: boolean;
  onToggle?: (run: KovaaKRunListItem) => void;
  selected?: boolean;
}) {
  const isPending = run.readiness_state === "pending_analysis";
  const evidence = [
    { label: "Stats", state: historyEvidenceState(sourceState(run, "stats")) },
    { label: "Performance", state: historyEvidenceState(sourceState(run, "performance")) },
    { label: "Raw", state: historyEvidenceState(sourceState(run, "raw") ?? run.trace_quality.availability) },
    { label: "视频", state: historyEvidenceState(sourceState(run, "mp4") ?? sourceState(run, "video")) },
  ];
  return (
    <div
      className="task4-rowline"
    >
      <div className="task4-row-main">
        <div className="task4-row-title">
          <span className="task4-name">{presentRecordLabel({
            scenario: run.scenario,
            trainingAt: run.created_at,
            analysisCompletedAt: null,
          })}</span>
          {!isPending ? runRecordBadge(run) : null}
        </div>
        <div className="task4-row-sub">
          <span>训练时间：{formatHistoryDate(run.created_at)}</span>
          {!isPending && run.limitations.length > 0 ? (
            <span>{run.limitations.map((limitation) => getHistoryStatusText(limitation)).join("；")}</span>
          ) : null}
        </div>
        <div className="task4-row-sub task4-ev-row">
          {evidence.map(({ label, state }) => (
            <EvidenceChip key={label} label={label} state={state} />
          ))}
        </div>
      </div>
      {onToggle ? (
        <div className="task4-row-actions">
          <input
            aria-label={`选择 ${run.scenario ?? "未知场景"}（${formatHistoryDate(run.created_at)}）`}
            checked={Boolean(selected)}
            disabled={disabled}
            onChange={() => onToggle(run)}
            title={disabled ? "证据不足以分析" : undefined}
            type="checkbox"
          />
        </div>
      ) : null}
    </div>
  );
}

function AnalysisRow({
  disabled,
  onToggle,
  selected,
  session,
}: {
  disabled?: boolean;
  onToggle?: (session: SessionListItem) => void;
  selected?: boolean;
  session: SessionListItem;
}) {
  const tone = sessionTone(session.status);
  const recordLabel = presentRecordLabel({
    scenario: session.scenario,
    trainingAt: session.training_at,
    analysisCompletedAt: session.analysis_completed_at ?? session.finished_at,
  });
  return (
    <div className="task4-rowline">
      <div className="task4-row-main">
        <div className="task4-row-title">
          <span className="task4-name">{recordLabel}</span>
          <span className={`task4-badge task4-badge-${tone === "success" ? "ok" : tone === "error" ? "err" : "neu"}`}>
            {tone === "success" ? <span className="task4-badge-dot" /> : null}
            {sessionStatus(session.status)}
          </span>
          <span className="task4-badge task4-badge-mode">{inputModeLabel(session.input_mode)}</span>
          {session.input_mode === "input_native" ? <span className="task4-badge task4-badge-preview">预览</span> : null}
        </div>
        <div className="task4-row-sub">
          <span>训练：{formatHistoryDate(session.training_at)}</span>
          <span>分析：{formatHistoryDate(session.analysis_completed_at ?? session.finished_at)}</span>
          <span>摘要：{session.summary_label ?? "暂无摘要"}</span>
        </div>
      </div>
      {onToggle ? (
        <div className="task4-row-actions">
          <input
            aria-label={`选择分析 ${session.scenario ?? "未知场景"}（${formatHistoryDate(session.training_at ?? session.created_at)}）`}
            checked={Boolean(selected)}
            disabled={disabled}
            onChange={() => onToggle(session)}
            title={disabled ? "分析未完成，暂无结果可讨论" : undefined}
            type="checkbox"
          />
        </div>
      ) : null}
    </div>
  );
}

function RunSectionState({
  kind,
  runDiscovery,
}: {
  kind: "pending" | "records";
  runDiscovery: RunDiscoveryState;
}) {
  const pending = kind === "pending";
  if (runDiscovery === "browser_unavailable" || runDiscovery === "service_unavailable") {
    const browserUnavailable = runDiscovery === "browser_unavailable";
    const title = pending
      ? browserUnavailable ? "当前无法发现待分析 Run" : "待分析 Run 暂时不可用"
      : browserUnavailable ? "当前无法发现训练 Run" : "训练 Run 暂时不可用";
    const detail = browserUnavailable
      ? pending
        ? "Run 发现需要桌面应用能力；这里不会把不可读取误报成没有记录。"
        : "Run 发现需要桌面应用能力；恢复后可以重新读取。"
      : "恢复桌面服务后可以重新读取。";
    return <Notice tone="warning" title={title}>{detail}</Notice>;
  }

  return (
    <Empty className="task4-panel task4-state-panel" title={runDiscovery === "loading" ? pending ? "正在读取待分析 Run" : "正在读取训练 Run" : pending ? "没有待确认训练" : "还没有其它训练记录"}>
      {pending ? "完成新的 Challenge 后，满足 readiness 的 Run 会出现在这里。" : "已确认或已分析的 Run 会保留在这里。"}
    </Empty>
  );
}

export function HistoryClient() {
  const router = useRouter();
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [refresh, setRefresh] = useState<RefreshState>("loading");
  const [runDiscovery, setRunDiscovery] = useState<RunDiscoveryState>("loading");
  const [initialError, setInitialError] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<number[]>([]);
  const [selectedAnalysisIds, setSelectedAnalysisIds] = useState<number[]>([]);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const selectedCount = selectedRunIds.length + selectedAnalysisIds.length;

  const loadHistory = useCallback(async (initial = false) => {
    setRefresh("loading");
    const canDiscoverRuns = isDesktopRuntime();
    setRunDiscovery(canDiscoverRuns ? "loading" : "browser_unavailable");
    const [runResult, sessionResult] = await Promise.allSettled([
      canDiscoverRuns ? listKovaakRuns() : Promise.reject(new Error("Run discovery is desktop-only")),
      getHistorySessions(),
    ]);
    setRunDiscovery(runResult.status === "fulfilled" ? "available" : canDiscoverRuns ? "service_unavailable" : "browser_unavailable");
    const anySuccess = runResult.status === "fulfilled" || sessionResult.status === "fulfilled";
    if (runResult.status === "fulfilled") setRuns(runResult.value.runs);
    if (sessionResult.status === "fulfilled") setSessions(sessionResult.value.sessions);
    if (!anySuccess) {
      setRefresh("unavailable");
      if (initial) setInitialError(true);
    } else {
      setRefresh("idle");
      setInitialError(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory(true);
  }, [loadHistory]);

  const sections = useMemo(() => buildHistorySections({ runs, sessions }), [runs, sessions]);

  const toggleRun = (run: KovaaKRunListItem) => {
    if (selectedRunIds.includes(run.id)) {
      setSelectionNotice(null);
      setSelectedRunIds(selectedRunIds.filter((id) => id !== run.id));
    } else if (selectedCount >= MAX_SELECTED_RUNS) {
      setSelectionNotice(`最多同时选 ${MAX_SELECTED_RUNS} 条一起交给 Coach。`);
    } else {
      setSelectionNotice(null);
      setSelectedRunIds([...selectedRunIds, run.id]);
    }
  };

  const toggleAnalysis = (session: SessionListItem) => {
    if (selectedAnalysisIds.includes(session.id)) {
      setSelectionNotice(null);
      setSelectedAnalysisIds(selectedAnalysisIds.filter((id) => id !== session.id));
    } else if (selectedCount >= MAX_SELECTED_RUNS) {
      setSelectionNotice(`最多同时选 ${MAX_SELECTED_RUNS} 条一起交给 Coach。`);
    } else {
      setSelectionNotice(null);
      setSelectedAnalysisIds([...selectedAnalysisIds, session.id]);
    }
  };

  // 「让 Coach 分析」：把勾选的训练拼成话术交给 Coach 输入框，用户发送后
  // 由 Coach 走 analysis.create_from_run 逐条触发（后端按 tier 自动降级）。
  // intent query 让 AppShell 在没有进行中的会话时用新草稿承接这个新意图。
  const startCoachAnalysis = () => {
    const draft = buildCoachAnalysisDraft({
      runs: runs.filter((run) => selectedRunIds.includes(run.id)),
      analyses: sessions
        .filter((session) => selectedAnalysisIds.includes(session.id))
        .map((session) => ({
          run_ref: session.analysis_ref,
          scenario: session.scenario ?? null,
          created_at: session.training_at ?? session.created_at ?? null,
        })),
    });
    if (!draft) return;
    window.sessionStorage.setItem(COACH_PENDING_INTENT_KEY, JSON.stringify({ draft }));
    router.push("/?intent=coach-analysis");
  };

  if (initialError && runs.length === 0 && sessions.length === 0) {
    return (
      <div className="task4-page">
        <div className="task4-page-head">
          <div>
            <div className="task4-page-title">历史</div>
            <div className="task4-page-sub">训练记录和分析记录会在这里分层显示。</div>
          </div>
        </div>
        <ErrorState title="历史暂时不可用">
          <p>读取失败没有被显示成没有记录。</p>
          <Button onClick={() => void loadHistory(true)} variant="secondary">重试</Button>
        </ErrorState>
      </div>
    );
  }

  return (
    <div className="task4-page">
      <div className="task4-page-head">
        <div className="task4-page-title-row">
          <IconButton label="返回 Coach" onClick={() => router.push("/")} size="compact" title="返回 Coach">←</IconButton>
          <div className="task4-page-title">历史</div>
        </div>
        <div className="task4-page-actions">
          {selectedCount > 0 ? (
            <Button onClick={startCoachAnalysis} size="compact" variant="primary">
              让 Coach 分析{selectedCount > 1 ? `（${selectedCount}）` : ""}
            </Button>
          ) : null}
          <Button onClick={() => void loadHistory()} size="compact" variant="ghost">刷新</Button>
        </div>
      </div>

      {refresh === "unavailable" ? <Notice tone="warning" title="刷新暂时不可用">保留当前已读取内容；恢复本地服务后可以重试。</Notice> : null}
      {runDiscovery === "browser_unavailable" ? <Notice tone="info" title="Run 发现仅在桌面应用可用">浏览器可以查看分析记录；要查看自动采集的 Run，请在桌面应用中打开 History。</Notice> : null}
      {runDiscovery === "service_unavailable" ? <Notice tone="warning" title="Run 暂时不可用">桌面服务没有返回训练 Run；这不是"没有记录"。恢复服务后可以刷新。</Notice> : null}

      <section className="task4-sec" aria-labelledby="pending-title">
        <div className="task4-sec-head">
          <h2 id="pending-title" className="task4-sec-title">待分析训练</h2>
          <span className="task4-sec-count">{sections.pendingRuns.length}</span>
        </div>
        {selectionNotice ? <Notice tone="info">{selectionNotice}</Notice> : null}
        {sections.pendingRuns.length === 0 ? (
          <RunSectionState kind="pending" runDiscovery={runDiscovery} />
        ) : (
          <div className="task4-panel">
            {sections.pendingRuns.map((run) => (
              <RunRow
                key={run.run_ref}
                onToggle={toggleRun}
                run={run}
                selected={selectedRunIds.includes(run.id)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="task4-sec" aria-labelledby="analysis-title">
        <div className="task4-sec-head">
          <h2 id="analysis-title" className="task4-sec-title">分析记录</h2>
          <span className="task4-sec-count">{sections.analysisRecords.length}</span>
        </div>
        {sections.analysisRecords.length === 0 ? (
          <Empty className="task4-panel task4-state-panel" title="还没有分析记录">
            完成一局 KovaaK 训练后，记录会保留在这里。
          </Empty>
        ) : (
          <div className="task4-panel">
            {sections.analysisRecords.map((session) => (
              <AnalysisRow
                disabled={session.status !== "done"}
                key={session.analysis_ref}
                onToggle={toggleAnalysis}
                selected={selectedAnalysisIds.includes(session.id)}
                session={session}
              />
            ))}
          </div>
        )}
      </section>

      <section className="task4-sec" aria-labelledby="runs-title">
        <div className="task4-sec-head">
          <h2 id="runs-title" className="task4-sec-title">训练记录</h2>
          <span className="task4-sec-count">{sections.runRecords.length}</span>
        </div>
        {sections.runRecords.length === 0 ? (
          <RunSectionState kind="records" runDiscovery={runDiscovery} />
        ) : (
          <div className="task4-panel">
            {sections.runRecords.map((run) => (
              <RunRow
                disabled={run.supported_input_modes.length === 0}
                key={run.run_ref}
                onToggle={toggleRun}
                run={run}
                selected={selectedRunIds.includes(run.id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
