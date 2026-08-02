"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  analyzeKovaakRun,
  getCaptureStatus,
  importDesktopPaths,
  listKovaakRuns,
  uploadVideo,
} from "@/lib/api";
import {
  buildRunAnalysisRequest,
  getHistoryStatusText,
  getRunModeAvailability,
  isRunPauseFailClosed,
  presentRunInspector,
} from "@/lib/contracts";
import { parseKovaaKConfig } from "@/lib/csv";
import {
  isDesktopRuntime,
  pickDesktopCsvPath,
  pickDesktopVideoPath,
  setDesktopCaptureEnabled,
} from "@/lib/desktop";
import type { CalibrationValues, CaptureStatusV1, InputMode, KovaaKRunListItem } from "@/lib/types";
import { Badge, Button, Empty, ErrorState, Field, FieldControl, Loading, Notice, Panel } from "@/ui/primitives";

import { EvidenceChip, PageHeading, PreviewBadge } from "./Task3Shared";

const MODE_COPY: Record<InputMode, { title: string; desc: string; shortDesc: string }> = {
  input_native: {
    title: "输入原生",
    desc: "仅使用 Stats + Performance + Raw Input。\n不能得到：任何视觉、目标相对误差类结论。",
    shortDesc: "仅 Stats + Performance + Raw Input；不生成任何视觉结论。",
  },
  multimodal: {
    title: "多源模式",
    desc: "以 Raw Input 运动学为主结果，视频提供回放与视觉校验。\n能得到：全部输入运动学 + 可定位的视觉证据。",
    shortDesc: "Raw Input 运动学为主，视频提供回放与视觉校验。",
  },
  video_fallback: {
    title: "视频兼容",
    desc: "没有 Raw Input 时的兼容路径（Stats + MP4）。\n不能得到：输入原生测量；不作为长期主分析方向。",
    shortDesc: "无 Raw Input 时的兼容路径；不生成输入原生测量。",
  },
};

const MODE_SUMMARY: Record<InputMode, { full: string; short: string }> = {
  multimodal: {
    full: "输入运动学为主，视频负责回放与视觉校验；若视觉校验失败，输入原生结果仍然保留。",
    short: "视觉校验失败时保留输入原生结果",
  },
  input_native: {
    full: "预览 / 实验模式，仅使用原生输入数据。",
    short: "预览 / 实验模式",
  },
  video_fallback: {
    full: "没有 Raw Input 时的兼容路径，需要同时选择匹配的 MP4 + Stats。",
    short: "无 Raw Input 时的兼容路径",
  },
};

function positiveNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
}

function isEvidenceOk(state: string | undefined): boolean {
  const s = state ?? "missing";
  return s === "available" || s === "attached" || s === "aligned";
}

function isHistoryOk(value: string): boolean {
  return value === "可用" || value === "已关联" || value === "已对齐" || value === "已选择";
}

function evidenceChipText(label: string, state: string | undefined, limitations: string[]): string | undefined {
  if (isEvidenceOk(state)) return undefined;
  const limit = limitations.find((l) => l.toLowerCase().includes(label.toLowerCase()));
  if (limit) return limit;
  return `${label}${getHistoryStatusText(state)}`;
}

function capturePresentation(status: CaptureStatusV1 | null): {
  label: string;
  tone: "info" | "warning" | "error";
  detail: string;
} {
  if (!status || status.availability === "unavailable") {
    return { label: "状态不可用", tone: "error", detail: "没有把读取失败伪装成未启用。" };
  }
  if (status.runtime_health === "unavailable") {
    return { label: "采集失败", tone: "error", detail: "桌面采集运行时不可用，请重试。" };
  }
  if (!status.capture_enabled) {
    return { label: "未启用", tone: "info", detail: "可启用自动采集，或使用下方手动 fallback。" };
  }
  if (status.finalization_state === "finalizing") {
    return { label: "正在整理", tone: "info", detail: "正在关联 Stats、Performance、Raw Input 与视频。" };
  }
  if (status.kovaak_process_present && status.replay_buffer_active) {
    return { label: "采集中", tone: "warning", detail: "KovaaK 已检测到，回放缓冲与输入采集正在运行。" };
  }
  if (status.runs.some((run) => run.raw_attached || run.video_attached)) {
    return { label: "已完成", tone: "info", detail: "检测到已整理的 Run，请在下方确认。" };
  }
  return { label: "待命", tone: "info", detail: "等待 KovaaK 进程与下一局 Challenge。" };
}

