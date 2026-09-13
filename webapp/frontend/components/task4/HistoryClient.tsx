"use client";

import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { getHistorySessions, getKovaaKLocalDirectories, listKovaakRuns } from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import {
  buildCoachAnalysisDraft,
  buildHistorySections,
  COACH_PENDING_INTENT_KEY,
  formatHistoryDate,
  getHistoryStatusText,
  presentRecordLabel,
} from "@/lib/contracts";
import type { KovaaKLocalDirectoriesV1, KovaaKRunListItem, KovaaKWatcherStatusV1, SessionListItem } from "@/lib/types";
import { IconAlertCircle, IconChevronDown, IconChevronLeft, IconRefresh } from "@/ui/icons";
import { Button, Empty, ErrorState, IconButton, Notice } from "@/ui/primitives";
import { startWindowDraggingOnBackground } from "@/components/task3/TauriWindowControls";

type RefreshState = "idle" | "loading" | "unavailable";
type RunDiscoveryState = "loading" | "available" | "browser_unavailable" | "service_unavailable";

/** 「让 Coach 分析」一次最多引用的训练条数（分析逐条串行进行）。 */
const MAX_SELECTED_RUNS = 5;

/** 列表默认只显示前 5 条（0911 点点第三批 D），底部「显示全部 N 条」展开。 */
const MAX_VISIBLE_RUNS = 5;

/** 「训练记录」区块折叠状态持久化；命名跟随 aiming-cookie.* 惯例，默认折叠。 */
const RUNS_COLLAPSED_KEY = "aiming-cookie.ui.history-runs-collapsed";

/** 「待分析训练」「训练记录」前 5 条折叠的展开状态（0911 点点第三批 D）：
    一个对象 key 同存两个区块（{"pending":bool,"records":bool}），沿用
    aiming-cookie.* 惯例；默认都只显示前 5 条。 */
const RUN_EXPAND_KEY = "aiming-cookie.ui.history-run-expand";

type RunExpandState = { pending: boolean; records: boolean; analysis: boolean };

/** 「分析记录」整段折叠状态持久化（0911 点点第四轮）；命名跟随
    history-runs-collapsed 惯例，默认展开（存储值 "1" = 收起）。 */
const ANALYSIS_COLLAPSED_KEY = "aiming-cookie.ui.history-analysis-collapsed";

/** 「待分析训练」整段折叠（0911 点点五轮：三区块头统一）。默认展开。 */
const PENDING_COLLAPSED_KEY = "aiming-cookie.ui.history-pending-collapsed";

/** 视频 finalize 由后台完成；这些终态之外的 Run 证据还会变化，需要继续跟进。 */
const RUN_FINALIZED_STATES = new Set(["finalized", "source_unavailable", "unavailable"]);

/** 「更新于 N 前」距最近一次读取超过该毫秒数视为陈旧，文字转轻提醒色。 */
const STALE_AFTER_MS = 2 * 60 * 1000;

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
    // 与 multimodal 同一执行语义的遥测档（后端 V2 语义等价映射）：
    // 界面统一叫「多源模式」，不把管线代号漏到文案里（0911 审计 §12.6）。
    telemetry_multimodal: "多源模式",
    video_fallback: "视频兼容",
  }[mode] ?? mode;
}

/** 异常人话映射（0911 点点第三批 F）：恢复上一批删掉的 limitationLabel 精简版。
    limitation 键形如 "raw_unavailable"（后端 _run_evidence_view 生成）。 */
const LIMITATION_KIND_LABEL: Record<string, string> = {
  stats: "Stats",
  performance: "Performance",
  raw: "Raw",
  video: "视频",
};

const LIMITATION_AVAILABILITY_LABEL: Record<string, string> = {
  failed: "读取失败",
  invalid: "内容已变化",
  missing: "未提供",
  not_present: "未提供",
  unavailable: "来源不可用",
};

function limitationLabel(limitation: string): string {
  if (limitation === "canonical_window_missing") return "时间窗口未对齐";
  const separator = limitation.lastIndexOf("_");
  if (separator <= 0) return limitation;
  const kind = LIMITATION_KIND_LABEL[limitation.slice(0, separator)] ?? limitation.slice(0, separator);
  const availability = limitation.slice(separator + 1);
  return `${kind} ${LIMITATION_AVAILABILITY_LABEL[availability] ?? availability}`;
}

