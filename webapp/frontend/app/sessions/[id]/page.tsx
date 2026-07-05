"use client";

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { getSession } from "@/lib/api";
import type { SessionStatus, SessionStatusEnum } from "@/lib/types";

/**
 * Processing page.
 *
 * Polls GET /api/sessions/{id} every 2s and reflects the backend's three
 * states (queued → running → done | failed) into the 4-step pipeline UI.
 *
 * No gradients — per 点点's explicit preference. The stitch reference's
 * ink-bleed atmospheric blobs and the animated pipeline-line gradient are
 * both removed.
 *
 * No fabricated step progress: the backend exposes only status, not a
 * percentage or a current step. So we map the three states onto the pipeline
 * statically:
 *   queued        → step 0 (Parsing) active, "排队中"
 *   running       → step 1 (Trajectory) active, "分析中…"
 *   done          → router.push to ./report
 *   failed        → error card + retry
 *
 * Golf copy in the stitch reference (挥杆数据 / 最佳弹道 / 击球节奏) and the
 * fake social-proof coaches ("由 12 位资深教练模型同步优化" + portraits)
 * are removed entirely.
 */

const POLL_INTERVAL_MS = 2000;

const PIPELINE_STEPS = [
  {
    key: "parsing",
    label: "Parsing",
    sub: "数据解析",
  },
  {
    key: "trajectory",
    label: "Trajectory",
    sub: "轨迹追踪",
  },
  {
    key: "kinematics",
    label: "Kinematics",
    sub: "运动学建模",
  },
  {
    key: "narration",
    label: "Narration",
    sub: "生成执教报告",
  },
] as const;

/** Rotating coach tips — all real, aim-training aligned. */
const COACH_TIPS: readonly { tag: string; body: string }[] = [
  {
    tag: "Becker 2020",
    body: "flick 减速段是命中成败最强的预测信号——爆发靠本能，命中靠刹车。",
  },
  {
    tag: "神经募集",
    body: "微调速度反映神经募集效率，优秀选手通常在 120ms 内完成视觉反馈闭环。",
  },
  {
    tag: "SPARC",
    body: "SPARC 是频域无量纲的平滑度指标，跨速度公平，慢瞄和快甩可以同台比较。",
  },
  {
    tag: "Tracking",
    body: "跟踪场景里，速度匹配比爆发速度更重要——能跟住，才有资格谈爆发。",
  },
];

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; data: SessionStatus }
  | { kind: "err"; message: string };

/** Map backend status → index of the pipeline step currently active (0-based). */
function activeStepIndex(status: SessionStatusEnum): number {
  switch (status) {
    case "queued":
      return 0;
    case "running":
      return 1;
    case "done":
      return PIPELINE_STEPS.length - 1;
    case "failed":
      return 0;
  }
}

