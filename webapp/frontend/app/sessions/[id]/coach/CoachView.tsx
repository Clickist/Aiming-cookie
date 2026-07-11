"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getChatHistory,
  getTimeline,
  getVideoUrl,
  sendChatMessage,
} from "@/lib/api";
import type {
  ChatMessage,
  ChatResponse,
  Timeline,
  TimelineEvent,
} from "@/lib/types";

/**
 * Coach dialogue view: 左 65% 视频 + 自定义 timeline, 右 35% 聊天。
 * 视频与聊天的时间戳联动是核心创新——
 *   - 教练 → 视频:点教练消息里的时间戳胶囊 → video.currentTime = ts +
 *     (若是区间)对应 [start, end] 高亮 active-segment 样式。
 *   - 视频 → 教练:"锁定当前时间轴"按钮 → 输入框上方显示"📎 已锁定 0:23",
 *     发送时附 pinned_frame_sec 给后端。
 *
 * 布局/class 直接抄 stitch_cursor_design_system (2)/code.html。
 */

interface CoachViewProps {
  sessionId: number;
  /** 教练身份栏显示的 archetype label(来自 server 端 diagnosis.profile.label)。 */
  archetypeLabel: string;
}

const STARTER_CHIPS = ["减速段分析", "握持建议", "我的反向修正太多?"];
const SPEED_OPTIONS = [0.5, 1, 2] as const;

/** 区间时间戳,如 "0:12-0:15"。点 = seek 到 start,高亮 [start, end]。 */
const RANGE_RE = /(\d+):(\d{2})-(\d+):(\d{2})/g;
/** 单点时间戳,如 "0:12"。点 = seek 到该时刻(无高亮区间)。 */
const POINT_RE = /\b(\d+):(\d{2})\b/g;

/** 把 mm:ss 或 mm:ss-mm:ss 解析成秒。 */
function tsToSec(mm: string, ss: string): number {
  return Number(mm) * 60 + Number(ss);
}

function fmtSec(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const mm = Math.floor(sec / 60);
  const ss = Math.floor(sec % 60);
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}

/** 拆消息为 segments:把时间戳胶囊外的文本与胶囊本身交替列出。 */
interface TimestampToken {
  /** 区间或单点的开始秒。 */
  startSec: number;
  /** 区间结束秒;单点时 === startSec。 */
  endSec: number;
  /** 原始匹配文本("0:12-0:15" / "0:12")。 */
  raw: string;
}
interface TextSeg {
  kind: "text";
  text: string;
}
interface TsSeg {
  kind: "ts";
  token: TimestampToken;
}
type Segment = TextSeg | TsSeg;

function parseTimestamps(text: string): Segment[] {
  // 先匹配区间(贪婪),再在剩余文本上匹配单点;用占位符替换避免重复匹配。
  const tokens: { start: number; end: number; raw: string; index: number; length: number }[] = [];
  let m: RegExpExecArray | null;
  RANGE_RE.lastIndex = 0;
  while ((m = RANGE_RE.exec(text)) !== null) {
    tokens.push({
      start: tsToSec(m[1], m[2]),
      end: tsToSec(m[3], m[4]),
      raw: m[0],
      index: m.index,
      length: m[0].length,
    });
  }
  POINT_RE.lastIndex = 0;
  while ((m = POINT_RE.exec(text)) !== null) {
    // 跳过被区间占用的位置
    const overlaps = tokens.some(
      (t) => m!.index >= t.index && m!.index < t.index + t.length,
    );
    if (overlaps) continue;
    const sec = tsToSec(m[1], m[2]);
    tokens.push({
      start: sec,
      end: sec,
      raw: m[0],
      index: m.index,
      length: m[0].length,
    });
  }
  tokens.sort((a, b) => a.index - b.index);

  const segs: Segment[] = [];
  let cursor = 0;
  for (const t of tokens) {
    if (t.index > cursor) {
      segs.push({ kind: "text", text: text.slice(cursor, t.index) });
    }
    segs.push({
      kind: "ts",
      token: { startSec: t.start, endSec: t.end, raw: t.raw },
    });
    cursor = t.index + t.length;
  }
  if (cursor < text.length) {
    segs.push({ kind: "text", text: text.slice(cursor) });
  }
  return segs;
}