/** 行内红色感叹号的悬停/读屏文案（0911 点点第三批 F）：替代 chips 墙与红底
    badge；来源不可用终态统一说「训练来源已不可用」，其余按 limitation 人话。 */
function runIssueText(run: KovaaKRunListItem): string | null {
  if (run.finalization_state === "source_unavailable" || run.finalization_state === "unavailable") {
    return "训练来源已不可用";
  }
  if (run.limitations.length === 0) return null;
  return run.limitations.map(limitationLabel).join("；");
}

function runRecordBadge(run: KovaaKRunListItem) {
  if (run.analysis_count > 0 || run.readiness_state === "analyzed") {
    return <span className="task4-badge task4-badge-neu">已分析</span>;
  }
  return null;
}

/** 行内时间只留时分（0911 点点第三批 B）：日期由组头表达，原「训练时间」前缀退役。 */
function trainingTimeLabel(iso: string | null | undefined): string {
  if (!iso) return "--:--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--:--";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

/** 千分位（0911 点点第三批 E）：41,250；显示层最多 1 位小数（wire 保留全精度）。 */
function formatScore(score: number): string {
  return score.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/** 相对时间文案（0911 点点第二批）：<60 秒=N 秒前、<60 分钟=N 分钟前、否则 N 小时前。 */
function relativeUpdatedAt(nowMs: number, atMs: number): string {
  const seconds = Math.max(0, Math.floor((nowMs - atMs) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  return `${Math.floor(minutes / 60)} 小时前`;
}

/** 训练日分组标签：今天/昨天/M月D日（与 contracts.formatHistoryDate 的文案同源）。 */
function trainingDayLabel(iso: string | null | undefined): string {
  if (!iso) return "时间未知";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const now = new Date();
  const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(date, now)) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return "昨天";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

/** 按日把条目归组（0911 点点第二批；五轮起泛化供分析记录复用）：同日条目即使
    被列表交错也并成一组，组间保持首次出现的先后。纯渲染分组，不参与勾选/过滤。 */
function groupByDay<T>(items: T[], getDate: (item: T) => string | null | undefined): Array<{ label: string; items: T[] }> {
  const groups: Array<{ label: string; items: T[] }> = [];
  const lastIndex = new Map<string, number>();
  for (const item of items) {
    const label = trainingDayLabel(getDate(item));
    const index = lastIndex.get(label);
    if (index === undefined) {
      lastIndex.set(label, groups.length);
      groups.push({ label, items: [item] });
    } else {
      groups[index].items.push(item);
    }
  }
  return groups;
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
  // 对局时间优先 source_key 解析值；created_at 是批次发现时间，同批多局共享
  // （曾把不同对局渲染成"完全重复卡"，0911 审计 §12.6）。
  const trainingAt = run.training_at ?? run.created_at;
  // 异常标（0911 点点第三批 F）：limitations 非空或来源不可用终态 → 红色
  // 感叹号圈标（悬停 title 人话）；正常行完全无标记。
  const issue = runIssueText(run);
  // 整行可点勾选（0911 点点第二批）：disabled 行不接管点击、cursor 默认；
  // 键盘仍走原生 checkbox（Tab + 空格切换）。
  const interactive = Boolean(onToggle) && !disabled;
  return (
    <div
      className="task4-rowline task4-run-row"
      data-interactive={interactive || undefined}
      onClick={interactive && onToggle ? () => onToggle(run) : undefined}
    >
      {onToggle ? (
        // 勾选圆点居行首；点击止于此处：onChange 已切换一次，冒泡到整行会二次切换。
        <div className="task4-row-actions" onClick={(event) => event.stopPropagation()}>
          <input
            aria-label={`选择 ${run.scenario ?? "未知场景"}（${formatHistoryDate(trainingAt)}）`}
            checked={Boolean(selected)}
            className="task4-check"
            disabled={disabled}
            onChange={() => onToggle(run)}
            title={disabled ? "证据不足以分析" : undefined}
            type="checkbox"
          />
        </div>
      ) : null}
      {/* 单行布局（0911 点点第三批 B）：场景名弹性占满，分数/时间/异常标右聚。 */}
      <span className="task4-name">{presentRecordLabel({ scenario: run.scenario, titleOnly: true })}</span>
      {!isPending ? runRecordBadge(run) : null}
      {run.score != null ? <span className="task4-run-score">{formatScore(run.score)}</span> : null}
      <span className="task4-run-time">{trainingTimeLabel(trainingAt)}</span>
      {issue ? (
        <span
          aria-label={`训练异常：${issue}`}
          className="task4-run-issue"
          role="img"
          title={issue}
        >
          <IconAlertCircle height={14} width={14} />
        </span>
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
  const interactive = Boolean(onToggle) && !disabled;
  const analysisAt = session.analysis_completed_at ?? session.finished_at;
  // 单行化后副行退役：训练/分析时间与摘要收敛进整行悬停提示（0911 点点五轮）。
  const hoverText = [
    `模式 ${inputModeLabel(session.input_mode)}${session.input_mode === "input_native" ? "（预览）" : ""}`,
    session.training_at ? `训练 ${formatHistoryDate(session.training_at)}` : null,
    analysisAt ? `分析 ${formatHistoryDate(analysisAt)}` : null,
    session.summary_label ? `摘要：${session.summary_label}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <div
      className="task4-rowline task4-analysis-row"
      data-interactive={interactive || undefined}
      onClick={interactive && onToggle ? () => onToggle(session) : undefined}
      title={hoverText || undefined}
    >
      {onToggle ? (
        <div className="task4-row-actions" onClick={(event) => event.stopPropagation()}>
          <input
            aria-label={`选择分析 ${session.scenario ?? "未知场景"}（${formatHistoryDate(session.training_at ?? session.created_at)}）`}
            checked={Boolean(selected)}
            className="task4-check"
            disabled={disabled}
            onChange={() => onToggle(session)}
            title={disabled ? "分析未完成，暂无结果可讨论" : undefined}
            type="checkbox"
          />
        </div>
      ) : null}
      <span className="task4-name">{presentRecordLabel({ scenario: session.scenario, titleOnly: true })}</span>
      {/* 0911 点点五轮对称化：正常（已完成）行不再挂任何徽章，只有失败/分析中
          这类非默认状态才显示；模式/预览等详情进整行悬停提示。 */}
      {tone !== "success" ? (
        <span className={`task4-badge task4-badge-${tone === "error" ? "err" : "neu"}`}>
          {sessionStatus(session.status)}
        </span>
      ) : null}
      <span className="task4-analysis-gap" />
      <span className="task4-analysis-time">{analysisAt ? formatHistoryDate(analysisAt) : "—"}</span>
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
  const [watcherStatus, setWatcherStatus] = useState<KovaaKWatcherStatusV1 | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<number[]>([]);
  const [selectedAnalysisIds, setSelectedAnalysisIds] = useState<number[]>([]);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const [scenarioFilter, setScenarioFilter] = useState("");
  // 「训练记录」折叠：默认收起，展开状态持久化到 localStorage（水合后再读，
  // 避免初始化器里同步碰 storage——与 AppShell 列宽恢复同一理由）。
  const [runRecordsOpen, setRunRecordsOpen] = useState(false);
  // 「分析记录」折叠（0911 点点第四轮）：默认展开，持久化同「训练记录」；
  // 收起时不渲染行，勾选集合保留、仍计入「让 Coach 分析」计数。
  const [analysisRecordsOpen, setAnalysisRecordsOpen] = useState(true);
  // 「待分析训练」折叠（0911 点点五轮）：默认展开，持久化同上。
  const [pendingRecordsOpen, setPendingRecordsOpen] = useState(true);
  // 前 5 条折叠（0911 点点第三批 D）：两个区块各一个开关、一个对象 key 持久化；
  // 勾选集合不折叠——被折掉的已勾选行仍计入「让 Coach 分析」计数。
  const [runExpand, setRunExpand] = useState<RunExpandState>({ pending: false, records: false, analysis: false });
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const selectedCount = selectedRunIds.length + selectedAnalysisIds.length;
  const allListsEmpty = runs.length === 0 && sessions.length === 0;

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
      // 时间基点 = 最近一次成功/部分成功读取的完成时刻（「更新于 N 前」据此显示）。
      const loadedAt = Date.now();
      setLastLoadedAt(loadedAt);
      setNowTick(loadedAt);
      setRefresh("idle");
      setInitialError(false);
    }
    // 桌面版空列表本来就会随上方轮询再次进入这里：顺路读 watcher 健康，
    // 用于区分「没找到目录」和「KovaaK 未导出」两种空态；不新增定时器。
    if (!canDiscoverRuns) {
      setWatcherStatus(null);
    } else if (allListsEmpty) {
      try {
        const directories: KovaaKLocalDirectoriesV1 = await getKovaaKLocalDirectories();
        setWatcherStatus(directories.watcher_status ?? null);
      } catch {
        setWatcherStatus(null);
      }
    }
  }, [allListsEmpty]);

  useEffect(() => {
    void loadHistory(true);
  }, [loadHistory]);

  useEffect(() => {
    try {
      setRunRecordsOpen(window.localStorage.getItem(RUNS_COLLAPSED_KEY) === "1");
    } catch {
      // storage 不可用时保持默认折叠。
    }
  }, []);

  const toggleRunRecords = useCallback(() => {
    setRunRecordsOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem(RUNS_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // 持久化失败不影响本次切换。
      }
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      setAnalysisRecordsOpen(window.localStorage.getItem(ANALYSIS_COLLAPSED_KEY) !== "1");
    } catch {
      // storage 不可用时保持默认展开。
    }
  }, []);

  const toggleAnalysisRecords = useCallback(() => {
    setAnalysisRecordsOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem(ANALYSIS_COLLAPSED_KEY, next ? "0" : "1");
      } catch {
        // 持久化失败不影响本次切换。
      }
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      setPendingRecordsOpen(window.localStorage.getItem(PENDING_COLLAPSED_KEY) !== "1");
    } catch {
      // storage 不可用时保持默认展开。
    }
  }, []);

  const togglePendingRecords = useCallback(() => {
    setPendingRecordsOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem(PENDING_COLLAPSED_KEY, next ? "0" : "1");
      } catch {
        // 持久化失败不影响本次切换。
      }
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(RUN_EXPAND_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<RunExpandState>;
        setRunExpand({ pending: parsed.pending === true, records: parsed.records === true, analysis: parsed.analysis === true });
      }
    } catch {
      // storage 不可用/内容损坏时保持默认前 5 条。
    }
  }, []);

  const toggleRunExpand = useCallback((kind: keyof RunExpandState) => {
    setRunExpand((current) => {
      const next = { ...current, [kind]: !current[kind] };
      try {
        window.localStorage.setItem(RUN_EXPAND_KEY, JSON.stringify(next));
      } catch {
        // 持久化失败不影响本次切换。
      }
      return next;
    });
  }, []);

  const sections = useMemo(() => buildHistorySections({ runs, sessions }), [runs, sessions]);

  // 场景名过滤（0911 点点第二批）：不区分大小写的子串匹配，空输入 = 全部。
  // 只影响列表渲染与区块计数；勾选集合、「让 Coach 分析」话术仍取全量数据。
  const normalizedFilter = scenarioFilter.trim().toLocaleLowerCase();
  const filteredSections = useMemo(() => {
    if (!normalizedFilter) return sections;
    const matches = (scenario: string | null | undefined) => Boolean(scenario?.toLocaleLowerCase().includes(normalizedFilter));
    return {
      pendingRuns: sections.pendingRuns.filter((run) => matches(run.scenario)),
      runRecords: sections.runRecords.filter((run) => matches(run.scenario)),
      analysisRecords: sections.analysisRecords.filter((session) => matches(session.scenario)),
    };
  }, [sections, normalizedFilter]);

  // 前 5 条可见切片（0911 点点第三批 D）：按现有排序取前 5，展开后显示全部。
  const visiblePendingRuns = runExpand.pending
    ? filteredSections.pendingRuns
    : filteredSections.pendingRuns.slice(0, MAX_VISIBLE_RUNS);
  const visibleRunRecords = runExpand.records
    ? filteredSections.runRecords
    : filteredSections.runRecords.slice(0, MAX_VISIBLE_RUNS);
  // 「分析记录」前 5 条切片（0911 点点五轮：与 Run 区对齐）；按分析完成日分组。
  const visibleAnalysisRecords = runExpand.analysis
    ? filteredSections.analysisRecords
    : filteredSections.analysisRecords.slice(0, MAX_VISIBLE_RUNS);

  // 桌面版且全部列表为空时，用 watcher 健康区分两种空态并给出下一步；
  // 加载中、服务不可用、状态未知或已在摄取数据时不显示。
  const watcherGuidance: KovaaKWatcherStatusV1 | null =
    runDiscovery === "available" && allListsEmpty && (watcherStatus === "no_candidates" || watcherStatus === "not_exporting")
      ? watcherStatus
      : null;

  // 视频 finalize 在页面停留期间完成（如「视频 −」变 ✓）；有未终态 Run 时轮询。
  // 桌面空列表也需持续发现新完成的本地训练；浏览器没有 Run 发现能力，不轮询。
  const shouldPollHistory = useMemo(
    () => isDesktopRuntime() && (runs.length === 0 || runs.some((run) => !RUN_FINALIZED_STATES.has(run.finalization_state))),
    [runs],
  );

  useEffect(() => {
    if (!shouldPollHistory) return undefined;
    // 顺路更新 nowTick：「更新于 N 前」随轮询自然刷新，不新增定时器。
    const timer = window.setInterval(() => { setNowTick(Date.now()); void loadHistory(); }, 5000);
    return () => window.clearInterval(timer);
  }, [shouldPollHistory, loadHistory]);

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

  const updatedLabel = lastLoadedAt === null ? "正在读取…" : `更新于 ${relativeUpdatedAt(nowTick, lastLoadedAt)}`;
  const updatedStale = lastLoadedAt !== null && nowTick - lastLoadedAt >= STALE_AFTER_MS;

  if (initialError && runs.length === 0 && sessions.length === 0) {
    return (
      <div className="task4-page-shell">
        {/* 顶栏挂 shell 层保持全宽（0911 点点第四轮），56px 实底兼作窗口拖拽区。 */}
        <div className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}>
          <div className="task4-col task4-head-col">
          <div className="task4-topbar-left">
            <IconButton label="返回 Coach" onClick={() => router.push("/")} size="compact" title="返回 Coach"><IconChevronLeft /></IconButton>
            <div className="task4-page-title">历史</div>
          </div>
          </div>
        </div>
        <div className="task4-page">
        <div className="task4-col">
          <ErrorState title="历史暂时不可用">
            <p>读取失败没有被显示成没有记录。</p>
            <Button onClick={() => void loadHistory(true)} variant="secondary">重试</Button>
          </ErrorState>
        </div>
        </div>
      </div>
    );
  }

  return (
      <div className="task4-page-shell">
        {/* 56px 实底顶栏（0911 点点第三批 A 顶栏化 + 第四轮 56px 全宽）：合并
            原透明拖拽带与大标题页头，自身就是那条拖拽带（空白处拖拽，交互元素
            由助手放行）；挂在 shell 层保持全宽，520px 滚动列从顶栏下缘起。
            左组紧凑排列返回键/标题/筛选框/更新状态/「让 Coach 分析」常驻胶囊，
            顶栏右端只留三键让位区。 */}
        <div className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}>
          <div className="task4-col task4-head-col">
          <div className="task4-topbar-left">
            <IconButton label="返回 Coach" onClick={() => router.push("/")} size="compact" title="返回 Coach"><IconChevronLeft /></IconButton>
            <div className="task4-page-title">历史</div>
            <input
              aria-label="按场景名筛选"
              className="task4-filter-input"
              onChange={(event) => setScenarioFilter(event.target.value)}
              placeholder="按场景名筛选…"
              type="text"
              value={scenarioFilter}
            />
            {/* 「更新于 N 前」状态行（0911 点点第二批 12）：
                点击整行 = 重新读取；轮询进行中箭头旋转；文字固定宽度防抖动。 */}
            <button
              aria-label={lastLoadedAt === null ? "刷新（正在读取）" : `刷新（上次更新 ${relativeUpdatedAt(nowTick, lastLoadedAt)}）`}
              className="task4-refresh-status"
              data-stale={updatedStale || undefined}
              onClick={() => void loadHistory()}
              title="刷新"
              type="button"
            >
              <span aria-hidden="true" className="task4-refresh-icon" data-loading={refresh === "loading" || undefined}>
                <IconRefresh height={14} width={14} />
              </span>
              <span className="task4-refresh-text">{updatedLabel}</span>
            </button>
            {/* 「让 Coach 分析」常驻胶囊（0911 点点第四轮）：未勾选为 disabled
                灰胶囊（点击无意义，原生 disabled + not-allowed 不误导）；勾选后
                primary 橙 + 计数。0913 点点：移出左组贴 580 列右缘
                （margin-inline-start: auto），右缘与下方列表面板对齐。 */}
          </div>
          <button
            className="task4-coach-pill"
            data-active={selectedCount > 0 || undefined}
            disabled={selectedCount === 0}
            onClick={startCoachAnalysis}
            type="button"
          >
            {selectedCount > 0 ? `让 Coach 分析（${selectedCount}）` : "让 Coach 分析"}
          </button>
          {/* 勾选超限提示随顶栏常驻视口（绝对定位挂在顶栏下缘渐隐带之下）。 */}
          {selectionNotice ? <Notice className="task4-head-notice" tone="info">{selectionNotice}</Notice> : null}
          </div>
        </div>
        <div className="task4-page">
        <div className="task4-col">
      {refresh === "unavailable" ? <Notice tone="warning" title="刷新暂时不可用">保留当前已读取内容；恢复本地服务后可以重试。</Notice> : null}
      {runDiscovery === "browser_unavailable" ? <Notice tone="info" title="Run 发现仅在桌面应用可用">浏览器可以查看分析记录；要查看自动采集的 Run，请在桌面应用中打开 History。</Notice> : null}
      {runDiscovery === "service_unavailable" ? <Notice tone="warning" title="Run 暂时不可用">桌面服务没有返回训练 Run；这不是"没有记录"。恢复服务后可以刷新。</Notice> : null}

      {watcherGuidance === "no_candidates" ? (
        <Empty className="task4-panel task4-state-panel" title="未找到你的 KovaaK 训练数据">
          <p>自动发现没有找到可用的 KovaaK 数据目录。</p>
          <Button onClick={() => router.push("/settings#kovaak-directories")} size="compact" variant="secondary">
            前往 设置 → KovaaK 本地目录 手动指定
          </Button>
        </Empty>
      ) : null}
      {watcherGuidance === "not_exporting" ? (
        <Empty className="task4-panel task4-state-panel" title="KovaaK 未在导出训练数据">
          <p>请在 KovaaK 中打开 设置 → 其他 → 统计数据输出，选择 Challenge Completion，然后完成一局挑战。</p>
        </Empty>
      ) : null}

      <section className="task4-sec" aria-labelledby="pending-title">
        {/* 「待分析训练」折叠（0911 点点五轮：三区块头统一，默认展开）。 */}
        <div className="task4-sec-head task4-sec-collapsible" onClick={togglePendingRecords}>
          <h2 id="pending-title" className="task4-sec-title">待分析训练</h2>
          <span className="task4-sec-count">{filteredSections.pendingRuns.length}</span>
          <button
            aria-expanded={pendingRecordsOpen}
            aria-label={pendingRecordsOpen ? "收起待分析训练" : "展开待分析训练"}
            className="task4-sec-caret"
            onClick={(event) => {
              event.stopPropagation();
              togglePendingRecords();
            }}
            type="button"
          >
            <IconChevronDown />
          </button>
        </div>
        {pendingRecordsOpen ? (filteredSections.pendingRuns.length === 0 ? (
          normalizedFilter ? (
            <Empty className="task4-panel task4-state-panel" title="没有匹配的记录">换个场景名关键词再试。</Empty>
          ) : (
            <RunSectionState kind="pending" runDiscovery={runDiscovery} />
          )
        ) : (
          <div className="task4-panel">
            {groupByDay(visiblePendingRuns, (run) => run.training_at ?? run.created_at).map((group, groupIndex) => (
              <Fragment key={`${group.label}-${groupIndex}`}>
                <div className="task4-day-label">{group.label} · {group.items.length}条</div>
                {group.items.map((run) => (
                  <RunRow
                    key={run.run_ref}
                    onToggle={toggleRun}
                    run={run}
                    selected={selectedRunIds.includes(run.id)}
                  />
                ))}
              </Fragment>
            ))}
            {filteredSections.pendingRuns.length > MAX_VISIBLE_RUNS ? (
              <button
                className="task4-expand-toggle"
                onClick={() => toggleRunExpand("pending")}
                type="button"
              >
                {runExpand.pending ? "收起" : `显示全部 ${filteredSections.pendingRuns.length} 条`}
              </button>
            ) : null}
          </div>
        )) : null}
      </section>

      <section className="task4-sec" aria-labelledby="analysis-title">
        {/* 「分析记录」整段折叠（0911 点点第四轮）：折叠头样式同「训练记录」
            整段折叠头，默认展开、状态持久化；收起时不渲染行，勾选集合保留、
            仍计入「让 Coach 分析」计数。 */}
        <div className="task4-sec-head task4-sec-collapsible" onClick={toggleAnalysisRecords}>
          <h2 id="analysis-title" className="task4-sec-title">分析记录</h2>
          <span className="task4-sec-count">{filteredSections.analysisRecords.length}</span>
          <button
            aria-expanded={analysisRecordsOpen}
            aria-label={analysisRecordsOpen ? "收起分析记录" : "展开分析记录"}
            className="task4-sec-caret"
            onClick={(event) => {
              event.stopPropagation();
              toggleAnalysisRecords();
            }}
            type="button"
          >
            <IconChevronDown />
          </button>
        </div>
        {analysisRecordsOpen ? (
          filteredSections.analysisRecords.length === 0 ? (
            normalizedFilter ? (
              <Empty className="task4-panel task4-state-panel" title="没有匹配的记录">换个场景名关键词再试。</Empty>
            ) : (
              <Empty className="task4-panel task4-state-panel" title="还没有分析记录">
                完成一局 KovaaK 训练后，记录会保留在这里。
              </Empty>
            )
          ) : (
            <div className="task4-panel">
              {/* 按分析完成日分组 + 前 5 条切片（0911 点点五轮：与 Run 区完全对齐）。 */}
              {groupByDay(visibleAnalysisRecords, (session) => session.analysis_completed_at ?? session.finished_at ?? session.created_at).map((group, groupIndex) => (
                <Fragment key={`${group.label}-${groupIndex}`}>
                  <div className="task4-day-label">{group.label} · {group.items.length}条</div>
                  {group.items.map((session) => (
                    <AnalysisRow
                      disabled={session.status !== "done"}
                      key={session.analysis_ref}
                      onToggle={toggleAnalysis}
                      selected={selectedAnalysisIds.includes(session.id)}
                      session={session}
                    />
                  ))}
                </Fragment>
              ))}
              {filteredSections.analysisRecords.length > MAX_VISIBLE_RUNS ? (
                <button
                  className="task4-expand-toggle"
                  onClick={() => toggleRunExpand("analysis")}
                  type="button"
                >
                  {runExpand.analysis ? "收起" : `显示全部 ${filteredSections.analysisRecords.length} 条`}
                </button>
              ) : null}
            </div>
          )
        ) : null}
      </section>

      <section className="task4-sec" aria-labelledby="runs-title">
        {/* 「训练记录」默认折叠（0911 点点第二批 8）：收起时只留区块头 + 箭头，
            不渲染行列表；已勾选 id 保留，不影响「让 Coach 分析」计数。 */}
        <div className="task4-sec-head task4-sec-collapsible" onClick={toggleRunRecords}>
          <h2 id="runs-title" className="task4-sec-title">训练记录</h2>
          <span className="task4-sec-count">{filteredSections.runRecords.length}</span>
          <button
            aria-expanded={runRecordsOpen}
            aria-label={runRecordsOpen ? "收起训练记录" : "展开训练记录"}
            className="task4-sec-caret"
            onClick={(event) => {
              event.stopPropagation();
              toggleRunRecords();
            }}
            type="button"
          >
            <IconChevronDown />
          </button>
        </div>
        {runRecordsOpen ? (
          filteredSections.runRecords.length === 0 ? (
            normalizedFilter ? (
              <Empty className="task4-panel task4-state-panel" title="没有匹配的记录">换个场景名关键词再试。</Empty>
            ) : (
              <RunSectionState kind="records" runDiscovery={runDiscovery} />
            )
          ) : (
            <div className="task4-panel">
              {/* 「训练记录」整段展开后，段内再套前 5 条规则（0911 点点第三批 D）。 */}
              {groupByDay(visibleRunRecords, (run) => run.training_at ?? run.created_at).map((group, groupIndex) => (
                <Fragment key={`${group.label}-${groupIndex}`}>
                  <div className="task4-day-label">{group.label} · {group.items.length}条</div>
                  {group.items.map((run) => (
                    <RunRow
                      disabled={run.supported_input_modes.length === 0}
                      key={run.run_ref}
                      onToggle={toggleRun}
                      run={run}
                      selected={selectedRunIds.includes(run.id)}
                    />
                  ))}
                </Fragment>
              ))}
              {filteredSections.runRecords.length > MAX_VISIBLE_RUNS ? (
                <button
                  className="task4-expand-toggle"
                  onClick={() => toggleRunExpand("records")}
                  type="button"
                >
                  {runExpand.records ? "收起" : `显示全部 ${filteredSections.runRecords.length} 条`}
                </button>
              ) : null}
            </div>
          )
        ) : null}
      </section>
        </div>
        </div>
      </div>
  );
}
