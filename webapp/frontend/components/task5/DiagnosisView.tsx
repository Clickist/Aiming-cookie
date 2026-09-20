import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { metricDescription, metricLabel, metricReference } from "@/lib/metric-format";
import { t, useT, type MessageKey } from "@/lib/i18n";
import { Badge, Button, Empty, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

// i18n 批 3：legacy 候选层级标签是字典键（MessageKey），渲染时经 t() 解析（§2c）。
const LEGACY_CANDIDATE_LEVEL_KEYS: Record<string, MessageKey> = {
  symptom: "analysis.diagnosis.levelSymptom",
  physical: "analysis.diagnosis.levelPhysical",
  training: "analysis.diagnosis.levelTraining",
};

function severityTone(severity: "info" | "watch" | "fix"): "neutral" | "warning" | "error" {
  if (severity === "fix") return "error";
  if (severity === "watch") return "warning";
  return "neutral";
}

function formatMetricValue(value: number | string | null, unit: string | null): string {
  if (value === null) return t("metric.value.unavailable");
  const shown = typeof value === "number" ? Number(value.toFixed(3)) : value;
  if (unit === "percent") return `${shown}%`;
  if (unit === "dimensionless" || unit === "ratio") return String(shown);
  return `${shown}${unit ? ` ${unit}` : ""}`;
}

function IssueBody({
  issue,
}: {
  issue: AnalysisWorkspacePresentation["issues"][number];
}) {
  const t = useT();
  if (issue.presentationKind === "registry-backed") {
    return (
      <div className={styles.issueBody}>
        {issue.priorityReason ? <p>{issue.priorityReason}</p> : null}
        <dl className={styles.issueCause}>
          <div>
            <dt>{t("analysis.diagnosis.candidateTitle")}</dt>
            <dd>{issue.candidateExplanation ?? t("analysis.diagnosis.candidateFallback")}</dd>
          </div>
          <div>
            <dt>{t("analysis.diagnosis.expectedTitle")}</dt>
            <dd>{issue.expectedResult ?? t("analysis.diagnosis.expectedFallback")}</dd>
          </div>
        </dl>
      </div>
    );
  }

  return (
    <div className={styles.issueBody}>
      {issue.priorityReason ? <p>{issue.priorityReason}</p> : null}
      {issue.rootCauses.length ? (
        <dl className={styles.issueCause}>
          {issue.rootCauses.map((cause) => {
            const levelKey = LEGACY_CANDIDATE_LEVEL_KEYS[cause.level];
            return (
              <div key={`${cause.level}-${cause.text}`}>
                <dt>{levelKey === undefined ? cause.level : t(levelKey)}</dt>
                <dd>{cause.text}</dd>
              </div>
            );
          })}
        </dl>
      ) : null}
    </div>
  );
}

export function DiagnosisView({
  onAskCoach,
  onSelectEvidence,
  onSelectMetric,
  presentation,
  selectedIssue,
}: {
  onAskCoach: () => void;
  onSelectEvidence: (issueIndex: number) => void;
  onSelectMetric: (metric: string) => void;
  presentation: AnalysisWorkspacePresentation;
  selectedIssue: number | null;
}) {
  const t = useT();
  const severityByMetric = presentation.issues.reduce<Record<string, "info" | "watch" | "fix">>(
    (acc, issue) => {
      for (const ref of issue.metricRefs) {
        if (!acc[ref] || issue.severity === "fix" || (issue.severity === "watch" && acc[ref] === "info")) {
          acc[ref] = issue.severity;
        }
      }
      return acc;
    },
    {},
  );

  const hasClassifiedScenario = presentation.family.status !== "unavailable";
  const prescription = hasClassifiedScenario
    ? presentation.issues.map((issue) => issue.prescriptions[0]).find(Boolean) ?? null
    : null;
  const expected = hasClassifiedScenario
    ? presentation.issues.map((issue) => issue.expectedResult).find(Boolean) ?? null
    : null;
  const { summary, summaryMode } = presentation.metrics;

  return (
    <div className={styles.diagnosisView}>
      <section className={styles.diagnosisLead} aria-labelledby="diagnosis-conclusion">
        <div className={styles.conclusion} id="diagnosis-conclusion">{presentation.headline}</div>
        {presentation.profile ? (
          <div className={styles.profileTag}>
            {presentation.profile.description ? (
              <span
                aria-describedby="analysis-profile-explanation"
                className={styles.profileLabel}
                tabIndex={0}
              >
                <Badge tone="neutral">{presentation.profile.label}</Badge>
                <span className={styles.profileTooltip} id="analysis-profile-explanation" role="tooltip">
                  {presentation.profile.description}
                </span>
              </span>
            ) : (
              <Badge tone="neutral">{presentation.profile.label}</Badge>
            )}
          </div>
        ) : null}
      </section>

      {presentation.issues.length === 0 ? (
        <Empty className={styles.metricSummaryEmpty} title={t("analysis.headline.none")}>
          {t("analysis.diagnosis.emptyBody")}
        </Empty>
      ) : (
        <section className={styles.issueSection} aria-labelledby="issues-title">
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle} id="issues-title">{t("analysis.diagnosis.issuesTitle")}</span>
            <span className={styles.sectionHint}>{t("analysis.diagnosis.issuesHint", { n: presentation.issues.length })}</span>
          </div>
          <div className={styles.issueList}>
            {presentation.issues.map((issue, index) => (
              <article
                className={styles.issueCard}
                data-selected={selectedIssue === index || undefined}
                key={`${issue.priority}-${issue.signal}`}
              >
                <div className={styles.issueHead}>
                  {issue.severity !== "info" ? (
                    <Status className={styles.issueSeverity} tone={severityTone(issue.severity)}>
                      {issue.severity === "fix" ? t("analysis.diagnosis.severityFix") : t("analysis.diagnosis.severityWatch")}
                    </Status>
                  ) : null}
                  {issue.claimLabel ? <Status tone="neutral">{issue.claimLabel}</Status> : null}
                  <span className={styles.issueName}>{issue.signal}</span>
                  <div className={styles.issueActions}>
                    <Button onClick={() => onSelectEvidence(index)} size="compact" variant="ghost">{t("analysis.diagnosis.viewEvidence")}</Button>
                    {issue.metricRefs[0] ? (
                      <Button onClick={() => onSelectMetric(issue.metricRefs[0])} size="compact" variant="ghost">
                        {t("analysis.diagnosis.viewMetric")}
                      </Button>
                    ) : null}
                    <Button onClick={onAskCoach} size="compact" variant="secondary">{t("analysis.diagnosis.askCoach")}</Button>
                  </div>
                </div>
                <IssueBody issue={issue} />
              </article>
            ))}
          </div>
        </section>
      )}

      {prescription || expected ? (
        <section className={styles.prescriptionSection} aria-labelledby="prescription-title">
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle} id="prescription-title">{t("analysis.diagnosis.prescriptionTitle")}</span>
          </div>
          <div className={styles.prescriptionPanel}>
            {prescription ? (
              <>
                <div className={styles.prescriptionTitle}>{prescription.scenario}</div>
                <p className={styles.prescriptionReason}>{prescription.reason}</p>
                {prescription.cue ? <Badge tone="info">{t("analysis.diagnosis.prescriptionCue", { cue: prescription.cue })}</Badge> : null}
              </>
            ) : (
              <p className={styles.prescriptionReason}>{expected}</p>
            )}
          </div>
        </section>
      ) : null}

      {!hasClassifiedScenario && presentation.issues.some((issue) => issue.prescriptions.length > 0) ? (
        <Notice tone="warning" title={t("analysis.diagnosis.unverifiedTitle")}>
          {t("analysis.diagnosis.unverifiedBody")}
        </Notice>
      ) : null}

      <section className={styles.metricSummary} aria-labelledby="core-metrics-title">
        <div className={styles.sectionHead}>
          <span className={styles.sectionTitle} id="core-metrics-title">
            {summaryMode === "descriptive" ? t("analysis.diagnosis.summaryDescriptiveTitle") : t("analysis.diagnosis.summaryFormalTitle")}
          </span>
          <span className={styles.sectionHint}>
            {summaryMode === "descriptive" ? t("analysis.diagnosis.summaryDescriptiveHint") : t("analysis.diagnosis.summaryFormalHint")}
          </span>
        </div>
        {summary.length ? (
          <div className={styles.metricSummaryPanel}>
            {summary.slice(0, 4).map((metric) => {
              const ref = metricReference(metric);
              const severity = severityByMetric[ref] ?? "info";
              return (
                <button
                  className={styles.metricRow}
                  data-metric={ref}
                  key={ref}
                  onClick={() => onSelectMetric(ref)}
                  type="button"
                >
                  <span className={styles.metricKey}>{metricLabel(metric)}</span>
                  <span className={styles.metricValue}>{formatMetricValue(metric.value, metric.unit)}</span>
                  <span className={styles.metricPlain}>
                    {metricDescription(metric)
                      ?? (metric.coverage === null ? t("analysis.diagnosis.coverageUnknown") : t("analysis.diagnosis.coverage", { pct: Math.round(metric.coverage * 100) }))}
                  </span>
                  {summaryMode !== "descriptive" ? (
                    <Status tone={severityTone(severity)}>
                      {severity === "fix" ? t("analysis.diagnosis.severityFix") : severity === "watch" ? t("analysis.diagnosis.severityWatch") : t("analysis.diagnosis.severityInfo")}
                    </Status>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : (
          <Empty className={styles.metricSummaryEmpty} title={t("analysis.diagnosis.noMetricsTitle")}>
            {t("analysis.diagnosis.noMetricsBody")}
          </Empty>
        )}
      </section>

      {presentation.limitations.length ? (
        <Notice title={t("analysis.diagnosis.scopeTitle")} tone="warning">
          {presentation.limitations.join(" ")}
        </Notice>
      ) : null}
    </div>
  );
}