interface ManualDropCardProps {
  title: string;
  accept: string;
  needText: string;
  selected: string | null;
  desktop: boolean;
  extra?: ReactNode;
  onSelect: () => void;
  onSelectFile: (file: File) => void;
  onClear: () => void;
}

function ManualDropCard({
  title,
  accept,
  needText,
  selected,
  desktop,
  extra,
  onSelect,
  onSelectFile,
  onClear,
}: ManualDropCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const handleClick = () => {
    if (desktop) {
      onSelect();
    } else {
      inputRef.current?.click();
    }
  };
  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (desktop) return;
    const file = event.dataTransfer.files?.[0];
    if (file) onSelectFile(file);
  };
  const handleKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleClick();
    }
  };
  return (
    <div
      className="task3-analyze-drop-card"
      data-filled={Boolean(selected)}
      onClick={handleClick}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
      onKeyDown={handleKey}
      role="button"
      tabIndex={0}
    >
      {!selected ? (
        <>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M12 16V4m0 0L8 8m4-4l4 4" />
            <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
          </svg>
          <div className="task3-analyze-drop-title">{title}</div>
          <div className="task3-analyze-drop-sub">拖入文件 · 或点击选择</div>
          <div className="task3-analyze-drop-need">{needText}</div>
          {!desktop ? (
            <input
              accept={accept}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onSelectFile(file);
                event.target.value = "";
              }}
              ref={inputRef}
              style={{ display: "none" }}
              type="file"
            />
          ) : null}
        </>
      ) : (
        <>
          <div className="task3-analyze-drop-title">{title}</div>
          <div style={{ fontWeight: 600, fontSize: "12.5px", wordBreak: "break-all" }}>{selected}</div>
          <div className="task3-analyze-drop-meta">已选择</div>
          {extra ? <div className="task3-analyze-drop-extra">{extra}</div> : null}
          <div className="task3-analyze-drop-actions">
            <button onClick={(event) => { event.stopPropagation(); handleClick(); }} type="button">更换</button>
            <span style={{ color: "var(--outline-variant)" }}> · </span>
            <button onClick={(event) => { event.stopPropagation(); onClear(); }} type="button">移除</button>
          </div>
          {!desktop ? (
            <input
              accept={accept}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onSelectFile(file);
                event.target.value = "";
              }}
              ref={inputRef}
              style={{ display: "none" }}
              type="file"
            />
          ) : null}
        </>
      )}
    </div>
  );
}

interface ManualFileButtonProps {
  label: string;
  accept: string;
  selected: string | null;
  desktop: boolean;
  onSelect: () => void;
  onSelectFile: (file: File) => void;
  onClear: () => void;
}

function ManualFileButton({ label, accept, selected, desktop, onSelect, onSelectFile, onClear }: ManualFileButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const handleClick = () => {
    if (desktop) {
      onSelect();
    } else {
      inputRef.current?.click();
    }
  };
  return (
    <div className="task3-analyze-manual-file-button">
      <Button onClick={handleClick} size="compact" variant="secondary">
        {selected ? `更换 ${label}…` : `选择 ${label}…`}
      </Button>
      {selected ? (
        <span className="task3-analyze-manual-file-name">
          {selected}
          <button className="task3-analyze-manual-file-clear" onClick={() => onClear()} type="button">移除</button>
        </span>
      ) : null}
      {!desktop ? (
        <input
          accept={accept}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onSelectFile(file);
            event.target.value = "";
          }}
          ref={inputRef}
          style={{ display: "none" }}
          type="file"
        />
      ) : null}
    </div>
  );
}

