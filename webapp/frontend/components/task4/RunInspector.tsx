"use client";

import { useMemo } from "react";

import { presentRunInspector } from "@/lib/contracts";
import type { KovaaKRunItem, KovaaKRunListItem } from "@/lib/types";
import { Button, Notice, Status } from "@/ui/primitives";

function EvidenceChip({ label, state }: { label: string; state: string | undefined }) {
  const normalized = state ?? "missing";
  let chipState: "ok" | "part" | "miss" | "bad" = "miss";
  if (["available", "attached", "aligned"].includes(normalized)) chipState = "ok";
  else if (normalized === "partial") chipState = "part";
  else if (normalized === "failed") chipState = "bad";
  const icon = chipState === "ok" ? "✓" : chipState === "part" ? "◐" : chipState === "bad" ? "✕" : "−";
  return (
    <span className="task4-ev" data-state={chipState}>
      <i aria-hidden="true">{icon}</i>
      <span>{label}</span>
    </span>
  );
}

function rawEvidenceState(run: KovaaKRunItem | KovaaKRunListItem, key: string): string | undefined {
  if (key === "video") return run.evidence_availability.mp4 ?? run.evidence_availability.video ?? run.source_availability.mp4;
  if (key === "raw") return run.evidence_availability.raw ?? run.trace_quality.availability;
  return run.evidence_availability[key] ?? run.source_availability[key];
}

export function RunInspector({ run }: {
  run: KovaaKRunItem | KovaaKRunListItem;
}) {
  const projection = useMemo(() => presentRunInspector(run), [run]);
  const hasAnalysis = run.analysis_count > 0;
  return (
    <div className="task4-inspector-body">
      <section aria-labelledby="run-identity-title">
        <h3 id="run-identity-title">训练身份</h3>
        <dl className="task4-facts">
          <div><dt>场景</dt><dd>{projection.identity.scenario}</dd></div>
          <div><dt>时间</dt><dd>{new Date(projection.identity.createdAt).toLocaleString("zh-CN")}</dd></div>
          <div><dt>整理状态</dt><dd>{projection.identity.finalization}</dd></div>
        </dl>
      </section>

      <section aria-labelledby="run-evidence-title">
        <h3 id="run-evidence-title">Evidence 来源</h3>
        <div className="task4-evidence-list">
          {Object.entries(projection.evidence).map(([key, value]) => (
            <div className="task4-evidence-row" key={key}>
              <EvidenceChip label={key === "video" ? "视频" : key === "performance" ? "Performance" : key === "stats" ? "Stats" : "Raw"} state={rawEvidenceState(run, key)} />
              <span>覆盖 {value.coverage === null ? "未提供" : `${Math.round(value.coverage * 100)}%`}</span>
              <span>对齐 {value.alignment === "aligned" ? "已对齐" : value.alignment}</span>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="run-capability-title">
        <h3 id="run-capability-title">分析能力</h3>
        <div className="task4-mode-list">
          {projection.capabilities.modes.map((mode) => (
            <div className="task4-mode-row" key={mode.code}>
              <strong>{mode.code === "input_native" ? "Input-native" : mode.code === "multimodal" ? "Multimodal" : "Video fallback"}</strong>
              <Status tone={mode.available ? "success" : "neutral"}>{mode.available ? "可选" : "不可选"}</Status>
              {!mode.available && mode.reason ? <span>{mode.reason}</span> : null}
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="run-analysis-title">
        <h3 id="run-analysis-title">关联 Analysis</h3>
        {hasAnalysis ? (
          <p className="task4-muted">已有 {run.analysis_count} 条分析记录，请从分析记录进入 Analysis workspace。</p>
        ) : (
          <p className="task4-muted">尚未创建 Analysis；重试会产生新的 attempt，不会覆盖原记录。</p>
        )}
      </section>

      <section aria-labelledby="run-operations-title">
        <h3 id="run-operations-title">主要操作</h3>
        <div className="task4-operation-list">
          <Button href={`/analyze?run=${encodeURIComponent(run.run_ref)}`} variant="primary">开始分析</Button>
          <Button data-operation="view_source" disabled variant="secondary">查看来源</Button>
          <Button data-operation="manage_storage" disabled variant="secondary">管理 Run 存储</Button>
        </div>
        <p className="task4-muted">当前版本没有独立来源查看或 Storage 管理页；Evidence 状态已在本 Inspector 展示。</p>
        {run.limitations.length ? <Notice tone="warning" title="来源限制">{run.limitations.join("、")}</Notice> : null}
      </section>
    </div>
  );
}
