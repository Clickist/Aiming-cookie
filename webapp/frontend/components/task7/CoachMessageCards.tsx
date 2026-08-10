"use client";

import { useEffect, useMemo, useState } from "react";

import { getSession } from "@/lib/api";
import { presentAnalysisWorkspace, type AnalysisMetricPresentation, type AnalysisWorkspacePresentation } from "@/lib/contracts";
import type { CoachMessageCardV1, CoachThreadMessageOut, TimelineEvent } from "@/lib/types";

const SEGMENT_SEEK_PADDING_MS = 2000;

const presentationCache = new Map<number, AnalysisWorkspacePresentation>();

function analysisIdFromRef(value: string): number | null {
  const match = /^analysis:([1-9][0-9]*)$/.exec(value);
  return match ? Number(match[1]) : null;
}

async function loadPresentation(analysisRef: string, signal: AbortSignal): Promise<AnalysisWorkspacePresentation | null> {
  const analysisId = analysisIdFromRef(analysisRef);
  if (analysisId === null) return null;
  const cached = presentationCache.get(analysisId);
  if (cached) return cached;
  const session = await getSession(analysisId, { signal });
  const presentation = presentAnalysisWorkspace(session);
  if (presentation) presentationCache.set(analysisId, presentation);
  return presentation;
}

function metricValue(metric: AnalysisMetricPresentation): string {
  if (metric.value === null) return "不可用";
  const value = typeof metric.value === "number"
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(metric.value)
    : metric.value;
  return metric.unit ? `${value} ${metric.unit}` : String(value);
}

function cardMetrics(card: CoachMessageCardV1, presentation: AnalysisWorkspacePresentation): AnalysisMetricPresentation[] {
  const metrics = [...presentation.metrics.formal, ...presentation.metrics.summary, ...presentation.metrics.limited]
    .filter((metric, index, all) => all.findIndex((item) => item.referenceKey === metric.referenceKey) === index);
  if (!card.target_ref) return metrics.slice(0, 3);
  const target = metrics.find((metric) => metric.referenceKey === card.target_ref || metric.key === card.target_ref);
  return target ? [target, ...metrics.filter((metric) => metric !== target).slice(0, 2)] : metrics.slice(0, 3);
}

function eventTime(event: TimelineEvent): number | null {
  if (typeof event.relative_ms === "number") return event.relative_ms;
  if (typeof event.time_s === "number") return event.time_s * 1000;
  return null;
}

function eventClass(type: string): string {
  if (type === "kill" || type === "miss" || type === "corrective" || type === "peak") return type;
  return "other";
}

function TimelineCard({ presentation }: { presentation: AnalysisWorkspacePresentation }) {
  const events = presentation.timeline.filter((event) => eventTime(event) !== null).slice(0, 32);
  const maxTime = Math.max(1, ...events.map((event) => eventTime(event) ?? 0));
  const counts = events.reduce<Record<string, number>>((result, event) => {
    const key = eventClass(event.type);
    result[key] = (result[key] ?? 0) + 1;
    return result;
  }, {});
  const summary = Object.entries(counts).map(([type, count]) => `${type} ${count}`).join("，");
  return (
    <>
      <div className="task7-message-card__chart" role="img" aria-label={events.length ? `事件时间线：${summary}` : "本次没有可展示的时间线事件"}>
        <svg preserveAspectRatio="none" viewBox="0 0 320 54">
          <line className="task7-message-card__chart-line" x1="4" x2="316" y1="27" y2="27" />
          {events.map((event, index) => (
            <circle
              className={`task7-message-card__event task7-message-card__event--${eventClass(event.type)}`}
              cx={4 + ((eventTime(event) ?? 0) / maxTime) * 312}
              cy={index % 2 === 0 ? 21 : 33}
              key={`${event.type}-${eventTime(event)}-${index}`}
              r="3.5"
            />
          ))}
        </svg>
      </div>
      <p className="task7-message-card__caption">{events.length ? `已显示 ${events.length} 个可定位事件` : "当前安全投影没有可展示的事件"}</p>
    </>
  );
}

function MessageCard({
  card,
  onOpenVideo,
  presentation,
}: {
  card: CoachMessageCardV1;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const metrics = cardMetrics(card, presentation);
  const firstTime = card.time_range_ms?.[0] ?? presentation.timeline.map(eventTime).find((value): value is number => value !== null) ?? 0;
  const title = card.kind === "metrics" ? "关键数据" : card.kind === "timeline" ? "事件时间线" : "视频证据";
  return (
    <article className="task7-message-card" data-kind={card.kind}>
      <header className="task7-message-card__header">
        <div>
          <span className="task7-message-card__eyebrow">{presentation.scenario}</span>
          <h3>{title}</h3>
        </div>
        <span className="task7-message-card__source">Analysis</span>
      </header>
      {card.kind === "metrics" ? (
        <>
          {presentation.issues[0] ? <p className="task7-message-card__lead">{presentation.issues[0].signal}</p> : null}
          <dl className="task7-message-card__metrics">
            {metrics.map((metric) => <div key={metric.referenceKey ?? metric.key}><dt>{metric.key}</dt><dd>{metricValue(metric)}</dd></div>)}
          </dl>
        </>
      ) : null}
      {card.kind === "timeline" ? <TimelineCard presentation={presentation} /> : null}
      {card.kind === "evidence" ? (
        <p className="task7-message-card__lead">{presentation.issues[0]?.signal ?? presentation.headline}</p>
      ) : null}
      {card.kind === "evidence" && presentation.video.kind === "seekable" && onOpenVideo ? (
        <button className="task7-message-card__action" onClick={() => {
          const paddedTime = Math.max(0, firstTime - SEGMENT_SEEK_PADDING_MS);
          onOpenVideo(card.analysis_ref, paddedTime);
        }} type="button">在视频中查看 <span aria-hidden="true">→</span></button>
      ) : null}
    </article>
  );
}

export function CoachMessageCards({
  message,
  onOpenVideo,
}: {
  message: CoachThreadMessageOut;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
}) {
  const cards = useMemo(() => (message.cards ?? []).slice(0, 4), [message.cards]);
  const [presentations, setPresentations] = useState<Record<string, AnalysisWorkspacePresentation | null>>({});
  const refsKey = cards.map((card) => card.analysis_ref).join("|");

  useEffect(() => {
    if (!cards.length) return undefined;
    const controller = new AbortController();
    void Promise.all(Array.from(new Set(cards.map((card) => card.analysis_ref))).map(async (analysisRef) => {
      try {
        return [analysisRef, await loadPresentation(analysisRef, controller.signal)] as const;
      } catch {
        return [analysisRef, null] as const;
      }
    })).then((entries) => {
      if (!controller.signal.aborted) setPresentations(Object.fromEntries(entries));
    });
    return () => controller.abort();
  }, [refsKey]);

  const visible = cards.flatMap((card, index) => {
    const presentation = presentations[card.analysis_ref];
    return presentation ? [{ card, index, presentation }] : [];
  });
  if (!visible.length) return null;

  return (
    <section aria-label="Coach 引用的分析内容" className="task7-message-cards">
      {visible.map(({ card, index, presentation }) => (
        <MessageCard card={card} key={`${card.kind}-${card.analysis_ref}-${index}`} onOpenVideo={onOpenVideo} presentation={presentation} />
      ))}
    </section>
  );
}
