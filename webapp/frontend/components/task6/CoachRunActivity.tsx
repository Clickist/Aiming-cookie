"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Coach 回合活动块（对话流里的工作态呈现）。
 *
 * 数据全部来自既有 sidecar 合同：SSE partial 帧的 thinking_text、工具 activity
 * 的 tool_call_id / tool_name / command_name / duration_ms / args_preview /
 * result_preview、事件本身的 created_at。本组件只做呈现与开合交互，不解释
 * 业务语义；工具的中文标签映射仍在 CoachPanel 的 TOOL_COMMAND_LABELS。
 *
 * 交互规约（对标 Open WebUI StatusItem / assistant-ui Reasoning、Cline ThinkingRow）：
 * - 流式期间思考块自动展开，完成后默认收起为「已思考 N 秒」；
 * - 用户手动切换过一次后，自动开合永久让位于用户（manualOpen 接管）；
 * - 已完成的工具步骤收敛为一行计数，可展开明细；
 * - 所有循环动画（shimmer/pulse/spin）在 prefers-reduced-motion 下关闭。
 */

export interface CoachToolStep {
  key: string;
  label: string;
  meta: string | null;
  state: "done" | "active" | "fail";
  command: string | null;
  /** 完成步：后端 duration_ms；展示为 0.8 秒 / m:ss。 */
  durationMs?: number | null;
  /** 活动步：开始事件 created_at 的毫秒值，用于经过时间跳动。 */
  startedAtMs?: number | null;
  /** 长任务（分析类）的本机历史 ETA，活动时与计时并列。 */
  etaSeconds?: number | null;
  argsPreview?: string | null;
  resultPreview?: string | null;
}

