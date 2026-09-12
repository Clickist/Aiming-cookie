"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getVersion } from "@tauri-apps/api/app";

import {
  deleteCalibrationProfile,
  getCalibrationProfile,
  getCaptureStatus,
  getExternalTelemetry,
  getProviderCatalog,
  getStorage,
  listIncompleteCaptures,
  listKovaakRuns,
  listProviderProfiles,
  removeIncompleteCapture,
  removeRunEvidence,
  revealStorageItem,
  saveCalibrationProfile,
} from "@/lib/api";
import { presentStorageCategories } from "@/lib/contracts";
import { describeCaptureRunEvent, summarizeCaptureRunStatus } from "@/lib/capture-events";
import { exportDesktopCaptureDiagnostics, isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import { checkForDesktopUpdate, type DesktopUpdate } from "@/lib/updater";
import { KovaaKConnectionPanel } from "@/components/kovaak/KovaaKConnectionPanel";
import { KovaaKDirectoriesPanel } from "@/components/kovaak/KovaaKDirectoriesPanel";
import { ProviderSettingsSection } from "@/components/task6/ProviderSettingsSection";
import type {
  CalibrationProfileV1,
  CaptureStatusV1,
  ExternalTelemetryConfigV1,
  IncompleteCaptureItemV1,
  KovaaKRunListItem,
  ProviderCatalogV1,
  ProviderProfile,
  StorageResponse,
} from "@/lib/types";
import {
  Button,
  Dialog,
  ErrorState,
  FieldControl,
  IconButton,
  Loading,
  Notice,
  Panel,
  Status,
  Toast,
} from "@/ui/primitives";
import { IconChevronLeft } from "@/ui/icons";
import { useTheme } from "@/ui/theme";
import { startWindowDraggingOnBackground } from "@/components/task3/TauriWindowControls";

type ConfirmAction = {
  title: string;
  impact: string;
  run: () => Promise<void>;
} | null;

const STORAGE_COLORS = [
  "var(--on-surface)",
  "var(--outline)",
  "var(--on-surface-variant)",
  "var(--outline-variant)",
];

// 「最近采集事件」最多展示的局数（新→旧）。
const RECENT_CAPTURE_EVENTS_LIMIT = 8;

type AppUpdateCheckState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "latest" }
  | { phase: "available"; update: DesktopUpdate }
  | { phase: "error" };

