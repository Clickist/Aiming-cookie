import { redirect } from "next/navigation";

import CoachView from "./CoachView";
import { getSession } from "@/lib/api";
import { analysisResultToCoachReport } from "@/lib/contracts";

/**
 * Coach dialogue route. Left = video + custom timeline; right = coach chat.
 * Status gate mirrors report page:
 *   - done    → render CoachView
 *   - queued | running → bounce to processing page
 *   - failed  → minimal error shell
 */
export default async function CoachPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const sessionId = Number(id);
  if (!Number.isFinite(sessionId) || sessionId <= 0) {
    redirect("/");
  }

  let status;
  try {
    status = await getSession(sessionId);
  } catch (err) {
    return (
      <CoachError
        title="无法连接后端"
        detail={err instanceof Error ? err.message : String(err)}
      />
    );
  }

  if (status.status === "queued" || status.status === "running") {
    redirect(`/sessions/${sessionId}`);
  }

  if (status.status === "failed") {
    return (
      <CoachError
        title="分析失败"
        detail={status.error?.message ?? "未知的后端错误"}
      />
    );
  }

  if (!status.result) {
    return <CoachError title="结果缺失" detail="status=done 但 result 为空" />;
  }

  const report = analysisResultToCoachReport(status.result);
  const archetypeLabel = report.diagnosis.profile.label;

  return <CoachView sessionId={sessionId} archetypeLabel={archetypeLabel} />;
}

function CoachError({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="min-h-dvh flex items-center justify-center px-md">
      <div className="bg-surface-container-low border border-outline rounded-lg p-lg max-w-[640px] w-full">
        <h1 className="text-headline-sm text-on-surface mb-sm">{title}</h1>
        <p className="text-body-md text-on-surface-variant break-words mb-md">
          {detail}
        </p>
        <a
          href="/history"
          className="text-label-md text-primary hover:opacity-80 transition-opacity"
        >
          返回历史记录
        </a>
      </div>
    </main>
  );
}