/** marker 视觉规则:type → {bg, isDot(圆点)/heightPx(竖条)} */
function markerRule(type: string): {
  bg: string;
  isDot: boolean;
  heightPx?: number;
} {
  switch (type) {
    case "kill":
      return { bg: "var(--color-event-kill)", isDot: false, heightPx: 12 };
    case "miss":
      return { bg: "var(--color-event-miss)", isDot: false, heightPx: 16 };
    case "corrective":
      return { bg: "var(--color-event-corrective)", isDot: false, heightPx: 12 };
    case "peak":
    default:
      return { bg: "var(--color-event-peak)", isDot: true };
  }
}

/* ===================================================================== */

export default function CoachView({ sessionId, archetypeLabel }: CoachViewProps) {
  // Lifted to parent so ChatPane (lock button + timestamp clicks) and VideoPane
  // (rendering) share one video element ref + seek logic — no window events,
  // no document.querySelector. See fix items #2/#3.
  const videoRef = useRef<HTMLVideoElement>(null);
  const [activeSeg, setActiveSeg] = useState<{ start: number; end: number } | null>(null);

  const seekTo = useCallback((start: number, end: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = start;
    setActiveSeg({ start, end });
    void v.play().catch(() => {});
  }, []);

  return (
    <div className="md:h-dvh flex flex-col md:overflow-hidden bg-background">
      {/* ---------- Top nav (stitch header) ---------- */}
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline z-50 shrink-0">
        <div className="flex items-center gap-sm">
          <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
            Aiming Cookie
          </span>
          <div className="h-4 w-px bg-outline mx-xs" />
          <span className="text-label-md text-on-surface-variant">
            Coach Dialogue · #{sessionId}
          </span>
        </div>
        <div className="flex items-center gap-md">
          <Link
            href="/history"
            className="text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            历史记录
          </Link>
          <Link
            href={`/sessions/${sessionId}/report`}
            className="text-primary text-label-md flex items-center gap-1 hover:opacity-80 transition-opacity"
          >
            <span className="material-symbols-outlined text-sm" aria-hidden="true">arrow_back</span>
            返回报告
          </Link>
        </div>
      </header>

      <main className="flex-1 flex flex-col md:flex-row min-h-0 overflow-y-auto md:overflow-hidden">
        <VideoPane sessionId={sessionId} videoRef={videoRef} activeSeg={activeSeg} />
        <ChatPane
          sessionId={sessionId}
          archetypeLabel={archetypeLabel}
          videoRef={videoRef}
          onSeek={seekTo}
        />
      </main>
    </div>
  );
}

/* ===================== 左:视频 + timeline ===================== */

interface VideoPaneProps {
  sessionId: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  activeSeg: { start: number; end: number } | null;
}