// 设置页的「应用更新」：与启动静默检查共用 lib/updater 的同一端点与验签；
// 安装期间锁住按钮，成功时进程直接重启（不会回到 idle）。
function AppUpdatePanel() {
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [checkState, setCheckState] = useState<AppUpdateCheckState>({ phase: "idle" });
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    let cancelled = false;
    void getVersion()
      .then((version) => {
        if (!cancelled) setAppVersion(version);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const checkNow = useCallback(async () => {
    setCheckState({ phase: "checking" });
    try {
      const update = await checkForDesktopUpdate();
      setCheckState(update ? { phase: "available", update } : { phase: "latest" });
    } catch {
      setCheckState({ phase: "error" });
    }
  }, []);

  const installNow = useCallback(async () => {
    if (checkState.phase !== "available") return;
    setInstalling(true);
    try {
      await checkState.update.install();
      // 安装成功时进程会重启；走到这里说明重启未发生，恢复为可重试。
      setInstalling(false);
    } catch {
      setInstalling(false);
    }
  }, [checkState]);

  if (!isDesktopRuntime()) {
    return <p className="task6-muted">浏览器预览不提供应用更新检查。</p>;
  }
  // 线框形态（0912 完美复刻）：第一行 = 「应用更新」+ 状态 chip；
  // 第二行 = 当前版本（居左）+ 行尾「检查更新」。chip 状态词与面板状态
  // 一一对应：未检查/检查中/已是最新/有新版本/检查失败。
  const chip =
    checkState.phase === "latest" ? { tone: "ok", label: "已是最新" }
    : checkState.phase === "available" ? { tone: "new", label: "有新版本" }
    : checkState.phase === "checking" ? { tone: "idle", label: "检查中…" }
    : checkState.phase === "error" ? { tone: "idle", label: "检查失败" }
    : { tone: "idle", label: "未检查" };
  return (
    <div className="task6-app-update">
      <div className="task6-app-update-head">
        <h3 className="task6-profile-group-title">应用更新</h3>
        <span className="task6-update-chip" data-tone={chip.tone}>{chip.label}</span>
      </div>
      <div className="task6-app-update-row">
        <p className="task6-muted">
          当前版本{appVersion ? ` v${appVersion}` : ""}
          {checkState.phase === "checking" ? " · 正在检查更新…" : null}
          {checkState.phase === "error" ? " · 检查失败，请稍后再试" : null}
          {installing ? " · 正在下载并安装，完成后应用会自动重启" : null}
        </p>
        <div className="task6-inline-actions">
          <Button
            disabled={checkState.phase === "checking" || installing}
            onClick={() => void checkNow()}
            size="compact"
            variant="secondary"
          >
            检查更新
          </Button>
          {checkState.phase === "available" && !installing ? (
            <Button onClick={() => void installNow()} size="compact">
              更新到 {checkState.update.version}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

/** 「8月27日」短日期；解析失败返回 null（不渲染，不硬造）。 */
function formatDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(date);
}

function captureLabel(value: boolean | null | undefined, yes: string, no: string): string {
  if (value == null) return "未知";
  return value ? yes : no;
}

function incompleteReasonLabel(value: IncompleteCaptureItemV1["reason"]): string {
  return value === "interrupted_finalization" ? "整理过程被中断" : "未归类的采集产物";
}

// 分区切换化（点点拍板线框）：左栏切换项，点击只显示对应屏。
// 0912 点点拍板：高级屏取消——诊断包挪回自动采集，外部遥测导入界面下线。
const NAV_ITEMS = [
  { id: "general", label: "通用" },
  { id: "llm-provider", label: "LLM Provider" },
  { id: "capture", label: "自动采集" },
  { id: "kovaak", label: "KovaaK" },
  { id: "storage", label: "数据与存储" },
] as const;

type SettingsSectionId = (typeof NAV_ITEMS)[number]["id"];

// 旧分区锚点 id → 新屏的别名：历史页等处的 /settings#kovaak-directories
// 深链仍要落到对应屏（旧分区现为新屏内的小节）。
const HASH_SECTION_ALIASES: Record<string, SettingsSectionId> = {
  "app-update": "general",
  advanced: "capture",
  capture: "capture",
  general: "general",
  "external-telemetry": "capture",
  kovaak: "kovaak",
  "kovaak-directories": "kovaak",
  "llm-provider": "llm-provider",
  profile: "general",
  storage: "storage",
  theme: "general",
};

// Consecutive unavailable polls (1s each) before the settings UI leaves the last
// known good capture status; single blips (~1/1000 polls) must not flash red.
const CAPTURE_UNAVAILABLE_POLL_LIMIT = 3;

// The first settings load must not block on a slow native control channel:
// after this long the snapshot keeps capture null and the 1 s poller fills it in.
const CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS = 3_000;

type SettingsSnapshot = {
  profiles: ProviderProfile[];
  catalog: ProviderCatalogV1 | null;
  calibration: CalibrationProfileV1;
  capture: CaptureStatusV1 | null;
  storage: StorageResponse | null;
  incomplete: IncompleteCaptureItemV1[];
  runs: KovaaKRunListItem[];
};

let settingsSnapshot: SettingsSnapshot | null = null;

function SettingsExit({ onExit }: { onExit: () => void }) {
  return <IconButton className="task6-settings-back" label="退出设置" onClick={onExit} size="compact" title="返回 Coach"><IconChevronLeft /></IconButton>;
}

export function SettingsWorkspace() {
  const router = useRouter();
  const { preference, setPreference } = useTheme();
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [catalog, setCatalog] = useState<ProviderCatalogV1 | null>(null);
  const [calibration, setCalibration] = useState<CalibrationProfileV1 | null>(null);
  const [capture, setCapture] = useState<CaptureStatusV1 | null>(null);
  const [storage, setStorage] = useState<StorageResponse | null>(null);
  const [incomplete, setIncomplete] = useState<IncompleteCaptureItemV1[]>([]);
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [cmPer360, setCmPer360] = useState("");
  const [fov, setFov] = useState("");
  const [activeNav, setActiveNav] = useState<SettingsSectionId>(NAV_ITEMS[0].id);
  const [captureConsent, setCaptureConsent] = useState(false);
  // 采集源状态点（点点 0912 拍板）：外部遥测源不健康时状态点一并转橙。
  const [externalTelemetry, setExternalTelemetry] = useState<ExternalTelemetryConfigV1 | null>(null);
  const [diagnosticExporting, setDiagnosticExporting] = useState(false);
  const contentRef = useRef<HTMLDivElement | null>(null);

  const desktop = isDesktopRuntime();

  const exportCaptureDiagnostics = async () => {
    setDiagnosticExporting(true);
    try {
      const path = await exportDesktopCaptureDiagnostics();
      if (path) setFeedback(`运行日志已导出：${path}`);
    } catch {
      setFeedback("运行日志导出失败，请重试。");
    } finally {
      setDiagnosticExporting(false);
    }
  };

  // 「打开文件位置」：只发条目 id/kind，本地路径由后端解析（path-free 合同）。
  const reveal = async (request: Parameters<typeof revealStorageItem>[0]) => {
    try {
      await revealStorageItem(request);
    } catch {
      setFeedback("无法打开文件位置：文件可能已被移动、删除或文件管理器不可用。");
    }
  };

  // 分区切换：左栏点按只显示对应屏；右屏滚动位置随之归零，
  // 不把上一屏的滚动深度带给下一屏。
  const selectSection = (id: SettingsSectionId) => {
    setActiveNav(id);
    if (contentRef.current) contentRef.current.scrollTop = 0;
  };

  const applySnapshot = useCallback((snapshot: SettingsSnapshot) => {
    setProfiles(snapshot.profiles);
    setCatalog(snapshot.catalog);
    setCalibration(snapshot.calibration);
    setCmPer360(snapshot.calibration.values.cm_per_360?.toString() ?? "");
    setFov(snapshot.calibration.values.fov?.toString() ?? "");
    setCapture(snapshot.capture);
    setStorage(snapshot.storage);
    setIncomplete(snapshot.incomplete);
    setRuns(snapshot.runs);
  }, []);

  const refreshRemote = useCallback(async () => {
    // 存储与未完成采集是重扫描端点：与其余请求一次性并行发出，到货后各自
    // 分区独立上屏，不再阻塞 Provider / Profile 首屏（数据先到先显示）。
    const heavyPromise = desktop
      ? Promise.all([getStorage(), listIncompleteCaptures()])
        .then(([nextStorage, nextIncomplete]) => {
          setStorage(nextStorage);
          setIncomplete(nextIncomplete.items);
          if (settingsSnapshot) {
            settingsSnapshot = { ...settingsSnapshot, storage: nextStorage, incomplete: nextIncomplete.items };
          }
        })
        .catch(() => {
          // 重数据拉取失败时保留上一份已知内容，不阻塞其余分区展示。
        })
      : null;
    const captureTimeout = new Promise<null>((resolve) => {
      window.setTimeout(() => resolve(null), CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS);
    });
    const [profileResult, catalogResult, calibrationResult, captureResult, runResult] = await Promise.all([
      listProviderProfiles(),
      getProviderCatalog().catch(() => null),
      getCalibrationProfile(),
      desktop ? Promise.race([getCaptureStatus(), captureTimeout]) : Promise.resolve(null),
      desktop ? listKovaakRuns().then((result) => result.runs) : Promise.resolve<KovaaKRunListItem[]>([]),
    ]);
    setProfiles(profileResult.profiles);
    setCatalog(catalogResult);
    setCalibration(calibrationResult);
    setCmPer360(calibrationResult.values.cm_per_360?.toString() ?? "");
    setFov(calibrationResult.values.fov?.toString() ?? "");
    setCapture(captureResult);
    setRuns(runResult);
    setLoadError(catalogResult === null);
    settingsSnapshot = {
      profiles: profileResult.profiles,
      catalog: catalogResult,
      calibration: calibrationResult,
      capture: captureResult,
      storage: settingsSnapshot?.storage ?? null,
      incomplete: settingsSnapshot?.incomplete ?? [],
      runs: runResult,
    };
  }, [desktop]);

  const refresh = useCallback(async (force = false) => {
    if (!force && settingsSnapshot) {
      // stale-while-revalidate：有缓存先渲染（立即出屏），后台静默刷新，
      // 各分区数据先到先显示，不整页回 loading。
      applySnapshot(settingsSnapshot);
      setLoadError(settingsSnapshot.catalog === null);
      setLoading(false);
      void refreshRemote().catch(() => setLoadError(true));
      return;
    }
    try {
      await refreshRemote();
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [applySnapshot, refreshRemote]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let unavailableStreak = 0;
    const pollCaptureStatus = async () => {
      // 外部遥测源健康随同一轮询刷新（状态点聚合用；失败不打扰用户）。
      void getExternalTelemetry().then(setExternalTelemetry).catch(() => undefined);
      try {
        const next = await getCaptureStatus();
        if (disposed) return;
        if (next.availability === "unavailable") {
          unavailableStreak += 1;
          if (unavailableStreak < CAPTURE_UNAVAILABLE_POLL_LIMIT) return;
        } else {
          unavailableStreak = 0;
        }
        setCapture(next);
      } catch {
        // Polling is best effort after the initial settings load.
      }
    };
    const timer = window.setInterval(() => void pollCaptureStatus(), 1_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [desktop]);

  // hash 深链入口：/settings#id 经别名映射到对应屏（scroll spy 已随
  // 锚点长卷一起退役，左栏点击本身只写 state、不写 hash）。
  useEffect(() => {
    const syncActiveNav = () => {
      const hash = window.location.hash.slice(1);
      setActiveNav(HASH_SECTION_ALIASES[hash] ?? NAV_ITEMS[0].id);
    };

    syncActiveNav();
    window.addEventListener("hashchange", syncActiveNav);
    return () => window.removeEventListener("hashchange", syncActiveNav);
  }, []);

  const ask = (title: string, impact: string, run: () => Promise<void>) => {
    setConfirmAction({ title, impact, run });
  };

  const confirm = async () => {
    if (!confirmAction) return;
    try {
      await confirmAction.run();
      setConfirmAction(null);
      await refresh(true);
    } catch {
      setFeedback("操作未完成，未伪造成功状态，请重试。");
    }
  };

  const saveProfileCalibration = async () => {
    const result = await saveCalibrationProfile({
      cm_per_360: cmPer360 ? Number(cmPer360) : null,
      fov: fov ? Number(fov) : null,
    });
    setCalibration(result);
    setFeedback("配置档默认值已保存");
  };

  const deleteProfileCalibration = async () => {
    const result = await deleteCalibrationProfile();
    setCalibration(result);
    setCmPer360(result.values.cm_per_360?.toString() ?? "");
    setFov(result.values.fov?.toString() ?? "");
    setFeedback("配置档默认值已删除，退回读取值");
  };

  const latestStatsCalibration = runs.find((run) => run.stats_calibration)?.stats_calibration ?? null;
  // Profile 卡输入框的灰字占位 = Stats 已读取值；没有读取值才是「未设置」。
  const statsCm360Placeholder = latestStatsCalibration?.cm_per_360 != null ? String(latestStatsCalibration.cm_per_360) : "未设置";
  const statsFovPlaceholder = latestStatsCalibration?.fov != null ? String(latestStatsCalibration.fov) : "未设置";
  // 三态按钮判定：与已存值不一致=有未保存输入（橙「保存」）；一致且存在覆盖=「删除」。
  const profileDirty = cmPer360 !== (calibration?.values.cm_per_360?.toString() ?? "") || fov !== (calibration?.values.fov?.toString() ?? "");
  const profileHasInput = Boolean(cmPer360 || fov);
  const profileOverrideActive = !profileDirty && Boolean(calibration?.configured);

  const storageCategories = storage ? presentStorageCategories(storage.categories) : [];
  const totalBytes = storage?.total_bytes ?? 0;
  const storageBar = totalBytes > 0
    ? storageCategories.map(([, bytes]) => Math.max(0, bytes / totalBytes * 100))
    : [];

  const recentRuns = useMemo(
    () => [...runs]
      .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
      .slice(0, RECENT_CAPTURE_EVENTS_LIMIT),
    [runs],
  );
  // 采集源状态点（点点 0912 拍板）：任一采集源（采集服务/外部遥测）异常即整点转橙。
  const externalBroken = externalTelemetry?.activation === "failed"
    || externalTelemetry?.activation === "runtime_unavailable";
  const captureIssue = (capture ? capture.runtime_health !== "healthy" : false) || externalBroken;

  // 清理行：attached 录像/Raw 的 Run + 行内 size/文件名事实（不可得为 null，
  // 该片段不渲染）；大小已知时按大小降序，未知者按原顺序垫底。
  const cleanupRows = useMemo(() => runs
    .filter((run) => run.video_artifact_ref || run.trace_quality.state === "attached")
    .map((run) => {
      const sizes = [run.video_size_bytes, run.raw_size_bytes]
        .filter((size): size is number => typeof size === "number" && size >= 0);
      const kinds = [
        ...(run.video_artifact_ref ? ["video" as const] : []),
        ...(run.trace_quality.state === "attached" ? ["raw" as const] : []),
      ];
      return {
        run,
        kinds,
        sizeBytes: sizes.length > 0 ? sizes.reduce((sum, size) => sum + size, 0) : null,
      };
    })
    .sort((a, b) => (b.sizeBytes ?? -1) - (a.sizeBytes ?? -1)), [runs]);

  const sortedIncomplete = useMemo(
    () => [...incomplete].sort((a, b) => b.size_bytes - a.size_bytes),
    [incomplete],
  );

  // 线框顺序：浅色 / 深色 / 跟随系统。
  const themeOptions = [
    { value: "light", label: "浅色" },
    { value: "dark", label: "深色" },
    { value: "system", label: "跟随系统" },
  ] as const;

  // 渐进渲染：页面框架常驻，不再整页 return Loading。各分区（Provider /
  // 采集 / 存储）在各自数据到达前显示局部 skeleton，数据先到先显示。
  if (loadError && !catalog && profiles.length === 0) {
    return <div className="task6-settings-page"><div className="task6-settings-state-header" onMouseDown={startWindowDraggingOnBackground}><SettingsExit onExit={() => router.push("/")} /><span>设置</span></div><ErrorState title="设置暂时不可用"><Button onClick={() => void refresh(true)} variant="secondary">重试</Button></ErrorState></div>;
  }

  return (
    <div className="task6-settings-page">
      <aside className="task6-settings-nav" aria-label="设置分区">
        {/* 背景随内容拉满整高；sticky 放内层，滚动时导航仍跟随。 */}
        <div className="task6-settings-nav-inner">
          {/* 标题行兼作窗口拖拽区（左键空白处）：横跨顶栏拆除后的拖拽补偿。 */}
          <div className="task6-settings-nav-title-row" onMouseDown={startWindowDraggingOnBackground}>
            <SettingsExit onExit={() => router.push("/")} />
            <div className="task6-settings-nav-title">设置</div>
          </div>
          <nav aria-label="设置导航">
            {NAV_ITEMS.map((item) => (
              <button
                aria-current={item.id === activeNav ? "true" : undefined}
                className="task6-settings-nav-link"
                data-current={item.id === activeNav || undefined}
                key={item.id}
                onClick={() => selectSection(item.id)}
                type="button"
              >
                {item.label}
              </button>
            ))}
          </nav>
        </div>
      </aside>

      {/* 主列本身就是满铺浅色面板（0912 拍板）：顶带（拖拽区）是面板内
          第一行，内容滚动列在其下——滚动容器从三键下缘起。 */}
      <div className="task6-settings-main">
        <div className="task6-settings-topband" onMouseDown={startWindowDraggingOnBackground} />
        <div className="task6-settings-content" ref={contentRef}>
        {loadError ? <Notice className="task6-settings-notice" tone="warning" title="部分设置未能刷新">已保留当前可用内容。请检查本地服务后重试。</Notice> : null}

        {/* 通用（置顶）：主题 / Profile 默认值 / 应用更新 三小节合一屏。 */}
        <section className="task6-settings-section" hidden={activeNav !== "general"} id="general" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">通用</span>
            <span className="task6-settings-section-note">主题、Profile 默认值与应用更新。</span>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <h3 className="task6-profile-group-title">主题</h3>
              <p className="task6-card-desc">跟随系统或手动指定。</p>
              {/* 线框形态：三张横排主题预览卡——上半是目标主题配色预览块，
                  下方是名称；选中卡描边 accent。预览块用字面色表达目标主题。 */}
              <div className="task6-theme-cards" role="radiogroup" aria-label="外观模式">
                {themeOptions.map((mode) => (
                  <label className="task6-theme-card" data-selected={preference === mode.value || undefined} key={mode.value}>
                    <input checked={preference === mode.value} name="theme" onChange={() => setPreference(mode.value)} type="radio" value={mode.value} />
                    <span aria-hidden="true" className="task6-theme-card-swatch" data-mode={mode.value} />
                    <span className="task6-theme-card-name">{mode.label}</span>
                  </label>
                ))}
              </div>
            </Panel>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <h3 className="task6-profile-group-title">Profile 默认值</h3>
              <p className="task6-card-desc">读取失败时才使用的默认值；不影响已完成分析。</p>
              <div className="task6-profile-fields">
                <div className="task6-form-row">
                  <label className="task6-form-row-label" htmlFor="task6-profile-cm360">cm/360</label>
                  <FieldControl id="task6-profile-cm360" inputMode="decimal" min="0.01" onChange={(event) => setCmPer360(event.target.value)} placeholder={statsCm360Placeholder} step="any" type="number" value={cmPer360} />
                </div>
                <div className="task6-form-row">
                  <label className="task6-form-row-label" htmlFor="task6-profile-fov">FOV</label>
                  <FieldControl id="task6-profile-fov" inputMode="decimal" max="180" min="0.01" onChange={(event) => setFov(event.target.value)} placeholder={statsFovPlaceholder} step="any" type="number" value={fov} />
                </div>
              </div>
              {/* 三态按钮（点点 0912 拍板）：灰「保存」（无输入，禁用）→ 橙「保存」
                  （有未保存输入，点击写入覆盖）→ 「删除」（已覆盖，点击退回 Stats 读取值）。 */}
              <div className="task6-card-actions">
                {profileOverrideActive ? (
                  <Button onClick={() => void deleteProfileCalibration().catch(() => setFeedback("未能删除配置档默认值，请重试。"))} size="compact" variant="primary">删除</Button>
                ) : (
                  <Button disabled={!profileDirty || !profileHasInput} onClick={() => void saveProfileCalibration().catch(() => setFeedback("配置档未能保存，请检查数值。"))} size="compact" variant="primary">保存</Button>
                )}
              </div>
            </Panel>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <AppUpdatePanel />
            </Panel>
          </div>
        </section>

        <section className="task6-settings-section" data-guidance-target="settings.provider_auth" hidden={activeNav !== "llm-provider"} id="llm-provider" tabIndex={-1}>
          <div className="task6-settings-section-header task6-settings-section-header-stacked">
            <span className="task6-settings-section-title">LLM Provider</span>
            <span className="task6-settings-section-note">管理 Coach 使用的模型服务。列表行尾绿点＝连接正常，红点＝探测不通（自动测活）。</span>
          </div>
          <ProviderSettingsSection
            catalog={catalog}
            loading={loading}
            notify={setFeedback}
            profiles={profiles}
            refresh={refresh}
          />
        </section>

        <section className="task6-settings-section" data-guidance-target="desktop.capture_control" hidden={activeNav !== "capture"} id="capture" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">自动采集</span>
            <span className="task6-settings-section-note">发现 KovaaK 训练并自动记录证据。</span>
          </div>
          {/* 0912 点点拍板：只留开关行（合并 KovaaK 状态）与最近采集事件；细节行退役。 */}
          <div className="task6-settings-subsection">
            <Panel>
              <div className="task6-settings-section-header">
                <h3 className="task6-profile-group-title">自动采集</h3>
                <span
                  aria-hidden="true"
                  className="task6-capture-status-dot"
                  data-issue={captureIssue || undefined}
                  title={captureIssue ? "采集服务不正常" : undefined}
                />
              </div>
              <p className="task6-card-desc">检测到 KovaaK 对局时自动录制画面与相对鼠标输入，只保存在本机。</p>
              {!desktop ? <Notice className="task6-settings-notice" tone="warning" title="浏览器模式">自动采集仅在 Desktop 可用。</Notice> : null}
              {desktop && capture === null ? <Loading>正在读取采集状态</Loading> : null}
              {capture?.availability === "unavailable" ? <Notice className="task6-settings-notice" tone="error" title="采集状态不可用">{capture.error?.message ?? "本地采集服务暂时不可用。"}</Notice> : null}
              {capture ? (
                <div className="task6-toggle-rows">
                  <div className="task6-toggle-row">
                    <div className="task6-toggle-row-text">
                      <span className="task6-muted">
                        检测到 KovaaK 进程后开始采集{capture.capture_enabled == null ? "" : capture.capture_enabled ? "，当前待命" : "，当前已关闭"}
                        {" · "}{captureLabel(capture.kovaak_process_present, "KovaaK 已检测到", "KovaaK 未运行")}
                      </span>
                    </div>
                    <div className="task6-toggle-row-side">
                      {desktop && capture.capture_enabled != null ? (
                        <Button
                          disabled={!capture.capture_enabled && !captureConsent}
                          onClick={() => void setDesktopCaptureEnabled(!capture.capture_enabled).then(() => refresh(true))}
                          variant="secondary"
                        >
                          {capture.capture_enabled ? "关闭未来采集" : "授权并启用自动采集"}
                        </Button>
                      ) : (
                        <span className={capture.capture_enabled ? "task6-ok" : undefined}>{captureLabel(capture.capture_enabled, "待命", "已关闭")}</span>
                      )}
                    </div>
                  </div>
                  {desktop && capture.capture_enabled === false ? (
                    <label className="task6-consent">
                      <input checked={captureConsent} onChange={(event) => setCaptureConsent(event.target.checked)} type="checkbox" />
                      <span>我同意采集 Raw Input 和 KovaaK 窗口回放，用于本机训练分析。</span>
                    </label>
                  ) : null}
                </div>
              ) : null}
            </Panel>
          </div>
          {desktop && recentRuns.length > 0 ? (
            <div className="task6-settings-subsection">
              <Panel>
                <h3 className="task6-profile-group-title">最近采集事件</h3>
                <ul className="task6-capture-event-list">
                  {recentRuns.map((run) => {
                    const described = describeCaptureRunEvent({
                      scenario: run.scenario,
                      video_attached: Boolean(run.video_artifact_ref),
                      raw_attached: run.trace_quality.state === "attached",
                      video_error: run.video_error,
                      trace_error: run.trace_error,
                      finalization_state: run.finalization_state,
                    });
                    // 线框形态：纯行（无边框壳，悬停高亮）= 语义色圆点 + 场景名 +
                    // 行尾精简状态词；完整归因（括号里的人话）收进悬停 title。
                    const status = summarizeCaptureRunStatus(described.videoLabel, described.traceLabel);
                    return (
                      <li
                        className="task6-capture-event"
                        data-tone={status.tone}
                        key={run.run_ref}
                        title={`视频 ${described.videoLabel} · 轨迹 ${described.traceLabel}`}
                      >
                        <span aria-hidden="true" className="task6-capture-event-dot" />
                        <span className="task6-capture-event-scenario">{described.scenario}</span>
                        <span className="task6-capture-event-status">{status.word}</span>
                      </li>
                    );
                  })}
                </ul>
              </Panel>
            </div>
          ) : null}
          {desktop ? (
            <div className="task6-capture-diagnostics">
              <Button disabled={diagnosticExporting} onClick={() => void exportCaptureDiagnostics()} variant="secondary">
                {diagnosticExporting ? "正在打包运行日志…" : "导出运行日志"}
              </Button>
              <span className="task6-settings-section-hint">给开发者排障用的：复现问题后立即导出，包含完整 native 错误、环境和采集状态，不包含 Raw 数据或 MP4。</span>
            </div>
          ) : null}
        </section>

        {/* KovaaK：本地目录 + KovaaKs 在线成绩 两张自包含卡（0912 去嵌套拍板）。 */}
        <section className="task6-settings-section" hidden={activeNav !== "kovaak"} id="kovaak" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">KovaaK</span>
            <span className="task6-settings-section-note">本地目录与 KovaaKs 在线成绩。</span>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <KovaaKDirectoriesPanel context="settings" />
            </Panel>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <KovaaKConnectionPanel context="settings" />
            </Panel>
          </div>
        </section>

        <section className="task6-settings-section" data-guidance-target="storage.incomplete" hidden={activeNav !== "storage"} id="storage" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">数据与存储</span>
            <span className="task6-settings-section-note">本机分析产物、录像与遥测的占用与清理。</span>
          </div>
          {/* 0912 线框拍板：总占用与按条目清理拆成两张自包含卡。 */}
          <div className="task6-settings-subsection">
            <Panel>
            {!desktop ? <Notice className="task6-settings-notice" tone="warning" title="Desktop 能力不可用">浏览器不会伪造本地占用或删除操作。</Notice> : null}
            {desktop && storage === null ? <Loading>正在读取存储占用</Loading> : null}
            {storage ? (
              <>
                <div className="task6-storage-total">
                  <span className="task6-storage-total-number">{formatBytes(totalBytes)}</span>
                  <span className="task6-muted">总占用</span>
                </div>
                <div className="task6-storage-bar">
                  {storageBar.map((width, index) => (
                    <div key={index} style={{ width: `${width}%`, background: STORAGE_COLORS[index % STORAGE_COLORS.length] }} />
                  ))}
                </div>
                {/* 线框图例：每分类一行内联（色块+名称+大小·%），整排流式换行。 */}
                <div className="task6-storage-legend">
                  {storageCategories.map(([label, bytes], index) => (
                    <span className="task6-storage-legend-item" key={label}>
                      <span aria-hidden="true" className="task6-storage-swatch" style={{ background: STORAGE_COLORS[index % STORAGE_COLORS.length] }} />
                      {label} {formatBytes(bytes)}{totalBytes > 0 ? ` · ${Math.round(bytes / totalBytes * 100)}%` : ""}
                    </span>
                  ))}
                </div>
              </>
            ) : null}
            </Panel>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <div className="task6-storage-cleanup">
                <h3 className="task6-profile-group-title">按条目清理</h3>
                {cleanupRows.map(({ run, kinds, sizeBytes }) => {
                  const evidenceLabel =
                    kinds.length === 2 ? "录像 + Raw" : kinds[0] === "video" ? "录像" : "Raw trace";
                  const facts = [evidenceLabel];
                  if (sizeBytes != null) facts.push(formatBytes(sizeBytes));
                  const dateText = formatDay(run.training_at ?? run.created_at);
                  if (dateText) facts.push(dateText);
                  const removalTitle =
                    kinds.length === 2 ? "移除录像与 Raw trace" : kinds[0] === "video" ? "移除 Run 录像" : "移除 Raw trace";
                  const removalImpact =
                    kinds.length === 2
                      ? "录像与 Raw trace 将从本机移除：依赖它们的证据引用变为 unavailable；Run metadata、Analysis 与你的源文件保留。"
                      : kinds[0] === "video"
                        ? "录像引用将变为 unavailable；Run metadata、Analysis 与用户源文件保留。"
                        : "依赖 Raw 的证据引用将变为 unavailable；Run metadata 与用户源文件保留。";
                  return (
                    <article className="task6-storage-row" key={run.run_ref}>
                      <div>
                        <strong>{run.scenario ?? "未知场景"}</strong>
                        <p>{facts.join(" · ")}</p>
                      </div>
                      <div className="task6-inline-actions">
                        <Button
                          onClick={() => void reveal(
                            run.video_artifact_ref
                              ? { kind: "run_video", run_id: run.id }
                              : { kind: "run_raw", run_id: run.id },
                          )}
                          size="compact"
                          variant="ghost"
                        >
                          打开文件位置
                        </Button>
                        <Button
                          className="task6-btn-danger-ghost"
                          onClick={() => ask(removalTitle, removalImpact, async () => {
                            for (const kind of kinds) await removeRunEvidence(run.id, kind);
                          })}
                          size="compact"
                          variant="ghost"
                        >
                          移除…
                        </Button>
                      </div>
                    </article>
                  );
                })}
                {sortedIncomplete.map((item) => {
                  const facts = [formatBytes(item.size_bytes)];
                  const dateText = formatDay(item.created_at);
                  if (dateText) facts.push(dateText);
                  facts.push(incompleteReasonLabel(item.reason));
                  return (
                    <article className="task6-storage-row" key={item.item_ref}>
                      <div>
                        <strong>未完成采集</strong>
                        <p>{facts.join(" · ")}</p>
                      </div>
                      <div className="task6-inline-actions">
                        <Button
                          onClick={() => void reveal({ kind: "incomplete_capture", item_ref: item.item_ref })}
                          size="compact"
                          variant="ghost"
                        >
                          打开文件位置
                        </Button>
                        <Button
                          className="task6-btn-danger-ghost"
                          disabled={!item.removable}
                          onClick={() => ask("移除未完成采集", item.impact.message, async () => { await removeIncompleteCapture(item.item_ref); })}
                          size="compact"
                          variant="ghost"
                        >
                          移除…
                        </Button>
                      </div>
                    </article>
                  );
                })}
                {desktop && cleanupRows.length === 0 && incomplete.length === 0 ? (
                  <p className="task6-muted">没有可清理的录像、Raw trace 或未完成采集。</p>
                ) : null}
              </div>
            </Panel>
          </div>
        </section>
        </div>
      </div>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">取消</Button><Button onClick={() => void confirm()} variant="danger">确认</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? "确认操作"}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
      {feedback ? <Toast onClose={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
