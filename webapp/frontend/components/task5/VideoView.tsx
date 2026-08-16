"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisVideoBlob } from "@/lib/api";
import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { getManagedVideoUrl, isDesktopRuntime } from "@/lib/desktop";
import { Button, Empty, Loading, Notice } from "@/ui/primitives";

import styles from "./task5.module.css";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function VideoView({
  analysisId,
  currentTimeMs,
  onCurrentTimeChange,
  presentation,
}: {
  analysisId: number;
  currentTimeMs: number;
  onCurrentTimeChange: (timeMs: number) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(presentation.video.kind === "seekable");
  const [loadFailed, setLoadFailed] = useState(false);
  const [durationMs, setDurationMs] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const lastAudibleVolumeRef = useRef(1);

  const loadEvidence = useCallback(async () => {
    if (presentation.video.kind === "native-only") return;
    setLoading(true);
    setLoadFailed(false);
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
  }, [analysisId, presentation.video.kind]);

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

  const timelineMax = Math.max(durationMs, 1);

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

  if (loading) return <Loading>正在读取本地视频</Loading>;

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
        <span className={styles.playerBadge}>{timeText}</span>
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
          <div className={styles.timelineCursor} style={{ insetInlineStart: `${cursorLeft}%` }} />
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
