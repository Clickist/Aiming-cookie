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
  return (
    <div className="kovaak-directories-panel" data-context={context}>
      {operation === "loading" ? <Status tone="neutral">正在读取本地目录状态…</Status> : null}
      {feedback ? <Notice tone={feedback.includes("已") ? "warning" : "error"}>{feedback}</Notice> : null}
      <div className="kovaak-directories-list">
        {(Object.keys(DIRECTORY_DETAILS) as DirectoryKind[]).map((kind) => {
          const detail = DIRECTORY_DETAILS[kind];
          const directory = directories?.[kind];
          return (
            <article className="kovaak-directory-row" key={kind}>
              <div>
                <strong>{detail.label}</strong>
                <p>{detail.description}</p>
                <Status tone={directory?.path ? "success" : "neutral"}>{selected[kind] ? "已选择，等待一起保存" : directory ? statusText(directory) : "状态未知"}</Status>
              </div>
              <Button disabled={operation !== "idle"} onClick={() => void selectDirectory(kind)} size="compact" variant="secondary">{selected[kind] || directory?.path ? "更换文件夹" : "选择文件夹"}</Button>
            </article>
          );
        })}
      </div>
      <p className="kovaak-module-note">自动发现失败时，请分别选择两个文件夹。完整本地路径不会显示给 Coach 或发送给 Provider。</p>
      <div className="kovaak-onboarding-actions">
        <Button disabled={operation !== "idle" || !selected.stats || !selected.performance} onClick={() => void save()} variant="secondary">保存并启用</Button>
        {context === "onboarding" && onContinue ? <Button disabled={operation !== "idle" || (!confirmed && !(selected.stats && selected.performance))} onClick={onContinue}>继续</Button> : null}
      </div>
    </div>
  );
}
