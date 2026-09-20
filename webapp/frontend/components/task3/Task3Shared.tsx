import type { ReactNode } from "react";

import { useT, type MessageKey } from "@/lib/i18n";
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

// i18n 批 2：与 history.status.* 同码同值的条目共用键（字典是单一事实源），
// 仅「部分可用 / 已对齐」两值无既有键，落在 analysis.evidence.* 下。
const EVIDENCE_KEYS: Record<string, MessageKey> = {
  available: "history.status.available",
  attached: "history.status.attached",
  partial: "analysis.evidence.partial",
  missing: "history.status.missing",
  unavailable: "history.status.sourceUnavailable",
  not_present: "history.status.notPresent",
  unsupported: "history.status.unsupported",
  aligned: "analysis.evidence.aligned",
  failed: "history.status.failed",
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
  const t = useT();
  const normalized = state ?? "missing";
  const ok = normalized === "available" || normalized === "attached" || normalized === "aligned";
  const part = normalized === "partial";
  const tone = ok ? "ok" : part ? "part" : "bad";
  const icon = ok ? "✓" : part ? "!" : "✕";
  const fallbackKey = EVIDENCE_KEYS[normalized];
  const displayText = text ? text : ok ? label : fallbackKey ? t(fallbackKey) : normalized;
  return (
    <span className={`task3-evidence-chip task3-evidence-chip--${tone}`}>
      <i aria-hidden="true">{icon}</i>
      <span>{displayText}</span>
    </span>
  );
}

export function PreviewBadge() {
  const t = useT();
  return <Badge className="task3-preview-badge">{t("analysis.badge.preview")}</Badge>;
}

// 三种分析模式与 contracts 的 analysis.input.* 同词，共用键；未知模式单独落键。
const MODE_LABEL_KEYS: Record<string, MessageKey> = {
  multimodal: "analysis.input.multimodal",
  input_native: "analysis.input.native",
  video_fallback: "analysis.input.videoFallback",
};

export function ModeBadge({ mode }: { mode: string | null | undefined }) {
  const t = useT();
  const key = MODE_LABEL_KEYS[mode ?? ""];
  return <Badge className="task3-mode-badge">{key ? t(key) : (mode ?? t("analysis.mode.unknown"))}</Badge>;
}
