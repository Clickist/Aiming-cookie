"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteSession, getStorage } from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop";
import type { StorageResponse, StorageSessionItem } from "@/lib/types";

type StorageState =
  | { kind: "loading" }
  | { kind: "browser" }
  | { kind: "ok"; storage: StorageResponse }
  | { kind: "err"; message: string };

const ACTIVE_STORAGE_STATUSES = new Set(["uploading", "queued", "running"]);

const STATUS_LABEL: Record<string, string> = {
  uploading: "上传中",
  queued: "排队中",
  running: "分析中",
  done: "已完成",
  failed: "失败",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toLocaleString("zh-CN", {
    maximumFractionDigits: value >= 100 ? 0 : 1,
  })} ${units[unitIndex]}`;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function canDeleteSession(status: string): boolean {
  if (ACTIVE_STORAGE_STATUSES.has(status)) return false;
  return status === "done" || status === "failed";
}

export function StorageSettings() {
  const [state, setState] = useState<StorageState>({ kind: "loading" });
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!isDesktopRuntime()) {
      setState({ kind: "browser" });
      return;
    }

    setState({ kind: "loading" });
    try {
      const storage = await getStorage();
      setState({ kind: "ok", storage });
    } catch (err) {
      setState({
        kind: "err",
        message: err instanceof Error ? err.message : "加载失败",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async (item: StorageSessionItem) => {
    if (!canDeleteSession(item.status)) return;

    const confirmed = window.confirm(
      `确定删除分析 #${item.session_id}？仅删除 Aiming Cookie 托管副本，原始文件保留。此操作不可恢复。`,
    );
    if (!confirmed) return;

    setDeletingId(item.session_id);
    try {
      await deleteSession(item.session_id);
      await load();
    } catch (err) {
      window.alert(
        err instanceof Error ? err.message : "删除失败，请稍后重试",
      );
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="mt-lg border border-outline bg-surface-container-low rounded-lg p-md">
      <p className="font-mono text-label-sm text-primary">STORAGE</p>
      <h2 className="mt-xs font-display text-headline-md text-on-surface">
        托管存储
      </h2>

      {state.kind === "loading" && (
        <p className="mt-sm text-body-md text-on-surface-variant">加载中…</p>
      )}

      {state.kind === "browser" && (
        <p className="mt-sm text-body-md text-on-surface-variant">
          仅桌面版可用。
        </p>
      )}

      {state.kind === "err" && (
        <div
          role="alert"
          className="mt-md border border-outline-variant bg-surface-container-high rounded-md px-md py-sm"
        >
          <p className="text-label-md text-on-surface">无法加载托管存储</p>
          <p className="mt-xs text-body-md text-on-surface-variant break-words">
            {state.message}
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-sm text-label-md text-primary hover:brightness-110"
          >
            重试
          </button>
        </div>
      )}

      {state.kind === "ok" && (
        <div className="mt-md">
          <p className="text-body-md text-on-surface">
            已用空间：{formatBytes(state.storage.total_bytes)}
          </p>

          {state.storage.sessions.length === 0 ? (
            <p className="mt-sm text-body-md text-on-surface-variant">
              还没有托管的分析副本。
            </p>
          ) : (
            <ul className="mt-md divide-y divide-outline-variant border border-outline rounded-md overflow-hidden">
              {state.storage.sessions.map((item) => {
                const deletable = canDeleteSession(item.status);
                const deleting = deletingId === item.session_id;

                return (
                  <li
                    key={item.session_id}
                    className="flex flex-col gap-sm px-md py-sm sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <p className="font-mono text-label-md text-on-surface">
                        #{item.session_id}
                      </p>
                      <p className="mt-xs text-label-sm text-on-surface-variant">
                        {STATUS_LABEL[item.status] ?? item.status} · {formatTime(item.created_at)}
                      </p>
                      <p className="mt-xs text-label-sm text-on-surface-variant">
                        工作区：{formatBytes(item.workspace_bytes)}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={!deletable || deleting}
                      onClick={() => void handleDelete(item)}
                      className="self-start text-label-md text-on-surface-variant hover:text-error disabled:cursor-not-allowed disabled:opacity-50 sm:self-auto"
                    >
                      {deleting ? "删除中…" : "删除"}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
