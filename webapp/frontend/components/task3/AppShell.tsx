"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  createCoachAgentRun,
  createCoachSession,
  deleteCoachSession,
  getDefaultProviderStatus,
  getProductState,
  listCoachSessions,
  listSessions,
  updateCoachSession,
} from "@/lib/api";
import {
  ANALYSIS_AUTO_TEACH_EVENT,
  buildAnalysisAutoTeachContent,
  markAnalysisAutoTaught,
  readAutoTaughtAnalyses,
  readLastCoachSessionId,
  writeLastCoachSessionId,
} from "@/lib/contracts";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import type { CoachAgentRunV1, ProviderProfileState } from "@/lib/types";
import { CoachPanel } from "@/components/task6/CoachPanel";
import { CoachVideoPane } from "@/components/task7/CoachVideoPane";
import SessionRail, { type SessionRailSession } from "@/components/task7/SessionRail";
import { startWindowDragging, TauriWindowControls } from "@/components/task3/TauriWindowControls";
import { Toast, useAnimatedPresence } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachVideoTarget = { analysisRef: string; timeMs: number };

function parseSessionId(raw: string | null): number | null {
  if (!raw || !/^[1-9][0-9]*$/.test(raw)) return null;
  return Number(raw);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const shellHidden = pathname.startsWith("/onboarding");
  const coachWorkspaceRoute = pathname === "/" || pathname === "/s" || pathname === "/s/";
  const settingsRoute = pathname.startsWith("/settings");
  const [capability, setCapability] = useState<CoachCapability>("loading");
  const [startupRouteResolved, setStartupRouteResolved] = useState(false);
  const [coachSessions, setCoachSessions] = useState<SessionRailSession[]>([]);
  const [selectedCoachSessionId, setSelectedCoachSessionId] = useState<number | null>(null);
  // 冷启动只尝试恢复一次「上次最后在看的会话」；会话列表未加载前不消耗这次机会。
  const hasRestoredLastSessionRef = useRef(false);
  const [draftSession, setDraftSession] = useState(false);
  const [videoTarget, setVideoTarget] = useState<CoachVideoTarget | null>(null);
  const [sessionFeedback, setSessionFeedback] = useState<string | null>(null);
  const [softStartRun, setSoftStartRun] = useState<CoachAgentRunV1 | null>(null);
  const settingsChildrenRef = useRef<ReactNode>(null);
  const settingsPresence = useAnimatedPresence(settingsRoute, 160);
  const startupPending = coachWorkspaceRoute && !startupRouteResolved;
  const showSessionRail = !shellHidden && !settingsRoute && !startupPending;
  const keepSessionRailMounted = !shellHidden && !startupPending;
  const routeSessionId = parseSessionId(searchParams.get("sessionId"));
  if (settingsRoute) settingsChildrenRef.current = children;
  const settingsOverlayChildren = settingsRoute ? children : settingsChildrenRef.current;
  const settingsOverlayVisible = (settingsRoute || settingsPresence.present) && settingsOverlayChildren !== null;

  useEffect(() => {
    if (!coachWorkspaceRoute) return undefined;
    const controller = new AbortController();
    void getProductState({ signal: controller.signal })
      .then(async (state) => {
        if (controller.signal.aborted) return;
        if (state.availability === "available" && state.onboarding_completed !== true) {
          router.replace("/onboarding");
          return;
        }
        if (isDesktopRuntime()) {
          try { await setDesktopCaptureEnabled(true); } catch { /* best-effort capture restore on restart */ }
        }
        setStartupRouteResolved(true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setStartupRouteResolved(true);
      });
    return () => controller.abort();
  }, [coachWorkspaceRoute, router]);

  useEffect(() => {
    if (shellHidden) return undefined;
    const controller = new AbortController();
    void getDefaultProviderStatus({ signal: controller.signal })
      .then((result) => setCapability(result.status))
      .catch(() => {
        if (!controller.signal.aborted) setCapability("unavailable");
      });
    return () => controller.abort();
  }, [shellHidden]);

  useEffect(() => {
    if (shellHidden) return undefined;
    const controller = new AbortController();
    void listCoachSessions({ signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      setCoachSessions(result.sessions as SessionRailSession[]);
    }).catch(() => undefined);
    return () => controller.abort();
  }, [shellHidden]);

  // 带着分析意图（History 的「让 Coach 分析」）进入 Coach 工作区：没有进行中
  // 的会话时用新草稿承接新意图，避免塞进旧对话；query 即刻清掉防止刷新重复触发。
  useEffect(() => {
    if (!coachWorkspaceRoute) return;
    if (new URLSearchParams(window.location.search).get("intent") !== "coach-analysis") return;
    if (selectedCoachSessionId === null && !draftSession) setDraftSession(true);
    window.history.replaceState(null, "", window.location.pathname);
  }, [coachWorkspaceRoute, searchParams, selectedCoachSessionId, draftSession]);

  useEffect(() => {
    if (draftSession) {
      if (selectedCoachSessionId !== null) setSelectedCoachSessionId(null);
      return;
    }
    if (routeSessionId !== null && coachSessions.some((session) => Number(session.id) === routeSessionId)) {
      setSelectedCoachSessionId(routeSessionId);
      return;
    }
    if (selectedCoachSessionId !== null && coachSessions.some((session) => Number(session.id) === selectedCoachSessionId)) {
      return;
    }
    // 应用重启后恢复上次最后在看的会话（仅冷启动的首次选择，且列表已加载）。
    if (!hasRestoredLastSessionRef.current && coachSessions.length > 0) {
      hasRestoredLastSessionRef.current = true;
      const lastViewedId = readLastCoachSessionId(window.localStorage);
      if (lastViewedId !== null && coachSessions.some((session) => Number(session.id) === lastViewedId)) {
        setSelectedCoachSessionId(lastViewedId);
        return;
      }
    }
    // 其余情况恢复 primary 会话（上次对话的延续）。
    const primary = coachSessions.find((session) => session.kind === "primary");
    setSelectedCoachSessionId(primary ? Number(primary.id) : coachSessions[0] ? Number(coachSessions[0].id) : null);
  }, [coachSessions, draftSession, routeSessionId, selectedCoachSessionId]);

  // 记录当前正在看的会话，供下次启动恢复。
  useEffect(() => {
    if (selectedCoachSessionId !== null) {
      writeLastCoachSessionId(window.localStorage, selectedCoachSessionId);
    }
  }, [selectedCoachSessionId]);

  useEffect(() => {
    setVideoTarget(null);
  }, [selectedCoachSessionId]);

  useEffect(() => {
    if (settingsRoute) return;
    if (document.activeElement instanceof HTMLElement && document.activeElement.closest('[data-settings-page="true"]')) {
      document.activeElement.blur();
    }
  }, [settingsRoute]);

  useEffect(() => {
    if (settingsRoute || settingsPresence.present || settingsChildrenRef.current === null) return;
    settingsChildrenRef.current = null;
    window.requestAnimationFrame(() => document.getElementById("main-content")?.focus());
  }, [settingsPresence.present, settingsRoute]);

  const reloadCoachSessions = useCallback(async (nextSelectedId?: number | null) => {
    const result = await listCoachSessions();
    const sessions = result.sessions as SessionRailSession[];
    setCoachSessions(sessions);
    setSelectedCoachSessionId((current) => {
      if (nextSelectedId !== undefined && nextSelectedId !== null && sessions.some((session) => Number(session.id) === nextSelectedId)) {
        return nextSelectedId;
      }
      if (current !== null && sessions.some((session) => Number(session.id) === current)) return current;
      // 无可恢复会话时返回 null；选中 useEffect 会统一把这种状态落到草稿。
      return null;
    });
  }, []);

  useEffect(() => {
    const handleSessionUpdated = () => {
      void reloadCoachSessions();
    };
    window.addEventListener("aiming-cookie:coach-session-updated", handleSessionUpdated);
    return () => window.removeEventListener("aiming-cookie:coach-session-updated", handleSessionUpdated);
  }, [reloadCoachSessions]);

  // 分析完成自动开讲：AnalysisWorkspace 活体观察到 done 时派发事件；这里在
  // Provider 可用时为该分析创建一次 Coach run（每个 Analysis 只开讲一次），
  // 由 CoachPanel 的 softStartRun 承接展示。
  useEffect(() => {
    const seen = readAutoTaughtAnalyses(window.localStorage);
    const handleAutoTeach = async (event: Event) => {
      const detail = (event as CustomEvent<{ analysis_ref?: unknown }>).detail;
      const analysisRef = detail?.analysis_ref;
      if (typeof analysisRef !== "string" || !/^analysis:[1-9][0-9]*$/.test(analysisRef)) return;
      if (seen.has(analysisRef)) return;
      seen.add(analysisRef);
      markAnalysisAutoTaught(window.localStorage, analysisRef);
      if (capability !== "ready") return;
      try {
        const run = await createCoachAgentRun(buildAnalysisAutoTeachContent(analysisRef));
        setSoftStartRun(run);
        setSelectedCoachSessionId((current) => (current === run.session_id ? current : run.session_id));
        window.dispatchEvent(new CustomEvent("aiming-cookie:coach-session-updated"));
      } catch {
        // 分析完成不再弹 Toast；自动开讲结果由 Coach 面板的动作指示器呈现。
      }
    };
    window.addEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
    return () => window.removeEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
  }, [capability]);

  // 自动开讲不能依赖用户守在分析页：轮询会话列表，把「本生命周期内
  // 观察到 running → done」的分析以同一事件派发（防重沿用 localStorage）。
  // 只触发新鲜转换，翻旧记录不开讲。
  useEffect(() => {
    const seenRunning = new Set<number>();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (cancelled) return;
      try {
        const response = await listSessions();
        if (cancelled) return;
        const finished: number[] = [];
        for (const item of response.sessions) {
          if (item.status === "queued" || item.status === "running") {
            seenRunning.add(item.id);
          } else if (item.status === "done" && seenRunning.has(item.id)) {
            seenRunning.delete(item.id);
            finished.push(item.id);
          } else {
            seenRunning.delete(item.id);
          }
        }
        for (const id of finished) {
          window.dispatchEvent(new CustomEvent(ANALYSIS_AUTO_TEACH_EVENT, {
            detail: { analysis_ref: `analysis:${id}` },
          }));
        }
      } catch {
        // 本地 runtime 暂不可达：下一轮重试。
      } finally {
        if (!cancelled) timer = setTimeout(tick, 5000);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const handleNewCoachSession = () => {
    setDraftSession(true);
    setSelectedCoachSessionId(null);
    router.push("/");
  };

  const ensureCoachSession = useCallback(async () => {
    try {
      const session = await createCoachSession();
      await reloadCoachSessions(session.id);
      setDraftSession(false);
      router.push(`/s?sessionId=${session.id}`);
      return session.id;
    } catch {
      return null;
    }
  }, [reloadCoachSessions, router]);

  const handleArchiveCoachSession = async (session: SessionRailSession) => {
    try {
      await updateCoachSession(Number(session.id), { status: "archived" });
    } catch {
      setSessionFeedback("未能归档会话，请重试。");
      return;
    }
    try {
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      setSessionFeedback("操作已完成，但会话列表暂时未能刷新。");
    }
  };

  const handleDeleteCoachSession = async (session: SessionRailSession) => {
    try {
      await deleteCoachSession(Number(session.id));
    } catch {
      setSessionFeedback("未能删除会话，请重试。");
      return;
    }
    try {
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      setSessionFeedback("操作已完成，但会话列表暂时未能刷新。");
    }
  };

  if (shellHidden) return <>{children}</>;

  return (
    <div className="task3-app">
      <a className="task3-skip-link" href="#main-content">跳到主要内容</a>
      <header
        className="task3-toolbar"
        onMouseDown={(event) => {
          if (event.button === 0) void startWindowDragging();
        }}
      >
        <span className="task3-logo" aria-label="Aiming Cookie">Aiming&nbsp;Cookie</span>
        <div className="task3-toolbar-spacer" />
        <TauriWindowControls />
      </header>
      <div
        className="task3-workspace"
        data-coach-workspace={coachWorkspaceRoute || undefined}
        data-settings-route={settingsRoute || undefined}
        data-session-rail={showSessionRail || undefined}
      >
        {keepSessionRailMounted ? (
          <SessionRail
            className={settingsRoute ? "task7-session-rail--route-hidden" : undefined}
            currentSessionId={draftSession ? "draft" : selectedCoachSessionId}
            onArchiveSession={(session) => void handleArchiveCoachSession(session)}
            onHistory={() => router.push("/history")}
            onNewSession={handleNewCoachSession}
            onSelectSession={(session) => {
              if (session.id === "draft") return; // 当前草稿，点击无操作
              setDraftSession(false);
              setSelectedCoachSessionId(Number(session.id));
              router.push(`/s?sessionId=${session.id}`);
            }}
            onSettings={() => router.push("/settings")}
            onSoftDeleteSession={(session) => void handleDeleteCoachSession(session)}
            providerStatus={capability === "ready" ? "ready" : capability === "loading" ? "loading" : capability === "unavailable" ? "unavailable" : "waiting"}
            sessions={draftSession ? [{ id: "draft", title: "新对话", kind: "conversation" }, ...coachSessions] : coachSessions}
          />
        ) : null}
        <main
          aria-hidden={settingsRoute || undefined}
          className="task3-route-content"
          id={settingsRoute ? undefined : "main-content"}
          tabIndex={-1}
        >
          {startupPending ? null : (
            <>
              <div
                aria-hidden={!coachWorkspaceRoute || undefined}
                className="task3-coach-view"
                data-video-open={Boolean(videoTarget) || undefined}
                style={{ display: coachWorkspaceRoute ? undefined : "none" }}
              >
                {coachWorkspaceRoute ? (
                  videoTarget ? <CoachVideoPane analysisRef={videoTarget.analysisRef} initialTimeMs={videoTarget.timeMs} onClose={() => setVideoTarget(null)} /> : null
                ) : null}
                <div className="task3-coach-conversation">
                  <CoachPanel
                    capability={capability}
                    draftSession={draftSession}
                    layoutMode="full"
                    onEnsureSession={ensureCoachSession}
                    onOpenVideo={(analysisRef, timeMs = 0) => setVideoTarget({ analysisRef, timeMs })}
                    pathname={pathname}
                    sessionId={selectedCoachSessionId}
                    softStartRun={softStartRun}
                  />
                </div>
              </div>
              {!coachWorkspaceRoute && !settingsRoute ? <div className="task3-page-view">{children}</div> : null}
            </>
          )}
        </main>
        {settingsOverlayVisible ? (
          <main
            aria-hidden={!settingsRoute || undefined}
            className="task3-route-content"
            data-settings-motion={settingsRoute
              ? settingsPresence.state === "open" ? "open" : "opening"
              : "closing"}
            data-settings-page="true"
            id={settingsRoute ? "main-content" : undefined}
            tabIndex={-1}
          >
            {settingsOverlayChildren}
          </main>
        ) : null}
      </div>
      {sessionFeedback ? <Toast onClose={() => setSessionFeedback(null)}>{sessionFeedback}</Toast> : null}
    </div>
  );
}
