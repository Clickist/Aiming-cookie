"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

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
import { checkForDesktopUpdate, type DesktopUpdate } from "@/lib/updater";
import { CoachPanel } from "@/components/task6/CoachPanel";
import { CoachVideoPane } from "@/components/task7/CoachVideoPane";
import SessionRail, { type SessionRailSession } from "@/components/task7/SessionRail";
import { startWindowDragging, TauriWindowControls } from "@/components/task3/TauriWindowControls";
import { UpdatePrompt } from "@/components/task3/UpdatePrompt";
import { Toast, useAnimatedPresence } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachVideoTarget = { analysisRef: string; timeMs: number; seq: number };

// 冷启动的捕获恢复要等 PyInstaller 后端起来才有产品状态可读：指数退避
// 重试（1s 起步、封顶 30s、最多 10 次），不与启动路由的首次请求绑定。
const CAPTURE_RESTORE_MAX_ATTEMPTS = 10;
const CAPTURE_RESTORE_FIRST_DELAY_MS = 1_000;
const CAPTURE_RESTORE_MAX_DELAY_MS = 30_000;

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
  const historyRoute = pathname.startsWith("/history");
  const [capability, setCapability] = useState<CoachCapability>("loading");
  const [startupRouteResolved, setStartupRouteResolved] = useState(false);
  const [coachSessions, setCoachSessions] = useState<SessionRailSession[]>([]);
  const [selectedCoachSessionId, setSelectedCoachSessionId] = useState<number | null>(null);
  // 冷启动只尝试恢复一次「上次最后在看的会话」；会话列表未加载前不消耗这次机会。
  const hasRestoredLastSessionRef = useRef(false);
  const [draftSession, setDraftSession] = useState(false);
  const [videoTarget, setVideoTarget] = useState<CoachVideoTarget | null>(null);
  // 视频面板开关动效（0827 拍板 reveal/cover 模型）：关闭先置 closing 让对话
  // 面板立即滑回盖住视频面板（data-video-open 随 closing 提前翻），滑完再真正
  // 卸载。面板绝对定位不占布局，提前翻状态不会引起任何回流挤压。
  const [videoClosing, setVideoClosing] = useState(false);
  const videoCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // @time 点击序号：同一时间码连续点击也要构成新的跳转意图信号
  // （到达即暂停＋脉冲，复盘升级 P0.3/D1），不能靠 initialTimeMs 值变化。
  const videoSeqRef = useRef(0);
  const openVideoPane = (analysisRef: string, timeMs = 0) => {
    if (videoCloseTimerRef.current) {
      clearTimeout(videoCloseTimerRef.current);
      videoCloseTimerRef.current = null;
    }
    setVideoClosing(false);
    setVideoTarget({ analysisRef, seq: (videoSeqRef.current += 1), timeMs });
  };
  const closeVideoPane = () => {
    if (!videoTarget || videoClosing) return;
    setVideoClosing(true);
    videoCloseTimerRef.current = setTimeout(() => {
      videoCloseTimerRef.current = null;
      setVideoTarget(null);
      setVideoClosing(false);
    }, 340);
  };
  // ── 对话列宽度拖拽（0827）：视频面板开启时，对话列左缘的把手可左右拖动
  // 调节对话列宽（视频面板吃剩余空间）。宽度持久化到 localStorage，null =
  // 走 CSS 默认 clamp。拖拽期间经 data-split-dragging 关掉宽度过渡防拖影。
  const VIDEO_SPLIT_MIN = 380;
  const VIDEO_SPLIT_KEY = "aiming-cookie.video-split-width";
  const coachViewRef = useRef<HTMLDivElement | null>(null);
  const [conversationWidth, setConversationWidth] = useState<number | null>(() => {
    // AppShell 会走 SSR，window 仅在客户端存在；服务端一律回落默认宽。
    if (typeof window === "undefined") return null;
    const stored = Number(window.localStorage.getItem(VIDEO_SPLIT_KEY));
    return Number.isFinite(stored) && stored >= VIDEO_SPLIT_MIN ? stored : null;
  });
  const startSplitDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const view = coachViewRef.current;
    if (!view) return;
    const viewRect = view.getBoundingClientRect();
    const clampWidth = (px: number) =>
      Math.round(Math.min(viewRect.width - VIDEO_SPLIT_MIN, Math.max(VIDEO_SPLIT_MIN, px)));
    view.dataset.splitDragging = "true";
    const onMove = (move: PointerEvent) => {
      setConversationWidth(clampWidth(viewRect.right - move.clientX));
    };
    const onUp = (up: PointerEvent) => {
      const final = clampWidth(viewRect.right - up.clientX);
      setConversationWidth(final);
      window.localStorage.setItem(VIDEO_SPLIT_KEY, String(final));
      delete view.dataset.splitDragging;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  const [sessionFeedback, setSessionFeedback] = useState<{ text: string; seq: number } | null>(null);
  const sessionFeedbackSeqRef = useRef(0);
  // 与 CoachPanel.notify 同款：Toast 关闭是 200ms 后的延迟回调，用户
  // 关掉提示后立刻重试又失败时，迟到的旧 onClose 会清掉新提示；
  // seq 兼作重挂载 key 与 onClose 新鲜度校验。
  const notifySessionFeedback = useCallback((message: string) => {
    sessionFeedbackSeqRef.current += 1;
    const seq = sessionFeedbackSeqRef.current;
    setSessionFeedback({ text: message, seq });
  }, []);
  const [softStartRun, setSoftStartRun] = useState<CoachAgentRunV1 | null>(null);
  // 桌面端更新可用提示：启动静默检查命中后由右下角 UpdatePrompt 呈现。
  const [desktopUpdate, setDesktopUpdate] = useState<DesktopUpdate | null>(null);
  const settingsChildrenRef = useRef<ReactNode>(null);
  const settingsPresence = useAnimatedPresence(settingsRoute, 160);
  const historyPresence = useAnimatedPresence(historyRoute, 160);
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
        setStartupRouteResolved(true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setStartupRouteResolved(true);
      });
    return () => controller.abort();
  }, [coachWorkspaceRoute, router]);

  // 桌面端捕获总开关的自动恢复：此前它挂在冷启动的一次 getProductState 上，
  // 首连失败（后端尚未就绪）就静默放弃，重启后捕获会一直停留在关闭状态。
  // 现在独立退避重试直到后端就绪并完成门控判断；门控语义与启动路由一致
  // （未走完 onboarding 不恢复），Rust 侧启动恢复先行成功时这里是幂等重放。
  useEffect(() => {
    if (!coachWorkspaceRoute || !isDesktopRuntime()) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const restore = async (attempt: number): Promise<void> => {
      try {
        const state = await getProductState();
        if (cancelled) return;
        // 与启动路由同一门控：未完成 onboarding 明确无需恢复，立即停止。
        if (state.availability === "available" && state.onboarding_completed !== true) return;
        await setDesktopCaptureEnabled(true);
        return; // 成功恢复或明确无需恢复即停。
      } catch {
        if (cancelled) return;
        if (attempt + 1 >= CAPTURE_RESTORE_MAX_ATTEMPTS) return;
        timer = setTimeout(
          () => void restore(attempt + 1),
          Math.min(CAPTURE_RESTORE_FIRST_DELAY_MS * 2 ** attempt, CAPTURE_RESTORE_MAX_DELAY_MS),
        );
      }
    };
    void restore(0);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [coachWorkspaceRoute]);

  // 桌面端启动静默检查更新：延迟到首屏稳定之后再问，检查失败完全静默；
  // 端点与验签公钥配置在 tauri.conf.json 的 plugins.updater。
  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      void checkForDesktopUpdate()
        .then((update) => {
          if (!cancelled && update) setDesktopUpdate(update);
        })
        .catch(() => undefined);
    }, 4_000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

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

  // Coach 回合活跃上报的落点：自动开讲据此让路（见下方 handleAutoTeach）。
  const activeCoachRunRef = useRef(false);
  const handleCoachActiveRunChange = useCallback((active: boolean) => {
    activeCoachRunRef.current = active;
  }, []);

  // 分析完成自动开讲：AnalysisWorkspace 活体观察到 done 时派发事件；这里在
  // Provider 可用时为该分析创建一次 Coach run（每个 Analysis 只开讲一次），
  // 由 CoachPanel 的 softStartRun 承接展示。当前分析只由 Coach 的
  // analysis.create_from_run 触发且该回合会阻塞到分析完成、直接讲述结果，
  // 回合进行中再开讲等于同一分析问两遍——故活跃回合期间跳过（不标记，
  // 之后重试等无回合场景仍可开讲）。开讲并入当前选中会话，不再每次新建。
  useEffect(() => {
    const seen = readAutoTaughtAnalyses(window.localStorage);
    const handleAutoTeach = async (event: Event) => {
      const detail = (event as CustomEvent<{ analysis_ref?: unknown }>).detail;
      const analysisRef = detail?.analysis_ref;
      if (typeof analysisRef !== "string" || !/^analysis:[1-9][0-9]*$/.test(analysisRef)) return;
      if (seen.has(analysisRef)) return;
      if (activeCoachRunRef.current) return;
      seen.add(analysisRef);
      markAnalysisAutoTaught(window.localStorage, analysisRef);
      if (capability !== "ready") return;
      try {
        const run = await createCoachAgentRun(
          buildAnalysisAutoTeachContent(analysisRef),
          selectedCoachSessionId == null ? {} : { sessionId: selectedCoachSessionId },
        );
        setSoftStartRun(run);
        setSelectedCoachSessionId((current) => (current === run.session_id ? current : run.session_id));
        window.dispatchEvent(new CustomEvent("aiming-cookie:coach-session-updated"));
      } catch {
        // 分析完成不再弹 Toast；自动开讲结果由 Coach 面板的动作指示器呈现。
      }
    };
    window.addEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
    return () => window.removeEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
  }, [capability, selectedCoachSessionId]);

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

  // 并发去重：双 Enter 窗口期内会同时调用 ensureCoachSession，共享同一次
  // 创建请求，避免一键产生多个空会话；完成或失败后清掉，下次调用重新创建。
  const ensureSessionInFlightRef = useRef<Promise<number | null> | null>(null);
  const ensureCoachSession = useCallback((): Promise<number | null> => {
    if (ensureSessionInFlightRef.current) return ensureSessionInFlightRef.current;
    const promise = (async () => {
      try {
        const session = await createCoachSession();
        await reloadCoachSessions(session.id);
        setDraftSession(false);
        router.push(`/s?sessionId=${session.id}`);
        return session.id;
      } catch {
        return null;
      } finally {
        ensureSessionInFlightRef.current = null;
      }
    })();
    ensureSessionInFlightRef.current = promise;
    return promise;
  }, [reloadCoachSessions, router]);

  const handleArchiveCoachSession = async (session: SessionRailSession) => {
    try {
      await updateCoachSession(Number(session.id), { status: "archived" });
    } catch {
      notifySessionFeedback("未能归档会话，请重试。");
      return;
    }
    try {
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      notifySessionFeedback("操作已完成，但会话列表暂时未能刷新。");
    }
  };

  const handleDeleteCoachSession = async (session: SessionRailSession) => {
    try {
      await deleteCoachSession(Number(session.id));
    } catch {
      notifySessionFeedback("未能删除会话，请重试。");
      return;
    }
    try {
      await reloadCoachSessions(selectedCoachSessionId === Number(session.id) ? null : undefined);
    } catch {
      notifySessionFeedback("操作已完成，但会话列表暂时未能刷新。");
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
        <span className="task3-logo" aria-label="Aiming Cookie">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="task3-logo-mark" src="/logo-mark.png" alt="" />
          Aiming&nbsp;Cookie
        </span>
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
                data-video-open={Boolean(videoTarget) && !videoClosing || undefined}
                ref={coachViewRef}
                style={{
                  display: coachWorkspaceRoute ? undefined : "none",
                  "--task3-conv-w": conversationWidth != null ? `${conversationWidth}px` : undefined,
                } as CSSProperties}
              >
                {coachWorkspaceRoute ? (
                  videoTarget ? <CoachVideoPane analysisRef={videoTarget.analysisRef} initialTimeMs={videoTarget.timeMs} jumpSeq={videoTarget.seq} onClose={closeVideoPane} /> : null
                ) : null}
                <div className="task3-coach-conversation">
                  <CoachPanel
                    capability={capability}
                    draftSession={draftSession}
                    layoutMode="full"
                    onActiveRunChange={handleCoachActiveRunChange}
                    onEnsureSession={ensureCoachSession}
                    onOpenVideo={openVideoPane}
                    pathname={pathname}
                    sessionId={selectedCoachSessionId}
                    softStartRun={softStartRun}
                  />
                </div>
                {/* 对话列左缘拖拽把手（0827）：仅视频面板开启时存在，横向拖动
                    调节对话列宽（视频面板吃剩余空间），只调宽度。 */}
                {videoTarget && !videoClosing ? (
                  <div
                    aria-label="调节对话面板宽度"
                    aria-orientation="vertical"
                    className="task3-video-split-handle"
                    onPointerDown={startSplitDrag}
                    role="separator"
                  />
                ) : null}
              </div>
              {!coachWorkspaceRoute && !settingsRoute ? (
                <div
                  className="task3-page-view"
                  data-page-motion={historyPresence.state === "open" ? "open" : "opening"}
                >
                  {children}
                </div>
              ) : null}
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
      {desktopUpdate ? (
        <UpdatePrompt onDismiss={() => setDesktopUpdate(null)} update={desktopUpdate} />
      ) : null}
      {sessionFeedback ? (
        <Toast key={sessionFeedback.seq} onClose={() => setSessionFeedback((current) => (current && current.seq === sessionFeedback.seq ? null : current))}>
          {sessionFeedback.text}
        </Toast>
      ) : null}
    </div>
  );
}
