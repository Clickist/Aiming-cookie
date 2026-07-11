"use client";

import Link from "next/link";

import PlotlyChart from "@/components/PlotlyChart";
import type {
  CoachReport,
  DiagnosisIssue,
  RootCause,
  Severity,
} from "@/lib/types";

/**
 * Renders the full Wave 2 coach report. Server page already gated on
 * status === "done" and unwrapped the CoachReport payload — this view only
 * handles presentation + empty-state fallbacks for optional fields.
 *
 * Layout = dark bento, 12-col editorial grid (matches
 * stitch_cursor_design_system/dark_mode_aiming_cookie_3/code.html).
 */

const SEVERITY_RANK: Record<Severity, number> = { fix: 3, watch: 2, info: 1 };

interface ReportViewProps {
  report: CoachReport;
  sessionId: number;
}

export default function ReportView({ report, sessionId }: ReportViewProps) {
  const { diagnosis, narration, notes } = report;
  const profile = diagnosis.profile;

  // Issues are pre-sorted by priority on the backend, but we re-sort here so
  // the contract holds even if the backend changes its sort strategy.
  const issues = [...diagnosis.issues].sort(
    (a, b) =>
      (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0) ||
      a.priority - b.priority,
  );

  const meta = diagnosis.meta ?? {};
  const radarFigure = report.figures?.radar;
  const decelFigure = report.figures?.decel_curve;
  const confidencePct = Math.round(profile.confidence * 100);

  return (
    <div className="min-h-dvh flex flex-col">
      {/* ---------- Top nav (matches app/page.tsx hairline bar) ---------- */}
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline sticky top-0 z-30">
        <div className="flex items-center gap-sm">
          <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
            Aiming Cookie
          </span>
          <div className="h-4 w-px bg-outline mx-xs" />
          <span className="text-label-md text-on-surface-variant">
            Coach Report · #{sessionId}
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
            href={`/sessions/${sessionId}`}
            className="text-label-md text-on-surface-variant/70 hover:text-primary transition-colors"
          >
            处理页
          </Link>
        </div>
      </header>

      <main className="flex-grow pt-lg pb-32">
        <div className="max-w-[var(--spacing-container-max)] mx-auto px-md">
          {/* ---------- Hero card (full width) ---------- */}
          <section className="mb-lg">
            <div className="glass-card p-xl flex flex-col md:flex-row justify-between items-start md:items-end gap-md relative overflow-hidden">
              <div className="z-10 w-full md:w-2/3">
                <span className="inline-block bg-surface-container-highest text-on-surface px-sm py-base rounded-full font-mono text-label-sm uppercase mb-sm tracking-widest">
                  Player archetype
                </span>
                <h1 className="font-display text-display-lg text-on-surface mb-sm tracking-tight">
                  {profile.label}
                </h1>
                {profile.secondary_tags.length > 0 && (
                  <div className="flex flex-wrap gap-xs mt-sm">
                    {profile.secondary_tags.map((tag) => (
                      <span
                        key={tag}
                        className="text-label-sm text-on-surface-variant border border-outline-variant px-sm py-base rounded-full"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <MetaLine meta={meta} />
              </div>
              <div className="z-10 w-full md:w-1/3 flex flex-col md:items-end">
                <div className="font-mono text-label-sm text-on-surface-variant uppercase mb-base">
                  匹配度
                </div>
                <div className="text-display-lg text-primary font-mono">
                  {confidencePct}%
                </div>
              </div>
              {/* Decorative orange hairline gradient — pure aesthetic. */}
              <div className="absolute right-0 top-0 h-full w-1 bg-gradient-to-b from-primary to-transparent pointer-events-none" />
            </div>
          </section>

          {/* ---------- Bento grid ---------- */}
          <div className="editorial-grid">
            {/* Coach narration (8 cols, left orange border) */}
            <section className="col-span-12 md:col-span-8 glass-card p-xl border-l-4 border-l-primary">
              <h2 className="font-display text-headline-sm text-on-surface mb-md flex items-center gap-xs">
                <span className="font-mono text-primary font-bold">§</span>
                教练讲解
              </h2>
              {narration ? (
                <>
                  <blockquote className="text-headline-sm leading-snug text-on-surface italic mb-lg whitespace-pre-wrap">
                    “{narration}”
                  </blockquote>
                  {notes.length > 0 && (
                    <p className="text-label-md text-on-surface-variant">
                      {notes.join(" · ")}
                    </p>
                  )}
                </>
              ) : (
                <div className="p-md bg-surface-container border border-outline rounded-md">
                  <p className="text-body-md text-on-surface-variant">
                    讲解生成失败。诊断数据仍在，参考下方结构化问题列表。
                  </p>
                  {notes.length > 0 && (
                    <p className="text-label-sm text-on-surface-variant mt-sm">
                      {notes.join(" · ")}
                    </p>
                  )}
                </div>
              )}
            </section>

            {/* Radar (4 cols) */}
            {radarFigure && (
              <section className="col-span-12 md:col-span-4 glass-card p-md flex flex-col min-h-[400px]">
                <h3 className="font-mono text-label-sm text-on-surface-variant uppercase self-start mb-md tracking-widest">
                  Skill Distribution
                </h3>
                <div className="flex-grow w-full min-h-[300px]">
                  <PlotlyChart figure={radarFigure} />
                </div>
              </section>
            )}

            {/* Prioritized issues (7 cols) */}
            <section className="col-span-12 md:col-span-7 space-y-md">
              <h2 className="font-display text-headline-sm text-on-surface mb-sm px-base">
                Prioritized mechanical issues
              </h2>
              {issues.length === 0 ? (
                <div className="glass-card p-md">
                  <p className="text-body-md text-on-surface-variant">
                    无诊断问题。本次表现稳定。
                  </p>
                </div>
              ) : (
                issues.map((issue) => (
                  <IssueCard key={issue.signal} issue={issue} />
                ))
              )}
            </section>

            {/* Deceleration curve (5 cols) */}
            {decelFigure && (
              <section className="col-span-12 md:col-span-5 glass-card p-md flex flex-col min-h-[400px]">
                <h3 className="font-mono text-label-sm text-on-surface-variant uppercase mb-lg tracking-widest">
                  Velocity Profile · Decel Curve
                </h3>
                <div className="flex-grow w-full min-h-[300px]">
                  <PlotlyChart figure={decelFigure} />
                </div>
              </section>
            )}
          </div>
        </div>
      </main>

      {/* ---------- Sticky bottom action bar ---------- */}
      <div className="fixed bottom-0 left-0 right-0 z-40 bg-surface-container-lowest/80 backdrop-blur-md border-t border-outline">
        <div className="max-w-[var(--spacing-container-max)] mx-auto px-md py-md flex flex-wrap justify-between items-center gap-md">
          <div className="hidden md:flex flex-col">
            <span className="font-mono text-label-sm text-on-surface-variant uppercase tracking-widest">
              Next step
            </span>
            <span className="text-body-md text-on-surface">
              和教练对话 · 训练计划 · 复测
            </span>
          </div>
          <div className="flex gap-md items-center w-full md:w-auto">
            <button
              type="button"
              disabled
              title="待接通(切片 4)"
              className="flex-grow md:flex-none px-xl py-md bg-transparent border border-outline text-on-surface-variant/60 font-mono text-label-md uppercase tracking-widest cursor-not-allowed"
            >
              导出 PDF
            </button>
            <button
              type="button"
              disabled
              title="待接通(切片 4)"
              className="flex-grow md:flex-none px-xl py-md bg-transparent border border-outline text-on-surface-variant/60 font-mono text-label-md uppercase tracking-widest cursor-not-allowed"
            >
              复测
            </button>
            <Link
              href={`/sessions/${sessionId}/coach`}
              className="flex-grow md:flex-none px-xl py-md bg-primary text-on-primary font-mono text-label-md uppercase tracking-widest font-bold hover:brightness-110 transition-all shadow-xl"
            >
              和教练对话 →
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- subcomponents ---------- */

function IssueCard({ issue }: { issue: DiagnosisIssue }) {
  const badgeClass =
    issue.severity === "fix"
      ? "bg-error-container text-on-error-container"
      : issue.severity === "watch"
        ? "bg-primary-container text-on-primary-container"
        : "bg-secondary-container text-on-secondary-container";

  const rootCauses = issue.root_causes ?? [];
  // Root cause chain is rendered as three ordered layers (symptom → physical →
  // training) when present; we preserve backend ordering verbatim.
  const causeByLevel = (level: string): RootCause | undefined =>
    rootCauses.find((rc) => rc.level === level);

  return (
    <div className="glass-card p-md group">
      <div className="flex items-start gap-md mb-sm">
        <div
          className={`${badgeClass} px-sm py-base rounded font-mono text-label-sm uppercase shrink-0`}
        >
          P{issue.priority}
        </div>
        <div className="flex-grow min-w-0">
          <h4 className="text-headline-sm text-on-surface mb-xs break-words">
            {issue.signal}
          </h4>
          <p className="text-label-md text-on-surface-variant">
            {issue.priority_reason}
          </p>
        </div>
      </div>

      {/* Root cause chain — only render layers that exist. */}
      {rootCauses.length > 0 && (
        <ol className="mt-md space-y-base border-l border-outline pl-md">
          {(["symptom", "physical", "training"] as const).map((level) => {
            const rc = causeByLevel(level);
            if (!rc) return null;
            return (
              <li key={level} className="flex gap-sm">
                <span className="font-mono text-label-sm text-on-surface-variant uppercase tracking-widest w-20 shrink-0">
                  {level}
                </span>
                <span className="text-body-md text-on-surface">{rc.text}</span>
              </li>
            );
          })}
        </ol>
      )}

      {/* Prescriptions as chips. */}
      {issue.prescriptions.length > 0 && (
        <div className="mt-md">
          <div className="font-mono text-label-sm text-on-surface-variant uppercase mb-base tracking-widest">
            训练处方
          </div>
          <div className="flex flex-wrap gap-xs">
            {issue.prescriptions.map((p, idx) => (
              <span
                key={`${p.scenario}-${idx}`}
                title={p.reason}
                className="text-label-md text-on-surface border border-primary px-sm py-base rounded-full hover:bg-primary hover:text-on-primary transition-colors cursor-help"
              >
                {p.scenario}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * One-line "Technical Meta" strip in the hero card. Renders whatever meta keys
 * the backend happens to carry — absent keys are simply omitted. We use
 * snake_case keys to match the wire JSON exactly (no remapping layer).
 */
function MetaLine({ meta }: { meta: Record<string, unknown> }) {
  const entries: Array<{ key: string; label: string; value: string }> = [];
  const cm = meta.cm_per_360;
  if (typeof cm === "number") {
    entries.push({
      key: "cm360",
      label: "cm/360",
      value: cm.toFixed(1),
    });
  }
  const fps = meta.fps;
  if (typeof fps === "number") {
    entries.push({ key: "fps", label: "fps", value: fps.toFixed(0) });
  }
  const scenario = meta.scenario;
  if (typeof scenario === "string" && scenario) {
    entries.push({ key: "scenario", label: "场景", value: scenario });
  }
  const summaryType = meta.summary_type;
  if (typeof summaryType === "string" && summaryType) {
    entries.push({
      key: "type",
      label: "type",
      value: summaryType,
    });
  }

  if (entries.length === 0) return null;

  return (
    <div className="mt-md flex flex-wrap items-center gap-xs font-mono text-label-sm text-on-surface-variant">
      {entries.map((e, idx) => (
        <span key={e.key} className="flex items-center gap-xs">
          {idx > 0 && <span className="text-outline-variant">·</span>}
          <span className="uppercase tracking-widest">{e.label}</span>
          <span className="text-on-surface">{e.value}</span>
        </span>
      ))}
    </div>
  );
}