export default function SessionProcessingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();

  // Next 15+: params is a Promise on dynamic pages — unwrap with React.use().
  const resolved = use(params);

  const sessionId = useMemo(() => {
    const n = Number(resolved.id);
    return Number.isFinite(n) ? n : NaN;
  }, [resolved.id]);

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [tipIndex, setTipIndex] = useState(0);
  const abortedRef = useRef(false);

  // Rotate the coach tip every 6s — independent of network.
  useEffect(() => {
    const id = setInterval(() => {
      setTipIndex((i) => (i + 1) % COACH_TIPS.length);
    }, 6000);
    return () => clearInterval(id);
  }, []);

  const poll = useCallback(async () => {
    if (!Number.isFinite(sessionId)) return;
    try {
      const data = await getSession(sessionId);
      setState({ kind: "ok", data });
    } catch (e) {
      setState({
        kind: "err",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }, [sessionId]);

  // Polling loop — fires once on mount, then every POLL_INTERVAL_MS.
  useEffect(() => {
    abortedRef.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const loop = async () => {
      if (abortedRef.current) return;
      await poll();
      if (abortedRef.current) return;
      timer = setTimeout(loop, POLL_INTERVAL_MS);
    };

    loop();

    return () => {
      abortedRef.current = true;
      clearTimeout(timer);
    };
  }, [poll]);

  // Side-effect: redirect to report when done. Kept out of render so React
  // doesn't warn about navigation during commit.
  useEffect(() => {
    if (state.kind === "ok" && state.data.status === "done") {
      router.push(`/sessions/${sessionId}/report`);
    }
  }, [state, sessionId, router]);

  // ---------- render ----------

  if (!Number.isFinite(sessionId)) {
    return (
      <Shell>
        <ErrorCard
          title="无效的 session ID"
          message={`无法解析 "${resolved.id}"`}
        />
      </Shell>
    );
  }

  if (state.kind === "err") {
    return (
      <Shell>
        <ErrorCard
          title="无法连接后端"
          message={state.message}
          onRetry={poll}
        />
      </Shell>
    );
  }

  const status: SessionStatusEnum | null =
    state.kind === "ok" ? state.data.status : null;

  const showFailedCard =
    state.kind === "ok" && state.data.status === "failed";

  if (showFailedCard) {
    return (
      <Shell>
        <ErrorCard
          title="分析失败"
          message={state.data.error ?? "后端未提供错误详情。"}
          onRetry={poll}
        />
      </Shell>
    );
  }

  const activeIdx =
    status === null ? 0 : activeStepIndex(status);

  const statusLabel = (() => {
    if (status === null) return "连接中…";
    if (status === "queued") return "排队中…";
    if (status === "running") return "分析中…";
    return "Loading Report";
  })();

  return (
    <Shell>
      <Header statusLabel={statusLabel} />
      <Pipeline activeIdx={activeIdx} />
      <CoachTipCard tip={COACH_TIPS[tipIndex]} />
      <ProgressBar statusLabel={statusLabel} status={status} />
    </Shell>
  );
}

// ---------- subcomponents ----------

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh flex flex-col bg-background text-on-surface">
      <TopAppBar />
      <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-md py-xl">
        {children}
      </main>
      <FooterBar />
    </div>
  );
}

function TopAppBar() {
  return (
    <header className="sticky top-0 z-50 w-full bg-background/80 backdrop-blur-md border-b border-outline-variant">
      <div className="max-w-[var(--spacing-container-max)] mx-auto px-md lg:px-lg h-16 flex items-center justify-between">
        <span className="font-headline-sm text-headline-sm font-bold text-primary">
          Cookie AI
        </span>
        <span className="hidden md:inline text-label-md text-on-surface-variant/60">
          Processing Analysis…
        </span>
      </div>
    </header>
  );
}

function Header({ statusLabel }: { statusLabel: string }) {
  return (
    <div className="text-center mb-xl max-w-[42rem]">
      <div className="inline-flex items-center gap-xs px-sm py-1 bg-outline-variant rounded-full mb-md">
        <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
        <span className="text-label-sm uppercase tracking-widest text-on-surface-variant">
          {statusLabel}
        </span>
      </div>
      <h1 className="font-display-lg text-display-lg text-on-surface mb-sm tracking-tight">
        正在构建你的<span className="text-primary italic">饼干</span>策略
      </h1>
      <p className="text-body-lg text-on-surface-variant max-w-[32rem] mx-auto">
        AI 正在解析你的录像与 Stats CSV，提取 flick 减速段，并生成个性化的执教报告。
      </p>
    </div>
  );
}

