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
import { Button, Dialog, Drawer, Empty, ErrorState, Loading, Notice, Status } from "@/ui/primitives";

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
  onConfirm,
  picked,
  onPick,
}: {
  run: KovaaKRunListItem;
  onInspect: (run: KovaaKRunListItem) => void;
  onConfirm: (run: KovaaKRunListItem) => void;
  picked: boolean;
  onPick: (run: KovaaKRunListItem) => void;
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
      onClick={isPending ? () => onPick(run) : undefined}
      style={isPending ? { cursor: "pointer" } : undefined}
    >
      {isPending ? <span className="task4-sel-dot" data-selected={picked ? "true" : "false"} /> : null}
      <div className="task4-row-main">
        <div className="task4-row-title">
          <span className="task4-name">{run.scenario ?? "未知场景"}</span>
          {!isPending ? runRecordBadge(run) : null}
        </div>
        <div className="task4-row-sub">
          <span>{formatHistoryDate(run.created_at)}</span>
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
          picked ? (
            <Button onClick={() => onConfirm(run)} size="compact">开始分析</Button>
          ) : (
            <Button onClick={() => onPick(run)} size="compact" variant="secondary">选择这条</Button>
          )
        ) : (
          <Button onClick={() => onInspect(run)} size="compact" variant="ghost">查看 Run</Button>
        )}
      </div>
    </div>
  );
}

function AnalysisRow({ session, onLoadDetail }: { session: SessionListItem; onLoadDetail: (id: number) => void }) {
  const tone = sessionTone(session.status);
  return (
    <div className="task4-rowline">
      <div className="task4-row-main">
        <div className="task4-row-title">
          <span className="task4-name">{session.scenario ?? session.summary_label ?? "未命名分析"}</span>
          <span className={`task4-badge task4-badge-${tone === "success" ? "ok" : tone === "error" ? "err" : "neu"}`}>
            {tone === "success" ? <span className="task4-badge-dot" /> : null}
            {sessionStatus(session.status)}
          </span>
          <span className="task4-badge task4-badge-mode">{inputModeLabel(session.input_mode)}</span>
          {session.input_mode === "input_native" ? <span className="task4-badge task4-badge-preview">预览</span> : null}
        </div>
        <div className="task4-row-sub">
          <span>{formatHistoryDate(session.created_at)}</span>
          <span>摘要：{session.summary_label ?? "暂无摘要"}</span>
        </div>
      </div>
      <div className="task4-row-actions">
        {session.status === "done" ? (
          <Button href={`/analysis/${session.id}`} size="compact" variant="secondary">查看</Button>
        ) : (
          <Button onClick={() => onLoadDetail(session.id)} size="compact" variant="secondary">查看</Button>
        )}
      </div>
    </div>
  );
}

