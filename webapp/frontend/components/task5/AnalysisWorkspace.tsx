"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getSession, retrySession } from "@/lib/api";
import {
  getAnalysisViewState,
  presentAnalysisWorkspace,
  type AnalysisViewState,
} from "@/lib/contracts";
import type { SessionStatus } from "@/lib/types";
import { Badge, Button, ErrorState, Loading, Notice, Status, Tabs } from "@/ui/primitives";

import { DataView } from "./DataView";
import { DiagnosisView } from "./DiagnosisView";
import styles from "./task5.module.css";
import { VideoView } from "./VideoView";

type WorkspaceTab = "diagnosis" | "video" | "data";

const FAMILY_STATUS_LABEL = {
  supported: "正式支持",
  descriptive: "描述性结果",
  unavailable: "暂不可用",
  "outcome-only": "仅结果层",
} as const;

function errorStatus(error: unknown): number | null {
  if (!(error instanceof Error)) return null;
  const match = /^ApiError_(\d{3})$/.exec(error.name);
  return match ? Number(match[1]) : null;
}

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

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN");
}

export function AnalysisWorkspace() {
  const params = useParams<{ analysisId: string }>();
  const router = useRouter();
  const analysisId = Number(params.analysisId);
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadErrorStatus, setLoadErrorStatus] = useState<number | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [tab, setTab] = useState<WorkspaceTab>("diagnosis");
  const [selectedIssue, setSelectedIssue] = useState<number | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [playheadMs, setPlayheadMs] = useState(0);

  const load = useCallback(async (showLoading: boolean) => {
    if (!Number.isSafeInteger(analysisId) || analysisId <= 0) {
      setLoadErrorStatus(404);
      setLoading(false);
      return;
    }
    if (showLoading) setLoading(true);
    try {
      const next = await getSession(analysisId);
      setSession(next);
      setLoadErrorStatus(null);
    } catch (error) {
      setLoadErrorStatus(errorStatus(error) ?? 503);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [analysisId]);

  useEffect(() => {
    void load(true);
  }, [load]);

  useEffect(() => {
    if (session?.status !== "queued" && session?.status !== "running" && session?.status !== "uploading") {
      return undefined;
    }
    const timer = window.setInterval(() => void load(false), 2000);
    return () => window.clearInterval(timer);
  }, [load, session?.status]);

  const viewState = getAnalysisViewState({
    loading,
    session,
    errorStatus: loadErrorStatus,
  });
  const presentation = useMemo(
    () => session ? presentAnalysisWorkspace(session) : null,
    [session],
  );

  const retry = async () => {
    if (!session || retrying) return;
    setRetrying(true);
    try {
      const next = await retrySession(session.id, { idempotencyKey: crypto.randomUUID() });
      if (next.id !== session.id) {
        router.push(`/analysis/${next.id}`);
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
    document.querySelector<HTMLButtonElement>(".task3-toolbar-action")?.click();
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
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">← 返回历史</Link>
          <Status tone="info">{stateLabel(viewState)}</Status>
        </header>
        <Loading>{viewState === "queued" ? "Analysis 正在等待处理" : "Analysis 正在生成确定性结果"}</Loading>
        <Notice title="进度来自真实任务状态">这里不显示推测百分比。可前往任务中心查看当前真实阶段，离开页面不会中断任务。</Notice>
        <Button href="/tasks" variant="secondary">查看任务中心</Button>
      </div>
    );
  }

  if (viewState === "failed" || viewState === "retryable") {
    return (
      <div className={styles.page}>
        <header className={styles.pendingHeader}>
          <Link href="/history">← 返回历史</Link>
          <Status tone={stateTone(viewState)}>{stateLabel(viewState)}</Status>
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

  return (
    <div className={styles.workspace}>
      <header className={styles.analysisHeader}>
        <div className={styles.headerTopline}>
          <Link className={styles.backLink} href="/history">← 返回历史</Link>
          <Status tone={stateTone(viewState)}>{stateLabel(viewState)}</Status>
        </div>
        <div className={styles.titleRow}>
          <div>
            <p className={styles.eyebrow}>Analysis #{presentation.analysisId}</p>
            <h1>{presentation.scenario}</h1>
            <p className={styles.timestamp}>{formatDate(presentation.createdAt)}</p>
          </div>
          <div className={styles.titleBadges} aria-label="分析合同摘要">
            <Badge tone={presentation.input.preview ? "warning" : "neutral"}>{presentation.input.label}</Badge>
            {presentation.input.preview ? <Badge tone="warning">预览 / 实验</Badge> : null}
            <Badge tone={presentation.family.status === "supported" ? "success" : "warning"}>
              {presentation.family.label} · {FAMILY_STATUS_LABEL[presentation.family.status]}
            </Badge>
            <Badge tone={presentation.partial ? "warning" : "neutral"}>
              {presentation.evidence.filter((item) => item.availability === "available").length}/{presentation.evidence.length} 来源可用
            </Badge>
          </div>
        </div>
        <Tabs
          aria-label="Analysis 视图"
          className={styles.titleTabs}
          items={[
            { value: "diagnosis", label: "诊断" },
            { value: "video", label: "视频" },
            { value: "data", label: "数据" },
          ]}
          onValueChange={(value) => setTab(value as WorkspaceTab)}
          value={tab}
        />
      </header>

      {presentation.partial ? (
        <Notice tone="warning" title="视觉结果部分不可用">
          输入原生结果仍然保留；页面不会用视觉失败覆盖已经成立的 native 事实。
        </Notice>
      ) : null}

      <div className={styles.view} role="tabpanel">
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
            onSelectSegment={setSelectedSegment}
            presentation={presentation}
            selectedIssue={selectedIssue}
            selectedSegment={selectedSegment}
          />
        ) : null}
        {tab === "data" ? (
          <DataView
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
