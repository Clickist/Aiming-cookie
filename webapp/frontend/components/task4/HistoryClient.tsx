"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getHistoryAnalysisDetail,
  getHistoryRun,
  getHistorySessions,
  getHistoryTrend,
  listKovaakRuns,
} from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import {
  buildHistorySections,
  getHistoryStatusText,
  getTrendPresentation,
} from "@/lib/contracts";
import type { HistoryTrend, KovaaKRunItem, KovaaKRunListItem, SessionListItem, SessionStatus } from "@/lib/types";
import { Button, Empty, ErrorState, Loading, Notice, Status } from "@/ui/primitives";

import { EvidenceChip, PageHeading, PreviewBadge } from "@/components/task3/Task3Shared";
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

function sourceState(run: KovaaKRunListItem, key: string): string | undefined {
  return run.evidence_availability[key] ?? run.source_availability[key];
}

function historyEvidenceState(state: string | undefined): string | undefined {
  if (!state) return undefined;
  return ["available", "attached", "partial", "missing", "unavailable", "not_present", "unsupported", "aligned", "failed"].includes(state)
    ? state
    : getHistoryStatusText(state);
}

function RunRow({
  run,
  onInspect,
  onConfirm,
}: {
  run: KovaaKRunListItem;
  onInspect: (run: KovaaKRunListItem) => void;
  onConfirm: (run: KovaaKRunListItem) => void;
}) {
  return (
    <article className="task4-record-row">
      <div className="task4-record-main">
        <span className="task3-task-ref">{run.readiness_state === "pending_analysis" ? "待分析训练" : "训练记录"}</span>
        <h3>{run.scenario ?? "未知场景"}</h3>
        <p>{new Date(run.created_at).toLocaleString("zh-CN")}</p>
      </div>
      <div className="task4-record-evidence">
        <EvidenceChip label="Stats" state={historyEvidenceState(sourceState(run, "stats"))} />
        <EvidenceChip label="Performance" state={historyEvidenceState(sourceState(run, "performance"))} />
        <EvidenceChip label="Raw" state={historyEvidenceState(sourceState(run, "raw") ?? run.trace_quality.availability)} />
        <EvidenceChip label="视频" state={historyEvidenceState(sourceState(run, "mp4") ?? sourceState(run, "video"))} />
      </div>
      <div className="task4-record-quality">
        <Status tone={run.readiness_state === "pending_analysis" ? "warning" : "neutral"}>
          {run.readiness_state === "pending_analysis" ? "待确认" : getHistoryStatusText(run.finalization_state)}
        </Status>
        <span>覆盖 {run.trace_quality.coverage === null ? "未知" : `${Math.round(run.trace_quality.coverage * 100)}%`}</span>
        <span>{run.limitations.length ? getHistoryStatusText(run.limitations[0]) : "质量正常"}</span>
      </div>
      <div className="task4-record-actions">
        {run.readiness_state === "pending_analysis" ? <Button onClick={() => onConfirm(run)}>确认并分析</Button> : null}
        <Button onClick={() => onInspect(run)} variant="secondary">查看 Run</Button>
      </div>
    </article>
  );
}

function AnalysisRow({ session, onLoadDetail }: { session: SessionListItem; onLoadDetail: (id: number) => void }) {
  return (
    <article className="task4-record-row task4-analysis-row">
      <div className="task4-record-main">
        <span className="task3-task-ref">分析记录</span>
        <h3>{session.scenario ?? session.summary_label ?? "未命名分析"}</h3>
        <p>{new Date(session.created_at).toLocaleString("zh-CN")}</p>
      </div>
      <div className="task4-record-quality">
        {session.input_mode === "input_native" ? <PreviewBadge /> : null}
        <Status tone={sessionTone(session.status)}>{sessionStatus(session.status)}</Status>
        <span>{session.input_mode === "input_native" ? "Input-native" : session.input_mode === "multimodal" ? "Multimodal" : "Video fallback"}</span>
      </div>
      <div className="task4-record-actions">
        <Button onClick={() => onLoadDetail(session.id)} variant="secondary">查看摘要</Button>
        {session.status === "done" ? <Link className="ac-button" data-variant="primary" href={`/analysis/${session.id}`}>进入 Analysis</Link> : null}
      </div>
    </article>
  );
}

