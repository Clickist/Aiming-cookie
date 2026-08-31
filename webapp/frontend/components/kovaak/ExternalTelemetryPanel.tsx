"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getExternalTelemetry,
  listExternalRuns,
  saveExternalTelemetryWatchRoot,
} from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import type { ExternalRunListItemV1, ExternalTelemetryConfigV1 } from "@/lib/types";
import { Button, Notice, Status } from "@/ui/primitives";

const MAX_LISTED_RUNS = 50;

/** 设置：外部遥测导入（ExternalTelemetryRun，proposal-only 标签）。 */
export function ExternalTelemetryPanel() {
  const [desktop, setDesktop] = useState(false);
  const [config, setConfig] = useState<ExternalTelemetryConfigV1 | null>(null);
  const [runs, setRuns] = useState<ExternalRunListItemV1[]>([]);
  const [watchRootInput, setWatchRootInput] = useState("");
  const [operation, setOperation] = useState<"idle" | "loading" | "saving">("loading");
  const [feedback, setFeedback] = useState<string | null>(null);

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
      setFeedback("外部遥测状态暂时无法读取，请稍后重试。");
    } finally {
      setOperation("idle");
    }
  }, []);

  useEffect(() => {
    setDesktop(isDesktopRuntime());
    if (isDesktopRuntime()) void load();
    else setOperation("idle");
  }, [load]);

  const save = async () => {
    const trimmed = watchRootInput.trim();
    if (!trimmed) {
      setFeedback("请填写外部遥测 cleaned 目录的完整本地路径。");
      return;
    }
    setOperation("saving");
    setFeedback(null);
    try {
      const next = await saveExternalTelemetryWatchRoot({ watch_root: trimmed });
      setConfig(next);
      if (next.activation === "failed") {
        setFeedback("路径已保存，但监听未能启动。请重新打开应用后检查。");
      } else if (next.activation === "runtime_unavailable") {
        setFeedback("路径已保存，下次启动应用后生效。");
      } else {
        setFeedback("已保存并开始监听。");
      }
      await load();
    } catch {
      setFeedback("路径没有保存。请确认填写的是完整绝对路径。");
    } finally {
      setOperation("idle");
    }
  };

  if (!desktop) {
    return (
      <div className="kovaak-directories-panel" data-context="settings">
        <Notice tone="warning" title="仅限桌面版">外部遥测导入需要通过 Windows 桌面版启用。</Notice>
      </div>
    );
  }

  const watcher = config?.watcher;
  const watcherState = typeof watcher?.directory_state === "string" ? watcher.directory_state : null;
  return (
    <div className="kovaak-directories-panel" data-context="settings">
      {operation === "loading" ? <Status tone="neutral">正在读取外部遥测状态…</Status> : null}
      {feedback ? <Notice tone={feedback.startsWith("已") || feedback.startsWith("路径已") ? "info" : "error"}>{feedback}</Notice> : null}
      <div className="kovaak-directories-list">
        <article className="kovaak-directory-row">
          <div>
            <strong>cleaned 数据目录</strong>
            <p>外部遥测清洗产物目录（只读监听，导入副本保存在本地数据根）。</p>
            <Status tone={config?.watch_root ? "success" : "neutral"}>
              {config?.watch_root
                ? `已监听，共 ${config.run_count} 条外部轮次${watcherState && watcherState !== "ready" ? `（watcher: ${watcherState}）` : ""}`
                : "尚未配置"}
            </Status>
          </div>
        </article>
        <article className="kovaak-directory-row">
          <div>
            <input
              type="text"
              value={watchRootInput}
              onChange={(event) => setWatchRootInput(event.target.value)}
              placeholder="例如 C:\\Users\\你\\Desktop\\FPSAimTrainer\\analysis\\external\\cleaned"
              disabled={operation !== "idle"}
              style={{ width: "100%" }}
            />
          </div>
          <Button disabled={operation !== "idle"} onClick={() => void save()} size="compact" variant="secondary">
            保存并监听
          </Button>
        </article>
      </div>
      {runs.length > 0 ? (
        <div className="kovaak-directories-list">
          {runs.slice(0, 10).map((run) => {
            const uncertain = run.proposal_status === "uncertain";
            const label = run.proposal_label
              ? `${run.proposal_label}${uncertain ? "?" : ""}`
              : "场景待标注";
            return (
              <article className="kovaak-directory-row" key={run.external_run_id}>
                <div>
                  <strong>
                    {run.source_file ?? "未知来源"} · round {run.round ?? "-"}{" "}
                    {uncertain ? "?" : ""}
                  </strong>
                  <p>
                    {label}
                    {run.proposal_score != null ? `（置信度 ${run.proposal_score.toFixed(3)}）` : ""}
                    {run.t2k_p50 != null ? ` · T2K p50 ${run.t2k_p50.toFixed(3)}s` : ""}
                    {run.matched_run_ids.length > 0 ? ` · 已配对 Run ${run.matched_run_ids[0]}` : ""}
                    {run.quality_issues.length > 0 ? ` · ${run.quality_issues.join(", ")}` : ""}
                  </p>
                </div>
              </article>
            );
          })}
          {(config?.run_count ?? 0) > runs.length ? (
            <p className="kovaak-module-note">仅显示最近 10 条，共 {config?.run_count} 条。</p>
          ) : null}
        </div>
      ) : null}
      <p className="kovaak-module-note">
        自动场景标签仅为提案（uncertain 显示 "?"），不会写入已确认的场景记忆。详见 docs/EXTERNAL_TELEMETRY_IMPORT.md。
      </p>
    </div>
  );
}