function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = String(safe % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatDuration(ms: number): string {
  return ms >= 10_000 ? formatClock(ms / 1000) : `${(ms / 1000).toFixed(1)} 秒`;
}

/** 每秒跳动的经过时间；sinceMs 为 null 时退化为静态 ETA 文案。 */
export function ElapsedTicker({
  sinceMs,
  etaSeconds = null,
}: {
  sinceMs: number | null;
  etaSeconds?: number | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (sinceMs === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [sinceMs]);

  if (sinceMs === null) {
    return etaSeconds != null ? <span className="task6-tool-eta">预计约 {etaSeconds} 秒</span> : null;
  }
  return (
    // aria-live off：每秒变化不进读屏播报队列，状态由整行文本表达。
    <span className="task6-tool-eta" aria-live="off">
      {formatClock((now - sinceMs) / 1000)}
      {etaSeconds != null ? <> · 预计约 {etaSeconds} 秒</> : null}
    </span>
  );
}

/** 单个已完成步骤行：有摘要可展开，无摘要则纯标签 + 耗时。 */
export function CoachDoneStep({ step }: { step: CoachToolStep }) {
  const [open, setOpen] = useState(false);
  const detail =
    [step.argsPreview, step.resultPreview].find((value) => typeof value === "string" && value.trim().length > 0) ??
    null;
  return (
    <>
      {detail ? (
        <button aria-expanded={open} className="task6-tool-detail-toggle" onClick={() => setOpen(!open)} type="button">
          {step.label}
        </button>
      ) : (
        <span className="task6-tool-label">{step.label}</span>
      )}
      {step.meta ? <span className="task6-tool-meta">{step.meta}</span> : null}
      {step.durationMs != null && Number.isFinite(step.durationMs) ? (
        <span className="task6-tool-eta">{formatDuration(step.durationMs)}</span>
      ) : null}
      <div className="task6-collapse" data-state={open ? "open" : "closed"} inert={!open || undefined}>
        <div className="task6-collapse-inner">{detail ? <pre className="task6-tool-detail">{detail}</pre> : null}</div>
      </div>
    </>
  );
}

/**
 * 思考折叠块。streaming=true 时标题「正在思考…」+ 计时并自动展开实时草稿；
 * 结束后父级冻结秒数传入，收起展示「已思考 N 秒」。manualOpen 一旦非空即
 * 永久接管开合，自动行为不再介入。
 */
export function CoachThinkingBlock({
  text,
  streaming,
  frozenSeconds = null,
  startedAtMs = null,
}: {
  text: string | null;
  streaming: boolean;
  frozenSeconds?: number | null;
  startedAtMs?: number | null;
}) {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const open = manualOpen !== null ? manualOpen : streaming;
  const hasContent = typeof text === "string" && text.trim().length > 0;

  useEffect(() => {
    if (!open || manualOpen !== null) return;
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [text, open, manualOpen]);

  if (!hasContent && !streaming && frozenSeconds == null) return null;

  return (
    <div className="task6-think-block" data-open={open}>
      <button
        aria-expanded={open}
        className="task6-think-head"
        onClick={() => setManualOpen(!(open))}
        type="button"
      >
        <span aria-hidden="true" className="task6-think-dot" data-live={streaming || undefined} />
        {streaming ? (
          <>
            <span className="task6-shimmer-text">正在思考</span>
            <ElapsedTicker sinceMs={startedAtMs} />
          </>
        ) : (
          <span className="task6-tool-meta">
            {frozenSeconds != null ? `已思考 ${Math.max(1, Math.round(frozenSeconds / 1000))} 秒` : "思考过程"}
          </span>
        )}
      </button>
      <div className="task6-collapse" data-state={open ? "open" : "closed"} inert={!open || undefined}>
        <div className="task6-collapse-inner">
          <div className="task6-think-body" ref={bodyRef}>
            {hasContent ? text : ""}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 工具步骤时间线：执行中/失败步骤逐行；完成步收敛为一行计数，可展开明细。 */
export function CoachStepList({
  steps,
  stopped = false,
}: {
  steps: CoachToolStep[];
  stopped?: boolean;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const activeSteps = steps.filter((step) => step.state !== "done");
  const doneSteps = steps.filter((step) => step.state === "done");

  // 全部完成时收敛为一行计数（沿用旧行为并升级为可展开）；
  // 有进行中/失败步骤时完成的直接落行，保持时间顺序可读。
  const collapseAll = activeSteps.length === 0 && doneSteps.length > 0;

  return (
    <ol aria-label="工具执行步骤" className="task6-tool-tl">
      {collapseAll ? (
        <li className="task6-done-group">
          <button
            aria-expanded={historyOpen}
            className="task6-done-toggle"
            onClick={() => setHistoryOpen((value) => !value)}
            type="button"
          >
            <span aria-hidden="true" className="task6-tool-dot" data-state="done" />
            <span className="task6-tool-body">
              <span className="task6-tool-meta">已完成 {doneSteps.length} 步 · 查看</span>
            </span>
          </button>
          <div className="task6-collapse" data-state={historyOpen ? "open" : "closed"} inert={!historyOpen || undefined}>
            <div className="task6-collapse-inner">
              <ul className="task6-done-list">
                {doneSteps.map((step) => (
                  <li className="task6-tool-step" data-state="done" key={step.key}>
                    <span aria-hidden="true" className="task6-tool-dot" />
                    <span className="task6-tool-body">
                      <CoachDoneStep step={step} />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </li>
      ) : null}
      {!collapseAll
        ? doneSteps.map((step) => (
            <li className="task6-tool-step" data-state="done" key={step.key}>
              <span aria-hidden="true" className="task6-tool-dot" />
              <span className="task6-tool-body">
                <CoachDoneStep step={step} />
              </span>
            </li>
          ))
        : null}
      {activeSteps.map((step) => {
        const state = stopped ? ("stopped" as const) : step.state;
        return (
          <li className="task6-tool-step" data-state={state} key={step.key}>
            <span
              aria-hidden="true"
              className={`task6-tool-dot${state === "active" ? " task6-pulse-dot" : ""}`}
            />
            <span className="task6-tool-body">
              <span className={`task6-tool-label${state === "active" ? " task6-shimmer-text" : ""}`}>
                {step.label}
              </span>
              {step.meta ? <span className="task6-tool-meta">{step.meta}</span> : null}
              {state === "active" ? (
                <ElapsedTicker sinceMs={step.startedAtMs ?? null} etaSeconds={step.etaSeconds ?? null} />
              ) : null}
            </span>
          </li>
        );
      })}
      {stopped ? (
        <li className="task6-tool-step" data-state="stopped">
          <span aria-hidden="true" className="task6-tool-dot" />
          <span className="task6-tool-body">
            <span className="task6-tool-label">回答已停止，可重新提问</span>
          </span>
        </li>
      ) : null}
    </ol>
  );
}
