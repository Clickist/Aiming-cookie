"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisEvidenceSegments, getAnalysisVideoBlob } from "@/lib/api";
import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { getManagedVideoUrl, isDesktopRuntime } from "@/lib/desktop";
import type { FrontendEvidenceSegmentV1, FrontendEvidenceSegmentsV1, TimelineEvent } from "@/lib/types";
import { Badge, Button, Empty, Loading, Notice } from "@/ui/primitives";

import styles from "./task5.module.css";

const SEGMENT_SEEK_PADDING_MS = 2000;

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
    kill: "击杀",
    miss: "未命中",
    peak: "速度峰值",
    corrective: "修正",
  }[type] ?? type;
}

function eventColorVar(type: string): string {
  if (type === "kill") return "var(--event-kill)";
  if (type === "miss") return "var(--event-miss)";
  if (type === "corrective") return "var(--event-corrective)";
  if (type === "peak") return "var(--event-peak)";
  return "var(--on-surface-variant)";
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
  const drawerRef = useRef<HTMLDivElement>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [segments, setSegments] = useState<FrontendEvidenceSegmentsV1 | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(presentation.video.kind === "seekable");
  const [loadFailed, setLoadFailed] = useState(false);
  const [segmentsLoading, setSegmentsLoading] = useState(presentation.video.kind === "seekable");
  const [segmentsFailed, setSegmentsFailed] = useState(false);
  const [durationMs, setDurationMs] = useState(0);
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [activeTypes, setActiveTypes] = useState<string[]>(() => Array.from(new Set(presentation.timeline.map((event) => event.type))));
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const lastAudibleVolumeRef = useRef(1);

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
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      setVideoUrl(null);
      setLoading(false);
      return;
    }
    try {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      if (isDesktopRuntime()) {
        setVideoUrl(await getManagedVideoUrl(analysisId));
      } else {
        const objectUrl = URL.createObjectURL(await getAnalysisVideoBlob(analysisId));
        objectUrlRef.current = objectUrl;
        setVideoUrl(objectUrl);
      }
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

  useEffect(() => () => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (video) video.playbackRate = speed;
  }, [speed, videoUrl]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = volume;
    video.muted = muted;
  }, [muted, volume, videoUrl]);

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

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
    }
  };

  const step = (direction: number) => {
    const video = videoRef.current;
    if (!video) return;
    seek((video.currentTime * 1000) + direction * 5000);
  };

  const setPlayerVolume = (nextVolume: number) => {
    const next = clamp(nextVolume, 0, 1);
    setVolume(next);
    if (next > 0) {
      lastAudibleVolumeRef.current = next;
      setMuted(false);
    } else {
      setMuted(true);
    }
  };

  const toggleMute = () => {
    if (muted || volume === 0) {
      setVolume(lastAudibleVolumeRef.current);
      setMuted(false);
      return;
    }
    lastAudibleVolumeRef.current = volume;
    setMuted(true);
  };

  const openSegment = (segment: FrontendEvidenceSegmentV1) => {
    onSelectSegment(segment.segment_id);
    setSelectedEvent(null);
    setDrawerOpen(true);
    if (typeof segment.playback.relative_start_ms === "number") {
      const paddedStart = Math.max(0, segment.playback.relative_start_ms - SEGMENT_SEEK_PADDING_MS);
      seek(paddedStart);
    }
  };

  const openEvent = (index: number) => {
    setSelectedEvent(index);
    onSelectSegment(null);
    const time = eventTimeMs(presentation.timeline[index]);
    if (time !== null) seek(time);
  };

  const closeOverlay = useCallback(() => {
    onSelectSegment(null);
    setSelectedEvent(null);
    setDrawerOpen(false);
  }, [onSelectSegment]);

  useEffect(() => {
    if (!activeSegment && !activeEvent && !drawerOpen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeOverlay();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activeEvent, activeSegment, drawerOpen, closeOverlay]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || currentTimeMs <= 0 || Math.abs(video.currentTime * 1000 - currentTimeMs) < 250) return;
    video.currentTime = currentTimeMs / 1000;
  }, [currentTimeMs, videoUrl]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return undefined;
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    return () => {
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
    };
  }, [videoUrl]);

  if (presentation.video.kind === "native-only") {
    return (
      <div className={styles.videoView}>
        <Empty title="没有可用视觉证据">
          本局没有录制视频：这条 input-native Analysis 只包含输入运动学与事件对齐，不显示空播放器，也不推断视觉结论。
        </Empty>
      </div>
    );
  }

  if (presentation.video.kind === "unavailable") {
    if (presentation.family.status === "supported") {
      return (
        <div className={styles.videoView}>
          <Notice tone="warning" title="视觉证据当前不可用">
            原生分析结果仍然保留。视频可能已被手动移除，或本地媒体服务暂时不可用。
          </Notice>
          <Button onClick={() => void loadEvidence()} variant="secondary">重试视觉证据</Button>
        </div>
      );
    }
    return (
      <div className={styles.videoView}>
        <Empty title="本档分析基于输入数据">
          本档分析不消费视觉测量；本局没有可附加的回放视频，这不代表证据被移除。
        </Empty>
      </div>
    );
  }

  if (loading) return <Loading>正在读取本地视频与证据片段</Loading>;

  if (loadFailed || !videoUrl) {
    return (
      <div className={styles.videoView}>
        <Notice tone="warning" title="视觉证据当前不可用">
          原生分析结果仍然保留。视频可能已被手动移除，或本地媒体服务暂时不可用。
        </Notice>
        <Button onClick={() => void loadEvidence()} variant="secondary">重试视觉证据</Button>
      </div>
    );
  }

  const progress = timelineMax > 0 ? (currentTimeMs / timelineMax) * 100 : 0;
  const cursorLeft = timelineMax > 0 ? (currentTimeMs / timelineMax) * 100 : 0;
  const timeText = `${formatRelativeTime(currentTimeMs)} / ${formatRelativeTime(timelineMax)}`;
  const audibleVolume = muted ? 0 : volume;
  const volumePercent = Math.round(audibleVolume * 100);
  const volumeIcon = volumePercent === 0 ? "\u{1F507}\uFE0E" : volumePercent < 50 ? "\u{1F509}\uFE0E" : "\u{1F50A}\uFE0E";

  return (
    <div className={styles.videoView}>
      <section className={styles.playerStage} aria-label="视频证据播放器">
        <video
          className={styles.video}
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
        <span className={styles.playerBadge}>{timeText}{activeSegment ? ` · ${activeSegment.title_key ?? activeSegment.segment_id}` : ""}</span>
        {presentation.family.status !== "supported" ? (
          <p className={styles.videoTierNote}>本档分析基于输入数据，视频回放仍可观看；回放不参与视觉测量结论。</p>
        ) : null}
        {drawerOpen ? (
          <div className={styles.segmentDrawer} ref={drawerRef} tabIndex={-1}>
            <header>
              <span>证据片段</span>
              <Badge tone="neutral">{segmentRows.length}</Badge>
              <button className={styles.drawerClose} onClick={() => setDrawerOpen(false)} type="button">✕</button>
            </header>
            <div className={styles.segmentDrawerBody}>
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
              {segmentRows.map((segment) => {
                const selected = segment.segment_id === selectedSegment;
                const related = issueEventRefs.some((ref) => segment.event_refs.includes(ref));
                return (
                  <button
                    className={styles.segmentCard}
                    data-related={related || undefined}
                    data-selected={selected || undefined}
                    key={segment.segment_id}
                    onClick={() => openSegment(segment)}
                    type="button"
                  >
                    <div className={styles.segmentTop}>
                      <span style={{ color: "var(--event-peak)" }}>●</span>
                      <span>{segment.title_key ?? segment.segment_id}</span>
                      {selected ? <Badge tone="neutral">正在播放</Badge> : null}
                    </div>
                    <div className={styles.segmentMeta}>
                      <span>{formatRelativeTime(segment.playback.relative_start_ms ?? 0)} – {formatRelativeTime(segment.playback.relative_end_ms ?? timelineMax)}</span>
                      {segment.source_coverage !== null ? <span>覆盖 {Math.round(segment.source_coverage * 100)}%</span> : null}
                      {segment.confidence !== null ? <span>置信度 {Math.round(segment.confidence * 100)}%</span> : null}
                    </div>
                    {segment.rank_reason ? <div className={styles.segmentMeta}>{segment.rank_reason}</div> : null}
                  </button>
                );
              })}
            </div>
            <div className={styles.segmentDrawerFoot}>本地回放，不上传；Coach 可引用但不读取视频内容</div>
          </div>
        ) : null}
      </section>

      <div className={styles.playerBar}>
        <button className={styles.playerBarBtn} onClick={() => step(-1)} type="button">⏮</button>
        <button className={styles.playerBarBtn} onClick={togglePlay} type="button">{isPlaying ? "⏸" : "▶"}</button>
        <button className={styles.playerBarBtn} onClick={() => step(1)} type="button">⏭</button>
        <span className={styles.playerBarTime}>{timeText}</span>
        <div className={styles.playerBarSpacer} />
        <button
          className={styles.playerBarBtn}
          onClick={() => setSpeed((current) => current === 1 ? 0.5 : 1)}
          type="button"
        >
          {speed}×
        </button>
        <button
          className={`${styles.playerBarBtn} ${styles.playerBarEvidence}`}
          onClick={() => setDrawerOpen((current) => !current)}
          type="button"
        >
          证据片段 {segmentRows.length}
        </button>
        <div className={styles.volumeControl}>
          <button
            aria-label={muted ? "取消静音" : "静音"}
            aria-pressed={muted}
            className={styles.playerBarBtn}
            onClick={toggleMute}
            title={muted ? "取消静音" : "静音"}
            type="button"
          >
            <span aria-hidden="true" className={styles.volumeIcon}>{volumeIcon}</span>
          </button>
          <div className={styles.volumePopover}>
            <input
              aria-label="音量"
              aria-orientation="vertical"
              className={styles.volumeSlider}
              max="100"
              min="0"
              onChange={(event) => setPlayerVolume(Number(event.currentTarget.value) / 100)}
              step="1"
              type="range"
              value={volumePercent}
            />
            <output className={styles.volumeValue}>{volumePercent}%</output>
          </div>
        </div>
        <button className={styles.playerBarBtn} onClick={() => {
          const video = videoRef.current;
          if (video) {
            if (document.fullscreenElement) {
              void document.exitFullscreen();
            } else {
              void video.requestFullscreen();
            }
          }
        }} type="button">⛶</button>
      </div>

      <section className={styles.timelineSection} aria-label="分析时间轴">
        <div className={styles.timeline}>
          <div className={styles.timelineTrack} />
          <div className={styles.timelineProgress} style={{ width: `${progress}%` }} />
          {!segmentsLoading && !segmentsFailed ? segmentRows.map((segment) => {
            const start = segment.playback.relative_start_ms;
            const end = segment.playback.relative_end_ms;
            if (start === null || end === null) return null;
            const related = issueEventRefs.some((ref) => segment.event_refs.includes(ref));
            return (
              <button
                aria-label={`证据片段 ${segment.title_key ?? segment.segment_id}`}
                className={styles.timelineSegment}
                data-related={related || undefined}
                key={segment.segment_id}
                onClick={() => openSegment(segment)}
                style={{ insetInlineStart: `${(start / timelineMax) * 100}%`, width: `${Math.max(0.4, ((end - start) / timelineMax) * 100)}%` }}
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
                className={styles.timelineEvent}
                data-event={event.type}
                key={`${event.type}-${time}-${index}`}
                onClick={() => openEvent(index)}
                style={{ insetInlineStart: `${(time / timelineMax) * 100}%`, background: eventColorVar(event.type) }}
                type="button"
              />
            );
          })}
          <div className={styles.timelineCursor} style={{ insetInlineStart: `${cursorLeft}%` }} />
          {activeEvent ? (
            <div className={styles.eventPopover} style={{ insetInlineStart: `${cursorLeft}%` }}>
              <header>
                <span>当前事件 · {eventLabel(activeEvent.type)}</span>
                <button className={styles.drawerClose} onClick={() => setSelectedEvent(null)} type="button">✕</button>
              </header>
              <div className={styles.eventPopoverTime}>{formatRelativeTime(eventTimeMs(activeEvent) ?? 0)}</div>
              <div className={styles.eventPopoverBody}>
                {activeEvent.label ? <p>{activeEvent.label}</p> : null}
                <p>来源：{activeEvent.source ?? "未提供"}</p>
              </div>
            </div>
          ) : null}
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
          <span className={styles.timelineTimeLeft}>{formatRelativeTime(0)}</span>
          <span className={styles.timelineTimeRight}>{formatRelativeTime(timelineMax)}</span>
        </div>

        <div className={styles.timelineLegend}>
          {Array.from(new Set(presentation.timeline.map((event) => event.type))).map((type) => {
            const active = activeTypes.includes(type);
            return (
              <button
                aria-pressed={active}
                className={styles.legendChip}
                data-event={type}
                key={type}
                onClick={() => setActiveTypes((current) => active ? current.filter((item) => item !== type) : [...current, type])}
                type="button"
              >
                <i style={{ background: eventColorVar(type) }} />
                {eventLabel(type)}
              </button>
            );
          })}
          {activeTypes.length === 0 ? <span className={styles.legendChipOff}>全部（已关闭，避免噪声）</span> : null}
        </div>
      </section>
    </div>
  );
}

function formatRelativeTime(value: number): string {
  const totalSeconds = Math.max(0, value) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}
