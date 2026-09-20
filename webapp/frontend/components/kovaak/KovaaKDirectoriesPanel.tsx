"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getKovaaKLocalDirectories,
  saveKovaaKLocalDirectories,
} from "@/lib/api";
import { isDesktopRuntime, pickDesktopDirectory } from "@/lib/desktop";
import { useT, type MessageKey } from "@/lib/i18n";
import type { KovaaKLocalDirectoriesV1 } from "@/lib/types";
import { Button, Notice, Status } from "@/ui/primitives";

type PanelContext = "onboarding" | "settings";
type Operation = "idle" | "loading" | "selecting" | "saving";
type DirectoryKind = "stats" | "performance";

interface KovaaKDirectoriesPanelProps {
  context: PanelContext;
  onContinue?: () => void;
}

const DIRECTORY_DETAILS: Record<DirectoryKind, { labelKey: MessageKey; pickerTitleKey: MessageKey; descriptionKey: MessageKey }> = {
  stats: {
    labelKey: "kovaak.directories.statsLabel",
    pickerTitleKey: "kovaak.directories.statsPickerTitle",
    descriptionKey: "kovaak.directories.statsDesc",
  },
  performance: {
    labelKey: "kovaak.directories.performanceLabel",
    pickerTitleKey: "kovaak.directories.performancePickerTitle",
    descriptionKey: "kovaak.directories.performanceDesc",
  },
};

