"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getExternalTelemetry,
  listExternalRuns,
  saveExternalTelemetryWatchRoot,
} from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import { useT } from "@/lib/i18n";
import type { ExternalRunListItemV1, ExternalTelemetryConfigV1 } from "@/lib/types";
import { Button, Notice, Status } from "@/ui/primitives";

const MAX_LISTED_RUNS = 50;

/** 设置：外部遥测导入（ExternalTelemetryRun，proposal-only 标签）。 */
export function ExternalTelemetryPanel() {
  const t = useT();
  const [desktop, setDesktop] = useState(false);
  const [config, setConfig] = useState<ExternalTelemetryConfigV1 | null>(null);
  const [runs, setRuns] = useState<ExternalRunListItemV1[]>([]);
  const [watchRootInput, setWatchRootInput] = useState("");
  const [operation, setOperation] = useState<"idle" | "loading" | "saving">("loading");
  // i18n 批 2 解耦：反馈语气随调用点显式给出，不再按中文前缀（startsWith("已")）猜。
  const [feedback, setFeedback] = useState<{ tone: "info" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    setOperation("loading");
    setFeedback(null);
    try {
      const next = await getExternalTelemetry();
      setConfig(next);
      setWatchRootInput(next.watch_root ?? "");
      if (next.watch_root) {
        const list = await listExternalRuns({ limit: MAX_LISTED_RUNS });
        setRuns(list.items);
      } else {
        setRuns([]);
      }
    } catch {
      setFeedback({ tone: "error", text: t("kovaak.telemetry.readFailed") });
    } finally {
      setOperation("idle");
    }
  }, [t]);

  useEffect(() => {
    setDesktop(isDesktopRuntime());
    if (isDesktopRuntime()) void load();
    else setOperation("idle");
  }, [load]);

  const save = async () => {
    const trimmed = watchRootInput.trim();
    if (!trimmed) {
      setFeedback({ tone: "error", text: t("kovaak.telemetry.pathRequired") });
      return;
    }
    setOperation("saving");
    setFeedback(null);
    try {
      const next = await saveExternalTelemetryWatchRoot({ watch_root: trimmed });
      setConfig(next);
      if (next.activation === "failed") {
        setFeedback({ tone: "info", text: t("kovaak.telemetry.savedWatcherFailed") });
      } else if (next.activation === "runtime_unavailable") {
        setFeedback({ tone: "info", text: t("kovaak.telemetry.savedNextLaunch") });
      } else {
        setFeedback({ tone: "info", text: t("kovaak.telemetry.savedWatching") });
      }
      await load();
    } catch {
      setFeedback({ tone: "error", text: t("kovaak.telemetry.saveFailed") });
    } finally {
      setOperation("idle");
    }
  };

  if (!desktop) {
    return (
      <div className="kovaak-directories-panel" data-context="settings">
        <Notice tone="warning" title={t("kovaak.telemetry.desktopOnlyTitle")}>{t("kovaak.telemetry.desktopOnlyBody")}</Notice>
      </div>
    );
  }

  const watcher = config?.watcher;
  const watcherState = typeof watcher?.directory_state === "string" ? watcher.directory_state : null;
  return (
    <div className="kovaak-directories-panel" data-context="settings">
      {operation === "loading" ? <Status tone="neutral">{t("kovaak.telemetry.loading")}</Status> : null}
      {feedback ? <Notice tone={feedback.tone}>{feedback.text}</Notice> : null}
      <div className="kovaak-directories-list">
        <article className="kovaak-directory-row">
          <div>
            <strong>{t("kovaak.telemetry.directoryLabel")}</strong>
            <p>{t("kovaak.telemetry.directoryDesc")}</p>
            <Status tone={config?.watch_root ? "success" : "neutral"}>
              {config?.watch_root
                ? `${t("kovaak.telemetry.watching", { count: config.run_count })}${watcherState && watcherState !== "ready" ? t("kovaak.telemetry.watcherSuffix", { state: watcherState }) : ""}`
                : t("kovaak.telemetry.notConfigured")}
            </Status>
          </div>
        </article>
        <article className="kovaak-directory-row">
          <div>
            <input
              type="text"
              value={watchRootInput}
              onChange={(event) => setWatchRootInput(event.target.value)}
              placeholder={t("kovaak.telemetry.pathPlaceholder")}
              disabled={operation !== "idle"}
              style={{ width: "100%" }}
            />
          </div>
          <Button disabled={operation !== "idle"} onClick={() => void save()} size="compact" variant="secondary">
            {t("kovaak.telemetry.saveAndWatch")}
          </Button>
        </article>
      </div>
      {runs.length > 0 ? (
        <div className="kovaak-directories-list">
          {runs.slice(0, 10).map((run) => {
            const uncertain = run.proposal_status === "uncertain";
            const label = run.proposal_label
              ? `${run.proposal_label}${uncertain ? "?" : ""}`
              : t("kovaak.telemetry.pendingLabel");
            return (
              <article className="kovaak-directory-row" key={run.external_run_id}>
                <div>
                  <strong>
                    {run.source_file ?? t("kovaak.telemetry.unknownSource")} · round {run.round ?? "-"}{" "}
                    {uncertain ? "?" : ""}
                  </strong>
                  <p>
                    {label}
                    {run.proposal_score != null ? t("kovaak.telemetry.confidence", { score: run.proposal_score.toFixed(3) }) : ""}
                    {run.t2k_p50 != null ? ` · T2K p50 ${run.t2k_p50.toFixed(3)}s` : ""}
                    {run.matched_run_ids.length > 0 ? t("kovaak.telemetry.matchedRun", { runId: run.matched_run_ids[0] }) : ""}
                    {run.quality_issues.length > 0 ? ` · ${run.quality_issues.join(", ")}` : ""}
                  </p>
                </div>
              </article>
            );
          })}
          {(config?.run_count ?? 0) > runs.length ? (
            <p className="kovaak-module-note">{t("kovaak.telemetry.recentOnly", { count: config?.run_count ?? 0 })}</p>
          ) : null}
        </div>
      ) : null}
      <p className="kovaak-module-note">
        {t("kovaak.telemetry.proposalNote")}
      </p>
    </div>
  );
}
