"use client";

import { useEffect, useRef, useState, type ComponentType } from "react";

import { CoachMessageText } from "@/components/task7/CoachMessageText";
import {
  IconChart,
  IconChevronDown,
  IconDatabase,
  IconFileText,
  IconFolder,
  IconSearch,
  IconSettings,
  IconSpark,
} from "@/ui/icons";

/**
 * Coach 回合工作流呈现（对话流里的工作态）。
 *
 * 数据全部来自既有 sidecar 合同：SSE partial 帧的 thinking_text（当前思考段
 * 全文）、activity 的 tool_call_id / tool_name / command_name / duration_ms /
 * args_preview / result_preview、事件 created_at，以及 thinking started 帧
 * 携带的上一段终文 thinking_text。本组件只做呈现与开合交互，不解释业务语义。
 *
 * 结构（0828 拍板）：思考与工具按时序交错渲染（CoachWorkStream）——"想一段
 * →做一步"保持前因后果可读，不再思考一堆、动作一堆。
 *
 * 视觉（0828 拍板）：无点线时间线；每行＝语义图标 + 文字 + 行尾折叠箭头；
 * 思考头部＝星芒 + 「思考中」扫光（流式）/「思考过程 · 持续了 N 秒」；
 * 思考与动作任何时刻默认折叠，展开与否全归用户（0828 再拍板，取代流式
 * 自动展开）；
 * 循环动画在 prefers-reduced-motion 下关闭。
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

/** 工作流时序段：一段思考或一步工具，按发生顺序交错排列。 */
export type CoachWorkSegment =
  | {
    kind: "thinking";
    key: string;
    text: string;
    streaming: boolean;
    startedAtMs: number | null;
    frozenMs: number | null;
  }
  | { kind: "tool"; step: CoachToolStep };

/** 冻结一个思考段：终文落定、流式终止、时长按冻结时刻与段起点差补算。 */
export function freezeThinkingSegment(
  segment: Extract<CoachWorkSegment, { kind: "thinking" }>,
  text: string | null,
  frozenAtMs: number | null,
): Extract<CoachWorkSegment, { kind: "thinking" }> {
  return {
    ...segment,
    text: text ?? segment.text,
    streaming: false,
    frozenMs:
      segment.frozenMs
      ?? (segment.startedAtMs != null && frozenAtMs != null && frozenAtMs > segment.startedAtMs
        ? frozenAtMs - segment.startedAtMs
        : null),
  };
}

/**
 * 终结收敛（0.1.12 真机修复）：回合到达终态（failed/stopped，以及 succeeded
 * 与归档清场之间的空窗）后，残留的流式思考段与活动工具步必须就地终结。
 * 失败路径没有成功路径 settleSucceeded 的归档清场，且实时段清空后会从
 * events 重建——不冻结，「思考中」扫光与经过计时就会永远挂在错误卡片上方。
 * 思考段冻结为「思考过程 · 持续了 N 秒」；活动工具步按 stepFromToolEvent 的
 * cancelled→fail 同款语义收尾为失败态。
 */
