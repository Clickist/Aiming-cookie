"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getSession, retrySession } from "@/lib/api";
import {
  ANALYSIS_AUTO_TEACH_EVENT,
  COACH_PENDING_INTENT_KEY,
  getAnalysisViewState,
  presentAnalysisWorkspace,
  type AnalysisViewState,
} from "@/lib/contracts";
import { analysisHref, analysisIdFromLocation } from "@/lib/navigation";
import { t, useT, type MessageKey } from "@/lib/i18n";
import type { SessionStatus } from "@/lib/types";
import { Badge, Button, ErrorState, Loading, Notice, Tabs } from "@/ui/primitives";

import { DataView } from "./DataView";
import { DiagnosisView } from "./DiagnosisView";
import styles from "./task5.module.css";
import { VideoView } from "./VideoView";

type WorkspaceTab = "diagnosis" | "video" | "data";
const ANALYSIS_TABS_ID = "analysis-view-tabs";
const ANALYSIS_PANEL_ID = "analysis-view-panel";
let cachedDoneSession: SessionStatus | null = null;

// i18n 批 3：映射值是字典键（MessageKey），调用时经 t() 解析（§2c）。
const FAMILY_STATUS_KEYS = {
  supported: "analysis.familyStatus.supported",
  descriptive: "analysis.familyStatus.descriptive",
  unavailable: "analysis.familyStatus.unavailable",
  "outcome-only": "analysis.familyStatus.outcomeOnly",
} as const satisfies Record<string, MessageKey>;

const EVIDENCE_SOURCE_KEYS: Record<string, MessageKey | undefined> = {
  raw: "analysis.evidence.raw",
  performance: "analysis.evidence.performance",
  stats: "analysis.evidence.stats",
  video: "analysis.evidence.video",
  mp4: "analysis.evidence.mp4",
  visual: "analysis.evidence.visual",
};

function errorStatus(error: unknown): number | null {
  if (!(error instanceof Error)) return null;
  const match = /^ApiError_(\d{3})$/.exec(error.name);
  return match ? Number(match[1]) : null;
}

const TASK_PHASE_KEYS: Record<string, MessageKey> = {
  preparing_training_record: "history.taskPhase.preparing",
  aligning_input_events: "history.taskPhase.aligning",
  computing_kinematics: "analysis.phase.kinematics",
  analyzing_video: "analysis.phase.video",
  generating_diagnostics: "history.taskPhase.diagnostics",
};

const STATE_KEYS: Record<AnalysisViewState, MessageKey> = {
  loading: "analysis.state.loading",
  queued: "history.status.queued",
  running: "history.taskState.running",
  done: "history.status.completed",
  failed: "analysis.state.failed",
  retryable: "analysis.state.retryable",
  "deleted-unavailable": "analysis.state.deletedUnavailable",
  unavailable: "analysis.state.unavailable",
};

function stateLabel(state: AnalysisViewState): string {
  return t(STATE_KEYS[state]);
}

function stateTone(state: AnalysisViewState): "neutral" | "info" | "success" | "warning" | "error" {
  if (state === "done") return "success";
  if (state === "queued" || state === "running") return "info";
  if (state === "retryable" || state === "deleted-unavailable") return "warning";
  if (state === "failed" || state === "unavailable") return "error";
  return "neutral";
}

