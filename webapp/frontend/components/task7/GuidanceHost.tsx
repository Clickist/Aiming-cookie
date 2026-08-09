"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { acknowledgeCoachGuidance } from "@/lib/api";
import {
  resolveGuidanceTarget,
  validateGuidancePrefill,
  type GuidanceTargetResolution,
} from "@/lib/navigation";
import type { GuidanceIntentV1 } from "@/lib/types";

export interface GuidanceEventDetail {
  run_ref: string;
  intent: GuidanceIntentV1;
}

export interface GuidanceHostProps {
  intent?: GuidanceIntentV1 | null;
  runRef?: string | null;
  onIntent?: (intent: GuidanceIntentV1 | null) => void;
}

type GuidanceOutcome = "completed" | "cancelled" | "failed" | "timed_out";

function isGuidanceIntent(value: unknown): value is GuidanceIntentV1 {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<GuidanceIntentV1>;
  return candidate.schema_version === "guidance_intent.v1"
    && typeof candidate.intent_id === "string"
    && typeof candidate.kind === "string"
    && typeof candidate.goal === "string";
}

function detailFromEvent(value: unknown): GuidanceEventDetail | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<GuidanceEventDetail>;
  return typeof candidate.run_ref === "string" && isGuidanceIntent(candidate.intent)
    ? { run_ref: candidate.run_ref, intent: candidate.intent }
    : null;
}

function targetFor(intent: GuidanceIntentV1 | null): GuidanceTargetResolution | null {
  const targetId = intent?.target?.target_id;
  return typeof targetId === "string" ? resolveGuidanceTarget(targetId) : null;
}

function safePrefillFor(intent: GuidanceIntentV1, target: GuidanceTargetResolution | null): Record<string, string> | null {
  if (!target) return intent.target == null ? {} : null;
  const raw = intent.target?.safe_prefill;
  if (raw != null && (typeof raw !== "object" || Array.isArray(raw))) return null;
  if (raw && Object.values(raw).some((value) => typeof value !== "string")) return null;
  return validateGuidancePrefill(target.targetId, raw ?? {}) ?? null;
}

/**
 * The sole UI owner for deterministic Coach guidance. It only knows semantic
 * targets and reports UI outcomes; product completion is re-verified by the
 * acknowledgement endpoint.
 */
export function GuidanceHost({ intent: suppliedIntent = null, runRef: suppliedRunRef = null, onIntent }: GuidanceHostProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [eventState, setEventState] = useState<GuidanceEventDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingPrefillRef = useRef<Record<string, string> | null>(null);

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = detailFromEvent((event as CustomEvent<unknown>).detail);
      if (detail) setEventState(detail);
    };
    window.addEventListener("aiming-cookie:coach-guidance", receive);
    return () => window.removeEventListener("aiming-cookie:coach-guidance", receive);
  }, []);

  const intent = suppliedIntent ?? eventState?.intent ?? null;
  const runRef = suppliedRunRef ?? eventState?.run_ref ?? null;
  const target = useMemo(() => targetFor(intent), [intent]);
  const prefill = useMemo(() => (intent ? safePrefillFor(intent, target) : null), [intent, target]);
  const invalidTarget = Boolean(intent && (intent.target && !target || prefill === null));

  useEffect(() => {
    if (!intent || !target || invalidTarget) return;
    if (target.route !== pathname) {
      pendingPrefillRef.current = prefill;
      router.push(target.route);
      return;
    }
    const focusTarget = target.sectionId
      ? document.getElementById(target.sectionId)
      : document.getElementById("main-content");
    if (focusTarget instanceof HTMLElement) {
      if (!focusTarget.hasAttribute("tabindex")) focusTarget.setAttribute("tabindex", "-1");
      focusTarget.focus({ preventScroll: true });
    }
    if (pendingPrefillRef.current || prefill && Object.keys(prefill).length > 0) {
      const safe = pendingPrefillRef.current ?? prefill ?? {};
      pendingPrefillRef.current = null;
      window.dispatchEvent(new CustomEvent("aiming-cookie:coach-guidance-prefill", { detail: { target_id: target.targetId, safe_prefill: safe } }));
    }
  }, [intent, invalidTarget, pathname, prefill, router, target]);

  const acknowledge = useCallback(async (outcome: GuidanceOutcome) => {
    if (!intent || !runRef || busy || invalidTarget) return;
    setBusy(true);
    setError(null);
    try {
      const result = await acknowledgeCoachGuidance({ run_ref: runRef, intent_id: intent.intent_id, outcome });
      const next = result.next_intent ?? null;
      setEventState(next ? { run_ref: result.run_ref, intent: next } : null);
      onIntent?.(next);
      window.dispatchEvent(new CustomEvent("aiming-cookie:coach-guidance-ack", {
        detail: { run_ref: result.run_ref, intent_id: result.intent_id, outcome, next_intent: next },
      }));
    } catch {
      setError("Guidance 状态暂时无法更新，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }, [busy, intent, invalidTarget, onIntent, runRef]);

  if (!intent) return null;
  if (invalidTarget || !runRef) {
    return (
      <aside aria-live="polite" className="task7-guidance-host" data-state="blocked">
        <strong>当前引导无法继续</strong>
        <p>目标不在受支持的产品范围内，Coach 不会模拟操作。</p>
      </aside>
    );
  }

  const needsUserAction = intent.kind === "user_action_required" || intent.kind === "ui_navigation" || intent.kind === "wait_for_state";
  const terminal = intent.kind === "completed" || intent.kind === "blocked";
  const label = intent.kind === "user_action_required"
    ? "完成后继续"
    : intent.kind === "wait_for_state"
      ? "已检查状态"
      : intent.kind === "ui_navigation"
        ? "已打开"
        : "关闭引导";

  return (
    <aside aria-live="polite" className="task7-guidance-host" data-kind={intent.kind} data-state={busy ? "busy" : "ready"}>
      <div className="task7-guidance-host__copy">
        <span className="task7-guidance-host__eyebrow">Coach 下一步</span>
        <strong>{intent.goal}</strong>
        {target ? <small>{target.targetId}</small> : null}
        {error ? <p role="alert">{error}</p> : null}
      </div>
      <div className="task7-guidance-host__actions">
        {!terminal && needsUserAction ? (
          <button disabled={busy} onClick={() => void acknowledge("completed")} type="button">{busy ? "正在检查" : label}</button>
        ) : null}
        {!terminal && needsUserAction ? (
          <button disabled={busy} onClick={() => void acknowledge("cancelled")} type="button">取消</button>
        ) : null}
        {terminal ? <button disabled={busy} onClick={() => void acknowledge("completed")} type="button">关闭</button> : null}
      </div>
    </aside>
  );
}
