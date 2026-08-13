"use client";

import { Fragment, type ReactNode } from "react";

const TIME_POINT_PATTERN = /@(\d+\.?\d*)s/g;

interface TextSegment {
  text: string;
  timeMs: number | null;
}

function parseTimePoints(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  TIME_POINT_PATTERN.lastIndex = 0;
  while ((match = TIME_POINT_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), timeMs: null });
    }
    segments.push({ text: match[0], timeMs: Math.round(parseFloat(match[1]) * 1000) });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), timeMs: null });
  }
  return segments.length ? segments : [{ text, timeMs: null }];
}

export function CoachMessageText({
  text,
  analysisRef,
  onOpenVideo,
}: {
  text: string;
  analysisRef?: string | null;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
}): ReactNode {
  const segments = parseTimePoints(text);
  return (
    <>
      {segments.map((segment, index) =>
        segment.timeMs !== null && analysisRef && onOpenVideo ? (
          <button
            className="task6-time-link"
            key={index}
            onClick={() => onOpenVideo(analysisRef, segment.timeMs!)}
            type="button"
          >
            {segment.text}
          </button>
        ) : segment.timeMs !== null ? (
          <span className="task6-time-link task6-time-link--static" key={index}>{segment.text}</span>
        ) : (
          <Fragment key={index}>{segment.text}</Fragment>
        ),
      )}
    </>
  );
}
