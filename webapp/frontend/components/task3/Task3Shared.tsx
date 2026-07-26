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

export function EvidenceChip({ label, state }: { label: string; state: string | undefined }) {
  const normalized = state ?? "missing";
  const tone = normalized === "available" || normalized === "attached" || normalized === "aligned"
    ? "success"
    : normalized === "partial"
      ? "warning"
      : "neutral";
  return (
    <span className="task3-evidence-chip">
      <span aria-hidden="true">{tone === "success" ? "✓" : tone === "warning" ? "!" : "−"}</span>
      <span>{label}</span>
      <span>{EVIDENCE_TEXT[normalized] ?? normalized}</span>
    </span>
  );
}

export function PreviewBadge() {
  return <Badge tone="warning">预览 / 实验</Badge>;
}
