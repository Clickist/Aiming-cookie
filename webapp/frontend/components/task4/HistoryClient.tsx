"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  attachCoachContext,
  getHistoryAnalysisDetail,
  getHistoryRun,
  getHistorySessions,
  listKovaakRuns,
} from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import {
  buildHistorySections,
  getHistoryStatusText,
  presentRecordLabel,
} from "@/lib/contracts";
import type { KovaaKRunItem, KovaaKRunListItem, SessionListItem, SessionStatus } from "@/lib/types";
import { Button, Dialog, Drawer, Empty, ErrorState, IconButton, Loading, Notice, Status } from "@/ui/primitives";

import { RunInspector } from "./RunInspector";

type RefreshState = "idle" | "loading" | "unavailable";
type RunDiscoveryState = "loading" | "available" | "browser_unavailable" | "service_unavailable";

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

function formatHistoryDate(iso: string | null | undefined): string {
  if (!iso) return "时间未知";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const isSameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (isSameDay(date, now)) return `今天 ${time}`;
  if (isSameDay(date, yesterday)) return `昨天 ${time}`;
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}月${day}日 ${time}`;
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
  onInspect,
  onCoachAnalysis,
  onToggle,
  selected,
}: {
  run: KovaaKRunListItem;
  onInspect: (run: KovaaKRunListItem) => void;
  onCoachAnalysis: (run: KovaaKRunListItem) => void;
  onToggle: (run: KovaaKRunListItem, selected: boolean) => void;
  selected: boolean;
}) {
  const isPending = run.readiness_state === "pending_analysis";
  const canSelect = run.readiness_state !== "incomplete_evidence";
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
      <label className="task4-row-select">
        <input
          aria-label={`选择训练 ${run.scenario ?? run.run_ref}`}
          checked={selected}
          disabled={!canSelect}
          onChange={(event) => onToggle(run, event.target.checked)}
          type="checkbox"
        />
      </label>
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
      <div className="task4-row-actions">
        {isPending ? (
          <Button onClick={() => onCoachAnalysis(run)} size="compact" variant="secondary">让 Coach 分析</Button>
        ) : (
          <Button onClick={() => onInspect(run)} size="compact" variant="ghost">查看 Run</Button>
        )}
      </div>
    </div>
  );
}

function AnalysisRow({
  onLoadDetail,
  onToggle,
  selected,
  session,
}: {
  session: SessionListItem;
  onLoadDetail: (id: number) => void;
  onToggle: (analysisRef: string, selected: boolean) => void;
  selected: boolean;
}) {
  const tone = sessionTone(session.status);
  const canAttach = session.status === "done";
  const recordLabel = presentRecordLabel({
    scenario: session.scenario,
    trainingAt: session.training_at,
    analysisCompletedAt: session.analysis_completed_at ?? session.finished_at,
  });
  return (
    <div className="task4-rowline">
      <label className="task4-row-select">
        <input
          aria-label={`选择分析 ${recordLabel}`}
          checked={selected}
          disabled={!canAttach}
          onChange={(event) => onToggle(session.analysis_ref, event.target.checked)}
          type="checkbox"
        />
      </label>
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
      <div className="task4-row-actions">
        <Button onClick={() => onLoadDetail(session.id)} size="compact" variant="secondary">查看摘要</Button>
      </div>
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
  const [selectedRun, setSelectedRun] = useState<KovaaKRunItem | KovaaKRunListItem | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SessionStatus | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [selectedAnalysisRefs, setSelectedAnalysisRefs] = useState<Set<string>>(() => new Set());
  const [selectedRunIds, setSelectedRunIds] = useState<Set<number>>(() => new Set());
  const [attachState, setAttachState] = useState<"idle" | "attaching">("idle");
  const [attachError, setAttachError] = useState<string | null>(null);

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

  const toggleAnalysisSelection = (analysisRef: string, selected: boolean) => {
    setSelectedAnalysisRefs((current) => {
      const next = new Set(current);
      if (selected) next.add(analysisRef);
      else next.delete(analysisRef);
      return next;
    });
    setAttachError(null);
  };

  const attachSelectedAnalyses = async () => {
    const analysisRefs = Array.from(selectedAnalysisRefs);
    if (!analysisRefs.length || attachState === "attaching") return;
    setAttachState("attaching");
    setAttachError(null);
    const results = await Promise.allSettled(
      analysisRefs.map((analysisRef) => attachCoachContext({ kind: "analysis", analysis_ref: analysisRef })),
    );
    const failed = results.filter((result) => result.status === "rejected").length;
    if (failed > 0) {
      setAttachState("idle");
      setAttachError(`${failed} 条分析未能引用，请重试。`);
      return;
    }
    setSelectedAnalysisRefs(new Set());
    setAttachState("idle");
    router.push("/");
  };

  const askCoachToAnalyze = (run: KovaaKRunListItem) => {
    const record = presentRecordLabel({
      scenario: run.scenario,
      trainingAt: run.created_at,
      analysisCompletedAt: null,
    });
    window.sessionStorage.setItem(
      "aiming-cookie.ui.coach-pending-intent",
      JSON.stringify({ draft: `请分析这次训练：${record}` }),
    );
    router.push("/");
  };

  const toggleRunSelection = (run: KovaaKRunListItem, selected: boolean) => {
    setSelectedRunIds((current) => {
      const next = new Set(current);
      if (selected) next.add(run.id);
      else next.delete(run.id);
      return next;
    });
  };

  const sendSelectedRunsToCoach = () => {
    const selected = runs.filter((run) => selectedRunIds.has(run.id));
    if (!selected.length) return;
    const analysisRefsByRun = new Map<number, string[]>();
    const analysisStatusByRun = new Map<number, "done" | "active" | "pending">();
    for (const session of sessions) {
      if (session.kovaak_run_id === null) continue;
      if (session.status === "done") {
        const refs = analysisRefsByRun.get(session.kovaak_run_id) ?? [];
        refs.push(session.analysis_ref);
        analysisRefsByRun.set(session.kovaak_run_id, refs);
        analysisStatusByRun.set(session.kovaak_run_id, "done");
      } else if (
        (session.status === "queued" || session.status === "running")
        && analysisStatusByRun.get(session.kovaak_run_id) !== "done"
      ) {
        analysisStatusByRun.set(session.kovaak_run_id, "active");
      }
    }
    window.sessionStorage.setItem(
      "aiming-cookie.ui.coach-pending-intent",
      JSON.stringify({
        kind: "batch-analysis",
        batch_ref: `analysis-batch:${Date.now()}`,
        runs: selected.map((run) => ({
          id: run.id,
          run_ref: run.run_ref,
          scenario: run.scenario,
          created_at: run.created_at,
          readiness_state: run.readiness_state,
          limitations: run.limitations,
          analysis_refs: analysisRefsByRun.get(run.id) ?? [],
          analysis_status: analysisStatusByRun.get(run.id) ?? "pending",
        })),
      }),
    );
    setSelectedRunIds(new Set());
    router.push("/");
  };

  const inspect = async (run: KovaaKRunListItem) => {
    setSelectedRun(run);
    if (!run.id) return;
    const detail = await getHistoryRun(run.id).catch(() => null);
    if (detail) setSelectedRun(detail);
  };

  const loadDetail = async (id: number) => {
    setDetailId(id);
    setDetail(null);
    setDetailLoading(true);
    setDetailError(false);
    const result = await getHistoryAnalysisDetail(id).catch(() => null);
    if (!result) setDetailError(true);
    else setDetail(result);
    setDetailLoading(false);
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
          <Button
            disabled={selectedRunIds.size === 0}
            onClick={sendSelectedRunsToCoach}
            size="compact"
            variant="primary"
          >
            交给 Coach（{selectedRunIds.size}）
          </Button>
          <Button
            disabled={selectedAnalysisRefs.size === 0 || attachState === "attaching"}
            onClick={() => void attachSelectedAnalyses()}
            size="compact"
            variant="primary"
          >
            {attachState === "attaching" ? "正在引用…" : `引用所选分析（${selectedAnalysisRefs.size}）`}
          </Button>
          <Button onClick={() => void loadHistory()} size="compact" variant="ghost">刷新</Button>
        </div>
      </div>

      {attachError ? <Notice tone="warning" title="分析引用失败">{attachError}</Notice> : null}
      {refresh === "unavailable" ? <Notice tone="warning" title="刷新暂时不可用">保留当前已读取内容；恢复本地服务后可以重试。</Notice> : null}
      {runDiscovery === "browser_unavailable" ? <Notice tone="info" title="Run 发现仅在桌面应用可用">浏览器可以查看分析记录；要查看自动采集的 Run，请在桌面应用中打开 History。</Notice> : null}
      {runDiscovery === "service_unavailable" ? <Notice tone="warning" title="Run 暂时不可用">桌面服务没有返回训练 Run；这不是“没有记录”。恢复服务后可以刷新。</Notice> : null}

      <section className="task4-sec" aria-labelledby="pending-title">
        <div className="task4-sec-head">
          <h2 id="pending-title" className="task4-sec-title">待分析训练</h2>
          <span className="task4-sec-count">{sections.pendingRuns.length}</span>
        </div>
        {sections.pendingRuns.length === 0 ? (
          <RunSectionState kind="pending" runDiscovery={runDiscovery} />
        ) : (
          <div className="task4-panel">
            {sections.pendingRuns.map((run) => (
              <RunRow
                key={run.run_ref}
                onCoachAnalysis={askCoachToAnalyze}
                onInspect={(item) => void inspect(item)}
                onToggle={toggleRunSelection}
                run={run}
                selected={selectedRunIds.has(run.id)}
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
                key={run.run_ref}
                onCoachAnalysis={askCoachToAnalyze}
                onInspect={(item) => void inspect(item)}
                onToggle={toggleRunSelection}
                run={run}
                selected={selectedRunIds.has(run.id)}
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
                key={session.analysis_ref}
                onLoadDetail={loadDetail}
                onToggle={toggleAnalysisSelection}
                selected={selectedAnalysisRefs.has(session.analysis_ref)}
                session={session}
              />
            ))}
          </div>
        )}
      </section>

      <Drawer
        onClose={() => setSelectedRun(null)}
        open={Boolean(selectedRun)}
        title={selectedRun ? `训练记录详情 · ${selectedRun.scenario ?? "未知场景"}` : "训练记录详情"}
      >
        {selectedRun ? <RunInspector run={selectedRun} /> : null}
      </Drawer>

      <Dialog onClose={() => setDetailId(null)} open={detailId !== null} title="分析摘要">
        {detailId !== null ? (
          detailLoading ? (
            <Loading>正在按需加载摘要</Loading>
          ) : detailError ? (
            <ErrorState title="分析详情暂时不可用">
              <p>原列表仍保留，稍后可以重试。</p>
              <Button onClick={() => void loadDetail(detailId)} variant="secondary">重试</Button>
            </ErrorState>
          ) : detail ? (
            <div className="task4-detail-summary">
              <Status tone={sessionTone(detail.status)}>{sessionStatus(detail.status)}</Status>
              <p>完整 Diagnosis、Video、Data 由 Analysis workspace 负责。</p>
              {detail.history ? (
                <dl className="task4-facts">
                  <div><dt>场景</dt><dd>{detail.history.scenario ?? "未知场景"}</dd></div>
                  <div><dt>来源</dt><dd>{Object.values(detail.history.source_availability).some((value) => value !== "available") ? "部分可用" : "可用"}</dd></div>
                  <div><dt>视频回放</dt><dd>{detail.history.visual_replay.seekable ? "可用" : "不可用"}</dd></div>
                </dl>
              ) : <p className="task4-muted">当前没有可用的安全摘要投影。</p>}
            </div>
          ) : null
        ) : null}
      </Dialog>
    </div>
  );
}
