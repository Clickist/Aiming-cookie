"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getSession, retrySession } from "@/lib/api";
import {
  ANALYSIS_AUTO_TEACH_EVENT,
  getAnalysisViewState,
  presentAnalysisWorkspace,
  type AnalysisViewState,
} from "@/lib/contracts";
import { analysisHref, analysisIdFromLocation } from "@/lib/navigation";
import type { SessionStatus } from "@/lib/types";
import { Badge, Button, ErrorState, Loading, Notice, Tabs } from "@/ui/primitives";

import { DataView } from "./DataView";
import { DiagnosisView } from "./DiagnosisView";
import styles from "./task5.module.css";
import { VideoView } from "./VideoView";

type WorkspaceTab = "diagnosis" | "video" | "data";
const ANALYSIS_TABS_ID = "analysis-view-tabs";
const ANALYSIS_PANEL_ID = "analysis-view-panel";
const COACH_PENDING_INTENT_KEY = "aiming-cookie.ui.coach-pending-intent";
const ANALYSIS_COACH_DRAFT = "这次分析的核心问题是什么？";
let cachedDoneSession: SessionStatus | null = null;

const FAMILY_STATUS_LABEL = {
  supported: "正式支持",
  descriptive: "描述性结果",
  unavailable: "暂不可用",
  "outcome-only": "仅结果层",
} as const;

const EVIDENCE_SOURCE_LABELS: Record<string, string | undefined> = {
  raw: "Raw Input",
  performance: "Performance",
  stats: "Stats",
  video: "视频",
  mp4: "视频",
  visual: "视觉",
};

function errorStatus(error: unknown): number | null {
  if (!(error instanceof Error)) return null;
  const match = /^ApiError_(\d{3})$/.exec(error.name);
  return match ? Number(match[1]) : null;
}

const TASK_PHASE_COPY: Record<string, string> = {
  preparing_training_record: "准备训练记录",
  aligning_input_events: "对齐输入事件",
  computing_kinematics: "计算运动指标",
  analyzing_video: "视频分析中：逐帧识别靶子，约需 1-2 分钟（视时长而定）",
  generating_diagnostics: "生成诊断",
};

function stateLabel(state: AnalysisViewState): string {
  return {
    loading: "正在读取",
    queued: "排队中",
    running: "分析中",
    done: "已完成",
    failed: "分析失败",
    retryable: "可重试",
    "deleted-unavailable": "引用不可用",
    unavailable: "服务不可用",
  }[state];
}

function stateTone(state: AnalysisViewState): "neutral" | "info" | "success" | "warning" | "error" {
  if (state === "done") return "success";
  if (state === "queued" || state === "running") return "info";
  if (state === "retryable" || state === "deleted-unavailable") return "warning";
  if (state === "failed" || state === "unavailable") return "error";
  return "neutral";
}

function evidenceSourceLabel(source: string): string {
  const normalized = source.toLowerCase();
  return EVIDENCE_SOURCE_LABELS[normalized]
    ?? (normalized.includes("raw") ? "Raw Input"
      : normalized.includes("performance") ? "Performance"
        : normalized.includes("stats") ? "Stats"
          : normalized.includes("video") || normalized.includes("mp4") || normalized.includes("visual") ? "视频"
            : source);
}

function evidenceChipState(availability: string): "ok" | "part" | "miss" {
  if (availability === "available") return "ok";
  if (availability === "limited") return "part";
  return "miss";
}

function formatDuration(session: SessionStatus): string | null {
  const start = session.started_at ?? session.created_at;
  const end = session.finished_at;
  if (!start || !end) return null;
  const diff = new Date(end).valueOf() - new Date(start).valueOf();
  if (!Number.isFinite(diff) || diff < 0) return null;
  const seconds = Math.round(diff / 1000);
  return `${seconds} 秒`;
}

