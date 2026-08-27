import assert from "node:assert/strict";
import { test } from "node:test";

import {
  projectEvidenceSegmentButtons,
  projectPeakFallbackButtons,
  projectTimelineMarkers,
  SEGMENT_KIND_LABELS,
  SIGNAL_SEGMENT_FALLBACK_LIMIT,
} from "./metric-format";
import type { TimelineEvent } from "./types";

// P1 事件上轴：标记数据契约 { timeMs, type, label } 的投影行为锁定。
// 规格出处：docs/video-pane-revamp-brief.md §二 P1（type→语义色 token 映射）。
// P2 信号片段循环：时间段按钮数据映射（权威 evidence-segments）与 peak
// 降级路径的行为锁定。规格出处：docs/video-pane-revamp-brief.md §二 P2＋D2/D5。

function event(overrides: Partial<TimelineEvent>): TimelineEvent {
  return { frame: null, time_s: null, relative_ms: null, type: "kill", label: "", source: null, ...overrides };
}

test("timeline markers keep kill / miss / peak buckets and drop corrective", () => {
  const markers = projectTimelineMarkers([
    event({ type: "peak", time_s: 2, label: "速度峰值" }),
    event({ type: "corrective", time_s: 3, label: "修正" }),
    event({ type: "kill", time_s: 4, label: "击杀" }),
    event({ type: "miss", time_s: 5, label: "未命中" }),
    event({ type: "tracking_loss", time_s: 6, label: "偏离" }),
    // brief：miss/death 归同一 --event-miss 桶。
    event({ type: "death", time_s: 7, label: "阵亡" }),
  ]);
  assert.deepEqual(
    markers.map((marker) => marker.type),
    ["peak", "kill", "miss", "miss"],
  );
});

test("timeMs derives from relative_ms first, else from time_s×1000, else the event is dropped", () => {
  const markers = projectTimelineMarkers([
    // relative_ms 是权威值，原样透传不做二次换算。
    event({ type: "kill", relative_ms: 12500 }),
    event({ type: "peak", time_s: 3.3333 }),
    event({ type: "miss", label: "无时间" }),
    event({ type: "kill", time_s: Number.NaN }),
    // 负时间视为无效证据，不编造。
    event({ type: "kill", relative_ms: -1 }),
  ]);
  assert.deepEqual(
    markers.map((marker) => [marker.timeMs, marker.type]),
    [[3333, "peak"], [12500, "kill"]],
  );
});

test("markers sort ascending by time and keep backend labels with kind-label fallback", () => {
  const markers = projectTimelineMarkers([
    event({ type: "kill", time_s: 9, label: "击杀" }),
    event({ type: "peak", time_s: 1, label: "速度峰值" }),
    event({ type: "miss", time_s: 5, label: "" }),
  ]);
  assert.deepEqual(
    markers.map((marker) => [marker.timeMs, marker.label]),
    [[1000, "速度峰值"], [5000, "未命中"], [9000, "击杀"]],
  );
});

/* ── P2 信号片段循环：按钮数据映射（brief §二 P2 §四核查项 2）────────── */

function segment(overrides: Record<string, unknown>): Parameters<typeof projectEvidenceSegmentButtons>[0]["segments"][number] {
  return {
    segment_id: "analysis:42:segment:worst:20",
    analysis_ref: "analysis:42",
    analyzer_ref: "native_flicking.v1",
    segment_kind: "worst",
    start_ms: 1785151593273,
    end_ms: 1785151593750,
    focus_start_ms: 1785151593273,
    focus_end_ms: 1785151593750,
    title_key: "static_clicking.worst",
    rank_reason: "worst",
    issue_refs: [],
    metric_refs: [],
    event_refs: [],
    available_channels: [],
    source_coverage: null,
    confidence: null,
    limitations: [],
    playback: {
      schema_version: "evidence_segment_playback.v1",
      availability: "available",
      video_route: "/api/sessions/42/video",
      relative_start_ms: 9393,
      relative_end_ms: 9870,
      limitations: [],
    },
    ...overrides,
  } as Parameters<typeof projectEvidenceSegmentButtons>[0]["segments"][number];
}

function payload(segments: Array<Parameters<typeof projectEvidenceSegmentButtons>[0]["segments"][number]>) {
  return {
    schema_version: "frontend_evidence_segments.v1" as const,
    analysis_ref: "analysis:42",
    video_availability: "available" as const,
    video_route: "/api/sessions/42/video",
    canonical_window_start_ms: 1785151583880,
    segments,
  };
}

