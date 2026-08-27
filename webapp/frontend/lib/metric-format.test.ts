import assert from "node:assert/strict";
import { test } from "node:test";

import { projectTimelineMarkers } from "./metric-format";
import type { TimelineEvent } from "./types";

// P1 事件上轴：标记数据契约 { timeMs, type, label } 的投影行为锁定。
// 规格出处：docs/video-pane-revamp-brief.md §二 P1（type→语义色 token 映射）。

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
