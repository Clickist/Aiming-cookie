import type { ReactNode } from "react";

import { Badge } from "@/ui/primitives";

export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="task3-page-heading">
      <div>
        {eyebrow ? <div className="task3-eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="task3-page-actions">{actions}</div> : null}
    </header>
  );
}

const EVIDENCE_TEXT: Record<string, string> = {
  available: "可用",
  attached: "已关联",
  partial: "部分可用",
  missing: "缺失",
  unavailable: "来源不可用",
  not_present: "未提供",
  unsupported: "不支持",
  aligned: "已对齐",
  failed: "失败",
};

export function EvidenceChip({
  label,
  state,
  text,
}: {
  label: string;
  state: string | undefined;
  text?: string;
}) {
  const normalized = state ?? "missing";
  const ok = normalized === "available" || normalized === "attached" || normalized === "aligned";
  const part = normalized === "partial";
  const tone = ok ? "ok" : part ? "part" : "bad";
  const icon = ok ? "✓" : part ? "!" : "✕";
  const displayText = text ? text : ok ? label : EVIDENCE_TEXT[normalized] ?? normalized;
  return (
    <span className={`task3-evidence-chip task3-evidence-chip--${tone}`}>
      <i aria-hidden="true">{icon}</i>
      <span>{displayText}</span>
    </span>
  );
}

export function PreviewBadge() {
  return <Badge className="task3-preview-badge">预览 / 实验</Badge>;
}

const MODE_LABELS: Record<string, string> = {
  multimodal: "多源模式",
  input_native: "输入原生",
  video_fallback: "视频兼容",
};

export function ModeBadge({ mode }: { mode: string | null | undefined }) {
  return <Badge className="task3-mode-badge">{MODE_LABELS[mode ?? ""] ?? mode ?? "未知模式"}</Badge>;
}
