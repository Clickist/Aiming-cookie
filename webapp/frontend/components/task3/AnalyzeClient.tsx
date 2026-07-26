"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  analyzeKovaakRun,
  getCaptureStatus,
  importDesktopPaths,
  listKovaakRuns,
  uploadVideo,
} from "@/lib/api";
import { buildRunAnalysisRequest, getRunModeAvailability } from "@/lib/contracts";
import { parseKovaaKConfig } from "@/lib/csv";
import {
  isDesktopRuntime,
  pickDesktopCsvPath,
  pickDesktopVideoPath,
  setDesktopCaptureEnabled,
} from "@/lib/desktop";
import type { CalibrationValues, CaptureStatusV1, InputMode, KovaaKRunListItem } from "@/lib/types";
import { Button, Empty, ErrorState, Field, FieldControl, Loading, Notice, Panel, Status } from "@/ui/primitives";

import { EvidenceChip, PageHeading, PreviewBadge } from "./Task3Shared";

const MODE_COPY: Record<InputMode, { title: string; need: string; get: string; limit: string }> = {
  input_native: {
    title: "Input-native",
    need: "Stats + Performance + Raw Input + 可用对齐",
    get: "输入事件、运动学与确定性诊断",
    limit: "没有视频回放或视觉结论",
  },
  multimodal: {
    title: "Multimodal",
    need: "完整 native evidence + MP4",
    get: "native 结果，并增加视频定位与视觉校验",
    limit: "视觉失败时只保留 native 结果",
  },
  video_fallback: {
    title: "Video fallback",
    need: "MP4 + 对应 Stats",
    get: "视频 CV 诊断与回放",
    limit: "没有 Raw Input provenance",
  },
};

function positiveNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
}

function capturePresentation(status: CaptureStatusV1 | null): { label: string; tone: "neutral" | "info" | "success" | "warning" | "error"; detail: string } {
  if (!status || status.availability === "unavailable") {
    return { label: "状态不可用", tone: "error", detail: "没有把读取失败伪装成未启用。" };
  }
  if (status.runtime_health === "unavailable") {
    return { label: "采集失败", tone: "error", detail: "桌面采集运行时不可用，请重试。" };
  }
  if (!status.capture_enabled) {
    return { label: "未启用", tone: "neutral", detail: "可启用自动采集，或使用下方手动 fallback。" };
  }
  if (status.finalization_state === "finalizing") {
    return { label: "正在整理", tone: "info", detail: "正在关联 Stats、Performance、Raw Input 与视频。" };
  }
  if (status.kovaak_process_present && status.replay_buffer_active) {
    return { label: "采集中", tone: "warning", detail: "KovaaK 已检测到，回放缓冲与输入采集正在运行。" };
  }
  if (status.runs.some((run) => run.raw_attached || run.video_attached)) {
    return { label: "已完成", tone: "success", detail: "检测到已整理的 Run，请在下方确认。" };
  }
  return { label: "待命", tone: "info", detail: "等待 KovaaK 进程与下一局 Challenge。" };
}