function TrendChart() {
  return (
    <svg className="task4-trend-chart" viewBox="0 0 150 40" aria-hidden="true" width="150" height="40">
      <path
        d="M4 26 L40 22 L76 28 L112 20 L146 23 L146 40 L4 40 Z"
        fill="currentColor"
        opacity="0.08"
      />
      <path
        d="M4 26 L40 22 L76 28 L112 20 L146 23"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle cx="40" cy="22" fill="currentColor" r="3" />
      <circle cx="112" cy="20" fill="currentColor" r="3" />
    </svg>
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
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [refresh, setRefresh] = useState<RefreshState>("loading");
  const [runDiscovery, setRunDiscovery] = useState<RunDiscoveryState>("loading");
  const [initialError, setInitialError] = useState(false);
  const [selectedRun, setSelectedRun] = useState<KovaaKRunItem | KovaaKRunListItem | null>(null);
  const [selectedPendingRun, setSelectedPendingRun] = useState<KovaaKRunListItem | null>(null);
  const [pickedPendingRef, setPickedPendingRef] = useState<string | null>(null);
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
  const pickedPending = sections.pendingRuns.find((run) => run.run_ref === pickedPendingRef) ?? sections.pendingRuns[0] ?? null;

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

  const pageSubText = useMemo(() => {
    if (refresh === "unavailable") return "最近刷新失败 · 旧内容已保留";
    if (runDiscovery === "browser_unavailable") return "最近刷新：刚刚 · Run 发现需要桌面应用";
    if (runDiscovery === "service_unavailable") return "最近刷新：刚刚 · Run 来源暂时不可用";
    return "最近刷新：刚刚 · 全部来源正常";
  }, [refresh, runDiscovery]);

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
        <div>
          <div className="task4-page-title">历史</div>
          <div className="task4-page-sub">{pageSubText}</div>
        </div>
        <div className="task4-page-actions">
          <Button onClick={() => void loadHistory()} size="compact" variant="ghost">刷新</Button>
          <Button href="/analyze" size="compact">＋ 新建分析</Button>
        </div>
      </div>

      {refresh === "unavailable" ? <Notice tone="warning" title="刷新暂时不可用">保留当前已读取内容；恢复本地服务后可以重试。</Notice> : null}
      {runDiscovery === "browser_unavailable" ? <Notice tone="info" title="Run 发现仅在桌面应用可用">浏览器可以查看分析记录；要查看自动采集的 Run，请在桌面应用中打开 History。</Notice> : null}
      {runDiscovery === "service_unavailable" ? <Notice tone="warning" title="Run 暂时不可用">桌面服务没有返回训练 Run；这不是“没有记录”。恢复服务后可以刷新。</Notice> : null}

      <section className="task4-sec" aria-labelledby="pending-title">
        <div className="task4-sec-head">
          <h2 id="pending-title" className="task4-sec-title">待分析训练</h2>
          <span className="task4-sec-count">{sections.pendingRuns.length}</span>
          <span className="task4-sec-hint">自动采集已整理完成 · 选择一条开始分析</span>
        </div>
        {sections.pendingRuns.length === 0 ? (
          <RunSectionState kind="pending" runDiscovery={runDiscovery} />
        ) : (
          <div className="task4-panel">
            {sections.pendingRuns.map((run) => (
              <RunRow
                key={run.run_ref}
                onConfirm={setSelectedPendingRun}
                onInspect={(item) => void inspect(item)}
                onPick={(item) => setPickedPendingRef(item.run_ref)}
                picked={pickedPending?.run_ref === run.run_ref}
                run={run}
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
                onConfirm={setSelectedPendingRun}
                onInspect={(item) => void inspect(item)}
                onPick={() => undefined}
                picked={false}
                run={run}
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
            <Link className="ac-button" data-variant="primary" href="/analyze">新建分析</Link>
          </Empty>
        ) : (
          <div className="task4-panel">
            {sections.analysisRecords.map((session) => (
              <AnalysisRow key={session.analysis_ref} onLoadDetail={loadDetail} session={session} />
            ))}
          </div>
        )}
      </section>

      <section className="task4-sec" aria-labelledby="trend-title">
        <div className="task4-sec-head">
          <h2 id="trend-title" className="task4-sec-title">长期趋势</h2>
          <span className="task4-sec-hint">仅比较同场景、同模式、同校准的记录</span>
        </div>
        {trendLoading ? (
          <Loading>正在读取趋势</Loading>
        ) : trend ? (
          <div className="task4-panel task4-trend-panel">
            <TrendChart />
            <div>
              <div className="task4-trend-text">{getTrendPresentation(trend).summary}</div>
              <div className="task4-trend-sub">{getTrendPresentation(trend).comparable ? "趋势基于可比记录生成。" : "记录不满足比较条件，未生成伪 PB。"}</div>
            </div>
          </div>
        ) : (
          <div className="task4-panel task4-trend-empty">
            <span>仅在场景、模式、指标、单位、校准与质量均满足时生成趋势；不会制造伪 PB。</span>
            <Button onClick={() => void loadTrend()} size="compact" variant="secondary">读取可比趋势</Button>
          </div>
        )}
      </section>

      <Dialog
        footer={selectedPendingRun ? (
          <div className="task4-operation-list">
            <Button href={`/analyze?run=${encodeURIComponent(selectedPendingRun.run_ref)}`}>去新建分析</Button>
            <Button onClick={() => setSelectedPendingRun(null)} variant="secondary">取消</Button>
          </div>
        ) : null}
        onClose={() => setSelectedPendingRun(null)}
        open={Boolean(selectedPendingRun)}
        title="确认这条 Run"
      >
        {selectedPendingRun ? (
          <>
            <p>{selectedPendingRun.scenario ?? "未知场景"} · {formatHistoryDate(selectedPendingRun.created_at)}</p>
            <Notice title="下一步">确认只会把这条 Run 带到新建分析页，不会自动提交任务；其它待分析 Run 保持原状。</Notice>
          </>
        ) : null}
      </Dialog>

      <Drawer
        onClose={() => setSelectedRun(null)}
        open={Boolean(selectedRun)}
        title={selectedRun ? `Run 详情 · ${selectedRun.scenario ?? "未知场景"}` : "Run 详情"}
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
