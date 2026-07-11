"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import ReportView from "./ReportView";
import { getSession } from "@/lib/api";
import { analysisResultToCoachReport } from "@/lib/contracts";
import type { SessionStatus } from "@/lib/types";

type ReportState =
  | { kind: "loading" }
  | { kind: "status"; data: SessionStatus }
  | { kind: "error"; detail: string };

/**
 * This route must load in the client: a Tauri WebView obtains its dynamic
 * loopback runtime connection via invoke, which is unavailable to Node server
 * components.
 */
export default function ReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const { id } = use(params);
  const sessionId = useMemo(() => Number(id), [id]);
  const [state, setState] = useState<ReportState>({ kind: "loading" });

  useEffect(() => {
    if (!Number.isFinite(sessionId) || sessionId <= 0) {
      router.replace("/");
      return;
    }

    const controller = new AbortController();
    getSession(sessionId, { signal: controller.signal })
      .then((data) => setState({ kind: "status", data }))
      .catch((err) => {
        if (!controller.signal.aborted) {
          setState({
            kind: "error",
            detail: err instanceof Error ? err.message : String(err),
          });
        }
      });
    return () => controller.abort();
  }, [router, sessionId]);

  useEffect(() => {
    if (state.kind === "status" && (state.data.status === "queued" || state.data.status === "running")) {
      router.replace(`/sessions/${sessionId}`);
    }
  }, [router, sessionId, state]);

  if (!Number.isFinite(sessionId) || sessionId <= 0) {
    return null;
  }
  if (state.kind === "loading") {
    return <ReportError title="正在加载报告" detail="正在读取分析结果…" />;
  }
  if (state.kind === "error") {
    return <ReportError title="无法连接后端" detail={state.detail} />;
  }
  if (state.data.status === "queued" || state.data.status === "running") {
    return <ReportError title="分析仍在进行" detail="正在返回处理进度页面…" />;
  }
  if (state.data.status === "failed") {
    return (
      <ReportError
        title="分析失败"
        detail={state.data.error?.message ?? "未知的后端错误"}
      />
    );
  }
  if (!state.data.result) {
    return <ReportError title="结果缺失" detail="status=done 但 result 为空" />;
  }

  return (
    <ReportView
      report={analysisResultToCoachReport(state.data.result)}
      sessionId={sessionId}
    />
  );
}

function ReportError({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="min-h-dvh flex items-center justify-center px-md">
      <div className="bg-surface-container-low border border-outline rounded-lg p-lg max-w-[640px] w-full">
        <h1 className="text-headline-sm text-on-surface mb-sm">{title}</h1>
        <p className="text-body-md text-on-surface-variant break-words">{detail}</p>
        <Link
          href="/history"
          className="inline-block mt-md text-label-md text-primary hover:brightness-110"
        >
          返回历史记录
        </Link>
      </div>
    </main>
  );
}