export function settleTerminalWorkSegments(
  segments: CoachWorkSegment[],
  settledAtMs: number | null,
): CoachWorkSegment[] {
  let changed = false;
  const next = segments.map((segment) => {
    if (segment.kind === "thinking" && segment.streaming) {
      changed = true;
      return freezeThinkingSegment(segment, null, settledAtMs);
    }
    if (segment.kind === "tool" && segment.step.state === "active") {
      changed = true;
      return { kind: "tool" as const, step: { ...segment.step, state: "fail" as const } };
    }
    return segment;
  });
  return changed ? next : segments;
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

/* 工具语义图标（0828）：行首 glyph 按 command 映射，兜底齿轮。 */
type GlyphComponent = ComponentType<{ className?: string; "data-live"?: boolean | undefined }>;

const COMMAND_GLYPHS: Record<string, GlyphComponent> = {
  read: IconFileText,
  write: IconFileText,
  ls: IconFolder,
  get_analysis_summary: IconFileText,
  get_coach_knowledge: IconFileText,
  run_product_command: IconDatabase,
  "run.list": IconDatabase,
  "history.list": IconDatabase,
  "kovaak_scores.lookup": IconSearch,
  "kovaak_scores.refresh_connected": IconSearch,
  "eloshapes.query": IconSearch,
  "purchase_links.lookup": IconSearch,
  "profile.aiming.snapshot": IconSearch,
  "peripheral_profile.get": IconSearch,
  "history.trend": IconChart,
  "analysis.compare": IconChart,
  "analysis.create_from_run": IconChart,
  "analysis.retry": IconChart,
  "analysis.outcomes.timeline": IconChart,
};

function CommandGlyph({ command }: { command: string | null }) {
  const Glyph = (command != null ? COMMAND_GLYPHS[command] : undefined) ?? IconSettings;
  return <Glyph className="task6-tool-glyph" />;
}

/* ── 思考折叠块 ────────────────────────────────────────────────────────
   头部＝星芒 + 文字（流式「思考中」扫光 / 完成「思考过程 · 持续了 N 秒」）
   + 行尾折叠箭头。任何时刻默认收起（0828 拍板再翻转：交错呈现保留，展开
   与否全归用户，自动开合不再介入）。 */

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
  const [open, setOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const hasContent = typeof text === "string" && text.trim().length > 0;

  useEffect(() => {
    if (!open) return;
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [text, open]);

  if (!hasContent && !streaming && frozenSeconds == null) return null;

  return (
    <div className="task6-think-block" data-open={open}>
      <button
        aria-expanded={open}
        className="task6-think-head"
        onClick={() => setOpen(!(open))}
        type="button"
      >
        <IconSpark className="task6-think-glyph" data-live={streaming || undefined} />
        {streaming ? (
          <>
            <span className="task6-shimmer-text">思考中</span>
            <ElapsedTicker sinceMs={startedAtMs} />
          </>
        ) : (
          <span className="task6-tool-meta">
            {/* 完成态文案对齐 ZCode 参照（0827 拍板）：持续时长进标题行，不再用「已思考」 */}
            {frozenSeconds != null ? `思考过程 · 持续了 ${Math.max(1, Math.round(frozenSeconds / 1000))} 秒` : "思考过程"}
          </span>
        )}
        <IconChevronDown aria-hidden="true" className="task6-caret" data-open={open} />
      </button>
      <div className="task6-collapse" data-state={open ? "open" : "closed"} inert={!open || undefined}>
        <div className="task6-collapse-inner">
          {/* 思考内容走受控富渲染（0828 调研对齐生产级聊天）：模型的思考
              常带 markdown 列表/加粗，pre-wrap 平铺是"一坨字"观感的根因；
              不传 analysisRef，思考里的 @time 呈静态样式不可点。 */}
          <div className="task6-think-body" ref={bodyRef}>
            {hasContent ? <CoachMessageText text={text} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── 工具行骨架：语义图标 + 内容 + 行尾折叠箭头 ─────────────────────── */

function StepBody({ step }: { step: CoachToolStep }) {
  return (
    <>
      {step.meta ? <span className="task6-tool-meta">{step.meta}</span> : null}
      {step.state === "active" ? (
        <ElapsedTicker sinceMs={step.startedAtMs ?? null} etaSeconds={step.etaSeconds ?? null} />
      ) : null}
      {step.state === "done" && step.durationMs != null && Number.isFinite(step.durationMs) ? (
        <span className="task6-tool-eta">{formatDuration(step.durationMs)}</span>
      ) : null}
    </>
  );
}

/** 明细（参数/结果摘要）折叠体：done/fail 步可展开时挂在这行 body 下方。 */
function StepDetail({ step, open }: { step: CoachToolStep; open: boolean }) {
  const nonEmpty = (value: string | null | undefined): value is string =>
    typeof value === "string" && value.trim().length > 0;
  // fail 行明细＝命令＋报错都展示（stderr 才是用户展开的目的）；
  // done 行维持单摘要（参数优先）。
  const first = [step.argsPreview, step.resultPreview].find(nonEmpty);
  const details = step.state === "fail"
    ? [step.argsPreview, step.resultPreview].filter(nonEmpty)
    : first
      ? [first]
      : [];
  return (
    <div className="task6-collapse" data-state={open ? "open" : "closed"} inert={!open || undefined}>
      <div className="task6-collapse-inner">
        {details.map((detail, index) => (
          <pre className="task6-tool-detail" key={index}>{detail}</pre>
        ))}
      </div>
    </div>
  );
}

function hasStepDetail(step: CoachToolStep): boolean {
  return (step.argsPreview ?? "").trim().length > 0 || (step.resultPreview ?? "").trim().length > 0;
}

/** 行尾箭头开关：箭头是用户最自然的点击目标（0906 点点实测点箭头无反应），
    与行首文字开关共用同一展开状态，故与文字开关互不嵌套、各自绑定。 */
function CaretToggle({ open, onToggle, label }: { open: boolean; onToggle: () => void; label: string }) {
  return (
    <button
      aria-expanded={open}
      aria-label={`${open ? "收起" : "展开"}${label}明细`}
      className="task6-caret-toggle"
      onClick={onToggle}
      type="button"
    >
      <IconChevronDown aria-hidden="true" className="task6-caret" data-open={open} />
    </button>
  );
}

/** 单步行（active/fail/done）：done/fail 且带摘要时可展开明细——失败行
    展开＝让用户看到命令与报错，永远打不开的红行等于没有反馈（0911 审计）。 */
function WorkStepLine({ step }: { step: CoachToolStep }) {
  const [open, setOpen] = useState(false);
  const expandable = (step.state === "done" || step.state === "fail") && hasStepDetail(step);
  const toggle = () => setOpen(!open);
  return (
    <li className="task6-tool-step" data-state={step.state}>
      <CommandGlyph command={step.command} />
      <span className="task6-tool-body">
        {expandable ? (
          <button aria-expanded={open} className="task6-tool-detail-toggle" onClick={toggle} type="button">
            {step.label}
          </button>
        ) : (
          <span className={`task6-tool-label${step.state === "active" ? " task6-shimmer-text" : ""}`}>
            {step.label}
          </span>
        )}
        <StepBody step={step} />
        <StepDetail step={step} open={open} />
      </span>
      {expandable ? <CaretToggle open={open} onToggle={toggle} label={step.label} /> : null}
    </li>
  );
}

/** 从参数摘要里提炼一行可读的差异化信息（路径/查询词等）——组行已带
    标签，组内明细不再重复「读取文件」这种同名词（0828 点点拍板）。 */
function stepBrief(step: CoachToolStep): string | null {
  const raw = step.argsPreview ?? "";
  for (const field of ["path", "query", "item_name", "file", "analysis_ref"]) {
    const match = new RegExp(`"${field}"\\s*:\\s*"([^"]{1,96})"`).exec(raw);
    if (match) return match[1];
  }
  return null;
}

/** 组内明细行：一行到底——参数路径 + 非零耗时，无内层展开、无零秒、
    无行首图标（0828 点点拍板：文字与组行「读取文件」的文字头对齐）。 */
function GroupStepLine({ step }: { step: CoachToolStep }) {
  const brief = stepBrief(step) ?? step.meta;
  const hasText = brief != null && brief.trim().length > 0;
  const duration =
    step.durationMs != null && Number.isFinite(step.durationMs) && step.durationMs >= 1000
      ? formatDuration(step.durationMs)
      : null;
  return (
    <li className="task6-tool-step task6-tool-step--brief" data-state="done">
      <span className="task6-tool-body">
        {hasText ? <span className="task6-tool-meta">{brief}</span> : null}
        {duration ? <span className="task6-tool-eta">{duration}</span> : null}
      </span>
    </li>
  );
}

/** 同名连续完成步聚合行：「读取文件 · 4 次 · 共 3.2秒」，展开为逐行明细。 */
function WorkGroupLine({ label, steps }: { label: string; steps: CoachToolStep[] }) {
  const [open, setOpen] = useState(false);
  const count = steps.length;
  const totalMs = steps.reduce((sum, s) => sum + (typeof s.durationMs === "number" ? s.durationMs : 0), 0);
  // 组可展开＝组内至少一行有可读的差异化摘要——展开后只有纯路径列表，
  // 没有任何可显示内容的组不套可展开壳。
  const expandable = steps.some((step) => (stepBrief(step) ?? step.meta)?.trim().length ? true : false);
  const totalTail = totalMs >= 1000 ? ` · 共 ${formatDuration(totalMs)}` : "";
  const summary = count > 1 ? `${label} · ${count} 次${totalTail}` : label;
  const toggle = () => setOpen(!open);
  return (
    <li className="task6-tool-step" data-state="done">
      <CommandGlyph command={steps[0]?.command ?? null} />
      <span className="task6-tool-body">
        {expandable ? (
          <button aria-expanded={open} className="task6-tool-detail-toggle" onClick={toggle} type="button">
            {summary}
          </button>
        ) : (
          <span className="task6-tool-label">{summary}</span>
        )}
        {expandable ? (
          <div className="task6-collapse" data-state={open ? "open" : "closed"} inert={!open || undefined}>
            <div className="task6-collapse-inner">
              <ul className="task6-done-list">
                {steps.map((step) => (
                  <GroupStepLine key={step.key} step={step} />
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </span>
      {expandable ? <CaretToggle open={open} onToggle={toggle} label={summary} /> : null}
    </li>
  );
}

/**
 * 工作流时序流（0828 拍板）：思考段与工具段按发生顺序交错渲染——"想一段
 * →做一步"保持前因后果。连续同名完成步仍聚合成一行摘要；stopped 尾行收尾。
 * run 流式与归档共用本组件（归档 segments 来自 localStorage v2）。
 */
export function CoachWorkStream({
  segments,
  stopped = false,
}: {
  segments: CoachWorkSegment[];
  stopped?: boolean;
}) {
  // 总折叠默认收起（0912 点点拍板）；重挂（新回合/切回归档）回到折叠态。
  const [expanded, setExpanded] = useState(false);
  type Row =
    | { kind: "thinking"; segment: Extract<CoachWorkSegment, { kind: "thinking" }> }
    | { kind: "group"; label: string; steps: CoachToolStep[] }
    | { kind: "tool"; step: CoachToolStep };

  const rows: Row[] = [];
  for (const segment of segments) {
    if (segment.kind === "thinking") {
      rows.push({ kind: "thinking", segment });
      continue;
    }
    const { step } = segment;
    if (step.state !== "done") {
      rows.push({ kind: "tool", step });
      continue;
    }
    const last = rows[rows.length - 1];
    if (last && last.kind === "group" && last.label === step.label) {
      last.steps.push(step);
    } else {
      rows.push({ kind: "group", label: step.label, steps: [step] });
    }
  }

  const visible = rows.filter((row) => row.kind !== "thinking" || row.segment.text.trim().length > 0 || row.segment.streaming);
  if (visible.length === 0 && !stopped) return null;

  // 总折叠（0912 点点拍板，默认收起）：折叠行＝「已工作 m:ss」＋当前活动
  // 摘要，展开才是完整交错时间线。时长：进行中走表（ElapsedTicker，起点＝
  // 最早段 startedAtMs）；归档按段起点/冻结时长推算静态总时长，推不出退化
  // 为不带时长的「工作过程」。摘要：流式思考＝「思考中」；活动工具步＝其
  // label——折叠后 run 状态仍有可读出口。
  let hasLive = false;
  let startMs: number | null = null;
  let endMs: number | null = null;
  let activityLabel: string | null = null;
  const noteStep = (step: CoachToolStep) => {
    if (step.state === "active") {
      hasLive = true;
      activityLabel = activityLabel ?? step.label;
    }
    if (step.startedAtMs != null) {
      startMs = startMs === null ? step.startedAtMs : Math.min(startMs, step.startedAtMs);
      const stepEnd = step.state === "done" && step.durationMs != null
        ? step.startedAtMs + step.durationMs
        : step.startedAtMs;
      endMs = endMs === null ? stepEnd : Math.max(endMs, stepEnd);
    }
  };
  for (const row of visible) {
    if (row.kind === "thinking") {
      if (row.segment.streaming) {
        hasLive = true;
        activityLabel = activityLabel ?? "思考中";
      }
      if (row.segment.startedAtMs != null) {
        startMs = startMs === null ? row.segment.startedAtMs : Math.min(startMs, row.segment.startedAtMs);
        const segEnd = row.segment.frozenMs != null ? row.segment.startedAtMs + row.segment.frozenMs : null;
        if (segEnd != null) endMs = endMs === null ? segEnd : Math.max(endMs, segEnd);
      }
    } else if (row.kind === "group") {
      row.steps.forEach(noteStep);
    } else {
      noteStep(row.step);
    }
  }

  return (
    <div aria-label="工作过程" className="task6-work-stream" role="list" data-summarized="true">
      <button
        aria-expanded={expanded}
        className="task6-work-summary"
        onClick={() => setExpanded(!(expanded))}
        type="button"
      >
        <IconChevronDown aria-hidden="true" className="task6-caret" data-open={expanded} />
        {startMs != null ? (
          hasLive ? (
            <span className="task6-work-summary-label">
              已工作&nbsp;<ElapsedTicker sinceMs={startMs} />
            </span>
          ) : endMs != null && endMs > startMs ? (
            <span className="task6-work-summary-label">已工作 {formatDuration(endMs - startMs)}</span>
          ) : (
            <span className="task6-work-summary-label">工作过程</span>
          )
        ) : (
          <span className="task6-work-summary-label">工作过程</span>
        )}
        {activityLabel ? <span className="task6-work-summary-activity">{activityLabel}</span> : null}
      </button>
      <div className="task6-collapse" data-state={expanded ? "open" : "closed"} inert={!expanded || undefined}>
        <div className="task6-collapse-inner">
          {visible.map((row, index) => {
            if (row.kind === "thinking") {
              const segment = row.segment;
              return (
                <CoachThinkingBlock
                  key={segment.key}
                  frozenSeconds={segment.frozenMs}
                  startedAtMs={segment.startedAtMs}
                  streaming={segment.streaming}
                  text={segment.text}
                />
              );
            }
            if (row.kind === "group") {
              return <WorkGroupLine key={`g${index}`} label={row.label} steps={row.steps} />;
            }
            return <WorkStepLine key={row.step.key} step={row.step} />;
          })}
          {stopped ? (
            <div className="task6-tool-step" data-state="stopped" role="listitem">
              <CommandGlyph command={null} />
              <span className="task6-tool-body">
                <span className="task6-tool-label">回答已停止，可重新提问</span>
              </span>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
