"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteSession, listTasks, retrySession } from "@/lib/api";
import { presentTask } from "@/lib/contracts";
import type { TaskDetailV1, TaskPhase } from "@/lib/types";
import { Button, Dialog, Empty, ErrorState, Loading, Notice, Status } from "@/ui/primitives";

import { ModeBadge, PageHeading, PreviewBadge } from "./Task3Shared";

const ALL_PHASES: Array<{ code: TaskPhase; label: string }> = [
  { code: "preparing_training_record", label: "准备训练记录" },
  { code: "aligning_input_events", label: "对齐输入事件" },
  { code: "computing_kinematics", label: "计算运动学" },
  { code: "analyzing_video", label: "分析视频" },
  { code: "generating_diagnostics", label: "生成诊断" },
];

function analysisId(task: TaskDetailV1): number | null {
  const value = task.analysis_ref?.match(/^analysis:(\d+)$/)?.[1];
  return value ? Number(value) : null;
}

function modeLabel(mode: string | null | undefined): string {
  const labels: Record<string, string> = {
    multimodal: "多源模式",
    input_native: "输入原生",
    video_fallback: "视频兼容",
  };
  return labels[mode ?? ""] ?? mode ?? "未知模式";
}

function StageStepper({ task }: { task: TaskDetailV1 }) {
  const phases = task.input_mode === "input_native"
    ? ALL_PHASES.filter((phase) => phase.code !== "analyzing_video")
    : ALL_PHASES;
  const currentIndex = phases.findIndex((phase) => phase.code === task.phase);
  return (
    <ol className="task3-stage-stepper" aria-label="真实分析阶段">
      {phases.map((phase, index) => {
        const done = task.state === "done" || (currentIndex >= 0 && index < currentIndex);
        const current = task.state === "running" && index === currentIndex;
        const symbol = done ? "✓" : current ? "●" : "";
        return (
          <li className="task3-step" aria-current={current ? "step" : undefined} data-current={current || undefined} data-done={done || undefined} key={phase.code}>
            <span aria-hidden="true" className="task3-step-dot">{symbol}</span>
            <span>{phase.label}</span>
            {index < phases.length - 1 ? <span aria-hidden="true" className="task3-step-line" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

function TaskStatusBadge({ task, copy }: { task: TaskDetailV1; copy: { state: string; failureDomain: string | null } }) {
  let tone: "neutral" | "info" | "success" | "warning" | "error" = "neutral";
  let text = copy.state;
  let dot = false;

  if (task.state === "done") {
    tone = "success";
    text = task.partial_outcome ? "部分可用" : "已完成";
    dot = true;
  } else if (task.state === "failed") {
    tone = "error";
    text = copy.failureDomain ? `失败 · ${copy.failureDomain}` : "失败";
  } else if (task.partial_outcome) {
    tone = "warning";
    text = "部分可用";
  } else if (task.state === "running") {
    tone = "warning";
    text = "运行中";
    dot = true;
  } else if (task.state === "retrying") {
    tone = "warning";
    text = "正在重试";
    dot = true;
  } else if (task.state === "importing") {
    tone = "info";
    text = "正在导入";
  } else if (task.state === "queued") {
    tone = "neutral";
    text = "排队中";
  }

  return (
    <Status tone={tone}>
      {dot ? <span aria-hidden="true" className="task3-status-dot" /> : null}
      {text}
    </Status>
  );
}

function taskNote(task: TaskDetailV1): string | null {
  if (task.state === "done") {
    const time = task.finished_at ? new Date(task.finished_at).toLocaleString("zh-CN") : null;
    return time ? `完成于 ${time} · 已通过全局通知提醒，不强制跳转。` : "已完成。";
  }
  if (task.state === "running") {
    return task.input_mode === "input_native"
      ? "本地分析通常需要几分钟，不显示进度百分比；input-native 无视频阶段。"
      : "本地分析通常需要几分钟，不显示进度百分比。";
  }
  if (task.state === "queued") return "将在运行中的任务完成后开始。";
  if (task.state === "importing") return "正在导入训练记录。";
  if (task.state === "retrying") return "正在重试失败阶段。";
  if (task.partial_outcome) return "输入原生结果完整保留；只有视频视觉证据不可用，不视为整体失败。";
  if (task.failure) {
    return `${task.failure.message} 重试会产生新的尝试，不覆盖这条失败记录。`;
  }
  return null;
}

export function TasksClient() {
  const [tasks, setTasks] = useState<TaskDetailV1[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [actionError, setActionError] = useState("");
  const [busyRef, setBusyRef] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TaskDetailV1 | null>(null);

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await listTasks();
      if (response.availability !== "available") {
        setUnavailable(true);
      } else {
        setTasks(response.tasks);
        setUnavailable(false);
      }
    } catch {
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const hasActiveTask = useMemo(
    () => tasks.some((task) => task.state === "importing" || task.state === "queued" || task.state === "running" || task.state === "retrying"),
    [tasks],
  );

  useEffect(() => {
    if (!hasActiveTask) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [hasActiveTask, load]);

  const retry = async (task: TaskDetailV1) => {
    const id = analysisId(task);
    if (id === null || !task.retryable) return;
    setBusyRef(task.task_ref);
    setActionError("");
    try {
      await retrySession(id, { idempotencyKey: crypto.randomUUID() });
      await load();
    } catch {
      setActionError("未能创建新的重试 attempt；原失败记录保持不变。");
    } finally {
      setBusyRef(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget?.can_delete) return;
    const id = analysisId(deleteTarget);
    if (id === null) return;
    setBusyRef(deleteTarget.task_ref);
    setActionError("");
    try {
      await deleteSession(id);
      setDeleteTarget(null);
      await load();
    } catch {
      setActionError("删除未完成。任务记录仍保留，请稍后重试。");
    } finally {
      setBusyRef(null);
    }
  };

  return (
    <div className="task3-page task3-page--narrow task3-tasks-page">
      <PageHeading
        description="分析在后台继续运行；完成后用通知和角标提醒你，不会强制跳转。"
        eyebrow="Tasks"
        title="任务状态"
      />

      {actionError ? <Notice tone="error">{actionError}</Notice> : null}
      {loading ? (
        <Loading>正在恢复任务</Loading>
      ) : unavailable ? (
        <ErrorState title="任务状态暂时不可用">
          <p>读取失败没有被显示成空列表。请恢复本地服务后重试。</p>
          <Button onClick={() => void load(true)} variant="secondary">重试</Button>
        </ErrorState>
      ) : tasks.length === 0 ? (
        <Empty title="还没有分析任务"><Button href="/analyze">新建分析</Button></Empty>
      ) : (
        <>
          <div className="task3-tasks-panel" aria-live="polite">
            {tasks.map((task) => {
              const copy = presentTask(task);
              const id = analysisId(task);
              const busy = busyRef === task.task_ref;
              const note = taskNote(task);
              return (
                <article className="task3-task-item" data-state={task.state} key={task.task_ref ?? task.analysis_ref}>
                  <div className="task3-task-head">
                    <span className="task3-task-name" title={task.run_ref ?? task.analysis_ref ?? "本地导入"}>
                      {task.run_ref ?? task.analysis_ref ?? "本地导入"}
                    </span>
                    <ModeBadge mode={task.input_mode} />
                    {task.input_mode === "input_native" ? <PreviewBadge /> : null}
                    <TaskStatusBadge copy={copy} task={task} />
                    <span className="task3-task-meta">
                      {task.state === "running" || task.state === "retrying"
                        ? "可离开本页"
                        : task.created_at
                          ? new Date(task.created_at).toLocaleString("zh-CN")
                          : "时间不可用"}
                    </span>
                    <span className="task3-task-actions">
                      {task.state === "done" && id !== null ? (
                        <>
                          <Button href={`/analysis/${id}`} size="compact" variant="secondary">查看诊断</Button>
                          <Link className="ac-button" data-size="compact" data-variant="ghost" href="/history">返回历史</Link>
                        </>
                      ) : null}
                      {task.retryable ? (
                        <Button disabled={busy} onClick={() => void retry(task)} size="compact" variant="primary">{busy ? "正在重试" : "重试"}</Button>
                      ) : null}
                      {task.can_delete ? (
                        <Button disabled={busy} onClick={() => setDeleteTarget(task)} size="compact" variant="danger">删除</Button>
                      ) : null}
                    </span>
                  </div>

                  {task.state === "running" || task.state === "done" ? <StageStepper task={task} /> : null}
                  {task.state === "queued" || task.state === "importing" || task.state === "retrying" ? (
                    <p className="task3-task-note">当前阶段：{copy.phase ?? "准备训练记录"}</p>
                  ) : null}

                  {note ? <p className="task3-task-note">{note}</p> : null}
                </article>
              );
            })}
          </div>
          <Notice className="task3-tasks-global-note" tone="info">
            运行中与排队中的任务不可删除；应用重启后仍在这里恢复显示。失败域分开：源文件、输入对齐、本地运动学、视频分析、Provider、Coach、网络。
          </Notice>
        </>
      )}

      <Dialog
        footer={<><Button onClick={() => setDeleteTarget(null)} size="compact" variant="secondary">取消</Button><Button onClick={() => void remove()} size="compact" variant="danger">删除任务记录</Button></>}
        onClose={() => setDeleteTarget(null)}
        open={deleteTarget !== null}
        title="确认删除"
      >
        <p>只删除这条 Analysis 与其所属产物，不删除 Run、自动录制、Raw Input 或用户源 Stats/Performance。</p>
      </Dialog>
    </div>
  );
}
