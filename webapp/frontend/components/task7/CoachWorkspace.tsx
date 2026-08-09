"use client";

import { FormEvent, useState } from "react";

export type CoachWorkspaceStatus =
  | "waiting-provider"
  | "capturing"
  | "analysis"
  | "missing-video"
  | "failed"
  | "completed";

export interface CoachWorkspaceMessage {
  id?: string | number;
  role: "user" | "assistant" | "system" | string;
  content: string;
  createdAt?: string;
}

export interface CoachWorkspaceVideoState {
  status: "idle" | "loading" | "ready" | "missing" | "failed";
  src?: string | null;
  poster?: string | null;
  currentTimeMs?: number | null;
  durationMs?: number | null;
}

/** Accepts both the local Fast Lane shape and the existing snake_case evidence projection. */
export interface CoachWorkspaceEvidenceSegment {
  id?: string;
  segmentId?: string;
  segment_id?: string;
  label?: string;
  title?: string;
  title_key?: string | null;
  detail?: string | null;
  startMs?: number | null;
  endMs?: number | null;
  start_ms?: number | null;
  end_ms?: number | null;
  focus_start_ms?: number | null;
  focus_end_ms?: number | null;
  [key: string]: unknown;
}

export interface CoachWorkspaceProps {
  status: CoachWorkspaceStatus;
  messages: CoachWorkspaceMessage[];
  video: CoachWorkspaceVideoState;
  evidenceSegments: CoachWorkspaceEvidenceSegment[];
  onSend: (message: string) => void | Promise<void>;
  onEvidenceSelect: (segment: CoachWorkspaceEvidenceSegment) => void;
}

const STATUS_COPY: Record<CoachWorkspaceStatus, { label: string; description: string }> = {
  "waiting-provider": {
    label: "等待 Provider",
    description: "Provider 准备好后，Coach 会继续处理当前工作区。",
  },
  capturing: {
    label: "正在采集",
    description: "视频与输入证据正在收集，Coach 会在采集完成后开始分析。",
  },
  analysis: {
    label: "正在分析",
    description: "Coach 正在把视频证据整理成下一步可执行的练习。",
  },
  "missing-video": {
    label: "缺少视频",
    description: "当前分析仍可查看对话和其他证据，但没有可定位的视频。",
  },
  failed: {
    label: "处理失败",
    description: "这次 Coach 处理没有完成，已有的消息和证据仍保留在这里。",
  },
  completed: {
    label: "分析完成",
    description: "从证据片段开始回看，或继续询问 Coach 下一步怎么练。",
  },
};

function segmentKey(segment: CoachWorkspaceEvidenceSegment, index: number): string {
  return segment.id ?? segment.segmentId ?? segment.segment_id ?? `evidence-${index + 1}`;
}

function segmentTitle(segment: CoachWorkspaceEvidenceSegment, index: number): string {
  return segment.label ?? segment.title ?? segment.title_key ?? `证据片段 ${index + 1}`;
}

function segmentStartMs(segment: CoachWorkspaceEvidenceSegment): number | null {
  return segment.startMs ?? segment.start_ms ?? segment.focus_start_ms ?? null;
}

function segmentEndMs(segment: CoachWorkspaceEvidenceSegment): number | null {
  return segment.endMs ?? segment.end_ms ?? segment.focus_end_ms ?? null;
}

