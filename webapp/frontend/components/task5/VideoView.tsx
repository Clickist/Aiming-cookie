"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisVideoBlob } from "@/lib/api";
import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { getManagedVideoUrl, isDesktopRuntime } from "@/lib/desktop";
import { projectTimelineMarkers } from "@/lib/metric-format";
import { Button, Empty, Loading, Notice } from "@/ui/primitives";

import styles from "./task5.module.css";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/* ── 视频面板复盘升级 P0（brief §二）常量 ────────────────────────────────
   P0.1 逐帧步进：HTML 标准不在 video 元数据上暴露帧率，用
   getVideoPlaybackQuality 起播后的短窗差分总解码帧数推算，不可得时回退
   33ms（≈30fps）。Shift 按 FRAME_COARSE_FACTOR 加倍粗调。原 ⏮⏭ ±5s
   不删除语义——保留为按钮长按快速跳转。 */
const FRAME_STEP_FALLBACK_MS = 33;
const FRAME_COARSE_FACTOR = 2;
const LONG_STEP_MS = 5000;
const HOLD_DELAY_MS = 350;
const HOLD_REPEAT_MS = 180;
/** P0.2 变速三档循环：0.25× → 0.5× → 1× → 回 0.25×。 */
const SPEED_STEPS = [0.25, 0.5, 1];
/** P0.3 到达脉冲：动画两拍后回到静息可读态；reduced-motion 由 CSS 关掉动画只留静态高亮。 */
const ARRIVE_FLASH_MS = 900;

