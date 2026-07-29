import type { AnalysisWorkspacePresentation } from "@/lib/contracts";
import { Badge, Button, Empty, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

const LEGACY_CANDIDATE_LEVEL_LABEL: Record<string, string> = {
  symptom: "观察到的表现",
  physical: "动作机制候选",
  training: "历史训练方向",
};

function severityTone(severity: "info" | "watch" | "fix"): "neutral" | "warning" | "error" {
  if (severity === "fix") return "error";
  if (severity === "watch") return "warning";
  return "neutral";
}

function formatMetricValue(value: number | string | null, unit: string | null): string {
  if (value === null) return "不可用";
  const shown = typeof value === "number" ? Number(value.toFixed(3)) : value;
  return `${shown}${unit ? ` ${unit}` : ""}`;
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
  return (
    <div className={styles.diagnosisTrack}>
      <section className={styles.diagnosisLead} aria-labelledby="diagnosis-title">
        <p className={styles.sectionKicker}>重点观察</p>
        <h2 id="diagnosis-title">{presentation.headline}</h2>
        {presentation.profile ? (
          <div className={styles.profileLine}>
            <Badge>{presentation.profile.label}</Badge>
            <span>
              可信度 {presentation.profile.confidence === null ? "未提供" : `${Math.round(presentation.profile.confidence * 100)}%`}
            </span>
            {presentation.profile.tags.map((tag) => <span key={tag}>{tag}</span>)}
          </div>
        ) : (
          <p className={styles.muted}>证据不足时不补写“正常”画像。</p>
        )}
      </section>

      {presentation.issues.length === 0 ? (
        <Empty title="当前证据不足以形成重点观察">
          查看数据来源和限制，或在后续收集更完整的证据。
        </Empty>
      ) : (
        <section className={styles.issueList} aria-label="观察项">
          {presentation.issues.map((issue, index) => (
            <article className={styles.issue} data-selected={selectedIssue === index || undefined} key={`${issue.priority}-${issue.signal}`}>
              <header className={styles.issueHeader}>
                <div>
                  <p className={styles.sectionKicker}>观察项 {index + 1}</p>
                  <h3>{issue.signal}</h3>
                </div>
                <div className={styles.profileLine}>
                  {issue.presentationKind === "registry-backed" ? <Badge>证据等级：{issue.claimLabel}</Badge> : null}
                  <Status tone={severityTone(issue.severity)}>{issue.severity === "fix" ? "优先观察" : issue.severity === "watch" ? "持续观察" : "信息"}</Status>
                </div>
              </header>
              <p className={styles.priorityReason}>{issue.priorityReason}</p>

              {issue.presentationKind === "registry-backed" ? (
                <>
                  <div className={styles.prescriptionBlock} aria-label="候选解释">
                    <h4>候选解释</h4>
                    <span>{issue.candidateExplanation ?? "当前 Analysis 未提供额外候选解释。"}</span>
                  </div>
                  <div className={styles.prescriptionBlock} aria-label="规则化练习建议">
                    <h4>规则化练习建议</h4>
                    <span>{issue.expectedResult
                      ? `一次只围绕该观察练习，并在可比条件下复测：${issue.expectedResult}`
                      : "一次只围绕该观察调整练习；具体场景、动作提示和剂量不由当前 Analysis 推断。"}</span>
                  </div>
                </>
              ) : issue.hasHistoricalCandidateDetails ? (
                <div className={styles.prescriptionBlock} aria-label="历史候选说明">
                  <h4>历史候选说明</h4>
                  <div className={styles.rootCauseGrid}>
                    {issue.rootCauses.length ? issue.rootCauses.map((cause) => (
                      <div className={styles.rootCause} key={`${cause.level}-${cause.text}`}>
                        <span>{LEGACY_CANDIDATE_LEVEL_LABEL[cause.level] ?? cause.level}</span>
                        <p>{cause.text}</p>
                      </div>
                    )) : <p className={styles.muted}>历史记录未提供候选解释。</p>}
                  </div>
                  {issue.prescriptions.length ? issue.prescriptions.map((prescription) => (
                    <div key={`${prescription.scenario}-${prescription.reason}`}>
                      <strong>{prescription.scenario}</strong>
                      <span>{prescription.reason}</span>
                      {prescription.cue ? <span>提示：{prescription.cue}</span> : null}
                    </div>
                  )) : null}
                </div>
              ) : (
                <p className={styles.muted}>当前观察未关联版本化知识，且没有历史候选说明可展示。</p>
              )}

              {issue.limitations.length ? <Notice tone="warning">{issue.limitations.join(" · ")}</Notice> : null}
              <footer className={styles.issueActions}>
                <Button onClick={() => onSelectEvidence(index)} variant="secondary">查看证据</Button>
                {issue.metricRefs[0] ? <Button onClick={() => onSelectMetric(issue.metricRefs[0])} variant="ghost">查看指标</Button> : null}
                <Button onClick={onAskCoach} variant="ghost">问 Coach</Button>
              </footer>
            </article>
          ))}
        </section>
      )}

      <section className={styles.metricSummary} aria-labelledby="core-metrics-title">
        <div className={styles.sectionHeading}>
          <div><p className={styles.sectionKicker}>Core metrics</p><h2 id="core-metrics-title">核心指标摘要</h2></div>
          <span>{presentation.metrics.formal.length} 项正式指标</span>
        </div>
        {presentation.metrics.formal.length ? (
          <div className={styles.metricSummaryRows}>
            {presentation.metrics.formal.slice(0, 4).map((metric) => (
              <button className={styles.metricSummaryRow} key={metric.key} onClick={() => onSelectMetric(metric.key)} type="button">
                <strong>{metric.key}</strong>
                <span>{formatMetricValue(metric.value, metric.unit)}</span>
                <small>{metric.coverage === null ? "覆盖未知" : `覆盖 ${Math.round(metric.coverage * 100)}%`} · {metric.availability}</small>
              </button>
            ))}
          </div>
        ) : <Empty title="没有可正式展示的指标">实验性或不可用指标不会混入核心摘要。</Empty>}
      </section>

      <section className={styles.evidenceSourceSection} aria-labelledby="evidence-source-title">
        <div className={styles.sectionHeading}>
          <div><p className={styles.sectionKicker}>Evidence</p><h2 id="evidence-source-title">证据来源</h2></div>
        </div>
        <div className={styles.evidenceRows}>
          {presentation.evidence.map((item) => (
            <div className={styles.evidenceRow} key={item.source}>
              <strong>{item.source}</strong>
              <span>{item.availability}</span>
              <span>{item.alignment ?? "未提供对齐状态"}</span>
            </div>
          ))}
        </div>
        {presentation.limitations.length ? <Notice tone="warning" title="当前限制">{presentation.limitations.join(" · ")}</Notice> : null}
      </section>
    </div>
  );
}