function VideoPane({ sessionId, videoRef, activeSeg }: VideoPaneProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    getTimeline(sessionId, { signal: ctrl.signal })
      .then(setTimeline)
      .catch((e) => {
        // timeline 拉失败不致命——markers 区域留空
        console.warn("timeline 拉取失败:", e);
      });
    return () => ctrl.abort();
  }, [sessionId]);

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  }, [videoRef]);

  const onLoadedMeta = () => {
    const v = videoRef.current;
    if (!v) return;
    setDuration(Number.isFinite(v.duration) ? v.duration : 0);
    v.playbackRate = rate;
  };

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    setCurrent(v.currentTime);
    // A-B 循环:到达 active segment end 时回 start(只在 end>start 时启用)
    if (activeSeg && activeSeg.end > activeSeg.start && v.currentTime >= activeSeg.end) {
      v.currentTime = activeSeg.start;
    }
  };

  const onRateChange = (r: number) => {
    setRate(r);
    if (videoRef.current) videoRef.current.playbackRate = r;
  };

  /** 点击 track 任意位置 seek。 */
  const seekToRatio = (ratio: number) => {
    const v = videoRef.current;
    if (!v || !duration) return;
    const clamped = Math.max(0, Math.min(1, ratio));
    v.currentTime = clamped * duration;
    setCurrent(v.currentTime);
  };

  // Pointer-capture based drag seek — no window listeners, so no cleanup gap
  // if the component unmounts mid-drag (fix item #5).
  const onTrackPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return; // right/middle click should not start drag
    const track = trackRef.current;
    if (!track || !duration) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    const rect = track.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    seekToRatio(ratio);
  };

  const onTrackPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    seekToRatio(ratio);
  };

  const onTrackPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
  };

  const onTrackKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!duration) return;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      seekToRatio((current - 5) / duration);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      seekToRatio((current + 5) / duration);
    }
  };

  const progressRatio = duration > 0 ? current / duration : 0;
  const activeStartPct =
    activeSeg && duration > 0 ? (activeSeg.start / duration) * 100 : 0;
  const activeWidthPct =
    activeSeg && duration > 0
      ? Math.max(0.5, ((activeSeg.end - activeSeg.start) / duration) * 100)
      : 0;

  return (
    <section className="w-full md:w-[65%] flex flex-col bg-background p-md min-h-0 md:overflow-hidden border-b md:border-b-0 md:border-r border-outline shrink-0">
      <div className="flex-1 flex flex-col justify-center min-h-0">
        <div className="relative w-full aspect-video bg-black rounded-lg overflow-hidden shadow-2xl">
          <video
            ref={videoRef}
            src={getVideoUrl(sessionId)}
            className="w-full h-full object-contain"
            onLoadedMetadata={onLoadedMeta}
            onTimeUpdate={onTimeUpdate}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onError={() =>
              setVideoError("视频加载失败(可能未找到文件或已归档)")
            }
            playsInline
          />
          {videoError && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/80 p-md text-center">
              <p className="text-body-md text-on-surface-variant">{videoError}</p>
            </div>
          )}
        </div>
      </div>

      {/* ---------- 自定义 timeline ---------- */}
      <div className="w-full mt-md bg-surface-container p-sm rounded-xl border border-outline flex flex-col gap-sm shadow-sm">
        <div className="flex items-center gap-md">
          <button
            type="button"
            onClick={togglePlay}
            className="text-on-surface hover:text-primary transition-colors"
            aria-label={playing ? "暂停" : "播放"}
          >
            <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>
              {playing ? "pause" : "play_arrow"}
            </span>
          </button>

          <div
            ref={trackRef}
            role="slider"
            tabIndex={0}
            aria-label="视频时间轴"
            aria-valuemin={0}
            aria-valuemax={Math.floor(duration)}
            aria-valuenow={Math.floor(current)}
            aria-valuetext={fmtSec(current)}
            onPointerDown={onTrackPointerDown}
            onPointerMove={onTrackPointerMove}
            onPointerUp={onTrackPointerUp}
            onKeyDown={onTrackKeyDown}
            className="flex-1 relative h-12 bg-background/50 rounded flex flex-col justify-center overflow-hidden cursor-pointer touch-none focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            {/* dot grid background */}
            <div
              className="absolute inset-0 opacity-10 pointer-events-none"
              style={{
                backgroundImage: "radial-gradient(#2D2B26 1px, transparent 1px)",
                backgroundSize: "20px 20px",
              }}
            />
            {/* active segment highlight */}
            {activeSeg && (
              <div
                className="absolute h-full bg-primary/20 border-l border-r border-primary z-0"
                style={{
                  left: `${activeStartPct}%`,
                  width: `${activeWidthPct}%`,
                }}
              />
            )}
            {/* markers */}
            <div className="relative h-full w-full flex items-center pointer-events-none">
              {(timeline?.events ?? []).map((ev: TimelineEvent, i: number) => {
                if (duration <= 0) return null;
                const pct = (ev.time_s / duration) * 100;
                const rule = markerRule(ev.type);
                return (
                  <div
                    key={`${ev.type}-${i}`}
                    title={ev.label}
                    className="absolute"
                    style={
                      rule.isDot
                        ? {
                            left: `${pct}%`,
                            width: 6,
                            height: 6,
                            borderRadius: 9999,
                            backgroundColor: rule.bg,
                            boxShadow: "0 0 8px color-mix(in srgb, var(--color-event-peak) 50%, transparent)",
                          }
                        : {
                            left: `${pct}%`,
                            width: 1,
                            height: rule.heightPx,
                            backgroundColor: rule.bg,
                          }
                    }
                  />
                );
              })}
            </div>
            {/* base progress line */}
            <div className="absolute h-0.5 bg-outline w-full bottom-0" />
            <div
              className="absolute h-0.5 bg-primary bottom-0"
              style={{ width: `${progressRatio * 100}%` }}
            />
            {/* playhead */}
            <div
              className="absolute h-full w-[2px] bg-primary z-10"
              style={{ left: `${progressRatio * 100}%` }}
            >
              <div className="absolute -top-1 -left-[3px] w-2 h-2 rounded-full bg-primary" />
            </div>
          </div>

          {/* metadata */}
          <div className="flex items-center gap-md">
            <span className="font-mono text-label-md text-on-surface whitespace-nowrap">
              {fmtSec(current)}{" "}
              <span className="text-on-surface-variant">/ {fmtSec(duration)}</span>
            </span>
            <div className="flex bg-background rounded p-1 border border-outline">
              {SPEED_OPTIONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => onRateChange(r)}
                  aria-pressed={rate === r}
                  className={`px-2 py-0.5 text-xs font-mono rounded transition-colors ${
                    rate === r
                      ? "bg-surface-container text-primary font-bold"
                      : "hover:bg-surface-container"
                  }`}
                >
                  {r}x
                </button>
              ))}
            </div>
            <button
              type="button"
              title="A-B 循环(占位)"
              aria-label="A-B 循环(占位)"
              className="text-on-surface-variant hover:text-primary transition-colors flex items-center gap-1 text-label-sm"
            >
              <span className="material-symbols-outlined text-sm">repeat</span>
              A-B
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ===================== 右:聊天区 ===================== */