export function VideoView({
  analysisId,
  currentTimeMs,
  jumpTarget = null,
  onCurrentTimeChange,
  presentation,
}: {
  analysisId: number;
  currentTimeMs: number;
  /** 外部证据跳转意图信号（Coach @time / 讨论芯片）：seq 每次点击递增。 */
  jumpTarget?: { seq: number; ms: number } | null;
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
  // 逐帧步进档位（ms）：fps 推算成功后原地更新，不触发重渲染。
  const frameStepRef = useRef(FRAME_STEP_FALLBACK_MS);
  const fpsSampledRef = useRef(false);
  // ±5s 长按（旧快进退的保留路径）与到达脉冲计时器。
  const holdDelayRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const holdRepeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const holdingLongRef = useRef(false);
  const arriveClearRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [arriveSeq, setArriveSeq] = useState(0);
  const [arriveActive, setArriveActive] = useState(false);
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

  /* P1 事件上轴：分析 timeline → { timeMs, type, label } 标记（kill/miss/
     peak 三类；corrective 数据粗估本批不上）。换分析才重算，与播放解耦。 */
  const timelineMarkers = useMemo(
    () => projectTimelineMarkers(presentation.timeline),
    [presentation.timeline],
  );
  /** 标记在轨道上的百分比位置（与 progress/cursor 同一约定）。 */
  const markerPercent = (timeMs: number) =>
    clamp((timeMs / timelineMax) * 100, 0, 100);

  const seek = (timeMs: number) => {
    const next = clamp(timeMs, 0, timelineMax);
    onCurrentTimeChange(next);
    if (videoRef.current) videoRef.current.currentTime = next / 1000;
  };

  /* P1 标记点击＝seek 并暂停在锚点帧：复用 P0.3 的到达链路（暂停 +
     arriveSeq 驱动 cursor key 重放一次性脉冲），与 jumpTarget 效果同一套
     状态机制，但不经过外部跳转信号。 */
  const seekAndArrive = (timeMs: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = clamp(timeMs, 0, timelineMax) / 1000;
    if (!video.paused) video.pause();
    setArriveActive(true);
    setArriveSeq((seq) => seq + 1);
    if (arriveClearRef.current) clearTimeout(arriveClearRef.current);
    arriveClearRef.current = setTimeout(() => setArriveActive(false), ARRIVE_FLASH_MS);
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

  /* P0.1 逐帧步进：按钮与 ,/. 键共用同一函数，呈现天然同步。
     coarse=true 为 Shift 加倍粗调。 */
  const stepFrame = (direction: number, coarse: boolean) => {
    const video = videoRef.current;
    if (!video) return;
    seek((video.currentTime * 1000) + direction * frameStepRef.current * (coarse ? FRAME_COARSE_FACTOR : 1));
  };

  /** ±5s 长按跳转：替换旧 ⏮⏭ 固定快进退后的保留语义。 */
  const stepLong = (direction: number) => {
    const video = videoRef.current;
    if (!video) return;
    seek((video.currentTime * 1000) + direction * LONG_STEP_MS);
  };

  /** 键盘步进入口经 latestRef 转发，监听器免随渲染重挂（时长/帧率读 ref）。 */
  const stepFrameRef = useRef(stepFrame);
  stepFrameRef.current = stepFrame;

  const clearHoldJump = useCallback(() => {
    if (holdDelayRef.current) {
      clearTimeout(holdDelayRef.current);
      holdDelayRef.current = null;
    }
    if (holdRepeatRef.current) {
      clearInterval(holdRepeatRef.current);
      holdRepeatRef.current = null;
    }
    holdingLongRef.current = false;
  }, []);

  useEffect(() => () => {
    clearHoldJump();
    if (arriveClearRef.current) clearTimeout(arriveClearRef.current);
  }, [clearHoldJump]);

  /** 长按超过 HOLD_DELAY_MS 即进入 ±5s 连发；期间放行的 click 只算一次帧步进。 */
  const beginHoldJump = (direction: number) => {
    clearHoldJump();
    holdDelayRef.current = setTimeout(() => {
      holdingLongRef.current = true;
      stepLong(direction);
      holdRepeatRef.current = setInterval(() => stepLong(direction), HOLD_REPEAT_MS);
    }, HOLD_DELAY_MS);
  };

  /** P0.2 三档循环：越界值（理论不可达）安全回落到第一档。 */
  const cycleSpeed = () => {
    setSpeed((current) => {
      const index = SPEED_STEPS.indexOf(current);
      return SPEED_STEPS[(index + 1) % SPEED_STEPS.length] ?? SPEED_STEPS[0];
    });
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
    // P0.1 帧率推算：换源即重置为兜底常量，起播后短窗差分推算实际 fps。
    frameStepRef.current = FRAME_STEP_FALLBACK_MS;
    fpsSampledRef.current = false;
    let sampleTimer: ReturnType<typeof setTimeout> | null = null;
    const estimateFrameStep = () => {
      if (fpsSampledRef.current || typeof video.getVideoPlaybackQuality !== "function") return;
      fpsSampledRef.current = true;
      const base = video.getVideoPlaybackQuality();
      const baseAt = performance.now();
      sampleTimer = setTimeout(() => {
        sampleTimer = null;
        const elapsed = performance.now() - baseAt;
        const frames = video.getVideoPlaybackQuality().totalVideoFrames - base.totalVideoFrames;
        // 暂停中解码帧不推进；窗口太短或无推进都保持 33ms 兜底，不编造。
        if (!video.paused && elapsed >= 300 && frames > 0) {
          const fps = (frames * 1000) / elapsed;
          if (fps >= 8 && fps <= 120) {
            frameStepRef.current = clamp(Math.round(1000 / fps), 4, 120);
          }
        }
      }, 600);
    };
    const onPlay = () => {
      setIsPlaying(true);
      estimateFrameStep();
    };
    const onPause = () => setIsPlaying(false);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    return () => {
      if (sampleTimer) clearTimeout(sampleTimer);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
    };
  }, [videoUrl]);

  /* P0.1 键盘 ,/. 逐帧步进——只由本组件挂载时生效（本组件只在视频面板
     内渲染），且 composer/任何输入面聚焦时不抢键，杜绝全域字母键误触；
     IME 守卫与仓库既有惯例一致（isComposing + keyCode 229）。 */
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "," && event.key !== ".") return;
      if (event.isComposing || event.keyCode === 229) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        // 文本输入面（composer 等）不劫持；时间轴 range 不在此列
        // （滑杆聚焦时仍可逐帧），按钮聚焦同样照常步进。
        const textEntry =
          tag === "TEXTAREA"
          || tag === "SELECT"
          || target.isContentEditable
          || (tag === "INPUT" && (target as HTMLInputElement).type !== "range");
        if (textEntry) return;
      }
      event.preventDefault();
      stepFrameRef.current(event.key === "," ? -1 : 1, event.shiftKey);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  /* P0.3 @time 到达即暂停＋一次性高亮脉冲（D1/D7）：跳转意图由 jumpTarget
     显式携带（seq 每次 @time 点击递增），与播放反馈、手动拖动天然隔离——
     手动拖进度条走 onChange → seek 的自身通道，不会触发这里。
     ms=0 是「打开这条分析」的普通入口（讨论芯片等），不暂停不脉冲。 */
  useEffect(() => {
    if (!jumpTarget || jumpTarget.ms <= 0) return;
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = jumpTarget.ms / 1000;
    if (!video.paused) video.pause();
    setArriveActive(true);
    setArriveSeq((seq) => seq + 1);
    if (arriveClearRef.current) clearTimeout(arriveClearRef.current);
    arriveClearRef.current = setTimeout(() => setArriveActive(false), ARRIVE_FLASH_MS);
  }, [jumpTarget]);

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
        <button
          aria-label="后退一帧"
          className={styles.playerBarBtn}
          onBlur={clearHoldJump}
          onClick={() => {
            // 长按连发期间放行的 click 不再叠加帧步进。
            if (holdingLongRef.current) return;
            stepFrame(-1, false);
          }}
          onPointerCancel={clearHoldJump}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            beginHoldJump(-1);
          }}
          onPointerLeave={clearHoldJump}
          onPointerUp={clearHoldJump}
          title="逐帧后退（, 键；Shift 加倍）· 长按连续回退 5 秒"
          type="button"
        >⏮</button>
        <button className={styles.playerBarBtn} onClick={togglePlay} type="button">{isPlaying ? "⏸" : "▶"}</button>
        <button
          aria-label="前进一帧"
          className={styles.playerBarBtn}
          onBlur={clearHoldJump}
          onClick={() => {
            if (holdingLongRef.current) return;
            stepFrame(1, false);
          }}
          onPointerCancel={clearHoldJump}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            beginHoldJump(1);
          }}
          onPointerLeave={clearHoldJump}
          onPointerUp={clearHoldJump}
          title="逐帧前进（. 键；Shift 加倍）· 长按连续快进 5 秒"
          type="button"
        >⏭</button>
        <span className={styles.playerBarTime}>{timeText}</span>
        <div className={styles.playerBarSpacer} />
        <button
          aria-label={`播放速度 ${speed}×，点击切换到下一档`}
          className={styles.playerBarBtn}
          data-active={speed !== 1 || undefined}
          onClick={cycleSpeed}
          title="变速循环：0.25× → 0.5× → 1×"
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
          {/* P1 事件上轴——视觉标记层（2px 竖条＋类型形状头），契约栈序插在
              progress 与 cursor 之间；P2 band 层预留在本行与上行之间。
              纯展示不接管指针：竖条容器的 inset-inline 对齐轨道 10px 内边距，
              百分比按真实轨道宽计算；进度/播放头的 ±10px 斜差不在此顺手修正
              （不得动拖动输入层命中区）。 */}
          {timelineMarkers.length > 0 ? (
            <div aria-hidden="true" className={styles.timelineMarkers}>
              {timelineMarkers.map((marker, index) => (
                <span
                  className={styles.timelineMarker}
                  data-event={marker.type}
                  key={`${marker.type}-${marker.timeMs}-${index}`}
                  style={{ insetInlineStart: `${markerPercent(marker.timeMs)}%` }}
                />
              ))}
            </div>
          ) : null}
          {/* key 随到达序号变化＝一次性脉冲重放；data-arrive 窗口内
              呈现 var(--ring) 高亮（reduced-motion 下为静态高亮一次）。 */}
          <div
            className={styles.timelineCursor}
            data-arrive={arriveActive ? "true" : undefined}
            key={`cursor-${arriveSeq}`}
            style={{ insetInlineStart: `${cursorLeft}%` }}
          />
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
          {/* P1 标记命中层：时间轴 range 盖满全轴是拖动主干道，标记的
              hover/点击无法从其下方命中，故以窄命中区按钮浮在其上（z8）；
              点击＝seek 并暂停在锚点帧。窄宽把对拖动的占用压到最小。 */}
          {timelineMarkers.length > 0 ? (
            <div className={styles.timelineMarkerLayer}>
              {timelineMarkers.map((marker, index) => {
                const tip = `${formatRelativeTime(marker.timeMs)} ${marker.label}`;
                return (
                  <button
                    aria-label={tip}
                    className={styles.timelineMarkerHit}
                    data-event={marker.type}
                    key={`hit-${marker.type}-${marker.timeMs}-${index}`}
                    onClick={() => seekAndArrive(marker.timeMs)}
                    style={{ insetInlineStart: `${markerPercent(marker.timeMs)}%` }}
                    type="button"
                  >
                    <span aria-hidden="true" className={styles.markerTip} role="tooltip">{tip}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
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