test("P2 authoritative windows map evidence-segments into ordered segment buttons", () => {
  const buttons = projectEvidenceSegmentButtons(payload([
    segment({}),
    segment({
      segment_id: "analysis:42:segment:improved:65",
      segment_kind: "improved",
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "available",
        video_route: "/api/sessions/42/video",
        relative_start_ms: 1000,
        relative_end_ms: 2000,
        limitations: [],
      },
    }),
  ]));
  // 起止毫秒用 preroll 校正后的视频相对区间；id 保留关联锚；结果按起点升序。
  assert.equal(buttons.length, 2);
  assert.deepEqual(
    buttons.map((button) => button.id),
    ["analysis:42:segment:improved:65", "analysis:42:segment:worst:20"],
  );
  assert.deepEqual(
    buttons.map((button) => [button.startMs, button.endMs, button.kindLabel]),
    [[1000, 2000, SEGMENT_KIND_LABELS.improved], [9393, 9870, SEGMENT_KIND_LABELS.worst]],
  );
});

test("P2 segment mapping fails closed on unusable playback windows and unknown kinds", () => {
  const buttons = projectEvidenceSegmentButtons(payload([
    // unavailable 播放档（relative 为 null）→ 丢弃，交给降级路径。
    segment({
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "unavailable",
        video_route: null,
        relative_start_ms: null,
        relative_end_ms: null,
        limitations: ["local_video_seek_unavailable"],
      },
    }),
    // NaN 起点 / 零长区间 → 丢弃。
    segment({
      segment_id: "analysis:42:segment:x:9",
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "available",
        video_route: "/api/sessions/42/video",
        relative_start_ms: Number.NaN,
        relative_end_ms: 500,
        limitations: [],
      },
    }),
    segment({
      segment_id: "analysis:42:segment:y:8",
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "available",
        video_route: "/api/sessions/42/video",
        relative_start_ms: 700,
        relative_end_ms: 700,
        limitations: [],
      },
    }),
    // 未知 kind 原样透传不编造语义。
    segment({ segment_id: "analysis:42:segment:z:7", segment_kind: "exotic" }),
    // kind 缺失 → 兜底「片段」。
    segment({ segment_id: "analysis:42:segment:w:6", segment_kind: null }),
  ]));
  assert.deepEqual(
    buttons.map((button) => [button.id, button.kindLabel]),
    [["analysis:42:segment:z:7", "exotic"], ["analysis:42:segment:w:6", "片段"]],
  );
});

test("P2 fallback derives peak-centered windows from P1 markers; kill/miss stay point markers", () => {
  const markers = projectTimelineMarkers([
    event({ type: "peak", time_s: 10 }),
    event({ type: "kill", time_s: 20 }),
    event({ type: "miss", time_s: 30 }),
    event({ type: "corrective", time_s: 40 }),
    event({ type: "peak", time_s: 3.4, label: "甩枪" }),
  ]);
  const buttons = projectPeakFallbackButtons(markers);
  assert.equal(buttons.length, 2);
  const [early, late] = buttons;
  assert.ok(early && late);
  // 锚点 ± 固定窗口；label 沿用标记词。
  assert.equal(early.id, "peak-3400");
  assert.deepEqual([early.startMs, early.endMs], [900, 5900]);
  assert.equal(early.kindLabel, "甩枪");
  assert.deepEqual([late.startMs, late.endMs], [7500, 12500]);
});

test("P2 fallback window bounds clamp to media duration and the row keeps a hard cap", () => {
  const markers = projectTimelineMarkers(
    Array.from({ length: 20 }, (_, index) =>
      event({ type: "peak", time_s: 1 + index })),
  );
  const capped = projectPeakFallbackButtons(markers);
  assert.equal(capped.length, SIGNAL_SEGMENT_FALLBACK_LIMIT);
  // 有时长上限：终点钳在时长内，越界退化为空区间的窗口剔除。
  const clamped = projectPeakFallbackButtons(markers, { maxMs: 4000 });
  assert.ok(clamped.length > 0 && clamped.length <= SIGNAL_SEGMENT_FALLBACK_LIMIT);
  for (const button of clamped) {
    assert.ok(button.startMs >= 0 && button.endMs <= 4000 && button.endMs > button.startMs);
  }
  const first = clamped[0];
  assert.ok(first);
  assert.deepEqual([first.startMs, first.endMs], [0, 3500]);
});