interface ChatPaneProps {
  sessionId: number;
  archetypeLabel: string;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onSeek: (start: number, end: number) => void;
}

function ChatPane({ sessionId, archetypeLabel, videoRef, onSeek }: ChatPaneProps) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [pinnedSec, setPinnedSec] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    getChatHistory(sessionId, { signal: ctrl.signal })
      .then((r) => setHistory(r.history))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [sessionId]);

  // 新消息时滚到底
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history]);

  // textarea auto-grow
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }, [input]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setError(null);
    setInput("");
    try {
      const pinned = pinnedSec;
      setPinnedSec(null);
      const r: ChatResponse = await sendChatMessage(
        sessionId,
        trimmed,
        pinned ?? undefined,
      );
      setHistory(r.history);
      if (r.reply == null && r.notes.length > 0) {
        setError(r.notes.join(" · "));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  /** "锁定当前时间轴"按钮:取视频当前 currentTime(通过父级共享的 videoRef)。 */
  const lockCurrentTime = () => {
    const v = videoRef.current;
    if (!v) return;
    setPinnedSec(Number.isFinite(v.currentTime) ? v.currentTime : 0);
  };

  const onChipClick = (text: string) => {
    // chips 里有 "0:12 异常点"——直接发原文本(后端不感知时间戳)
    void send(text);
  };

  return (
    <section className="w-full md:w-[35%] h-[60vh] md:h-auto bg-surface-container-low flex flex-col min-h-0 relative md:overflow-hidden">
      {/* chat header */}
      <div className="flex items-center justify-between px-md py-sm border-b border-outline bg-surface-container shrink-0">
        <div className="flex items-center gap-sm">
          <div className="w-8 h-8 rounded bg-primary-container flex items-center justify-center">
            <span className="material-symbols-outlined text-on-primary-container text-base">
              psychology
            </span>
          </div>
          <div className="flex flex-col">
            <span className="font-sans text-label-md font-bold text-on-surface">
              AI 教练 · {archetypeLabel} 专项
            </span>
          </div>
        </div>
        <Link
          href={`/sessions/${sessionId}/report`}
          className="text-on-surface-variant hover:text-on-surface transition-colors"
          aria-label="返回报告"
        >
          <span className="material-symbols-outlined text-sm" aria-hidden="true">close</span>
        </Link>
      </div>

      {/* thread */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-md space-y-md no-scrollbar"
      >
        {loading ? (
          <div className="text-center text-on-surface-variant text-label-md py-md">
            加载对话…
          </div>
        ) : history.length === 0 ? (
          <EmptyHint />
        ) : (
          history.map((m, i) => (
            <MessageBubble key={`${i}-${m.created_at}`} message={m} onSeek={onSeek} />
          ))
        )}
        {sending && (
          <div className="flex gap-sm">
            <div className="w-8 h-8 rounded bg-primary-container shrink-0 flex items-center justify-center mt-1">
              <span className="material-symbols-outlined text-on-primary-container text-xs animate-pulse">
                auto_awesome
              </span>
            </div>
            <div className="bg-background border border-outline p-md rounded-xl rounded-tl-none shadow-sm">
              <p className="text-body-md text-on-surface-variant">教练思考中…</p>
            </div>
          </div>
        )}
      </div>

      {/* interaction layer */}
      <div className="p-md bg-surface-container border-t border-outline">
        {/* starter chips(仅历史为空时显示) */}
        {history.length === 0 && !loading && (
          <div className="flex gap-xs mb-md overflow-x-auto no-scrollbar">
            {STARTER_CHIPS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => onChipClick(c)}
                className="shrink-0 bg-background border border-outline px-sm py-1.5 rounded-full text-[11px] font-bold text-on-surface-variant hover:border-primary hover:text-primary transition-all whitespace-nowrap"
              >
                {c}
              </button>
            ))}
          </div>
        )}

        {error && (
          <p className="text-label-sm text-error mb-xs break-words">{error}</p>
        )}

        {/* 锁定条(显示已锁定时间) */}
        {pinnedSec !== null && (
          <button
            type="button"
            onClick={() => setPinnedSec(null)}
            className="flex items-center gap-xs px-sm py-1 border-b border-outline/50 mb-xs text-[10px] text-on-surface-variant font-bold uppercase tracking-wider hover:text-primary transition-colors"
            title="点击取消锁定"
          >
            <span className="material-symbols-outlined text-xs text-primary">
              push_pin
            </span>
            已锁定 {fmtSec(pinnedSec)}(点击取消)
          </button>
        )}

        {/* input */}
        <div className="bg-background border border-outline rounded-xl flex flex-col gap-xs shadow-inner focus-within:border-primary transition-colors">
          <div className="flex items-end gap-sm">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder="问教练任何问题…"
              rows={1}
              className="flex-1 bg-transparent border-none focus:ring-0 focus:outline-none text-on-surface text-body-md py-sm px-sm placeholder:text-on-surface-variant/40 resize-none font-sans"
            />
            <button
              type="button"
              onClick={lockCurrentTime}
              title="锁定当前时间轴"
              aria-label="锁定当前时间轴"
              className="w-8 h-8 rounded-lg flex items-center justify-center text-on-surface-variant hover:text-primary hover:bg-surface-container transition-colors mb-1"
            >
              <span className="material-symbols-outlined text-sm">push_pin</span>
            </button>
            <button
              type="button"
              onClick={() => void send(input)}
              disabled={!input.trim() || sending}
              aria-label="发送消息"
              className="w-10 h-10 rounded-lg bg-primary flex items-center justify-center text-on-primary hover:brightness-110 active:scale-95 transition-all mb-1 mr-1 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <span
                className="material-symbols-outlined text-base"
                style={{ fontVariationSettings: "'FILL' 1" }}
              >
                send
              </span>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------- 子组件 ---------- */

function EmptyHint() {
  return (
    <div className="text-center py-md px-sm">
      <p className="text-body-md text-on-surface mb-xs">和你的 AI 教练对话</p>
      <p className="text-label-md text-on-surface-variant">
        点击下方推荐问题,或自己提问。教练会基于本次诊断结果给建议。
      </p>
    </div>
  );
}

function MessageBubble({
  message,
  onSeek,
}: {
  message: ChatMessage;
  onSeek: (start: number, end: number) => void;
}) {
  const isUser = message.role === "user";
  // 解析时间戳(只对 assistant 消息——user 消息按字面渲染)
  const segs = useMemo(
    () => (isUser ? null : parseTimestamps(message.content)),
    [isUser, message.content],
  );
  const time = message.created_at?.slice(11, 16) ?? "";

  if (isUser) {
    return (
      <div className="flex flex-col items-end gap-xs w-full">
        <div className="max-w-[85%] bg-primary text-on-primary p-md rounded-xl rounded-tr-none shadow-md">
          <p className="text-body-md font-medium leading-relaxed whitespace-pre-wrap break-words">
            {message.content}
          </p>
        </div>
        <span className="text-[10px] text-on-surface-variant font-mono text-right pr-1 uppercase tracking-widest">
          {time} · YOU
        </span>
      </div>
    );
  }

  return (
    <div className="flex gap-sm">
      <div className="w-8 h-8 rounded bg-primary-container shrink-0 flex items-center justify-center mt-1">
        <span className="material-symbols-outlined text-on-primary-container text-xs">
          auto_awesome
        </span>
      </div>
      <div className="flex flex-col gap-xs max-w-[90%]">
        <div className="bg-background border border-outline p-md rounded-xl rounded-tl-none shadow-sm">
          <p className="text-body-md text-on-surface leading-relaxed break-words">
            {segs
              ? segs.map((s, i) =>
                  s.kind === "text" ? (
                    <span key={i}>{s.text}</span>
                  ) : (
                    <button
                      key={i}
                      type="button"
                      onClick={() => onSeek(s.token.startSec, s.token.endSec)}
                      className="inline-flex items-center gap-1 bg-primary/10 text-primary px-2 py-0.5 rounded-full font-mono text-xs border border-primary/20 cursor-pointer hover:bg-primary/20 transition-all mx-[1px]"
                    >
                      {s.token.raw}
                    </button>
                  ),
                )
              : message.content}
          </p>
        </div>
        <span className="text-[10px] text-on-surface-variant font-mono pl-1 uppercase tracking-widest">
          {time} · AI COACH
        </span>
      </div>
    </div>
  );
}
