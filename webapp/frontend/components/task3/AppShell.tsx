"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import {
  attachCoachContext,
  createCoachSession,
  deleteCoachSession,
  getDefaultProviderStatus,
  getProductState,
  listCoachSessions,
  updateCoachSession,
} from "@/lib/api";
import type { ProviderProfileState } from "@/lib/types";
import { CoachPanel } from "@/components/task6/CoachPanel";
import { CoachVideoPane } from "@/components/task7/CoachVideoPane";
import { GuidanceHost, type GuidanceEventDetail } from "@/components/task7/GuidanceHost";
import SessionRail, { type SessionRailSession } from "@/components/task7/SessionRail";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachVideoTarget = { analysisRef: string; timeMs: number };

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [capability, setCapability] = useState<CoachCapability>("loading");
  const [startupRouteResolved, setStartupRouteResolved] = useState(false);
  const [coachSessions, setCoachSessions] = useState<SessionRailSession[]>([]);
  const [selectedCoachSessionId, setSelectedCoachSessionId] = useState<number | null>(null);
  const [draftSession, setDraftSession] = useState(false);
  const [videoTarget, setVideoTarget] = useState<CoachVideoTarget | null>(null);
  const [guidance, setGuidance] = useState<GuidanceEventDetail | null>(null);
  const shellHidden = pathname.startsWith("/onboarding");
  const coachWorkspaceRoute = pathname === "/" || pathname.startsWith("/s/");
  const settingsRoute = pathname.startsWith("/settings");
  const showSessionRail = !shellHidden && !settingsRoute;
  const keepSessionRailMounted = !shellHidden;
  const requestedSessionId = /^\/s\/(\d+)$/.exec(pathname)?.[1];
  const routeSessionId = requestedSessionId ? Number(requestedSessionId) : null;

  useEffect(() => {
    if (!coachWorkspaceRoute) return undefined;
    const controller = new AbortController();
    void getProductState({ signal: controller.signal })
      .then((state) => {
        if (controller.signal.aborted) return;
        if (state.availability === "available" && state.onboarding_completed !== true) {
          router.replace("/onboarding");
          return;
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

  useEffect(() => {
    if (shellHidden) return undefined;
    const receiveGuidance = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (!detail || typeof detail !== "object") return;
      const candidate = detail as Partial<GuidanceEventDetail>;
      if (typeof candidate.run_ref !== "string" || !candidate.intent || typeof candidate.intent !== "object") return;
      if ((candidate.intent as { schema_version?: unknown }).schema_version !== "guidance_intent.v1") return;
      setGuidance({ run_ref: candidate.run_ref, intent: candidate.intent as GuidanceEventDetail["intent"] });
    };
    window.addEventListener("aiming-cookie:coach-guidance", receiveGuidance);
    return () => window.removeEventListener("aiming-cookie:coach-guidance", receiveGuidance);
  }, [shellHidden]);

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
      router.push(`/s/${session.id}`);
      return session.id;
    } catch {
      return null;
    }
  }, [reloadCoachSessions, router]);

  const handleArchiveCoachSession = async (session: SessionRailSession) => {
    try {
      await updateCoachSession(Number(session.id), { status: "archived" });
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      // Keep the current selection when an archive request fails.
    }
  };

  const handleDeleteCoachSession = async (session: SessionRailSession) => {
    try {
      await deleteCoachSession(Number(session.id));
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      // Keep the current selection when a delete request fails.
    }
  };

  if (shellHidden) return <>{children}</>;
  if (coachWorkspaceRoute && !startupRouteResolved) return null;

  return (
    <div className="task3-app">
      <a className="task3-skip-link" href="#main-content">跳到主要内容</a>
      <header className="task3-toolbar">
        <span className="task3-logo" aria-label="Aiming Cookie">Aiming&nbsp;Cookie</span>
        <div className="task3-toolbar-spacer" />
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
            currentSessionId={selectedCoachSessionId}
            onArchiveSession={(session) => void handleArchiveCoachSession(session)}
            onHistory={() => router.push("/history")}
            onNewSession={handleNewCoachSession}
            onSelectSession={(session) => {
              setDraftSession(false);
              setSelectedCoachSessionId(Number(session.id));
              router.push(`/s/${session.id}`);
            }}
            onSettings={() => router.push("/settings")}
            onSoftDeleteSession={(session) => void handleDeleteCoachSession(session)}
            providerStatus={capability === "ready" ? "ready" : capability === "loading" ? "loading" : capability === "unavailable" ? "unavailable" : "waiting"}
            sessions={coachSessions}
          />
        ) : null}
        <main
          className="task3-route-content"
          data-settings-page={settingsRoute || undefined}
          id="main-content"
          tabIndex={-1}
        >
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
                currentAnalysisRef={null}
                draftSession={draftSession}
                layoutMode="full"
                onEnsureSession={ensureCoachSession}
                onOpenVideo={(analysisRef, timeMs = 0) => setVideoTarget({ analysisRef, timeMs })}
                onRequestContext={async (analysisRef) => {
                  await attachCoachContext(
                    { kind: "analysis", analysis_ref: analysisRef },
                    selectedCoachSessionId === null ? {} : { sessionId: selectedCoachSessionId },
                  );
                }}
                pathname={pathname}
                sessionId={selectedCoachSessionId}
              />
            </div>
          </div>
          {!coachWorkspaceRoute ? <div className="task3-page-view">{children}</div> : null}
        </main>
      </div>
      <GuidanceHost
        intent={guidance?.intent ?? null}
        onIntent={(next) => setGuidance((current) => next && current ? { ...current, intent: next } : null)}
        runRef={guidance?.run_ref ?? null}
      />
    </div>
  );
}