export function AnalyzeClient() {
  const router = useRouter();
  const [desktop, setDesktop] = useState(false);
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [runsLoading, setRunsLoading] = useState(desktop);
  const [runsError, setRunsError] = useState(false);
  const [captureStatus, setCaptureStatus] = useState<CaptureStatusV1 | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [inputMode, setInputMode] = useState<InputMode | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [statsFile, setStatsFile] = useState<File | null>(null);
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [statsPath, setStatsPath] = useState<string | null>(null);
  const [manualCm, setManualCm] = useState("");
  const [manualFov, setManualFov] = useState("");
  const [statsFov, setStatsFov] = useState<number | null>(null);

  useEffect(() => {
    setDesktop(isDesktopRuntime());
  }, []);

  const loadDesktopState = useCallback(async () => {
    if (!desktop) return;
    setRunsLoading(true);
    setRunsError(false);
    const [runResult, captureResult] = await Promise.allSettled([
      listKovaakRuns(),
      getCaptureStatus(),
    ]);
    if (runResult.status === "fulfilled") {
      const pending = runResult.value.runs.filter((run) => run.readiness_state === "pending_analysis");
      setRuns(pending);
      setSelectedRunId(pending.length === 1 ? pending[0].id : null);
    } else {
      setRunsError(true);
    }
    setCaptureStatus(captureResult.status === "fulfilled" ? captureResult.value : null);
    setRunsLoading(false);
  }, [desktop]);

  useEffect(() => {
    void loadDesktopState();
  }, [loadDesktopState]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  useEffect(() => {
    if (selectedRun && inputMode && !selectedRun.supported_input_modes.includes(inputMode)) {
      setInputMode(null);
    }
  }, [inputMode, selectedRun]);

  const calibration = (): CalibrationValues | undefined => {
    const cm = positiveNumber(manualCm);
    const fov = positiveNumber(manualFov);
    return cm || fov ? { cm_per_360: cm, fov } : undefined;
  };

  const startRunAnalysis = async () => {
    if (!selectedRun || !inputMode || !getRunModeAvailability(selectedRun, inputMode).available) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await analyzeKovaakRun(
        selectedRun.id,
        buildRunAnalysisRequest({ inputMode, manualOverride: calibration() }),
        { idempotencyKey: crypto.randomUUID() },
      );
      router.push("/tasks");
    } catch {
      setSubmitError("分析任务未创建。Run 仍保持待分析状态，请检查来源后重试。");
      setSubmitting(false);
    }
  };

  const startManualFallback = async () => {
    const ready = desktop ? Boolean(videoPath && statsPath) : Boolean(videoFile && statsFile);
    if (!ready) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      if (desktop && videoPath && statsPath) {
        await importDesktopPaths({
          videoPath,
          csvPath: statsPath,
          manualOverride: calibration(),
        });
      } else if (videoFile && statsFile) {
        await uploadVideo(videoFile, {
          csv: statsFile,
          manualOverride: calibration(),
        });
      }
      router.push("/tasks");
    } catch {
      setSubmitError("手动来源未通过合同校验。请确认同时选择匹配的 MP4 与 Stats CSV。");
      setSubmitting(false);
    }
  };

  const chooseStatsFile = async (file: File | null) => {
    setStatsFile(file);
    setStatsFov(null);
    if (!file) return;
    try {
      setStatsFov(parseKovaaKConfig(await file.text()).fov ?? null);
    } catch {
      setStatsFov(null);
    }
  };

  const enableCapture = async () => {
    setSubmitError("");
    try {
      await setDesktopCaptureEnabled(true);
      await loadDesktopState();
    } catch {
      setSubmitError("自动采集未能启用，没有把失败状态伪装成成功。");
    }
  };

  const capture = capturePresentation(captureStatus);
  const manualReady = desktop ? Boolean(videoPath && statsPath) : Boolean(videoFile && statsFile);

  return (
    <div className="task3-page task3-analyze-page">
      <PageHeading
        actions={desktop ? <Button onClick={() => void loadDesktopState()} variant="secondary">刷新 Run</Button> : undefined}
        description="确认一条自动整理的 Run，或使用独立的 MP4 + Stats fallback。"
        eyebrow="New Analysis"
        title="新建分析"
      />

      <section className="task3-capture-strip" aria-labelledby="capture-status-title">
        <div>
          <span className="task3-section-kicker">自动采集</span>
          <h2 id="capture-status-title">{desktop ? "桌面采集状态" : "浏览器预览"}</h2>
        </div>
        <Status tone={desktop ? capture.tone : "neutral"}>{desktop ? capture.label : "桌面能力不可用"}</Status>
        <p>{desktop ? capture.detail : "浏览器不伪造 Run discovery、Raw Input、回放缓冲或 launch-token 状态。"}</p>
        {desktop && captureStatus?.capture_enabled === false ? <Button onClick={() => void enableCapture()} variant="secondary">启用自动采集</Button> : null}
      </section>

      <section className="task3-section" aria-labelledby="pending-runs-title">
        <div className="task3-section-heading">
          <div><span className="task3-section-kicker">Pending Run</span><h2 id="pending-runs-title">选择本次训练</h2></div>
          {runs.length > 1 ? <span>{runs.length} 条待确认 · 必须选择一条</span> : null}
        </div>
        {!desktop ? (
          <Empty title="浏览器中不读取桌面 Run">请使用下方手动 fallback；桌面版会在这里列出自动整理的训练。</Empty>
        ) : runsLoading ? (
          <Loading>正在读取待分析 Run</Loading>
        ) : runsError ? (
          <ErrorState title="Run 列表暂时不可用"><Button onClick={() => void loadDesktopState()} variant="secondary">重试</Button></ErrorState>
        ) : runs.length === 0 ? (
          <Empty title="还没有待分析的训练">完成一局 KovaaK Challenge 后再刷新，或使用下方手动 fallback。</Empty>
        ) : (
          <div className="task3-run-list">
            {runs.map((run) => (
              <label className="task3-run-item" data-selected={selectedRunId === run.id || undefined} key={run.run_ref}>
                <input checked={selectedRunId === run.id} name="run" onChange={() => { setSelectedRunId(run.id); setInputMode(null); }} type="radio" />
                <span className="task3-run-main">
                  <strong>{run.scenario || "未知场景"}</strong>
                  <small>{new Date(run.created_at).toLocaleString("zh-CN")}</small>
                </span>
                <span className="task3-evidence-row">
                  <EvidenceChip label="Stats" state={run.evidence_availability.stats ?? run.source_availability.stats} />
                  <EvidenceChip label="Performance" state={run.evidence_availability.performance ?? run.source_availability.performance} />
                  <EvidenceChip label="Raw" state={run.evidence_availability.raw ?? run.trace_quality.availability} />
                  <EvidenceChip label="视频" state={run.evidence_availability.mp4 ?? run.evidence_availability.video} />
                </span>
                {run.limitations.length ? <span className="task3-run-issue">来源限制：{run.limitations.join("、")}</span> : null}
              </label>
            ))}
          </div>
        )}
      </section>

      {selectedRun ? (
        <section className="task3-section" aria-labelledby="mode-title">
          <div className="task3-section-heading"><div><span className="task3-section-kicker">Input mode</span><h2 id="mode-title">选择分析模式</h2></div></div>
          <div className="task3-mode-grid">
            {(Object.keys(MODE_COPY) as InputMode[]).map((mode) => {
              const availability = getRunModeAvailability(selectedRun, mode);
              const copy = MODE_COPY[mode];
              return (
                <label className="task3-mode-card" data-disabled={!availability.available || undefined} data-selected={inputMode === mode || undefined} key={mode}>
                  <input checked={inputMode === mode} disabled={!availability.available} name="input-mode" onChange={() => setInputMode(mode)} type="radio" />
                  <span className="task3-mode-title"><strong>{copy.title}</strong>{mode === "input_native" ? <PreviewBadge /> : null}</span>
                  <span><b>需要</b>{copy.need}</span>
                  <span><b>得到</b>{copy.get}</span>
                  <span><b>不能得到</b>{copy.limit}</span>
                  {!availability.available ? <small>后端合同未将此模式列为可用。{availability.limitations[0] ? ` ${availability.limitations[0]}` : ""}</small> : null}
                </label>
              );
            })}
          </div>
          <div className="task3-calibration-row">
            <Field hint="Stats 自动读取优先；只有该字段未读取到时才采用本局覆盖。" label="本局 cm/360 覆盖（可选）">
              <FieldControl inputMode="decimal" onChange={(event) => setManualCm(event.target.value)} placeholder="例如 42" value={manualCm} />
            </Field>
            <Field hint="Stats 自动读取优先；留空表示不覆盖。" label="本局 FOV 覆盖（可选）">
              <FieldControl inputMode="decimal" onChange={(event) => setManualFov(event.target.value)} placeholder="例如 103" value={manualFov} />
            </Field>
          </div>
          <div className="task3-submit-row">
            <span>未选择的 Run 会继续保留为待分析，不合并也不自动删除。</span>
            <Button disabled={!inputMode || submitting} onClick={() => void startRunAnalysis()}>{submitting ? "正在创建" : "开始分析"}</Button>
          </div>
        </section>
      ) : null}

      <section className="task3-manual-fallback" aria-labelledby="manual-title">
        <div className="task3-section-heading">
          <div><span className="task3-section-kicker">Manual fallback</span><h2 id="manual-title">手动选择 MP4 + Stats</h2></div>
          <span>不会根据视频猜测 CSV</span>
        </div>
        <p>这是独立的 video-fallback 路径，不要求 Raw Input，也不会伪造 native provenance。</p>
        <div className="task3-drop-grid">
          <Panel title="MP4 录像" tone="recessed">
            {desktop ? (
              <><p>{videoPath ? "已选择桌面文件" : "尚未选择录像"}</p><Button onClick={() => void pickDesktopVideoPath().then(setVideoPath)} variant="secondary">选择 MP4</Button></>
            ) : (
              <input accept="video/mp4" aria-label="选择 MP4 录像" onChange={(event) => setVideoFile(event.target.files?.[0] ?? null)} type="file" />
            )}
          </Panel>
          <Panel title="KovaaK Stats CSV" tone="recessed">
            {desktop ? (
              <><p>{statsPath ? "已选择桌面文件" : "尚未选择 Stats"}</p><Button onClick={() => void pickDesktopCsvPath().then(setStatsPath)} variant="secondary">选择 Stats</Button></>
            ) : (
              <><input accept=".csv,text/csv" aria-label="选择 Stats CSV" onChange={(event) => void chooseStatsFile(event.target.files?.[0] ?? null)} type="file" />{statsFov ? <small>Stats 中检测到 FOV {statsFov}，提交后由后端按优先级采用。</small> : null}</>
            )}
          </Panel>
        </div>
        <div className="task3-submit-row">
          <span>必须同时选择匹配的 MP4 与 Stats，合同校验通过后才能开始。</span>
          <Button disabled={!manualReady || submitting} onClick={() => void startManualFallback()}>{submitting ? "正在创建" : "开始 video fallback"}</Button>
        </div>
      </section>

      {captureStatus?.pause_fail_closed ? <Notice tone="warning" title="检测到暂停局">该 Run 按 fail-closed 处理，不会被当作完整训练证据。</Notice> : null}
      {submitError ? <Notice tone="error">{submitError}</Notice> : null}
    </div>
  );
}
