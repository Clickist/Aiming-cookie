"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getKovaaKLocalDirectories,
  saveKovaaKLocalDirectories,
} from "@/lib/api";
import { isDesktopRuntime, pickDesktopDirectory } from "@/lib/desktop";
import type { KovaaKLocalDirectoriesV1 } from "@/lib/types";
import { Button, Notice, Status } from "@/ui/primitives";

type PanelContext = "onboarding" | "settings";
type Operation = "idle" | "loading" | "selecting" | "saving";
type DirectoryKind = "stats" | "performance";

interface KovaaKDirectoriesPanelProps {
  context: PanelContext;
  onContinue?: () => void;
}

const DIRECTORY_DETAILS: Record<DirectoryKind, { label: string; pickerTitle: string; description: string }> = {
  stats: {
    label: "Stats 文件夹",
    pickerTitle: "选择 KovaaK Stats 文件夹",
    description: "包含 KovaaK 导出的训练统计文件。",
  },
  performance: {
    label: "Performance 文件夹",
    pickerTitle: "选择 KovaaK Performance 文件夹",
    description: "包含 KovaaK 的 Performance 训练记录。",
  },
};

function statusText(directory: KovaaKLocalDirectoriesV1[DirectoryKind]): string {
  if (!directory.path) return "未找到";
  if (directory.matching_files === "found") return `已发现 ${directory.matching_file_count} 个文件`;
  return "文件夹已找到，尚未发现训练文件";
}

export function KovaaKDirectoriesPanel({ context, onContinue }: KovaaKDirectoriesPanelProps) {
  const [desktop, setDesktop] = useState(false);
  const [directories, setDirectories] = useState<KovaaKLocalDirectoriesV1 | null>(null);
  const [selected, setSelected] = useState<Partial<Record<DirectoryKind, string>>>({});
  const [operation, setOperation] = useState<Operation>("loading");
  const [feedback, setFeedback] = useState<string | null>(null);

  const load = useCallback(async () => {
    setOperation("loading");
    setFeedback(null);
    try {
      setDirectories(await getKovaaKLocalDirectories());
    } catch {
      setFeedback("本地目录状态暂时无法读取，请稍后重试。");
    } finally {
      setOperation("idle");
    }
  }, []);

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
      const path = await pickDesktopDirectory(DIRECTORY_DETAILS[kind].pickerTitle);
      if (path) setSelected((current) => ({ ...current, [kind]: path }));
    } catch {
      setFeedback("无法打开文件夹选择器，请重试。");
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
      setFeedback("请分别选择 Stats 和 Performance 文件夹后再保存。");
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
      if (next.activation === "failed") setFeedback("目录已保存，但监听未能切换。请重新打开应用后检查。 ");
      else if (next.stats.matching_files === "no_matching_files" || next.performance.matching_files === "no_matching_files") {
        setFeedback("目录已启用。AC 会在每次启动时自动开启 KovaaK 统计导出（Challenge Completion；若 KovaaK 正在运行会跳过，下次启动 AC 时补开）。仍未发现文件时可手动设置：设置 → 其他 → 统计数据输出 → Challenge Completion（英文界面：Settings → MAIN → Statistics Export → Challenge Completion），改完需完全退出并重启 KovaaK。 ");
      }
    } catch {
      setFeedback("目录没有保存。请确认选择的是两个可读取的不同文件夹。 ");
    } finally {
      setOperation("idle");
    }
  };

  if (!desktop) {
    return <div className="kovaak-directories-panel" data-context={context}><Notice tone="warning" title="仅限桌面版">本地训练目录需要通过 Windows 桌面版的系统文件夹选择器确认。</Notice></div>;
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
      return `Stats ${stats.matching_file_count ?? 0} 个文件 · Performance ${performance.matching_file_count ?? 0} 个文件已发现。`;
    }
    return `Stats：${stats ? statusText(stats) : "状态未知"} · Performance：${performance ? statusText(performance) : "状态未知"}。`;
  };
  return (
    <div className="kovaak-directories-panel" data-context={context}>
      {operation === "loading" ? <Status tone="neutral">正在读取本地目录状态…</Status> : null}
      {feedback ? <Notice tone={feedback.includes("已") ? "warning" : "error"}>{feedback}</Notice> : null}
      {mergedSettingsView ? (
        <>
          <h3 className="task6-profile-group-title">本地目录</h3>
          <p className="task6-card-desc">{mergedDesc()}</p>
          <div className="kovaak-directories-footer">
            <p className="kovaak-module-note">两个文件夹均由 AC 自动发现</p>
            <Button disabled={operation !== "idle"} onClick={() => void pickBothDirectories()} size="compact" variant="secondary">更换文件夹</Button>
          </div>
        </>
      ) : (
        <>
          {context === "settings" ? (
            <>
              <h3 className="task6-profile-group-title">本地目录</h3>
              <p className="task6-card-desc">确认 KovaaK 的 Stats 与 Performance 文件夹，AC 据此发现训练并自动开启统计导出。</p>
            </>
          ) : null}
          <div className="kovaak-directories-list">
            {(Object.keys(DIRECTORY_DETAILS) as DirectoryKind[]).map((kind) => {
              const detail = DIRECTORY_DETAILS[kind];
              const directory = directories?.[kind];
              return (
                <article className="task6-form-row kovaak-directory-entry" key={kind}>
                  <span className="task6-form-row-label">{detail.label}</span>
                  <div className="kovaak-directory-entry-info">
                    <p>{detail.description}</p>
                    {/* 只读状态是纯文本行，不再用输入框/徽标壳。 */}
                    <p>
                      {selected[kind]
                        ? "已选择，等待一起保存"
                        : directory
                          ? statusText(directory)
                          : "状态未知"}
                    </p>
                  </div>
                  <Button disabled={operation !== "idle"} onClick={() => void selectDirectory(kind)} size="compact" variant="secondary">{selected[kind] || directory?.path ? "更换文件夹" : "选择文件夹"}</Button>
                </article>
              );
            })}
          </div>
          {/* 线框形态：「保存并启用」不独占整行——与提示小字同行，按钮在行尾。 */}
          <div className="kovaak-directories-footer">
            <p className="kovaak-module-note">自动发现失败时，请分别选择两个文件夹。完整本地路径不会显示给 Coach 或发送给 Provider。</p>
            <Button disabled={operation !== "idle" || !selected.stats || !selected.performance} onClick={() => void save()} variant="secondary">保存并启用</Button>
          </div>
        </>
      )}
      {context === "onboarding" && onContinue ? (
        <div className="kovaak-onboarding-actions">
          <Button disabled={operation !== "idle" || (!confirmed && !(selected.stats && selected.performance))} onClick={onContinue}>继续</Button>
        </div>
      ) : null}
    </div>
  );
}