export function evidenceSourceLabel(source: string): string {
  const normalized = source.toLowerCase();
  const key = EVIDENCE_SOURCE_KEYS[normalized];
  if (key !== undefined) return t(key);
  if (normalized.includes("raw")) return t("analysis.evidence.raw");
  if (normalized.includes("performance")) return t("analysis.evidence.performance");
  if (normalized.includes("stats")) return t("analysis.evidence.stats");
  if (normalized.includes("video") || normalized.includes("mp4") || normalized.includes("visual")) return t("analysis.evidence.video");
  return source;
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
  return t("analysis.duration.seconds", { n: seconds });
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
  const t = useT();
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
    const intent = { draft: t("analysis.coach.askCoreIssue") };
    try {
      window.sessionStorage.setItem(COACH_PENDING_INTENT_KEY, JSON.stringify(intent));
    } catch {
      // The event still supplies the draft when the Coach panel is already mounted.
    }
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-draft", { detail: intent }));
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-open"));
  };

  if (viewState === "loading") {
    return <div className={styles.page}><Loading>{t("analysis.loading.reading")}</Loading></div>;
  }

  if (viewState === "deleted-unavailable") {
    return (
      <div className={styles.page}>
        <ErrorState title={t("analysis.error.deletedTitle")}>
          <p>{t("analysis.error.deletedBody")}</p>
          <Button href="/history" variant="secondary">{t("analysis.error.backToHistory")}</Button>
        </ErrorState>
      </div>
    );
  }

  if (viewState === "unavailable") {
    return (
      <div className={styles.page}>
        <ErrorState title={t("analysis.error.unavailableTitle")}>
          <p>{t("analysis.error.unavailableBody")}</p>
          <Button onClick={() => void load(true)} variant="secondary">{t("analysis.error.retryReading")}</Button>
        </ErrorState>
      </div>
    );
  }

  if (viewState === "queued" || viewState === "running") {
    const phaseKey = session?.task_phase ? TASK_PHASE_KEYS[session.task_phase] : undefined;
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">{t("analysis.pending.backLink")}</Link>
          <Badge tone={stateTone(viewState)}>{stateLabel(viewState)}</Badge>
        </header>
        <Loading>
          {viewState === "queued"
            ? t("analysis.pending.queuedCopy")
            : phaseKey !== undefined ? t(phaseKey) : t("analysis.pending.runningCopy")}
        </Loading>
        <Notice tone="info" title={t("analysis.pending.progressTitle")}>{t("analysis.pending.progressBody")}</Notice>
        <Button href="/tasks" variant="secondary">{t("analysis.pending.viewTasks")}</Button>
      </div>
    );
  }

  if (viewState === "failed" || viewState === "retryable") {
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">{t("analysis.pending.backLink")}</Link>
          <Badge tone={stateTone(viewState)}>{stateLabel(viewState)}</Badge>
        </header>
        <ErrorState title={t("analysis.error.failedTitle")}>
          <p>{session?.error?.message ?? t("analysis.error.noFailureDetail")}</p>
          {viewState === "retryable" ? (
            <Button disabled={retrying} onClick={() => void retry()}>{retrying ? t("analysis.error.creatingAttempt") : t("analysis.error.retry")}</Button>
          ) : null}
        </ErrorState>
      </div>
    );
  }

  if (!presentation) {
    return (
      <div className={styles.page}>
        <ErrorState title={t("analysis.error.contractTitle")}>
          <p>{t("analysis.error.contractBody")}</p>
          <Button href="/history" variant="secondary">{t("analysis.error.backToHistory")}</Button>
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
          <Link className={styles.backLink} href="/history">{t("analysis.header.backLink")}</Link>
          <div className={styles.headerTitleWrap}>
            <div className={styles.titleLine}>
              <h1>{presentation.scenario}</h1>
              <div className={styles.headerBadges} aria-label={t("analysis.header.contractSummaryAria")}>
                <span className={styles.evidenceSummary}>
                  <button
                    aria-describedby="analysis-evidence-summary"
                    aria-label={t("analysis.header.evidenceAria")}
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
                {presentation.input.preview ? <Badge tone="warning">{t("analysis.header.previewBadge")}</Badge> : null}
                <Badge tone="neutral">{presentation.family.label} · {t(FAMILY_STATUS_KEYS[presentation.family.status])}</Badge>
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
          aria-label={t("analysis.tabs.ariaLabel")}
          className={styles.titleTabs}
          id={ANALYSIS_TABS_ID}
          items={[
            { value: "diagnosis", label: t("analysis.tabs.diagnosis") },
            { value: "video", label: t("analysis.tabs.video") },
            { value: "data", label: t("analysis.tabs.data") },
          ]}
          onValueChange={(value) => setTab(value as WorkspaceTab)}
          panelId={ANALYSIS_PANEL_ID}
          value={tab}
        />
      </header>

      {presentation.partial ? (
        <Notice className={styles.partialNotice} tone="warning" title={t("analysis.notice.partialTitle")}>
          {t("analysis.notice.partialBody")}
        </Notice>
      ) : null}
      {loadWarning ? <Notice className={styles.partialNotice} tone="warning" title={t("analysis.notice.loadWarningTitle")}>{t("analysis.notice.loadWarningBody")}</Notice> : null}

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
