"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteSession, listTasks, retrySession } from "@/lib/api";
import { presentTask } from "@/lib/contracts";
import type { TaskDetailV1, TaskPhase } from "@/lib/types";
import { Button, Dialog, Empty, ErrorState, Loading, Notice, Status } from "@/ui/primitives";

import { PageHeading, PreviewBadge } from "./Task3Shared";

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

function taskTone(task: TaskDetailV1): "neutral" | "info" | "success" | "warning" | "error" {
  if (task.state === "done") return task.partial_outcome ? "warning" : "success";
  if (task.state === "failed") return "error";
  if (task.state === "running" || task.state === "retrying") return "info";
  return "neutral";
}

function StageStepper({ task }: { task: TaskDetailV1 }) {
  const phases = task.input_mode === "input_native"
    ? ALL_PHASES.filter((phase) => phase.code !== "analyzing_video")
    : ALL_PHASES;
  const currentIndex = phases.findIndex((phase) => phase.code === task.phase);
  return (
    <ol className="task3-stage-stepper" aria-label="真实分析阶段">
      {phases.map((phase, index) => {
        const complete = task.state === "done" || (currentIndex >= 0 && index < currentIndex);
        const current = task.state === "running" && index === currentIndex;
        return (
          <li aria-current={current ? "step" : undefined} data-complete={complete || undefined} data-current={current || undefined} key={phase.code}>
            <span aria-hidden="true">{complete ? "✓" : index + 1}</span>
            <small>{phase.label}</small>
          </li>
        );
      })}
    </ol>
  );
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
    <div className="task3-page task3-tasks-page">
      <PageHeading
        actions={<Button onClick={() => void load(true)} variant="secondary">刷新</Button>}
        description="任务在后台继续运行；离开页面后仍可在这里找回结果、失败原因和重试历史。"
        eyebrow="Tasks"
        title="任务中心"
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
        <div className="task3-task-list" aria-live="polite">
          {tasks.map((task) => {
            const copy = presentTask(task);
            const id = analysisId(task);
            const busy = busyRef === task.task_ref;
            return (
              <article className="task3-task-row" key={task.task_ref ?? task.analysis_ref}>
                <header>
                  <div>
                    <span className="task3-task-ref">{task.run_ref ?? task.analysis_ref ?? "本地导入"}</span>
                    <h2>{task.analysis_type === "flicking" ? "Flicking 分析" : task.analysis_type ?? "分析任务"}</h2>
                  </div>
                  <div className="task3-task-statuses">
                    {task.input_mode === "input_native" ? <PreviewBadge /> : null}
                    <Status tone={taskTone(task)}>{copy.state}</Status>
                  </div>
                </header>

                {task.state === "running" || task.state === "done" ? <StageStepper task={task} /> : null}
                {task.state === "queued" || task.state === "importing" || task.state === "retrying" ? (
                  <div className="task3-current-phase">当前阶段：{copy.phase ?? "准备训练记录"}</div>
                ) : null}

                {task.partial_outcome ? (
                  <Notice tone="warning" title="部分结果可用">
                    视觉阶段不可用，但 native deterministic 结果已保留；不会把整条分析显示为失败。
                  </Notice>
                ) : null}
                {task.failure ? (
                  <Notice tone="error" title={`${copy.failureDomain ?? "分析"}失败`}>
                    {task.failure.message} {task.failure.retryable ? "可以创建新的重试 attempt。" : "当前合同不允许重试。"}
                  </Notice>
                ) : null}

                {task.attempt_history && task.attempt_history.length > 1 ? (
                  <details className="task3-attempts">
                    <summary>{task.attempt_history.length} 次 attempt</summary>
                    <ol>{task.attempt_history.map((attempt) => <li key={attempt.attempt_ref}>第 {attempt.attempt_number} 次 · {presentTask({ ...task, state: attempt.state, phase: attempt.phase, failure: attempt.failure }).state}</li>)}</ol>
                  </details>
                ) : null}

                <footer>
                  <span>{task.created_at ? new Date(task.created_at).toLocaleString("zh-CN") : "时间不可用"}</span>
                  <div>
                    {task.state === "done" && id !== null ? <Button href={`/analysis/${id}`} variant="secondary">查看结果</Button> : null}
                    {task.retryable ? <Button disabled={busy} onClick={() => void retry(task)} variant="secondary">{busy ? "正在重试" : "重试"}</Button> : null}
                    {task.can_delete ? <Button disabled={busy} onClick={() => setDeleteTarget(task)} variant="ghost">删除</Button> : null}
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      )}

      <Dialog
        footer={<><Button onClick={() => setDeleteTarget(null)} variant="secondary">取消</Button><Button onClick={() => void remove()} variant="danger">删除任务记录</Button></>}
        onClose={() => setDeleteTarget(null)}
        open={deleteTarget !== null}
        title="确认删除"
      >
        <p>只删除这条 Analysis 与其所属产物，不删除 Run、自动录制、Raw Input 或用户源 Stats/Performance。</p>
      </Dialog>
    </div>
  );
}
