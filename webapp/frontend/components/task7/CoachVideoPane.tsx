"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { VideoView } from "@/components/task5/VideoView";
import { getSession } from "@/lib/api";
import { presentAnalysisWorkspace, type AnalysisWorkspacePresentation } from "@/lib/contracts";
import { IconClose } from "@/ui/icons";
import { Button, ErrorState, Loading } from "@/ui/primitives";

export function analysisIdFromRef(value: string): number | null {
  const match = /^analysis:([1-9][0-9]*)$/.exec(value);
  return match ? Number(match[1]) : null;
}

/** Shared analysis-presentation cache (video pane and message cards reuse it). */
export const presentationCache = new Map<number, AnalysisWorkspacePresentation>();

/** Run 号缓存：与 presentation 一并抓取，用于标题区分同名场景的多次训练。 */
const runIdCache = new Map<number, number | null>();

/**
 * 呈现缓存失效：同一分析重新分析后缓存结果是旧态，不失效的话视频面板
 * 一直显示旧结果直至重启。传 id 清对应两条缓存项；不传清空。
 * 两条开讲触发路径（CoachPanel 轮询 + 历史分析页活体观察）都经由
 * AppShell 的 handleAutoTeach，在这里单点调用即可全覆盖。
 */
export function invalidateAnalysisPresentationCache(analysisId?: number): void {
  if (analysisId === undefined) {
    presentationCache.clear();
    runIdCache.clear();
    return;
  }
  presentationCache.delete(analysisId);
  runIdCache.delete(analysisId);
}

export function CoachVideoPane({
  analysisRef,
  coachMessages = [],
  initialTimeMs = 0,
  jumpSeq = 0,
  onClose,
}: {
  analysisRef: string;
  /** 当前会话 assistant 讲解文本：底部回看 chips 跟随正文 @time。 */
  coachMessages?: ReadonlyArray<string>;
  initialTimeMs?: number;
  /** 每次 @time 点击递增（AppShell 维护）：同一时间码重复点击也构成新跳转意图。 */
  jumpSeq?: number;
  onClose: () => void;
}) {
  const [presentation, setPresentation] = useState<AnalysisWorkspacePresentation | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [playheadMs, setPlayheadMs] = useState(initialTimeMs);
  // @time 跳转意图信号（复盘升级 P0.3/D1）：seq 每次 initialTimeMs 变化递增，
  // 与播放反馈、手动拖动解耦——VideoView 只在该信号上做「到达即暂停＋脉冲」。
  const [jumpTarget, setJumpTarget] = useState<{ seq: number; ms: number } | null>(null);
  const jumpSeqRef = useRef(0);
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
    jumpSeqRef.current = Math.max(jumpSeqRef.current + 1, jumpSeq);
    setJumpTarget({ seq: jumpSeqRef.current, ms: initialTimeMs });
    void load(controller.signal);
    return () => controller.abort();
  }, [initialTimeMs, jumpSeq, load, revision]);

  return (
    <section aria-label="Coach 视频讲解" className="task7-coach-video-pane">
      <header className="task7-coach-video-pane__header">
        <div>
          <span>视频讲解</span>
          <h2>
            {presentation?.scenario ?? "训练视频"}
            {runId != null ? ` · run ${runId}` : ""}
          </h2>
        </div>
        <button aria-label="关闭视频讲解" className="task7-coach-video-pane__close" onClick={onClose} title="关闭视频讲解" type="button"><IconClose /></button>
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
            coachMessages={coachMessages}
            currentTimeMs={playheadMs}
            jumpTarget={jumpTarget}
            onCurrentTimeChange={setPlayheadMs}
            presentation={presentation}
          />
        ) : null}
      </div>
    </section>
  );
}