export function KovaaKDirectoriesPanel({ context, onContinue }: KovaaKDirectoriesPanelProps) {
  const t = useT();
  const [desktop, setDesktop] = useState(false);
  const [directories, setDirectories] = useState<KovaaKLocalDirectoriesV1 | null>(null);
  const [selected, setSelected] = useState<Partial<Record<DirectoryKind, string>>>({});
  const [operation, setOperation] = useState<Operation>("loading");
  // i18n 批 2 解耦：反馈语气随调用点显式给出，不再按中文子串（includes("已")）猜。
  const [feedback, setFeedback] = useState<{ tone: "warning" | "error"; text: string } | null>(null);

  const statusText = useCallback((directory: KovaaKLocalDirectoriesV1[DirectoryKind]): string => {
    if (!directory.path) return t("kovaak.directories.notFound");
    if (directory.matching_files === "found") return t("kovaak.directories.foundFiles", { count: directory.matching_file_count ?? 0 });
    return t("kovaak.directories.folderFoundNoFiles");
  }, [t]);

  const load = useCallback(async () => {
    setOperation("loading");
    setFeedback(null);
    try {
      setDirectories(await getKovaaKLocalDirectories());
    } catch {
      setFeedback({ tone: "error", text: t("kovaak.directories.readFailed") });
    } finally {
      setOperation("idle");
    }
  }, [t]);

  useEffect(() => {
    const available = isDesktopRuntime();
    setDesktop(available);
    if (available) void load();
    else setOperation("idle");
  }, [load]);

  const selectDirectory = async (kind: DirectoryKind) => {
    setOperation("selecting");
    setFeedback(null);
    try {
      const path = await pickDesktopDirectory(t(DIRECTORY_DETAILS[kind].pickerTitleKey));
      if (path) setSelected((current) => ({ ...current, [kind]: path }));
    } catch {
      setFeedback({ tone: "error", text: t("kovaak.directories.pickerFailed") });
    } finally {
      setOperation("idle");
    }
  };

  /** 线框合并形态（settings）的单一「更换文件夹」：依次弹出 Stats、Performance 两个选择器。 */
  const pickBothDirectories = async () => {
    await selectDirectory("stats");
    await selectDirectory("performance");
  };

  const save = async () => {
    if (!selected.stats || !selected.performance) {
      setFeedback({ tone: "error", text: t("kovaak.directories.bothRequired") });
      return;
    }
    setOperation("saving");
    setFeedback(null);
    try {
      const next = await saveKovaaKLocalDirectories({
        stats_dir: selected.stats,
        performance_dir: selected.performance,
      });
      setDirectories(next);
      setSelected({});
      if (next.activation === "failed") setFeedback({ tone: "warning", text: t("kovaak.directories.savedWatcherFailed") });
      else if (next.stats.matching_files === "no_matching_files" || next.performance.matching_files === "no_matching_files") {
        setFeedback({ tone: "warning", text: t("kovaak.directories.savedAutoExport") });
      }
    } catch {
      setFeedback({ tone: "error", text: t("kovaak.directories.saveFailed") });
    } finally {
      setOperation("idle");
    }
  };

  if (!desktop) {
    return <div className="kovaak-directories-panel" data-context={context}><Notice tone="warning" title={t("kovaak.directories.desktopOnlyTitle")}>{t("kovaak.directories.desktopOnlyBody")}</Notice></div>;
  }

  const confirmed = Boolean(directories?.stats.path && directories?.performance.path);
  // 0912 线框拍板（settings）：自动发现成功且没有待保存的手动选择时，
  // 压缩成一行说明 + 一行「自动发现」+ 单个「更换文件夹」；失败/手动选择
  // 时才展开逐文件夹回退与「保存并启用」。
  const mergedSettingsView = context === "settings" && confirmed
    && !selected.stats && !selected.performance;
  const mergedDesc = () => {
    const stats = directories?.stats;
    const performance = directories?.performance;
    if (stats?.matching_files === "found" && performance?.matching_files === "found") {
      return t("kovaak.directories.mergedFound", {
        stats: stats.matching_file_count ?? 0,
        performance: performance.matching_file_count ?? 0,
      });
    }
    return t("kovaak.directories.mergedStatus", {
      stats: stats ? statusText(stats) : t("kovaak.directories.statusUnknown"),
      performance: performance ? statusText(performance) : t("kovaak.directories.statusUnknown"),
    });
  };
  return (
    <div className="kovaak-directories-panel" data-context={context}>
      {operation === "loading" ? <Status tone="neutral">{t("kovaak.directories.loading")}</Status> : null}
      {feedback ? <Notice tone={feedback.tone}>{feedback.text}</Notice> : null}
      {mergedSettingsView ? (
        <>
          <h3 className="task6-profile-group-title">{t("kovaak.directories.sectionTitle")}</h3>
          <p className="task6-card-desc">{mergedDesc()}</p>
          <div className="kovaak-directories-footer">
            <p className="kovaak-module-note">{t("kovaak.directories.autoDiscovered")}</p>
            <Button disabled={operation !== "idle"} onClick={() => void pickBothDirectories()} size="compact" variant="secondary">{t("kovaak.directories.changeFolders")}</Button>
          </div>
        </>
      ) : (
        <>
          {context === "settings" ? (
            <>
              <h3 className="task6-profile-group-title">{t("kovaak.directories.sectionTitle")}</h3>
              <p className="task6-card-desc">{t("kovaak.directories.settingsDesc")}</p>
            </>
          ) : null}
          <div className="kovaak-directories-list">
            {(Object.keys(DIRECTORY_DETAILS) as DirectoryKind[]).map((kind) => {
              const detail = DIRECTORY_DETAILS[kind];
              const directory = directories?.[kind];
              return (
                <article className="task6-form-row kovaak-directory-entry" key={kind}>
                  <span className="task6-form-row-label">{t(detail.labelKey)}</span>
                  <div className="kovaak-directory-entry-info">
                    <p>{t(detail.descriptionKey)}</p>
                    {/* 只读状态是纯文本行，不再用输入框/徽标壳。 */}
                    <p>
                      {selected[kind]
                        ? t("kovaak.directories.selectedPending")
                        : directory
                          ? statusText(directory)
                          : t("kovaak.directories.statusUnknown")}
                    </p>
                  </div>
                  <Button disabled={operation !== "idle"} onClick={() => void selectDirectory(kind)} size="compact" variant="secondary">{selected[kind] || directory?.path ? t("kovaak.directories.changeFolders") : t("kovaak.directories.selectFolder")}</Button>
                </article>
              );
            })}
          </div>
          {/* 线框形态：「保存并启用」不独占整行——与提示小字同行，按钮在行尾。 */}
          <div className="kovaak-directories-footer">
            <p className="kovaak-module-note">{t("kovaak.directories.manualHint")}</p>
            <Button disabled={operation !== "idle" || !selected.stats || !selected.performance} onClick={() => void save()} variant="secondary">{t("kovaak.directories.saveAndEnable")}</Button>
          </div>
        </>
      )}
      {context === "onboarding" && onContinue ? (
        <div className="kovaak-onboarding-actions">
          <Button disabled={operation !== "idle" || (!confirmed && !(selected.stats && selected.performance))} onClick={onContinue}>{t("kovaak.onboarding.continue")}</Button>
        </div>
      ) : null}
    </div>
  );
}
