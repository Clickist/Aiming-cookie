"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type AnimationEvent as ReactAnimationEvent, type CSSProperties, type ReactNode } from "react";

import { getDefaultProviderStatus, getSession, listTasks } from "@/lib/api";
import { clampCoachWidth, COACH_DEFAULT_WIDTH } from "@/lib/contracts";
import type { ProviderProfileState } from "@/lib/types";
import { CoachSidebar } from "@/components/task6/CoachSidebar";
import { useAnimatedPresence } from "@/ui/primitives";

const COACH_OPEN_KEY = "aiming-cookie.ui.coach-open";
const COACH_FIRST_ANALYSIS_KEY = "aiming-cookie.ui.coach-first-analysis-opened";
const COACH_WIDTH_KEY = "aiming-cookie.ui.coach-width";
const SETTINGS_RETURN_KEY = "aiming-cookie.ui.settings-return";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type SettingsMotion = "idle" | "closing";

function useWideLayout(): boolean {
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1160px)");
    const update = () => setWide(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return wide;
}

function currentLocation(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function settingsReturnPath(): string {
  const stored = window.sessionStorage.getItem(SETTINGS_RETURN_KEY);
  if (!stored || !stored.startsWith("/") || stored.startsWith("//") || stored.startsWith("/settings")) {
    return "/history";
  }
  return stored;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const previousPathnameRef = useRef(pathname);
  const wide = useWideLayout();
  const [capability, setCapability] = useState<CoachCapability>("loading");
  const [activeTaskCount, setActiveTaskCount] = useState<number | null>(null);
  const [coachOpen, setCoachOpen] = useState(false);
  const [coachWidth, setCoachWidth] = useState(COACH_DEFAULT_WIDTH);
  const [preferenceLoaded, setPreferenceLoaded] = useState(false);
  const [settingsMotion, setSettingsMotion] = useState<SettingsMotion>("idle");
  const shellHidden = pathname === "/" || pathname.startsWith("/onboarding");
  const settingsActive = pathname.startsWith("/settings");
  const coachSupported = !shellHidden && !pathname.startsWith("/settings");

  useEffect(() => {
    if (!shellHidden && !settingsActive) {
      window.sessionStorage.setItem(SETTINGS_RETURN_KEY, currentLocation());
    }
  }, [pathname, settingsActive, shellHidden]);

  useEffect(() => {
    if (previousPathnameRef.current === pathname) return;
    previousPathnameRef.current = pathname;
    if (!settingsActive) setSettingsMotion("idle");
  }, [pathname, settingsActive]);

  useEffect(() => {
    const stored = Number(window.localStorage.getItem(COACH_WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) setCoachWidth(clampCoachWidth(stored));
  }, []);

  useEffect(() => {
    if (shellHidden) return;
    const controller = new AbortController();
    void Promise.allSettled([
      getDefaultProviderStatus({ signal: controller.signal }),
      listTasks({ signal: controller.signal }),
    ]).then(([providerResult, tasksResult]) => {
      if (providerResult.status === "fulfilled") {
        setCapability(providerResult.value.status);
      } else if (!controller.signal.aborted) {
        setCapability("unavailable");
      }
      if (tasksResult.status === "fulfilled" && tasksResult.value.availability === "available") {
        setActiveTaskCount(tasksResult.value.tasks.filter((task) =>
          task.state === "importing" || task.state === "queued" || task.state === "running" || task.state === "retrying"
        ).length);
      }
    });
    return () => controller.abort();
  }, [shellHidden]);

  useEffect(() => {
    if (!coachSupported || capability === "loading") return;
    const stored = window.localStorage.getItem(COACH_OPEN_KEY);
    if (capability === "ready" && wide && stored !== "closed") {
      setCoachOpen(true);
    } else {
      setCoachOpen(capability === "ready" && stored === "open");
    }
    setPreferenceLoaded(true);
  }, [capability, coachSupported, wide]);

  useEffect(() => {
    if (!coachSupported || capability !== "ready" || !pathname.startsWith("/analysis/")) return;
    if (window.localStorage.getItem(COACH_FIRST_ANALYSIS_KEY) === "true") return;
    const analysisId = Number(pathname.split("/")[2]);
    if (!Number.isSafeInteger(analysisId) || analysisId <= 0) return;
    const controller = new AbortController();
    void getSession(analysisId, { signal: controller.signal }).then((session) => {
      if (session.status !== "done") return;
      setCoachOpen(true);
      setPreferenceLoaded(true);
      window.localStorage.setItem(COACH_FIRST_ANALYSIS_KEY, "true");
    }).catch(() => undefined);
    return () => controller.abort();
  }, [capability, coachSupported, pathname]);

  const toggleCoach = () => {
    const next = !coachOpen;
    setCoachOpen(next);
    window.localStorage.setItem(COACH_OPEN_KEY, next ? "open" : "closed");
    setPreferenceLoaded(true);
  };

  const closeCoach = () => {
    setCoachOpen(false);
    window.localStorage.setItem(COACH_OPEN_KEY, "closed");
    setPreferenceLoaded(true);
  };

  const updateCoachWidth = (requestedWidth: number) => {
    const nextWidth = clampCoachWidth(requestedWidth);
    setCoachWidth(nextWidth);
    window.localStorage.setItem(COACH_WIDTH_KEY, String(nextWidth));
  };

  const rememberSettingsReturn = () => {
    window.sessionStorage.setItem(SETTINGS_RETURN_KEY, currentLocation());
  };

  const closeSettings = () => {
    if (settingsMotion === "closing") return;
    if (prefersReducedMotion()) {
      router.replace(settingsReturnPath());
      return;
    }
    setSettingsMotion("closing");
  };

  const finishSettingsMotion = (event: ReactAnimationEvent<HTMLElement>) => {
    if (event.currentTarget !== event.target) return;
    if (settingsMotion === "closing") router.replace(settingsReturnPath());
  };

  const navItems = useMemo(() => [
    { href: "/history", label: "历史" },
    { href: "/analyze", label: "＋ 新建分析" },
  ], []);

  const tasksActive = pathname.startsWith("/tasks");
  const coachActive = coachOpen && coachSupported;
  const showCoach = coachSupported && coachOpen && preferenceLoaded;
  const coachPresence = useAnimatedPresence(showCoach, 160);

  if (shellHidden) return <>{children}</>;

  return (
    <div className="task3-app">
      <a className="task3-skip-link" href="#main-content">跳到主要内容</a>
      <header className="task3-toolbar">
        <span className="task3-logo" aria-label="Aiming Cookie">Aiming&nbsp;Cookie</span>
        <nav aria-label="主要导航" className="task3-primary-nav">
          {navItems.map((item) => {
            const active = item.href === "/analyze"
              ? pathname === "/analyze" || pathname.startsWith("/analysis/")
              : pathname.startsWith(item.href);
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={["t-btn", active ? "active" : ""].filter(Boolean).join(" ")}
                href={item.href}
                key={item.href}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="task3-toolbar-spacer" />
        <nav aria-label="工具导航" className="task3-tool-nav">
          <Link
            aria-current={tasksActive ? "page" : undefined}
            className={["t-btn", tasksActive ? "active" : ""].filter(Boolean).join(" ")}
            href="/tasks"
          >
            <span className="task3-task-nav-label">
              {activeTaskCount ? <span aria-hidden="true" className="task3-task-nav-dot" /> : null}
              任务状态
              {activeTaskCount ? <span className="task3-toolbar-badge">{activeTaskCount}</span> : null}
            </span>
          </Link>
          {coachSupported ? (
            <span className="task3-toolbar-tooltip">
              <button
                aria-expanded={coachOpen}
                aria-label="Coach"
                className={["t-icon", coachActive ? "active" : ""].filter(Boolean).join(" ")}
                onClick={toggleCoach}
                type="button"
              >
                <span aria-hidden="true">◧</span>
              </button>
            </span>
          ) : null}
          {settingsActive ? (
            <button
              aria-label="关闭设置"
              className="t-icon active"
              onClick={closeSettings}
              title="关闭设置"
              type="button"
            >
              <span aria-hidden="true">⚙</span>
            </button>
          ) : (
            <Link
              aria-label="设置"
              className="t-icon"
              href="/settings"
              onClick={rememberSettingsReturn}
            >
              <span aria-hidden="true">⚙</span>
            </Link>
          )}
        </nav>
      </header>
      <div
        className="task3-workspace"
        data-coach-open={coachPresence.state === "open" || undefined}
        style={{ "--task3-coach-width": `${coachWidth}px` } as CSSProperties}
      >
        <main
          className="task3-route-content"
          data-settings-page={settingsActive || undefined}
          data-settings-motion={settingsActive && settingsMotion === "closing" ? settingsMotion : undefined}
          id="main-content"
          key={pathname}
          onAnimationEnd={finishSettingsMotion}
          tabIndex={-1}
        >
          {children}
        </main>
        {coachPresence.present ? (
          <CoachSidebar
            capability={capability}
            onClose={closeCoach}
            onWidthChange={updateCoachWidth}
            open={showCoach}
            pathname={pathname}
            state={coachPresence.state}
            width={coachWidth}
          />
        ) : null}
      </div>
    </div>
  );
}