function isCoachLocator(value: unknown): value is { view: WorkspaceTab; relative_start_ms?: number } {
  if (!value || typeof value !== "object") return false;
  const locator = value as { view?: unknown; relative_start_ms?: unknown };
  if (locator.view !== "diagnosis" && locator.view !== "video" && locator.view !== "data") return false;
  return locator.relative_start_ms === undefined
    || (typeof locator.relative_start_ms === "number" && Number.isFinite(locator.relative_start_ms) && locator.relative_start_ms >= 0);
}

export function AnalysisWorkspace() {
  const pathname = usePathname();
  const router = useRouter();
  const search = typeof window === "undefined" ? "" : window.location.search;
  const analysisId = analysisIdFromLocation(pathname, search);
  const cachedSession = cachedDoneSession?.id === analysisId ? cachedDoneSession : null;
  const [session, setSession] = useState<SessionStatus | null>(cachedSession);
  const [loading, setLoading] = useState(cachedSession === null);
  const [loadErrorStatus, setLoadErrorStatus] = useState<number | null>(null);
  const [loadWarning, setLoadWarning] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [tab, setTab] = useState<WorkspaceTab>("diagnosis");
  const [selectedIssue, setSelectedIssue] = useState<number | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [playheadMs, setPlayheadMs] = useState(0);
  const sessionRef = useRef<SessionStatus | null>(cachedSession);

  const load = useCallback(async (showLoading: boolean) => {
    if (analysisId === null) {
      setLoadErrorStatus(404);
      setLoading(false);
      return;
    }
    if (showLoading && sessionRef.current === null) setLoading(true);
    try {
      const next = await getSession(analysisId);
      setSession(next);
      sessionRef.current = next;
      if (next.status === "done") cachedDoneSession = next;
      setLoadErrorStatus(null);
      setLoadWarning(false);
    } catch (error) {
      const status = errorStatus(error) ?? 503;
      if (sessionRef.current?.status === "done" && status !== 404 && status !== 410) {
        setLoadWarning(true);
        setLoadErrorStatus(null);
      } else {
        if (status === 404 || status === 410) {
          if (cachedDoneSession?.id === analysisId) cachedDoneSession = null;
          setSession(null);
          sessionRef.current = null;
        }
        setLoadErrorStatus(status);
      }
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [analysisId]);

  useEffect(() => {
    void load(true);
  }, [load]);

  useEffect(() => {
    if (!loadWarning && session?.status !== "queued" && session?.status !== "running" && session?.status !== "uploading") {
      return undefined;
    }
    const timer = window.setInterval(() => void load(false), 2000);
    return () => window.clearInterval(timer);
  }, [load, loadWarning, session?.status]);

  // 分析完成自动开讲：只在「本页活体观察到」同一 Analysis 的非终态 → done
  // 转换时派发一次事件（AppShell 在 Provider 可用时创建 Coach run）。直接打开
  // 已完成的分析不触发，避免翻旧记录时重复开讲。
  const prevStatusRef = useRef<{ id: number | null; status: string | null }>({ id: null, status: null });
  useEffect(() => {
    const status = session?.status ?? null;
    const previous = prevStatusRef.current;
    prevStatusRef.current = { id: analysisId, status };
    if (
      status === "done" && analysisId !== null
      && previous.id === analysisId
      && previous.status !== null && previous.status !== "done"
      && typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent(ANALYSIS_AUTO_TEACH_EVENT, {
        detail: { analysis_ref: `analysis:${analysisId}` },
      }));
    }
  }, [analysisId, session?.status]);

  const viewState = getAnalysisViewState({
    loading,
    session,
    errorStatus: loadErrorStatus,
  });
  const presentation = useMemo(
    () => session ? presentAnalysisWorkspace(session) : null,
    [session],
  );

  useEffect(() => {
    if (viewState !== "done" || !presentation) return undefined;
    const locateCoachContext = (event: Event) => {
      const locator = (event as CustomEvent<unknown>).detail;
      if (!isCoachLocator(locator)) return;
      setTab(locator.view);
      if (locator.view === "video" && locator.relative_start_ms !== undefined) {
        setPlayheadMs(locator.relative_start_ms);
      }
      event.preventDefault();
    };
    window.addEventListener("aiming-cookie:coach-locate", locateCoachContext);
    return () => window.removeEventListener("aiming-cookie:coach-locate", locateCoachContext);
  }, [presentation, viewState]);

  const retry = async () => {
    if (!session || retrying) return;
    setRetrying(true);
    try {
      const next = await retrySession(session.id, { idempotencyKey: crypto.randomUUID() });
      if (next.id !== session.id) {
        router.push(analysisHref(next.id));
      } else {
        setSession(next);
        setLoadErrorStatus(null);
      }
    } catch {
      setLoadErrorStatus(503);
    } finally {
      setRetrying(false);
    }
  };

  const openCoach = () => {
    const intent = { draft: ANALYSIS_COACH_DRAFT };
    try {
      window.sessionStorage.setItem(COACH_PENDING_INTENT_KEY, JSON.stringify(intent));
    } catch {
      // The event still supplies the draft when the Coach panel is already mounted.
    }
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-draft", { detail: intent }));
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-open"));
  };

  if (viewState === "loading") {
    return <div className={styles.page}><Loading>正在读取 Analysis</Loading></div>;
  }

  if (viewState === "deleted-unavailable") {
    return (
      <div className={styles.page}>
        <ErrorState title="这条 Analysis 已删除或不可用">
          <p>历史消息可以保留，但不再把其中的引用当作可用证据。</p>
          <Button href="/history" variant="secondary">返回历史</Button>
        </ErrorState>
      </div>
    );
  }

  if (viewState === "unavailable") {
    return (
      <div className={styles.page}>
        <ErrorState title="Analysis 暂时不可用">
          <p>读取失败没有被显示成空结果。恢复本地服务后可以重试。</p>
          <Button onClick={() => void load(true)} variant="secondary">重试读取</Button>
        </ErrorState>
      </div>
    );
  }

  if (viewState === "queued" || viewState === "running") {
    const phaseCopy = session?.task_phase ? TASK_PHASE_COPY[session.task_phase] : undefined;
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">← 返回历史</Link>
          <Badge tone={stateTone(viewState)}>{stateLabel(viewState)}</Badge>
        </header>
        <Loading>
          {viewState === "queued"
            ? "Analysis 正在等待处理"
            : phaseCopy ?? "Analysis 正在生成确定性结果"}
        </Loading>
        <Notice tone="info" title="进度来自真实任务状态">这里不显示推测百分比。可前往任务中心查看当前真实阶段，离开页面不会中断任务。</Notice>
        <Button href="/tasks" variant="secondary">查看任务中心</Button>
      </div>
    );
  }

  if (viewState === "failed" || viewState === "retryable") {
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">← 返回历史</Link>
          <Badge tone={stateTone(viewState)}>{stateLabel(viewState)}</Badge>
        </header>
        <ErrorState title="Analysis 没有完成">
          <p>{session?.error?.message ?? "后端未提供可公开的失败说明。"}</p>
          {viewState === "retryable" ? (
            <Button disabled={retrying} onClick={() => void retry()}>{retrying ? "正在创建新 attempt" : "重试"}</Button>
          ) : null}
        </ErrorState>
      </div>
    );
  }

  if (!presentation) {
    return (
      <div className={styles.page}>
        <ErrorState title="Analysis 合同版本不受支持">
          <p>当前页面只呈现安全的 Analysis v2 投影，不会猜测旧结果字段。</p>
          <Button href="/history" variant="secondary">返回历史</Button>
        </ErrorState>
      </div>
    );
  }

  const durationText = session ? formatDuration(session) : null;
  const evidenceItems = presentation.evidence.map((item) => ({
    ...item,
    label: evidenceSourceLabel(item.source),
    state: evidenceChipState(item.availability),
  }));
  const evidenceChips = evidenceItems.map((item) => (
    <span className={styles.evidenceChip} data-state={item.state} key={item.source}>
      <i>{item.state === "ok" ? "✓" : item.state === "part" ? "～" : "×"}</i>
      {item.label}
    </span>
  ));

  return (
    <div className={styles.workspace}>
      <header className={styles.analysisHeader}>
        <div className={styles.headerRow}>
          <Link className={styles.backLink} href="/history">← 历史</Link>
          <div className={styles.headerTitleWrap}>
            <div className={styles.titleLine}>
              <h1>{presentation.scenario}</h1>
              <div className={styles.headerBadges} aria-label="分析合同摘要">
                <span className={styles.evidenceSummary}>
                  <button
                    aria-describedby="analysis-evidence-summary"
                    aria-label="查看本次分析证据"
                    className={styles.evidenceTrigger}
                    type="button"
                  >
                    <Badge tone="success"><span className={styles.statusDot} />{stateLabel(viewState)}</Badge>
                  </button>
                  <span className={styles.evidenceTooltip} id="analysis-evidence-summary" role="tooltip">
                    {evidenceChips}
                  </span>
                </span>
                <Badge tone="info">{presentation.input.label}</Badge>
                {presentation.input.preview ? <Badge tone="warning">预览 / 实验</Badge> : null}
                <Badge tone="neutral">{presentation.family.label} · {FAMILY_STATUS_LABEL[presentation.family.status]}</Badge>
              </div>
            </div>
            <div className={styles.headerSubline}>
              {presentation.recordLabel}
              {durationText ? ` · ${durationText}` : null}
              {presentation.calibration.cmPer360 ? ` · ${presentation.calibration.cmPer360} cm/360` : null}
              {presentation.calibration.fov ? ` · FOV ${presentation.calibration.fov}` : null}
            </div>
          </div>
        </div>

        <Tabs
          aria-label="Analysis 视图"
          className={styles.titleTabs}
          id={ANALYSIS_TABS_ID}
          items={[
            { value: "diagnosis", label: "诊断" },
            { value: "video", label: "视频" },
            { value: "data", label: "数据" },
          ]}
          onValueChange={(value) => setTab(value as WorkspaceTab)}
          panelId={ANALYSIS_PANEL_ID}
          value={tab}
        />
      </header>

      {presentation.partial ? (
        <Notice className={styles.partialNotice} tone="warning" title="视觉结果部分不可用">
          输入原生结果仍然保留；页面不会用视觉失败覆盖已经成立的 native 事实。
        </Notice>
      ) : null}
      {loadWarning ? <Notice className={styles.partialNotice} tone="warning" title="分析服务刚刚短暂不可用">已保存的分析仍在本机，当前页面保留已读取内容；服务恢复后会自动刷新。</Notice> : null}

      <div
        aria-labelledby={`${ANALYSIS_TABS_ID}-${tab}-tab`}
        className={styles.view}
        id={ANALYSIS_PANEL_ID}
        role="tabpanel"
      >
        {tab === "diagnosis" ? (
          <DiagnosisView
            onAskCoach={openCoach}
            onSelectEvidence={(issueIndex) => {
              setSelectedIssue(issueIndex);
              setTab("video");
            }}
            onSelectMetric={(metric) => {
              setSelectedMetric(metric);
              setTab("data");
            }}
            presentation={presentation}
            selectedIssue={selectedIssue}
          />
        ) : null}
        {tab === "video" ? (
          <VideoView
            analysisId={presentation.analysisId}
            currentTimeMs={playheadMs}
            onCurrentTimeChange={setPlayheadMs}
            presentation={presentation}
          />
        ) : null}
        {tab === "data" ? (
          <DataView
            onSelectMetric={(metric) => {
              setSelectedMetric(metric);
              setTab("data");
            }}
            onSelectTime={(timeMs) => {
              setPlayheadMs(timeMs);
              setTab("video");
            }}
            presentation={presentation}
            selectedMetric={selectedMetric}
          />
        ) : null}
      </div>
    </div>
  );
}
