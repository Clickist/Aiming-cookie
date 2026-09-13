"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import {
  createCoachAgentRun,
  createCoachSession,
  createIntroSession,
  deleteCoachSession,
  getDefaultProviderStatus,
  getIntroSession,
  getProductState,
  listCoachSessions,
  updateCoachSession,
} from "@/lib/api";
import {
  ANALYSIS_AUTO_TEACH_EVENT,
  COACH_SESSION_UPDATED_EVENT,
  buildAnalysisAutoTeachContent,
  markAnalysisAutoTaught,
  readAutoTaughtAnalyses,
  readLastCoachSessionId,
  writeLastCoachSessionId,
} from "@/lib/contracts";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import { logFrontendError } from "@/lib/frontend-log";
import { triggerIntroSession } from "@/lib/intro-session";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { CoachAgentRunV1, CoachSessionOut, ProviderProfileState } from "@/lib/types";
import { checkForDesktopUpdate, type DesktopUpdate } from "@/lib/updater";
import { ErrorBoundary } from "@/components/task3/ErrorBoundary";
import { CoachPanel } from "@/components/task6/CoachPanel";
import { CoachVideoPane, invalidateAnalysisPresentationCache } from "@/components/task7/CoachVideoPane";
import SessionRail, { type SessionRailSession } from "@/components/task7/SessionRail";
import { startWindowDragging, startWindowDraggingOnBackground, TauriWindowControls } from "@/components/task3/TauriWindowControls";
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
  // onboarding 完成度由启动路由的 getProductState 解析；未完成/未知一律 false，
  // 首启「开场分析」触发据此门控（未走完 onboarding 不触发）。
  const [onboardingResolved, setOnboardingResolved] = useState(false);
  const [coachSessions, setCoachSessions] = useState<SessionRailSession[]>([]);
  const [selectedCoachSessionId, setSelectedCoachSessionId] = useState<number | null>(null);
  // 冷启动只尝试恢复一次「上次最后在看的会话」；会话列表未加载前不消耗这次机会。
  const hasRestoredLastSessionRef = useRef(false);
  const [draftSession, setDraftSession] = useState(false);
  // 首条发送交接窗（0912 审计）：新会话已建、列表刷新在途的窗口里 draft 条目
  // 已撤、真实条目还没进列表——侧栏条目凭空闪没。这里记住交接中的会话 id，
  // 渲染时给侧栏补一条占位，列表追平后自然让位。
  const [handoverSessionId, setHandoverSessionId] = useState<number | null>(null);
  const [videoTarget, setVideoTarget] = useState<CoachVideoTarget | null>(null);
  // 当前会话 assistant 讲解文本（CoachPanel 上报）：视频面板回看 chips
  // 跟随正文 @time，与正文引用同一份时间锚点。
  const [coachAssistantTexts, setCoachAssistantTexts] = useState<ReadonlyArray<string>>([]);
  const handleCoachMessagesChange = useCallback((texts: ReadonlyArray<string>) => {
    setCoachAssistantTexts((current) =>
      current.length === texts.length && current.every((text, index) => text === texts[index])
        ? current
        : texts);
  }, []);
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
  const [conversationWidth, setConversationWidth] = useState<number | null>(null);
  // 上次拖拽列宽的恢复放在水合之后：初始化器里同步读 localStorage 会让
  // 客户端首帧比 SSR HTML 多出 --task3-conv-w，触发 hydration mismatch
  //（0910 点点报）。晚一帧恢复，CSS 默认 clamp 兜底，肉眼不可见。
  useEffect(() => {
    const stored = Number(window.localStorage.getItem(VIDEO_SPLIT_KEY));
    if (Number.isFinite(stored) && stored >= VIDEO_SPLIT_MIN) setConversationWidth(stored);
  }, []);
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
      cleanup();
    };
    // pointercancel（触控笔抢指针等）：不落宽度，保持当前宽度，仅摘监听与拖拽态。
    const onCancel = () => {
      cleanup();
    };
    const cleanup = () => {
      delete view.dataset.splitDragging;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
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
        // onboarding 明确完成才放行首启「开场分析」触发；null/unknown 一律不触发。
        setOnboardingResolved(state.onboarding_completed === true);
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

  // 圆角窗口（0910 拍板，Win10 自绘）：桌面运行时给根元素挂 ac-window-rounded
  //（task3.css 据此画圆角+自绘阴影）；最大化/还原（含 Win+方向 snap）时切
  // ac-window-maximized，inset 归零铺满回直角。浏览器运行时不挂类，零影响。
  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    const root = document.documentElement;
    root.classList.add("ac-window-rounded");
    const win = getCurrentWindow();
    let disposed = false;
    const syncMaximized = async () => {
      try {
        const maximized = await win.isMaximized();
        if (!disposed) root.classList.toggle("ac-window-maximized", maximized);
      } catch {
        // is-maximized 不可用时保持圆角态，不影响其余功能。
      }
    };
    void syncMaximized();
    const unlisten = win.onResized(() => {
      void syncMaximized();
    });
    return () => {
      disposed = true;
      void unlisten.then((off) => off());
      root.classList.remove("ac-window-rounded", "ac-window-maximized");
    };
  }, []);

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
        .catch((error) => {
          // 启动静默检查失败仍不打扰用户，只留前端错误通道痕迹。
          logFrontendError("update-check", error instanceof Error ? error.message : String(error));
        });
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
      pendingBindSessionIdRef.current = null;
      setHandoverSessionId(null);
      if (selectedCoachSessionId !== null) setSelectedCoachSessionId(null);
      return;
    }
    // 交接窗钉扎：新会话绑定完成（选择到位或列表已含）或用户改道别处时
    // 解除；窗口内保持空选择（顶栏自然渲染「新对话」占位），不跑任何兜底。
    const pendingBind = pendingBindSessionIdRef.current;
    if (pendingBind !== null) {
      if (
        selectedCoachSessionId === pendingBind
        || (routeSessionId !== null && routeSessionId !== pendingBind)
        || coachSessions.some((session) => Number(session.id) === pendingBind)
      ) {
        pendingBindSessionIdRef.current = null;
        setHandoverSessionId(null);
      } else {
        return;
      }
    }
    if (routeSessionId !== null && coachSessions.some((session) => Number(session.id) === routeSessionId)) {
      setSelectedCoachSessionId(routeSessionId);
      return;
    }
    // 路由指向的会话还没出现在已加载列表里：保持当前选择（null＝顶栏自然
    // 渲染「新对话」占位），等 reloadCoachSessions 把列表追平后再绑定。
    // 绝不落到下面的 lastViewed/primary 兜底——回落会把顶栏/消息区闪成旧
    // 会话再自愈，删除当前会话后跳陈旧会话（§12.5 补遗）同此根。
    if (routeSessionId !== null) return;
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

  // 路线 B（0910）：模型命名在 run 终态后 1~8s 才落库——列表里见到
  // title_pending 就安排一次延迟补刷（去重，卸载清理），其余场景不空转。
  const titlePendingTimerRef = useRef<number | null>(null);
  useEffect(() => () => {
    if (titlePendingTimerRef.current !== null) window.clearTimeout(titlePendingTimerRef.current);
  }, []);
  const reloadCoachSessions = useCallback(async (nextSelectedId?: number | null) => {
    const result = await listCoachSessions();
    const sessions = result.sessions as SessionRailSession[];
    setCoachSessions(sessions);
    if (sessions.some((session) => (session as CoachSessionOut).title_pending) && titlePendingTimerRef.current === null) {
      titlePendingTimerRef.current = window.setTimeout(() => {
        titlePendingTimerRef.current = null;
        void reloadCoachSessions();
      }, 5000);
    }
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
    window.addEventListener(COACH_SESSION_UPDATED_EVENT, handleSessionUpdated);
    return () => window.removeEventListener(COACH_SESSION_UPDATED_EVENT, handleSessionUpdated);
  }, [reloadCoachSessions]);

  // 首启「开场分析」触发（PRD §6.1.1 / frontend-uiux-design §6.1.1）：
  // onboarding 已完成时，flag 未置（created=false）走幂等创建；flag 已置但
  // has_messages=false（当时 Provider 凭据不可用、kickoff 被闸门拦下）也补发
  // 一次幂等 POST，让 Provider 恢复后开场分析自动补跑（自愈，见
  // lib/intro-session.ts）。标题由 sidecar 定，前端只触发+呈现。in-flight
  // promise 存 ref，保证同一挂载只发一次（StrictMode 双跑复用同一 promise，
  // 不再补发），失败静默降级只进前端错误通道。不设跳过键——用户开新对话/
  // 切走即视为跳过，之后 sidecar flag 已置不再自动创建。
  const introTriggerRef = useRef<ReturnType<typeof triggerIntroSession> | null>(null);
  useEffect(() => {
    if (!coachWorkspaceRoute || !onboardingResolved || !startupRouteResolved) return undefined;
    if (introTriggerRef.current === null) {
      introTriggerRef.current = triggerIntroSession({
        getStatus: (signal) => getIntroSession({ signal }),
        create: (signal) => createIntroSession({ signal }),
        onError: (error) =>
          logFrontendError("intro-session", error instanceof Error ? error.message : String(error)),
      });
    }
    let cancelled = false;
    void introTriggerRef.current.then((result) => {
      if (cancelled || !result?.created || result.sessionId === null) return;
      const sessionId = result.sessionId;
      if (!Number.isSafeInteger(sessionId) || sessionId <= 0) return;
      // 复用既有打开会话路径（与 ensureCoachSession 同一条链）：交接窗钉扎
      // 防列表追平前选择回落闪旧会话，整表刷新 + 路由切换，标题由 sidecar 定。
      pendingBindSessionIdRef.current = sessionId;
      setHandoverSessionId(sessionId);
      void reloadCoachSessions(sessionId).catch(() => {});
      setDraftSession(false);
      router.push(`/s?sessionId=${sessionId}`);
    });
    return () => {
      cancelled = true;
    };
  }, [coachWorkspaceRoute, onboardingResolved, reloadCoachSessions, router, startupRouteResolved]);

  // Coach 回合活跃上报的落点：自动开讲据此让路（见下方 handleAutoTeach）。
  const activeCoachRunRef = useRef(false);
  const handleCoachActiveRunChange = useCallback((active: boolean) => {
    activeCoachRunRef.current = active;
  }, []);

  // 分析完成自动开讲：AnalysisWorkspace 活体观察与 CoachPanel 列表轮询
  // 观察到 done 时派发同一事件；这里在
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
      if (typeof analysisRef !== "string") return;
      const analysisMatch = /^analysis:([1-9][0-9]*)$/.exec(analysisRef);
      if (!analysisMatch) return;
      // 同一分析重新分析后缓存结果是旧态：开讲入口单点失效呈现缓存，
      // 视频面板下次打开即拉新（去重判断之前调用，覆盖已开讲过的重分析）。
      invalidateAnalysisPresentationCache(Number(analysisMatch[1]));
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
        window.dispatchEvent(new CustomEvent(COACH_SESSION_UPDATED_EVENT));
      } catch {
        // 分析完成不再弹 Toast；自动开讲结果由 Coach 面板的动作指示器呈现。
      }
    };
    window.addEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
    return () => window.removeEventListener(ANALYSIS_AUTO_TEACH_EVENT, handleAutoTeach);
  }, [capability, selectedCoachSessionId]);

  const handleNewCoachSession = () => {
    setDraftSession(true);
    setSelectedCoachSessionId(null);
    router.push("/");
  };

  // 并发去重：双 Enter 窗口期内会同时调用 ensureCoachSession，共享同一次
  // 创建请求，避免一键产生多个空会话；完成或失败后清掉，下次调用重新创建。
  const ensureSessionInFlightRef = useRef<Promise<number | null> | null>(null);
  // 首条发送交接窗钉扎（0911 审计 §12.3）：新会话已建、路由提交与列表刷新
  // 在途的窗口里选择为空——lastViewed/primary 兜底一旦在这个窗口触发，就会
  // 把标题/消息区闪成旧会话再自愈（发送窗内甚至把消息写进旧会话文件）。
  const pendingBindSessionIdRef = useRef<number | null>(null);
  const ensureCoachSession = useCallback((): Promise<number | null> => {
    if (ensureSessionInFlightRef.current) return ensureSessionInFlightRef.current;
    const promise = (async () => {
      try {
        const session = await createCoachSession();
        pendingBindSessionIdRef.current = session.id;
        setHandoverSessionId(session.id);
        // 会话列表整表刷新不阻塞首条发送链（0910 点点报"等了半天没反应"）：
        // 大会话量下这次 GET 可能数秒，await 会把 run 的创建一直压在后面。
        // rail 稍后自行追平；这里只负责立刻切路由与消息区。
        void reloadCoachSessions(session.id).catch(() => {});
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
    const sessionId = Number(session.id);
    // 乐观摘选（0912 晚）：归档的就是当前会话时立刻回到空选择（空首页），
    // 不等慢速列表刷新——否则被归档会话的整页视图（含错误卡）僵尸挂屏可达
    // 十几秒（listCoachSessions 在大会话量下很慢）。
    if (selectedCoachSessionId === sessionId) setSelectedCoachSessionId(null);
    try {
      await updateCoachSession(sessionId, { status: "archived" });
    } catch {
      notifySessionFeedback("未能归档会话，请重试。");
      return;
    }
    try {
      await reloadCoachSessions(selectedCoachSessionId === sessionId ? null : undefined);
    } catch {
      notifySessionFeedback("操作已完成，但会话列表暂时未能刷新。");
    }
  };

  const handleDeleteCoachSession = async (session: SessionRailSession) => {
    const sessionId = Number(session.id);
    // 乐观移除：确认后立刻从侧栏消失，不等服务端往返（确认后 1-3s 才消失
    // 像没点上，0911 审计 §四.8）；失败时 reload 从服务端取回真实列表。
    setCoachSessions((current) => current.filter((item) => Number(item.id) !== sessionId));
    // 乐观摘选（0912 晚）：删除的就是当前会话时立刻回空选择——选中态若等
    // reloadCoachSessions（可达十几秒）才清，被删会话的整页视图会一直挂着。
    if (selectedCoachSessionId === sessionId) setSelectedCoachSessionId(null);
    try {
      await deleteCoachSession(sessionId);
    } catch {
      notifySessionFeedback("未能删除会话，请重试。");
      void reloadCoachSessions().catch(() => {});
      return;
    }
    // 删除的正是路由指向的会话：清掉 URL 里的 sessionId，否则死 id 会在
    // F5 后残留（选择 effect 见 routeSessionId !== null 直接 return，永不落兜底）。
    if (routeSessionId === sessionId) {
      const params = new URLSearchParams(searchParams.toString());
      params.delete("sessionId");
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname);
    }
    notifySessionFeedback("会话已删除。");
    try {
      await reloadCoachSessions(selectedCoachSessionId === sessionId ? null : undefined);
    } catch {
      notifySessionFeedback("操作已完成，但会话列表暂时未能刷新。");
    }
  };

  // v6：空对话首页无顶栏（点点拍板），窗口三键独立常浮。
  const [coachHomeActive, setCoachHomeActive] = useState(false);

  if (shellHidden) return <ErrorBoundary>{children}</ErrorBoundary>;

  return (
    <div className="task3-app">
      <a className="task3-skip-link" href="#main-content">跳到主要内容</a>
      {/* 横跨顶栏已全局拆除（0910 拍板）：窗口三键改为全局常浮浮层，
          覆盖所有路由（Coach/历史/设置）；onboarding 自带一份，不经此处。 */}
      <div className="task3-wincontrols-global">
        <TauriWindowControls />
      </div>
      <div
        className="task3-workspace"
        data-coach-workspace={coachWorkspaceRoute || undefined}
        data-page-workspace={!coachWorkspaceRoute && !settingsRoute || undefined}
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
                    sessions={
                      draftSession
                        ? [{ id: "draft", title: "新对话", kind: "conversation" }, ...coachSessions]
                        : handoverSessionId !== null && !coachSessions.some((session) => Number(session.id) === handoverSessionId)
                          ? [{ id: handoverSessionId, title: "新对话", kind: "conversation" }, ...coachSessions]
                          : coachSessions
                    }
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
                {/* 保持挂载（点点拍板）：切到 /history、/settings 不卸载
                    CoachPanel/顶栏/stage，隐藏完全依赖外层 div 的 display:none
                    ——回来不用重新拉消息，SSE 与流式思考段不丢。 */}
                <ErrorBoundary>
                    {/* 共用顶栏（v6）：状态点 + 模型命名的会话标题；固定不动，
                        视频/对话面板的开合只发生在下方交换区。空对话首页
                        不渲染（窗口三键由全局浮层承担）。 */}
                    {coachHomeActive ? (
                      /* 首页无顶栏（v6 拍板）保留，但顶部槽位放一条透明拖拽
                         带：首页右侧顶栏也能拖窗口，不再只能拖 logo 区
                         （0911 点点）。左键空白才拖（OnBackground 助手）。 */
                      <div
                        aria-hidden="true"
                        className="task3-home-drag-band"
                        onMouseDown={startWindowDraggingOnBackground}
                      />
                    ) : (
                      <div
                        className="task3-coach-topbar"
                        onMouseDown={(event) => {
                          if (event.button === 0) void startWindowDragging();
                        }}
                      >
                        <span
                          className="task3-coach-status-dot"
                          data-state={capability}
                          title={capability === "ready" ? "Coach 已就绪" : capability === "loading" ? "正在读取 Coach 状态" : capability === "unavailable" ? "Coach 不可用" : "Coach 待配置"}
                        />
                        <span className="task3-coach-topbar-title">
                          {draftSession
                            ? "新对话"
                            : (coachSessions.find((session) => Number(session.id) === selectedCoachSessionId)?.title ?? "新对话")}
                        </span>
                        {/* 讨论条 portal 挂载点（v6 四轮）：CoachPanel 经 portal
                            把"本次讨论"条渲染到这里（标题之后、三键之前）。 */}
                        <div className="task3-coach-topbar-slot" id="task3-coach-topbar-slot" />
                      </div>
                    )}
                    <div className="task3-coach-stage">
                      {videoTarget ? <CoachVideoPane analysisRef={videoTarget.analysisRef} coachMessages={coachAssistantTexts} initialTimeMs={videoTarget.timeMs} jumpSeq={videoTarget.seq} onClose={closeVideoPane} /> : null}
                      <div className="task3-coach-conversation">
                  <CoachPanel
                    capability={capability}
                    draftSession={draftSession}
                    handoverSessionId={handoverSessionId}
                    onActiveRunChange={handleCoachActiveRunChange}
                    onCoachMessagesChange={handleCoachMessagesChange}
                    onEnsureSession={ensureCoachSession}
                    onHomeShellChange={setCoachHomeActive}
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
                </ErrorBoundary>
              </div>
              {!coachWorkspaceRoute && !settingsRoute ? (
                <div
                  className="task3-page-view"
                  data-page-motion={historyPresence.state === "open" ? "open" : "opening"}
                >
                  <ErrorBoundary>{children}</ErrorBoundary>
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
            <ErrorBoundary>{settingsOverlayChildren}</ErrorBoundary>
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
