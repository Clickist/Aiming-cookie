"use client";

import { useCallback, useEffect, useState } from "react";

import { VideoView } from "@/components/task5/VideoView";
import { getSession } from "@/lib/api";
import { presentAnalysisWorkspace, type AnalysisWorkspacePresentation } from "@/lib/contracts";
import { Button, ErrorState, Loading } from "@/ui/primitives";

export function analysisIdFromRef(value: string): number | null {
  const match = /^analysis:([1-9][0-9]*)$/.exec(value);
  return match ? Number(match[1]) : null;
}

/** Shared analysis-presentation cache (video pane and message cards reuse it). */
export const presentationCache = new Map<number, AnalysisWorkspacePresentation>();

/** Run 号缓存：与 presentation 一并抓取，用于标题区分同名场景的多次训练。 */
const runIdCache = new Map<number, number | null>();

export function CoachVideoPane({
  analysisRef,
  initialTimeMs = 0,
  onClose,
}: {
  analysisRef: string;
  initialTimeMs?: number;
  onClose: () => void;
}) {
  const [presentation, setPresentation] = useState<AnalysisWorkspacePresentation | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [playheadMs, setPlayheadMs] = useState(initialTimeMs);
  const [revision, setRevision] = useState(0);

  const load = useCallback(async (signal: AbortSignal) => {
    const analysisId = analysisIdFromRef(analysisRef);
    if (analysisId === null) {
      setPresentation(null);
      setRunId(null);
      setFailed(true);
      setLoading(false);
      return;
    }
    setLoading(true);
    setFailed(false);
    const cached = presentationCache.get(analysisId);
    if (cached) {
      setPresentation(cached);
      setRunId(runIdCache.get(analysisId) ?? null);
      setLoading(false);
      return;
    }
    try {
      const session = await getSession(analysisId, { signal });
      const next = presentAnalysisWorkspace(session);
      if (!next) throw new Error("analysis_presentation_unavailable");
      if (!signal.aborted) {
        presentationCache.set(analysisId, next);
        runIdCache.set(analysisId, session.kovaak_run_id);
        setPresentation(next);
        setRunId(session.kovaak_run_id);
      }
    } catch {
      if (!signal.aborted) {
        setPresentation(null);
        setRunId(null);
        setFailed(true);
      }
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [analysisRef]);

  useEffect(() => {
    const controller = new AbortController();
    setPlayheadMs(initialTimeMs);
    void load(controller.signal);
    return () => controller.abort();
  }, [initialTimeMs, load, revision]);

  return (
    <section aria-label="Coach 视频讲解" className="task7-coach-video-pane">
      <header className="task7-coach-video-pane__header">
        <div>
          <span>视频讲解</span>
          <h2>{presentation?.scenario ?? "训练视频"}</h2>
          {runId != null ? <small className="task7-coach-video-pane__run">run {runId}</small> : null}
        </div>
        <button aria-label="关闭视频讲解" className="task7-coach-video-pane__close" onClick={onClose} title="关闭视频讲解" type="button">×</button>
      </header>
      <div className="task7-coach-video-pane__body">
        {loading ? <Loading>正在读取本地视频与证据</Loading> : null}
        {!loading && failed ? (
          <ErrorState title="视频讲解暂时不可用">
            <Button onClick={() => setRevision((value) => value + 1)} variant="secondary">重试</Button>
          </ErrorState>
        ) : null}
        {!loading && presentation ? (
          <VideoView
            analysisId={presentation.analysisId}
            currentTimeMs={playheadMs}
            onCurrentTimeChange={setPlayheadMs}
            presentation={presentation}
          />
        ) : null}
      </div>
    </section>
  );
}
