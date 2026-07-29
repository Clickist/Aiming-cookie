"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisEvidenceSegments, getVideoUrl } from "@/lib/api";
import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { getManagedVideoUrl, isDesktopRuntime } from "@/lib/desktop";
import type { FrontendEvidenceSegmentV1, FrontendEvidenceSegmentsV1, TimelineEvent } from "@/lib/types";
import { Button, Empty, Loading, Notice } from "@/ui/primitives";

import styles from "./task5.module.css";

function eventTimeMs(event: TimelineEvent): number | null {
  if (typeof event.relative_ms === "number") return event.relative_ms;
  if (typeof event.time_s === "number") return event.time_s * 1000;
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function eventLabel(type: string): string {
  return {
    kill: "命中",
    miss: "未命中",
    peak: "速度峰值",
    corrective: "修正",
  }[type] ?? type;
}

export function VideoView({
  analysisId,
  currentTimeMs,
  onCurrentTimeChange,
  onSelectSegment,
  presentation,
  selectedIssue,
  selectedSegment,
}: {
  analysisId: number;
  currentTimeMs: number;
  onCurrentTimeChange: (timeMs: number) => void;
  onSelectSegment: (segmentId: string | null) => void;
  presentation: AnalysisWorkspacePresentation;
  selectedIssue: number | null;
  selectedSegment: string | null;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const overlayTriggerRef = useRef<HTMLElement | null>(null);
  const [segments, setSegments] = useState<FrontendEvidenceSegmentsV1 | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(presentation.video.kind === "seekable");
  const [loadFailed, setLoadFailed] = useState(false);
  const [segmentsLoading, setSegmentsLoading] = useState(presentation.video.kind === "seekable");
  const [segmentsFailed, setSegmentsFailed] = useState(false);
  const [durationMs, setDurationMs] = useState(0);
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [activeTypes, setActiveTypes] = useState<string[]>(() => Array.from(new Set(presentation.timeline.map((event) => event.type))));

  const loadSegments = useCallback(async () => {
    setSegmentsLoading(true);
    setSegmentsFailed(false);
    setSegments(null);
    try {
      setSegments(await getAnalysisEvidenceSegments(analysisId));
    } catch {
      setSegments(null);
      setSegmentsFailed(true);
    } finally {
      setSegmentsLoading(false);
    }
  }, [analysisId]);

  const loadEvidence = useCallback(async () => {
    if (presentation.video.kind === "native-only") return;
    setLoading(true);
    setLoadFailed(false);
    setSegments(null);
    setSegmentsLoading(false);
    setSegmentsFailed(false);
    if (presentation.video.kind !== "seekable") {
      setVideoUrl(null);
      setLoading(false);
      return;
    }
    try {
      const managed = isDesktopRuntime() ? await getManagedVideoUrl(analysisId) : null;
      setVideoUrl(managed ?? getVideoUrl(analysisId));
    } catch {
      setVideoUrl(null);
      setLoadFailed(true);
      setLoading(false);
      return;
    }
    setLoading(false);
    await loadSegments();
  }, [loadSegments, analysisId, presentation.video.kind]);

  useEffect(() => {
    void loadEvidence();
  }, [loadEvidence]);

  const segmentRows = segments?.segments ?? [];
  const issueEventRefs = selectedIssue === null ? [] : presentation.issues[selectedIssue]?.eventRefs ?? [];
  const visibleEvents = useMemo(() => presentation.timeline.filter((event) => activeTypes.includes(event.type)), [activeTypes, presentation.timeline]);
  const inferredEnd = Math.max(
    1,
    ...presentation.timeline.map((event) => eventTimeMs(event) ?? 0),
    ...segmentRows.map((segment) => segment.playback.relative_end_ms ?? 0),
  );
  const timelineMax = Math.max(durationMs, inferredEnd);
  const activeSegment = segmentRows.find((segment) => segment.segment_id === selectedSegment) ?? null;
  const activeEvent = selectedEvent === null ? null : presentation.timeline[selectedEvent] ?? null;

  const seek = (timeMs: number) => {
    const next = clamp(timeMs, 0, timelineMax);
    onCurrentTimeChange(next);
    if (videoRef.current) videoRef.current.currentTime = next / 1000;
  };

  const openSegment = (segment: FrontendEvidenceSegmentV1, trigger: HTMLElement) => {
    overlayTriggerRef.current = trigger;
    onSelectSegment(segment.segment_id);
    setSelectedEvent(null);
    if (typeof segment.playback.relative_start_ms === "number") seek(segment.playback.relative_start_ms);
  };

  const openEvent = (event: TimelineEvent, index: number, trigger: HTMLElement) => {
    overlayTriggerRef.current = trigger;
    setSelectedEvent(index);
    onSelectSegment(null);
    const time = eventTimeMs(event);
    if (time !== null) seek(time);
  };

  const closeOverlay = useCallback(() => {
    onSelectSegment(null);
    setSelectedEvent(null);
    window.requestAnimationFrame(() => overlayTriggerRef.current?.focus());
  }, [onSelectSegment]);

  useEffect(() => {
    if (!activeSegment && !activeEvent) return undefined;
    overlayRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeOverlay();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activeEvent, activeSegment, closeOverlay]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || currentTimeMs <= 0 || Math.abs(video.currentTime * 1000 - currentTimeMs) < 250) return;
    video.currentTime = currentTimeMs / 1000;
  }, [currentTimeMs, videoUrl]);

  if (presentation.video.kind === "native-only") {
    return (
      <div className={styles.videoColumn}>
        <Empty title="没有可用视觉证据">
          这条 input-native Analysis 只包含输入运动学与事件对齐，不显示空播放器，也不推断视觉结论。
        </Empty>
      </div>
    );
  }

  if (loading) return <Loading>正在读取本地视频与证据片段</Loading>;

  if (loadFailed || !videoUrl) {
    return (
      <div className={styles.videoColumn}>
        <Notice tone="warning" title="视觉证据当前不可用">
          原生分析结果仍然保留。视频可能已被手动移除，或本地媒体服务暂时不可用。
        </Notice>
        <Button onClick={() => void loadEvidence()} variant="secondary">重试视觉证据</Button>
      </div>
    );
  }

  const overlayTitle = activeSegment?.title_key ?? activeEvent?.label ?? null;
  return (
    <div className={styles.videoColumn}>
      <section className={styles.playerStage} aria-label="视频证据播放器">
        <video
          className={styles.video}
          controls
          onDurationChange={(event) => setDurationMs(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration * 1000 : 0)}
          onError={() => {
            setVideoUrl(null);
            setLoadFailed(true);
          }}
          onTimeUpdate={(event) => onCurrentTimeChange(event.currentTarget.currentTime * 1000)}
          preload="metadata"
          ref={videoRef}
          src={videoUrl}
        />
        {overlayTitle ? (
          <div className={styles.playerOverlay} ref={overlayRef} tabIndex={-1}>
            <header>
              <div><p className={styles.sectionKicker}>{activeSegment ? "EvidenceSegment" : "当前事件"}</p><h2>{overlayTitle}</h2></div>
              <Button onClick={closeOverlay} variant="ghost">关闭</Button>
            </header>
            {activeSegment ? (
              <dl>
                <div><dt>相对片段</dt><dd>{activeSegment.playback.relative_start_ms ?? "?"}–{activeSegment.playback.relative_end_ms ?? "?"} ms</dd></div>
                <div><dt>可信度</dt><dd>{activeSegment.confidence === null ? "未提供" : `${Math.round(activeSegment.confidence * 100)}%`}</dd></div>
                <div><dt>覆盖</dt><dd>{activeSegment.source_coverage === null ? "未提供" : `${Math.round(activeSegment.source_coverage * 100)}%`}</dd></div>
              </dl>
            ) : activeEvent ? <p>{eventLabel(activeEvent.type)} · {activeEvent.source ?? "来源未提供"}</p> : null}
          </div>
        ) : null}
      </section>

      <section className={styles.timelineSection} aria-label="分析时间轴">
        <div className={styles.timelineFilters} aria-label="事件类型筛选">
          <span>事件</span>
          {Array.from(new Set(presentation.timeline.map((event) => event.type))).map((type) => {
            const active = activeTypes.includes(type);
            return (
              <button aria-pressed={active} data-event={type} key={type} onClick={() => setActiveTypes((current) => active ? current.filter((item) => item !== type) : [...current, type])} type="button">
                {eventLabel(type)}
              </button>
            );
          })}
        </div>
        {segmentsLoading ? <Loading>正在读取证据片段</Loading> : null}
        {segmentsFailed ? (
          <Notice tone="warning" title="证据片段暂时不可用">
            视频仍可播放；片段恢复后可以重试读取。
            <Button onClick={() => void loadSegments()} size="compact" variant="secondary">重试证据片段</Button>
          </Notice>
        ) : null}
        {!segmentsLoading && !segmentsFailed && segmentRows.length === 0 ? (
          <Empty title="没有可用证据片段">此 Analysis 没有返回可定位的片段。</Empty>
        ) : null}
        <div className={styles.timelineTrack}>
          {!segmentsLoading && !segmentsFailed ? segmentRows.map((segment) => {
            const start = segment.playback.relative_start_ms;
            const end = segment.playback.relative_end_ms;
            if (start === null || end === null) return null;
            return (
              <button
                aria-label={`证据片段 ${segment.title_key ?? segment.segment_id}`}
                className={styles.segmentRange}
                data-related={issueEventRefs.some((ref) => segment.event_refs.includes(ref)) || undefined}
                key={segment.segment_id}
                onClick={(event) => openSegment(segment, event.currentTarget)}
                style={{ insetInlineStart: `${(start / timelineMax) * 100}%`, width: `${Math.max(0.8, ((end - start) / timelineMax) * 100)}%` }}
                type="button"
              />
            );
          }) : null}
          {visibleEvents.map((event) => {
            const index = presentation.timeline.indexOf(event);
            const time = eventTimeMs(event);
            if (time === null) return null;
            return (
              <button
                aria-label={`${eventLabel(event.type)} ${Math.round(time)} 毫秒`}
                className={styles.eventMarker}
                data-event={event.type}
                key={`${event.type}-${time}-${index}`}
                onClick={(trigger) => openEvent(event, index, trigger.currentTarget)}
                style={{ insetInlineStart: `${(time / timelineMax) * 100}%` }}
                type="button"
              />
            );
          })}
          <input
            aria-label="分析时间轴"
            className={styles.timelineInput}
            max={timelineMax}
            min={0}
            onChange={(event) => seek(Number(event.currentTarget.value))}
            step={10}
            type="range"
            value={clamp(currentTimeMs, 0, timelineMax)}
          />
        </div>
        <div className={styles.timelineMeta}><span>{Math.round(currentTimeMs)} ms</span><span>{Math.round(timelineMax)} ms</span></div>
      </section>

    </div>
  );
}