function Pipeline({ activeIdx }: { activeIdx: number }) {
  return (
    <div className="w-full max-w-[var(--spacing-container-max)] relative mb-xl">
      {/* static hairline track (the stitch's gradient line is removed) */}
      <div className="absolute top-6 left-0 w-full h-px bg-outline-variant z-0" />
      <div className="grid grid-cols-4 relative z-10">
        {PIPELINE_STEPS.map((step, i) => {
          const state =
            i < activeIdx ? "done" : i === activeIdx ? "active" : "pending";
          return (
            <div
              key={step.key}
              className="flex flex-col items-center"
            >
              <StepBadge state={state} />
              <span
                className={
                  "mt-md text-label-md " +
                  (state === "active"
                    ? "text-on-surface font-bold"
                    : state === "done"
                      ? "text-primary"
                      : "text-on-surface-variant")
                }
              >
                {step.label}
              </span>
              <span
                className={
                  "mt-1 text-label-sm " +
                  (state === "pending"
                    ? "text-on-surface-variant/40"
                    : "text-on-surface-variant")
                }
              >
                {step.sub}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StepBadge({
  state,
}: {
  state: "done" | "active" | "pending";
}) {
  const base =
    "w-12 h-12 rounded-full flex items-center justify-center mb-0 transition-all duration-300";
  if (state === "done") {
    return (
      <div
        className={`${base} border border-primary bg-background`}
        aria-label="已完成"
      >
        <CheckIcon className="w-5 h-5 text-primary" />
      </div>
    );
  }
  if (state === "active") {
    return (
      <div
        className={`${base} border-2 border-primary bg-background pulse-ring`}
        aria-label="进行中"
      >
        <SparkIcon className="w-5 h-5 text-primary" />
      </div>
    );
  }
  return (
    <div
      className={`${base} border border-outline-variant bg-background`}
      aria-label="待处理"
    >
      <DotIcon className="w-4 h-4 text-on-surface-variant" />
    </div>
  );
}

function CoachTipCard({ tip }: { tip: { tag: string; body: string } }) {
  return (
    <div className="w-full max-w-[36rem] bg-surface border-l-4 border-primary p-md lg:p-lg">
      <div className="flex items-start gap-md">
        <div className="p-xs bg-primary/10 rounded-lg shrink-0">
          <BulbIcon className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h3 className="font-headline-sm text-headline-sm text-on-surface mb-xs flex items-center gap-xs">
            教练小贴士
            <span className="text-label-sm uppercase bg-primary/5 px-2 py-0.5 rounded text-primary">
              {tip.tag}
            </span>
          </h3>
          <p className="text-body-md text-on-surface-variant leading-relaxed">
            {tip.body}
          </p>
        </div>
      </div>
    </div>
  );
}

function ProgressBar({
  statusLabel,
  status,
}: {
  statusLabel: string;
  status: SessionStatusEnum | null;
}) {
  // Static, honest progress indicator. No fabricated percentage — the bar
  // is purely a state cue: empty (queued), filled-but-indeterminate
  // (running), full (done).
  const widthPct =
    status === "done" ? "100%" : status === "running" ? "60%" : "10%";
  return (
    <div className="mt-xl w-64 text-center">
      <div className="h-1 w-full bg-outline-variant rounded-full overflow-hidden mb-sm">
        <div
          className="h-full bg-primary transition-all duration-1000 ease-out"
          style={{ width: widthPct }}
        />
      </div>
      <span className="text-label-sm text-on-surface-variant uppercase tracking-widest">
        {statusLabel}
      </span>
    </div>
  );
}

function ErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void | Promise<void>;
}) {
  return (
    <div className="w-full max-w-[36rem] bg-surface border border-error/40 border-l-4 border-l-error p-md lg:p-lg">
      <div className="flex items-start gap-md">
        <div className="p-xs bg-error/10 rounded-lg shrink-0">
          <WarnIcon className="w-5 h-5 text-error" />
        </div>
        <div className="flex-1">
          <h3 className="font-headline-sm text-headline-sm text-on-surface mb-xs">
            {title}
          </h3>
          <p className="text-body-md text-on-surface-variant leading-relaxed break-words">
            {message}
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={() => void onRetry()}
              className="mt-md inline-flex items-center px-sm py-xs bg-primary text-on-primary rounded text-label-md font-bold hover:bg-primary-fixed transition-colors"
            >
              重试
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function FooterBar() {
  return (
    <footer className="border-t border-outline-variant py-sm bg-surface-container-lowest">
      <div className="max-w-[var(--spacing-container-max)] mx-auto px-md lg:px-lg flex flex-col md:flex-row justify-between items-center gap-sm">
        <p className="text-label-sm text-on-surface-variant">
          © 2026 Aiming Cookie
        </p>
        <div className="flex gap-md items-center">
          {/* Placeholder — backend may not have a cancel endpoint yet. */}
          <Link
            href="/"
            className="text-label-sm text-on-surface-variant hover:text-primary transition-colors"
          >
            取消分析
          </Link>
        </div>
      </div>
    </footer>
  );
}

// ---------- inline icons (no external icon font dependency) ----------

type IconProps = { className?: string };

function CheckIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M5 12l5 5L20 7" />
    </svg>
  );
}

function SparkIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6L12 2z" />
    </svg>
  );
}

function DotIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
    </svg>
  );
}

function BulbIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 0 0-4 10.5c.5.5 1 1.5 1 2.5h6c0-1 .5-2 1-2.5A6 6 0 0 0 12 3z" />
    </svg>
  );
}

function WarnIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}
