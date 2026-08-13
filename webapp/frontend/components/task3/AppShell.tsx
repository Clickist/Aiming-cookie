"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import {
  createCoachSession,
  deleteCoachSession,
  getDefaultProviderStatus,
  getProductState,
  listCoachSessions,
  updateCoachSession,
} from "@/lib/api";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import type { ProviderProfileState } from "@/lib/types";
import { CoachPanel } from "@/components/task6/CoachPanel";
import { CoachVideoPane } from "@/components/task7/CoachVideoPane";
import SessionRail, { type SessionRailSession } from "@/components/task7/SessionRail";
import { startWindowDragging, TauriWindowControls } from "@/components/task3/TauriWindowControls";
import { Toast } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachVideoTarget = { analysisRef: string; timeMs: number };

function parseSessionId(raw: string | null): number | null {
  if (!raw || !/^[1-9][0-9]*$/.test(raw)) return null;
  return Number(raw);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [capability, setCapability] = useState<CoachCapability>("loading");
  const [startupRouteResolved, setStartupRouteResolved] = useState(false);
  const [coachSessions, setCoachSessions] = useState<SessionRailSession[]>([]);
  const [selectedCoachSessionId, setSelectedCoachSessionId] = useState<number | null>(null);
  const [draftSession, setDraftSession] = useState(false);
  const [videoTarget, setVideoTarget] = useState<CoachVideoTarget | null>(null);
  const [sessionFeedback, setSessionFeedback] = useState<string | null>(null);
  const shellHidden = pathname.startsWith("/onboarding");
  const searchParams = useSearchParams();
  const coachWorkspaceRoute = pathname === "/" || pathname === "/s" || pathname === "/s/";
  const startupPending = coachWorkspaceRoute && !startupRouteResolved;
  const settingsRoute = pathname.startsWith("/settings");
  const showSessionRail = !shellHidden && !settingsRoute && !startupPending;
  const keepSessionRailMounted = !shellHidden && !startupPending;
  const routeSessionId = parseSessionId(searchParams.get("sessionId"));

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

  useEffect(() => {
    setSelectedCoachSessionId((current) => {
      if (draftSession) return null;
      if (routeSessionId !== null && coachSessions.some((session) => Number(session.id) === routeSessionId)) {
        return routeSessionId;
      }
      if (current !== null && coachSessions.some((session) => Number(session.id) === current)) return current;
      const primary = coachSessions.find((session) => session.kind === "primary");
      return primary ? Number(primary.id) : coachSessions[0] ? Number(coachSessions[0].id) : null;
    });
  }, [coachSessions, draftSession, routeSessionId]);

  useEffect(() => {
    setVideoTarget(null);
  }, [selectedCoachSessionId]);

  const reloadCoachSessions = useCallback(async (nextSelectedId?: number | null) => {
    const result = await listCoachSessions();
    const sessions = result.sessions as SessionRailSession[];
    setCoachSessions(sessions);
    setSelectedCoachSessionId((current) => {
      if (nextSelectedId !== undefined && nextSelectedId !== null && sessions.some((session) => Number(session.id) === nextSelectedId)) {
        return nextSelectedId;
      }
      if (current !== null && sessions.some((session) => Number(session.id) === current)) return current;
      const primary = sessions.find((session) => session.kind === "primary");
      return primary ? Number(primary.id) : sessions[0] ? Number(sessions[0].id) : null;
    });
  }, []);

  useEffect(() => {
    const handleSessionUpdated = () => {
      void reloadCoachSessions();
    };
    window.addEventListener("aiming-cookie:coach-session-updated", handleSessionUpdated);
    return () => window.removeEventListener("aiming-cookie:coach-session-updated", handleSessionUpdated);
  }, [reloadCoachSessions]);

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
          className="task3-route-content"
          data-settings-page={settingsRoute || undefined}
          id="main-content"
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
                  />
                </div>
              </div>
              {!coachWorkspaceRoute ? <div className="task3-page-view">{children}</div> : null}
            </>
          )}
        </main>
      </div>
      {sessionFeedback ? <Toast onClose={() => setSessionFeedback(null)}>{sessionFeedback}</Toast> : null}
    </div>
  );
}