export function HistoryClient() {
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [refresh, setRefresh] = useState<RefreshState>("loading");
  const [runDiscovery, setRunDiscovery] = useState<RunDiscoveryState>("loading");
  const [initialError, setInitialError] = useState(false);
  const [selectedRun, setSelectedRun] = useState<KovaaKRunItem | KovaaKRunListItem | null>(null);
  const [selectedPendingRun, setSelectedPendingRun] = useState<KovaaKRunListItem | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SessionStatus | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [trend, setTrend] = useState<HistoryTrend | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);

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

  const loadTrend = async () => {
    setTrendLoading(true);
    setTrend(await getHistoryTrend("accuracy").catch(() => null));
    setTrendLoading(false);
  };

  if (initialError && runs.length === 0 && sessions.length === 0) {
    return (
      <div className="task3-page task4-history-page">
        <PageHeading description="训练记录和分析记录会在这里分层显示。" eyebrow="History" title="历史" />
        <ErrorState title="历史暂时不可用"><p>读取失败没有被显示成没有记录。</p><Button onClick={() => void loadHistory(true)} variant="secondary">重试</Button></ErrorState>
      </div>
    );
  }

  return (
    <div className="task3-page task4-history-page">
      <PageHeading
        actions={<Button onClick={() => void loadHistory()} variant="secondary">刷新</Button>}
        description="待分析训练、训练记录与分析记录分开管理；详细内容按需加载。"
        eyebrow="History"
        title="历史"
      />

      {refresh === "unavailable" ? <Notice tone="warning" title="刷新暂时不可用">保留当前已读取内容；恢复本地服务后可以重试。</Notice> : null}
      {runDiscovery === "browser_unavailable" ? <Notice tone="info" title="Run 发现仅在桌面应用可用">浏览器可以查看分析记录；要查看自动采集的 Run，请在桌面应用中打开 History。</Notice> : null}
      {runDiscovery === "service_unavailable" ? <Notice tone="warning" title="Run 暂时不可用">桌面服务没有返回训练 Run；这不是“没有记录”。恢复服务后可以刷新。</Notice> : null}

      <section className="task4-history-section" aria-labelledby="pending-title">
        <div className="task4-section-heading"><div><span className="task3-section-kicker">01 · Pending</span><h2 id="pending-title">待分析训练</h2></div><span>{sections.pendingRuns.length} 条</span></div>
        {sections.pendingRuns.length === 0 ? <Empty title={runDiscovery === "loading" ? "正在读取待分析 Run" : runDiscovery === "browser_unavailable" ? "当前无法发现待分析 Run" : runDiscovery === "service_unavailable" ? "待分析 Run 暂时不可用" : "没有待确认训练"}>{runDiscovery === "browser_unavailable" ? "Run 发现需要桌面应用能力；这里不会把不可读取误报成没有记录。" : runDiscovery === "service_unavailable" ? "恢复桌面服务后可以重新读取。" : "完成新的 Challenge 后，满足 readiness 的 Run 会出现在这里。"}</Empty> : (
          <div className="task4-record-list">{sections.pendingRuns.map((run) => <RunRow key={run.run_ref} onConfirm={setSelectedPendingRun} onInspect={(item) => void inspect(item)} run={run} />)}</div>
        )}
      </section>

      <section className="task4-history-section" aria-labelledby="runs-title">
        <div className="task4-section-heading"><div><span className="task3-section-kicker">02 · Runs</span><h2 id="runs-title">训练记录</h2></div><span>{sections.runRecords.length} 条</span></div>
        {sections.runRecords.length === 0 ? <Empty title={runDiscovery === "loading" ? "正在读取训练 Run" : runDiscovery === "browser_unavailable" ? "当前无法发现训练 Run" : runDiscovery === "service_unavailable" ? "训练 Run 暂时不可用" : "还没有其它训练记录"}>{runDiscovery === "browser_unavailable" ? "Run 发现需要桌面应用能力；恢复后可以重新读取。" : runDiscovery === "service_unavailable" ? "恢复桌面服务后可以重新读取。" : "已确认或已分析的 Run 会保留在这里。"}</Empty> : <div className="task4-record-list">{sections.runRecords.map((run) => <RunRow key={run.run_ref} onConfirm={setSelectedPendingRun} onInspect={(item) => void inspect(item)} run={run} />)}</div>}
      </section>

      <section className="task4-history-section" aria-labelledby="analysis-title">
        <div className="task4-section-heading"><div><span className="task3-section-kicker">03 · Analysis</span><h2 id="analysis-title">分析记录</h2></div><span>{sections.analysisRecords.length} 条</span></div>
        {sections.analysisRecords.length === 0 ? <Empty title="还没有分析记录"><Link className="ac-button" data-variant="primary" href="/analyze">新建分析</Link></Empty> : <div className="task4-record-list">{sections.analysisRecords.map((session) => <AnalysisRow key={session.analysis_ref} onLoadDetail={loadDetail} session={session} />)}</div>}
      </section>

      <section className="task4-trend-section" aria-labelledby="trend-title">
        <div className="task4-section-heading"><div><span className="task3-section-kicker">Trend</span><h2 id="trend-title">长期趋势</h2></div><Button disabled={trendLoading} onClick={() => void loadTrend()} variant="secondary">读取可比趋势</Button></div>
        {trend ? <Notice tone={getTrendPresentation(trend).comparable ? "info" : "warning"}>{getTrendPresentation(trend).summary}</Notice> : <p className="task4-muted">仅在场景、模式、指标、单位、校准与质量均满足时生成趋势；不会制造伪 PB。</p>}
      </section>

      {selectedPendingRun ? (
        <div className="task4-pending-confirm" role="dialog" aria-modal="true" aria-labelledby="pending-confirm-title">
          <div className="task4-confirm-panel">
            <h2 id="pending-confirm-title">确认这条 Run</h2>
            <p>{selectedPendingRun.scenario ?? "未知场景"} · {new Date(selectedPendingRun.created_at).toLocaleString("zh-CN")}</p>
            <Notice title="下一步">确认只会把这条 Run 带到新建分析页，不会自动提交任务；其它待分析 Run 保持原状。</Notice>
            <div className="task4-operation-list"><Button href={`/analyze?run=${encodeURIComponent(selectedPendingRun.run_ref)}`}>去新建分析</Button><Button onClick={() => setSelectedPendingRun(null)} variant="secondary">取消</Button></div>
          </div>
        </div>
      ) : null}

      {selectedRun ? <div className="task4-inspector-overlay"><RunInspector onClose={() => setSelectedRun(null)} run={selectedRun} /></div> : null}

      {detailId !== null ? (
        <div className="task4-detail-overlay" role="dialog" aria-modal="true" aria-labelledby="detail-title">
          <div className="task4-detail-panel">
            <header><h2 id="detail-title">分析摘要</h2><Button onClick={() => setDetailId(null)} variant="ghost">关闭</Button></header>
            {detailLoading ? <Loading>正在按需加载摘要</Loading> : detailError ? <ErrorState title="分析详情暂时不可用"><p>原列表仍保留，稍后可以重试。</p><Button onClick={() => void loadDetail(detailId)} variant="secondary">重试</Button></ErrorState> : detail ? (
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
            ) : null}
          </div>
        </div>
      ) : null}

    </div>
  );
}
