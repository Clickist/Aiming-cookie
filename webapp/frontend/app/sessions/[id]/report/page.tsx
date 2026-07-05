import { redirect } from "next/navigation";

import ReportView from "./ReportView";
import { getSession } from "@/lib/api";

/**
 * Coach report route. Renders the Wave 2 dark-bento report for a finished
 * session. Status gate:
 *   - done    → render ReportView with the CoachReport payload
 *   - queued | running → bounce to the processing page (Wave 2 sibling route)
 *   - failed  → render a minimal error shell
 *
 * `params` is a Promise in Next 15+ (Server Component async params); we await
 * it before parsing the id.
 */
export default async function ReportPage({
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
      <ReportError
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
      <ReportError
        title="分析失败"
        detail={status.error ?? "未知的后端错误"}
      />
    );
  }

  if (!status.result) {
    return <ReportError title="结果缺失" detail="status=done 但 result 为空" />;
  }

  return <ReportView report={status.result} sessionId={sessionId} />;
}

function ReportError({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="min-h-dvh flex items-center justify-center px-md">
      <div className="bg-surface-container-low border border-outline rounded-lg p-lg max-w-[640px] w-full">
        <h1 className="text-headline-sm text-on-surface mb-sm">{title}</h1>
        <p className="text-body-md text-on-surface-variant break-words">
          {detail}
        </p>
      </div>
    </main>
  );
}