function formatTime(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "时间未知";
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function roleLabel(role: CoachWorkspaceMessage["role"]): string {
  if (role === "user") return "你";
  if (role === "system") return "系统";
  return "Coach";
}

export function CoachWorkspace({
  status,
  messages,
  video,
  evidenceSegments,
  onSend,
  onEvidenceSelect,
}: CoachWorkspaceProps) {
  const [draft, setDraft] = useState("");
  const copy = STATUS_COPY[status];
  const videoUnavailable = status === "missing-video" || video.status === "missing";
  const videoLabel = videoUnavailable
    ? "没有可用视频"
    : video.status === "failed"
      ? "视频加载失败"
      : video.status === "loading"
        ? "正在加载视频"
        : "视频证据";
  const canSend = draft.trim().length > 0;

  const submitMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = draft.trim();
    if (!message) return;
    void onSend(message);
    setDraft("");
  };

  return (
    <section className="task7-coach-workspace" data-state={status} aria-label="Coach 工作区">
      <div className="task7-coach-workspace__status" data-state={status} role="status" aria-live="polite">
        <div>
          <p className="task7-coach-workspace__eyebrow">Coach</p>
          <h1>{copy.label}</h1>
          <p>{copy.description}</p>
        </div>
        <span className="task7-coach-workspace__state-dot" aria-hidden="true" />
      </div>

      <div className="task7-coach-workspace__panes">
        <div className="task7-coach-workspace__video-pane">
          <div className="task7-coach-workspace__pane-heading">
            <div>
              <p className="task7-coach-workspace__eyebrow">证据回看</p>
              <h2>{videoLabel}</h2>
            </div>
            {video.durationMs ? <span className="task7-coach-workspace__time">{formatTime(video.currentTimeMs ?? 0)} / {formatTime(video.durationMs)}</span> : null}
          </div>

          <div className="task7-coach-workspace__player-frame" data-state={video.status}>
            {video.status === "ready" && video.src && !videoUnavailable ? (
              <video className="task7-coach-workspace__player" controls preload="metadata" poster={video.poster ?? undefined} src={video.src} aria-label="Coach 视频证据" />
            ) : (
              <div className="task7-coach-workspace__player-placeholder" role="img" aria-label={videoLabel}>
                <span className="task7-coach-workspace__player-icon" aria-hidden="true">&#9654;</span>
                <strong>{videoLabel}</strong>
                <span>{videoUnavailable ? "选择其他证据或继续查看 Coach 对话。" : "视频准备好后会出现在这里。"}</span>
              </div>
            )}
          </div>

          <div className="task7-coach-workspace__evidence-header">
            <div>
              <h2>证据片段</h2>
              <p>选择片段后由工作区定位视频。</p>
            </div>
            <span className="task7-coach-workspace__count">{evidenceSegments.length}</span>
          </div>
          {evidenceSegments.length > 0 ? (
            <div className="task7-coach-workspace__evidence-list" role="list" aria-label="证据片段列表">
              {evidenceSegments.map((segment, index) => {
                const startMs = segmentStartMs(segment);
                const endMs = segmentEndMs(segment);
                return (
                  <div key={segmentKey(segment, index)} role="listitem">
                    <button
                      className="task7-coach-workspace__evidence-item"
                      type="button"
                      onClick={() => onEvidenceSelect(segment)}
                    >
                      <span className="task7-coach-workspace__evidence-time">{formatTime(startMs)}{endMs !== null ? ` - ${formatTime(endMs)}` : ""}</span>
                      <span className="task7-coach-workspace__evidence-copy">
                        <strong>{segmentTitle(segment, index)}</strong>
                        {segment.detail ? <span>{segment.detail}</span> : null}
                      </span>
                      <span className="task7-coach-workspace__evidence-arrow" aria-hidden="true">&#8594;</span>
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="task7-coach-workspace__empty">分析完成后，相关证据片段会显示在这里。</p>
          )}
        </div>

        <div className="task7-coach-workspace__conversation-pane">
          <div className="task7-coach-workspace__pane-heading">
            <div>
              <p className="task7-coach-workspace__eyebrow">对话</p>
              <h2>和 Coach 一起看问题</h2>
            </div>
            <span className="task7-coach-workspace__live-label">常驻</span>
          </div>

          <div className="task7-coach-workspace__messages" aria-live="polite" aria-label="Coach 对话">
            {messages.length > 0 ? messages.map((message, index) => (
              <article className="task7-coach-workspace__message" data-role={message.role} key={message.id ?? `${message.role}-${index}`}>
                <div className="task7-coach-workspace__message-meta">
                  <span>{roleLabel(message.role)}</span>
                  {message.createdAt ? <time dateTime={message.createdAt}>{message.createdAt}</time> : null}
                </div>
                <p>{message.content}</p>
              </article>
            )) : (
              <p className="task7-coach-workspace__empty">还没有对话。告诉 Coach 你想先确认哪一个问题。</p>
            )}
          </div>

          <form className="task7-coach-workspace__composer" onSubmit={submitMessage}>
            <label className="task7-coach-workspace__composer-label" htmlFor="task7-coach-message">发给 Coach</label>
            <textarea
              id="task7-coach-message"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="例如：我应该先改哪一个动作？"
              rows={3}
            />
            <div className="task7-coach-workspace__composer-actions">
              <span>消息只会进入当前 Coach 对话。</span>
              <button className="task7-coach-workspace__send" disabled={!canSend} type="submit">
                发送 <span aria-hidden="true">&#8594;</span>
              </button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
