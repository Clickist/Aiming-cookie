import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { metricDescription, metricLabel, metricReference } from "@/lib/metric-format";
import { Badge, Button, Empty, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

const LEGACY_CANDIDATE_LEVEL_LABEL: Record<string, string> = {
  symptom: "表现",
  physical: "物理原因",
  training: "训练方向",
};

function severityTone(severity: "info" | "watch" | "fix"): "neutral" | "warning" | "error" {
  if (severity === "fix") return "error";
  if (severity === "watch") return "warning";
  return "neutral";
}

function formatMetricValue(value: number | string | null, unit: string | null): string {
  if (value === null) return "不可用";
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
  if (issue.presentationKind === "registry-backed") {
    return (
      <div className={styles.issueBody}>
        {issue.priorityReason ? <p>{issue.priorityReason}</p> : null}
        <dl className={styles.issueCause}>
          <div>
            <dt>候选解释</dt>
            <dd>{issue.candidateExplanation ?? "当前 Analysis 未提供额外解释。"}</dd>
          </div>
          <div>
            <dt>可验证预期</dt>
            <dd>{issue.expectedResult ?? "当前 Analysis 未给出可验证预期。"}</dd>
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
          {issue.rootCauses.map((cause) => (
            <div key={`${cause.level}-${cause.text}`}>
              <dt>{LEGACY_CANDIDATE_LEVEL_LABEL[cause.level] ?? cause.level}</dt>
              <dd>{cause.text}</dd>
            </div>
          ))}
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
        <Empty className={styles.metricSummaryEmpty} title="当前证据不足以形成明确发现">
          查看数据来源和限制，或在后续收集更完整的证据。
        </Empty>
      ) : (
        <section className={styles.issueSection} aria-labelledby="issues-title">
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle} id="issues-title">分析发现</span>
            <span className={styles.sectionHint}>按优先级排序 · 最多展开 {presentation.issues.length} 个</span>
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
                      {issue.severity === "fix" ? "优先处理" : "需要关注"}
                    </Status>
                  ) : null}
                  {issue.claimLabel ? <Status tone="neutral">{issue.claimLabel}</Status> : null}
                  <span className={styles.issueName}>{issue.signal}</span>
                  <div className={styles.issueActions}>
                    <Button onClick={() => onSelectEvidence(index)} size="compact" variant="ghost">查看证据</Button>
                    {issue.metricRefs[0] ? (
                      <Button onClick={() => onSelectMetric(issue.metricRefs[0])} size="compact" variant="ghost">
                        查看指标
                      </Button>
                    ) : null}
                    <Button onClick={onAskCoach} size="compact" variant="secondary">问 Coach</Button>
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
            <span className={styles.sectionTitle} id="prescription-title">规则化练习建议</span>
          </div>
          <div className={styles.prescriptionPanel}>
            {prescription ? (
              <>
                <div className={styles.prescriptionTitle}>{prescription.scenario}</div>
                <p className={styles.prescriptionReason}>{prescription.reason}</p>
                {prescription.cue ? <Badge tone="info">训练 cue：{prescription.cue}</Badge> : null}
              </>
            ) : (
              <p className={styles.prescriptionReason}>{expected}</p>
            )}
          </div>
        </section>
      ) : null}

      {!hasClassifiedScenario && presentation.issues.some((issue) => issue.prescriptions.length > 0) ? (
        <Notice tone="warning" title="当前场景尚未完成核验">
          本页不展示具体场景建议；请先确认本局场景与可用分析范围。
        </Notice>
      ) : null}

      <section className={styles.metricSummary} aria-labelledby="core-metrics-title">
        <div className={styles.sectionHead}>
          <span className={styles.sectionTitle} id="core-metrics-title">
            {summaryMode === "descriptive" ? "本局指标" : "核心指标摘要"}
          </span>
          <span className={styles.sectionHint}>
            {summaryMode === "descriptive" ? "当前缺少可比较标准，只展示本局数值" : "完整数据在「数据」视图"}
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
                      ?? (metric.coverage === null ? "覆盖未知" : `覆盖 ${Math.round(metric.coverage * 100)}%`)}
                  </span>
                  {summaryMode !== "descriptive" ? (
                    <Status tone={severityTone(severity)}>
                      {severity === "fix" ? "优先处理" : severity === "watch" ? "需要关注" : "参考"}
                    </Status>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : (
          <Empty className={styles.metricSummaryEmpty} title="暂无可展示指标">
            本次分析没有产生可解释的指标；完整状态仍保留在数据视图中。
          </Empty>
        )}
      </section>

      {presentation.limitations.length ? (
        <Notice title="本次分析的适用范围" tone="warning">
          {presentation.limitations.join(" ")}
        </Notice>
      ) : null}
    </div>
  );
}