export function AnalyzeClient() {
  const router = useRouter();
  const requestedRunRef = typeof window === "undefined"
    ? null
    : new URLSearchParams(window.location.search).get("run");
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
      const requestedRun = pending.find((run) => run.run_ref === requestedRunRef);
      setSelectedRunId(requestedRun?.id ?? (pending.length === 1 ? pending[0].id : null));
    } else {
      setRunsError(true);
    }
    setCaptureStatus(captureResult.status === "fulfilled" ? captureResult.value : null);
    setRunsLoading(false);
  }, [desktop, requestedRunRef]);

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

  const calibration = useCallback((): CalibrationValues | undefined => {
    const cm = positiveNumber(manualCm);
    const fov = positiveNumber(manualFov);
    return cm || fov ? { cm_per_360: cm, fov } : undefined;
  }, [manualCm, manualFov]);

  const startRunAnalysis = useCallback(async () => {
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
  }, [selectedRun, inputMode, calibration, router]);

  const startManualFallback = useCallback(async () => {
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
  }, [desktop, videoPath, statsPath, videoFile, statsFile, calibration, router]);

  const chooseStatsFile = useCallback(async (file: File | null) => {
    setStatsFile(file);
    setStatsFov(null);
    if (!file) return;
    try {
      setStatsFov(parseKovaaKConfig(await file.text()).fov ?? null);
    } catch {
      setStatsFov(null);
    }
  }, []);

  const enableCapture = useCallback(async () => {
    setSubmitError("");
    try {
      await setDesktopCaptureEnabled(true);
      await loadDesktopState();
    } catch {
      setSubmitError("自动采集未能启用，没有把失败状态伪装成成功。");
    }
  }, [loadDesktopState]);

  const capture = useMemo(() => capturePresentation(captureStatus), [captureStatus]);
  const noticeTone = capture.tone === "error" ? "error" : capture.tone === "warning" ? "warning" : "info";
  const manualReady = desktop ? Boolean(videoPath && statsPath) : Boolean(videoFile && statsFile);

  const runView = useMemo(() => (selectedRun ? presentRunInspector(selectedRun) : null), [selectedRun]);
  const alignment = runView?.evidence.raw.alignment ?? "unknown";
  const alignmentOk = isEvidenceOk(alignment);
  const allEvidenceOk = runView ? Object.values(runView.evidence).every((e) => isHistoryOk(e.availability)) : false;
  const alignmentText = alignment === "aligned" || alignment === "available"
    ? "已对齐"
    : alignment === "failed"
      ? "对齐失败"
      : alignment === "partial"
        ? "部分对齐"
        : alignment;

  const videoName = desktop ? (videoPath || "MP4") : (videoFile?.name || "MP4");
  const statsName = desktop ? (statsPath || "Stats CSV") : (statsFile?.name || "Stats CSV");
  const canSubmitRun = Boolean(selectedRun && inputMode && getRunModeAvailability(selectedRun, inputMode).available);
  const canSubmitManual = manualReady;
  const canSubmit = canSubmitRun || canSubmitManual;

  let summaryTitle = "未准备好";
  let summaryFull = "选择一条训练或完成手动导入以开始分析。";
  let summaryShort = summaryFull;
  if (canSubmitRun && inputMode) {
    summaryTitle = MODE_COPY[inputMode].title;
    summaryFull = `${selectedRun?.scenario || "未知场景"} — ${MODE_SUMMARY[inputMode].full}`;
    summaryShort = `${selectedRun?.scenario || "未知场景"} — ${MODE_SUMMARY[inputMode].short}`;
  } else if (canSubmitManual) {
    summaryTitle = "手动导入";
    summaryFull = `${videoName} + ${statsName}`;
    summaryShort = summaryFull;
  }

  const submitAnalysis = useCallback(async () => {
    if (canSubmitRun) {
      await startRunAnalysis();
    } else if (canSubmitManual) {
      await startManualFallback();
    }
  }, [canSubmitRun, canSubmitManual, startRunAnalysis, startManualFallback]);

  const runListContent = (
    <section aria-labelledby="pending-runs-title" className="task3-analyze-section task3-analyze-run-section">
      <div className="task3-analyze-section-head">
        <h2 id="pending-runs-title">选择本次训练</h2>
        {runs.length > 1 ? <span className="task3-analyze-count">{runs.length} 条待分析</span> : null}
        {desktop ? (
          <Button onClick={() => void loadDesktopState()} size="compact" variant="ghost">刷新 Run</Button>
        ) : null}
      </div>
      {!desktop ? (
        <Panel className="task3-analyze-run-panel">
          <Empty title="浏览器中不读取桌面 Run">请使用下方手动 fallback；桌面版会在这里列出自动整理的训练。</Empty>
        </Panel>
      ) : runsLoading ? (
        <Panel className="task3-analyze-run-panel">
          <Loading>正在读取待分析 Run</Loading>
        </Panel>
      ) : runsError ? (
        <Panel className="task3-analyze-run-panel">
          <ErrorState title="Run 列表暂时不可用">
            <Button onClick={() => void loadDesktopState()} size="compact" variant="secondary">重试</Button>
          </ErrorState>
        </Panel>
      ) : runs.length === 0 ? (
        <Panel className="task3-analyze-run-panel">
          <Empty title="还没有待分析的训练">完成一局 KovaaK Challenge 后再刷新，或使用下方手动 fallback。</Empty>
        </Panel>
      ) : (
        <Panel className="task3-analyze-run-panel">
          {runs.map((run) => (
            <label
              className="task3-analyze-run-item"
              data-selected={selectedRunId === run.id || undefined}
              key={run.run_ref}
            >
              <span className="task3-analyze-run-radio">
                <input
                  checked={selectedRunId === run.id}
                  name="run"
                  onChange={() => {
                    setSelectedRunId(run.id);
                    setInputMode(null);
                  }}
                  type="radio"
                />
                <span aria-hidden="true" className="task3-analyze-run-dot" />
              </span>
              <span className="task3-analyze-run-main">
                <span className="task3-analyze-run-title">
                  <span className="task3-analyze-run-name">{run.scenario || "未知场景"}</span>
                </span>
                <span className="task3-analyze-run-sub">{new Date(run.created_at).toLocaleString("zh-CN")}</span>
                <span className="task3-analyze-run-evidence">
                  <EvidenceChip
                    label="Stats"
                    state={run.evidence_availability.stats ?? run.source_availability.stats}
                    text={evidenceChipText("Stats", run.evidence_availability.stats ?? run.source_availability.stats, run.limitations)}
                  />
                  <EvidenceChip
                    label="Performance"
                    state={run.evidence_availability.performance ?? run.source_availability.performance}
                    text={evidenceChipText("Performance", run.evidence_availability.performance ?? run.source_availability.performance, run.limitations)}
                  />
                  <EvidenceChip
                    label="Raw"
                    state={run.evidence_availability.raw ?? run.trace_quality.availability}
                    text={evidenceChipText("Raw", run.evidence_availability.raw ?? run.trace_quality.availability, run.limitations)}
                  />
                  <EvidenceChip
                    label="视频"
                    state={run.evidence_availability.mp4 ?? run.evidence_availability.video}
                    text={evidenceChipText("视频", run.evidence_availability.mp4 ?? run.evidence_availability.video, run.limitations)}
                  />
                </span>
              </span>
            </label>
          ))}
        </Panel>
      )}
      <p className="task3-analyze-note">多条训练时必须先选择一条；其余保留在历史的「待分析训练」，不会合并或删除。</p>
    </section>
  );

  const manualSection = (
    <section aria-labelledby="manual-title" className="task3-analyze-section task3-analyze-manual-section">
      <div className="task3-analyze-section-head">
        <h2 id="manual-title">手动导入</h2>
        <span className="task3-analyze-hint">没有自动采集时的兼容路径 · 两个文件都要选</span>
      </div>
      <p className="task3-analyze-note">
        同时提供一段 Challenge 录像（MP4）与对应的 Stats CSV——系统不会仅凭录像猜测对应的 Stats。
      </p>
      <div className="task3-analyze-manual-cards">
        <ManualDropCard
          accept="video/mp4"
          desktop={desktop}
          needText="两个文件都齐了之后才能开始分析"
          onClear={() => setVideoFile(null)}
          onSelect={() => void pickDesktopVideoPath().then(setVideoPath)}
          onSelectFile={(file) => setVideoFile(file)}
          selected={desktop ? videoPath : videoFile?.name ?? null}
          title="MP4 录像"
        />
          <ManualDropCard
            accept=".csv,text/csv"
            desktop={desktop}
            extra={statsFov ? `检测到 FOV ${statsFov}，提交后由后端按优先级采用。` : null}
            needText="两个文件都齐了之后才能开始分析"
            onClear={() => void chooseStatsFile(null)}
            onSelect={() => void pickDesktopCsvPath().then((path) => { if (path) setStatsPath(path); })}
            onSelectFile={(file) => void chooseStatsFile(file)}
            selected={desktop ? statsPath : statsFile?.name ?? null}
            title="拖入 Stats CSV"
          />
      </div>
      <div className="task3-analyze-manual-compact">
        <Panel>
          <p className="task3-analyze-note">同时选择 Challenge 录像（MP4）与对应 Stats CSV。</p>
          <div className="task3-analyze-manual-compact-actions">
            <ManualFileButton
              accept="video/mp4"
              desktop={desktop}
              label="MP4"
              onClear={() => setVideoFile(null)}
              onSelect={() => void pickDesktopVideoPath().then(setVideoPath)}
              onSelectFile={(file) => setVideoFile(file)}
              selected={desktop ? videoPath : videoFile?.name ?? null}
            />
            <ManualFileButton
              accept=".csv,text/csv"
              desktop={desktop}
              label="Stats CSV"
              onClear={() => void chooseStatsFile(null)}
              onSelect={() => void pickDesktopCsvPath().then((path) => { if (path) setStatsPath(path); })}
              onSelectFile={(file) => void chooseStatsFile(file)}
              selected={desktop ? statsPath : statsFile?.name ?? null}
            />
            </div>
            {statsFov ? <p className="task3-analyze-note">检测到 FOV {statsFov}，提交后由后端按优先级采用。</p> : null}
          </Panel>
        </div>
      </section>
  );
  const rightColumn = selectedRun ? (
    <>
      <section aria-labelledby="evidence-title" className="task3-analyze-section task3-analyze-evidence-section">
        <div className="task3-analyze-section-head">
          <h2 id="evidence-title">证据检查</h2>
          <span className="task3-analyze-hint">{selectedRun.scenario || "未知场景"}</span>
        </div>
        {runView ? (
          <Panel className="task3-analyze-evidence-panel">
            <dl className="task3-analyze-kv">
              <dt>Stats</dt>
              <dd>{runView.evidence.stats.availability}</dd>
              <dt>Performance</dt>
              <dd>{runView.evidence.performance.availability}</dd>
              <dt>Raw Input</dt>
              <dd>
                {runView.evidence.raw.availability}
                {runView.evidence.raw.coverage ? ` · 覆盖率 ${Math.round(runView.evidence.raw.coverage * 100)}%` : null}
              </dd>
              <dt>视频</dt>
              <dd>{runView.evidence.video.availability}</dd>
              <dt>时间对齐</dt>
              <dd>
                {allEvidenceOk && alignmentOk ? (
                  <span className="task3-analyze-ok">四个来源一致</span>
                ) : (
                  alignmentText
                )}
              </dd>
            </dl>
          </Panel>
        ) : null}
      </section>

      <section aria-labelledby="profile-title" className="task3-analyze-section task3-analyze-profile-section">
        <Panel className="task3-analyze-profile-panel">
          <dl className="task3-analyze-kv">
            <dt>分析设置</dt>
            <dd>
              <span className="task3-analyze-mono">
                {manualCm ? `${manualCm} cm/360` : "无法确定 cm/360"}
                {" · "}
                {manualFov ? `FOV ${manualFov}` : "无法确定 FOV"}
              </span>
              <Badge tone="neutral">Stats 自动读取</Badge>
            </dd>
          </dl>
          <p className="task3-analyze-note task3-analyze-profile-note-full">
            自动读取自本局 Stats，优先级最高。
            <a className="task3-analyze-link" href="#calibration-override">本局覆盖…</a>
            （只影响本次分析）；配置档默认值在
            <Link className="task3-analyze-link" href="/settings">设置</Link>
            中修改。读取失败时显示「无法确定」，不猜值。
          </p>
          <p className="task3-analyze-note task3-analyze-profile-note-short">
            在<Link className="task3-analyze-link" href="/settings">设置</Link>中修改。
          </p>
          <div className="task3-analyze-calibration-row" id="calibration-override">
            <Field hint="Stats 自动读取优先；留空表示不覆盖。" label="本局 cm/360 覆盖（可选）">
              <FieldControl inputMode="decimal" onChange={(event) => setManualCm(event.target.value)} placeholder="例如 42" value={manualCm} />
            </Field>
            <Field hint="Stats 自动读取优先；留空表示不覆盖。" label="本局 FOV 覆盖（可选）">
              <FieldControl inputMode="decimal" onChange={(event) => setManualFov(event.target.value)} placeholder="例如 103" value={manualFov} />
            </Field>
          </div>
        </Panel>
      </section>

      <section aria-labelledby="mode-title" className="task3-analyze-section task3-analyze-mode-section">
        <div className="task3-analyze-section-head">
          <h2 id="mode-title">分析模式</h2>
        </div>
        <div className="task3-analyze-mode-list">
          {(Object.keys(MODE_COPY) as InputMode[]).map((mode) => {
            const availability = getRunModeAvailability(selectedRun, mode);
            const copy = MODE_COPY[mode];
            return (
              <label
                className="task3-analyze-mode-card"
                data-disabled={!availability.available || undefined}
                data-selected={inputMode === mode || undefined}
                key={mode}
              >
                <span className="task3-analyze-mode-radio">
                  <input
                    checked={inputMode === mode}
                    disabled={!availability.available}
                    name="input-mode"
                    onChange={() => setInputMode(mode)}
                    type="radio"
                  />
                  <span aria-hidden="true" className="task3-analyze-mode-dot" />
                </span>
                <span>
                  <span className="task3-analyze-mode-name">
                    {copy.title}
                    {mode === "input_native" ? <PreviewBadge /> : null}
                  </span>
                  <span className="task3-analyze-mode-desc">
                    {copy.desc.split("\n").map((line, i, arr) => (
                      <span key={i}>
                        {line}
                        {i < arr.length - 1 ? <br /> : null}
                      </span>
                    ))}
                  </span>
                  <span className="task3-analyze-mode-desc-short">{copy.shortDesc}</span>
                  {!availability.available ? (
                    <span className="task3-analyze-mode-limit">
                      后端合同未将此模式列为可用。{availability.limitations[0] ? ` ${availability.limitations[0]}` : ""}
                    </span>
                  ) : null}
                </span>
              </label>
            );
          })}
        </div>
      </section>
    </>
  ) : null;

  return (
    <div className="task3-page task3-analyze-page">
      <PageHeading description="选择一条训练记录，确认证据后开始" title="新建分析" />
      <Notice className="task3-analyze-capture-notice" tone={noticeTone}>
        <span className="task3-analyze-capture-text">
          <b>自动采集：{desktop ? capture.label : "浏览器预览"}</b>
          {" — "}
          {desktop ? capture.detail : "浏览器不伪造 Run discovery、Raw Input、回放缓冲或 launch-token 状态。"}
        </span>
        {desktop && captureStatus?.capture_enabled === false ? (
          <span className="task3-analyze-capture-action">
            <Button onClick={() => void enableCapture()} size="compact" variant="secondary">启用自动采集</Button>
          </span>
        ) : null}
      </Notice>

      <div className="task3-analyze-grid" data-layout={selectedRun ? "split" : "single"}>
        {runListContent}
        {rightColumn}
        {manualSection}
      </div>

      {selectedRun && isRunPauseFailClosed(selectedRun) ? (
        <Notice className="task3-analyze-notice" tone="warning" title="检测到暂停局">
          该 Run 按 fail-closed 处理，不会被当作完整训练证据。
        </Notice>
      ) : null}
      {submitError ? (
        <Notice className="task3-analyze-notice" tone="error">
          {submitError}
        </Notice>
      ) : null}

      <div className="task3-analyze-actionbar">
        <span className="task3-analyze-actionbar-summary">
          <b>{summaryTitle}</b>
          <span className="task3-analyze-actionbar-desc">
            <span className="task3-analyze-actionbar-desc-full">{" · " + summaryFull}</span>
            <span className="task3-analyze-actionbar-desc-short">{" · " + summaryShort}</span>
          </span>
        </span>
        <Button disabled={!canSubmit || submitting} onClick={() => void submitAnalysis()}>
          {submitting ? "正在创建" : "开始分析"}
        </Button>
      </div>
    </div>
  );
}
