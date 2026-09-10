"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getVersion } from "@tauri-apps/api/app";

import {
  deleteCalibrationProfile,
  getCalibrationProfile,
  getCaptureStatus,
  getProviderCatalog,
  getStorage,
  listIncompleteCaptures,
  listKovaakRuns,
  listProviderProfiles,
  removeIncompleteCapture,
  removeRunEvidence,
  saveCalibrationProfile,
} from "@/lib/api";
import { presentStorageCategories } from "@/lib/contracts";
import { describeCaptureRunEvent } from "@/lib/capture-events";
import { exportDesktopCaptureDiagnostics, isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import { checkForDesktopUpdate, type DesktopUpdate } from "@/lib/updater";
import { KovaaKConnectionPanel } from "@/components/kovaak/KovaaKConnectionPanel";
import { KovaaKDirectoriesPanel } from "@/components/kovaak/KovaaKDirectoriesPanel";
import { ExternalTelemetryPanel } from "@/components/kovaak/ExternalTelemetryPanel";
import { ProviderSettingsSection } from "@/components/task6/ProviderSettingsSection";
import type {
  CalibrationProfileV1,
  CaptureStatusV1,
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
  Field,
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
  return (
    <div className="task6-app-update">
      <p className="task6-muted">
        当前版本{appVersion ? ` ${appVersion}` : ""}。
        {checkState.phase === "checking" ? " 正在检查更新…" : null}
        {checkState.phase === "latest" ? " 已是最新版本。" : null}
        {checkState.phase === "error" ? " 检查失败，请稍后再试。" : null}
        {checkState.phase === "available" ? ` 发现新版本 ${checkState.update.version}。` : null}
        {installing ? " 正在下载并安装，完成后应用会自动重启…" : null}
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
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function captureLabel(value: boolean | null | undefined, yes: string, no: string): string {
  if (value == null) return "未知";
  return value ? yes : no;
}

function rawPermissionLabel(value: CaptureStatusV1["raw_input_permission"]): string {
  return value === "granted" ? "已允许" : value === "denied" ? "已拒绝" : "尚未决定";
}

function runtimeHealthLabel(value: CaptureStatusV1["runtime_health"]): string {
  return value === "healthy" ? "采集服务正常" : value === "degraded" ? "采集服务降级" : "采集服务不可用";
}

function runtimeHealthTone(value: CaptureStatusV1["runtime_health"]): "success" | "warning" | "error" {
  return value === "healthy" ? "success" : value === "degraded" ? "warning" : "error";
}

function incompleteReasonLabel(value: IncompleteCaptureItemV1["reason"]): string {
  return value === "interrupted_finalization" ? "整理过程被中断" : "未归类的采集产物";
}

const NAV_ITEMS = [
  { id: "llm-provider", label: "LLM Provider" },
  { id: "profile", label: "Profile" },
  { id: "theme", label: "主题" },
  { id: "capture", label: "自动采集与 Raw Input" },
  { id: "kovaak-directories", label: "KovaaK 本地目录" },
  { id: "kovaak", label: "KovaaK 成绩" },
  { id: "storage", label: "存储" },
];

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
  const [activeNav, setActiveNav] = useState(NAV_ITEMS[0].id);
  const [captureConsent, setCaptureConsent] = useState(false);
  const [diagnosticExporting, setDiagnosticExporting] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const desktop = isDesktopRuntime();

  const exportCaptureDiagnostics = async () => {
    setDiagnosticExporting(true);
    try {
      const path = await exportDesktopCaptureDiagnostics();
      if (path) setFeedback(`采集诊断包已导出：${path}`);
    } catch {
      setFeedback("采集诊断包导出失败，请重试。");
    } finally {
      setDiagnosticExporting(false);
    }
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

  useEffect(() => {
    const syncActiveNav = () => {
      const hash = window.location.hash.slice(1);
      setActiveNav(NAV_ITEMS.some((item) => item.id === hash) ? hash : NAV_ITEMS[0].id);
    };

    syncActiveNav();
    window.addEventListener("hashchange", syncActiveNav);
    return () => window.removeEventListener("hashchange", syncActiveNav);
  }, []);

  // Scroll spy：内容区一页长滚动，导航条目是锚点；滚动时左侧当前分区高亮跟随。
  // v6 面板化：滚动容器是面板内容列（.task6-settings-content）。
  useEffect(() => {
    const root = rootRef.current;
    const scroller = root?.querySelector(".task6-settings-content");
    if (!root || !scroller) return;
    let ticking = false;
    const compute = () => {
      ticking = false;
      const top = scroller.getBoundingClientRect().top;
      let current = NAV_ITEMS[0].id;
      for (const item of NAV_ITEMS) {
        const section = document.getElementById(item.id);
        if (!section) continue;
        if (section.getBoundingClientRect().top - top <= 120) current = item.id;
      }
      if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) {
        current = NAV_ITEMS[NAV_ITEMS.length - 1].id;
      }
      setActiveNav(current);
    };
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(compute);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    compute();
    return () => scroller.removeEventListener("scroll", onScroll);
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

  const latestStatsCalibration = runs.find((run) => run.stats_calibration)?.stats_calibration ?? null;

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

  const themeOptions = [
    { value: "system", label: "跟随系统" },
    { value: "light", label: "浅色" },
    { value: "dark", label: "深色" },
  ] as const;

  // 渐进渲染：页面框架常驻，不再整页 return Loading。各分区（Provider /
  // 采集 / 存储）在各自数据到达前显示局部 skeleton，数据先到先显示。
  if (loadError && !catalog && profiles.length === 0) {
    return <div className="task6-settings-page"><div className="task6-settings-state-header" onMouseDown={startWindowDraggingOnBackground}><SettingsExit onExit={() => router.push("/")} /><span>设置</span></div><ErrorState title="设置暂时不可用"><Button onClick={() => void refresh(true)} variant="secondary">重试</Button></ErrorState></div>;
  }

  return (
    <div className="task6-settings-page" ref={rootRef}>
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
              <a
                aria-current={item.id === activeNav ? "true" : undefined}
                className="task6-settings-nav-link"
                data-current={item.id === activeNav || undefined}
                href={`#${item.id}`}
                key={item.id}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      </aside>

      <div className="task6-settings-content">
        {loadError ? <Notice className="task6-settings-notice" tone="warning" title="部分设置未能刷新">已保留当前可用内容。请检查本地服务后重试。</Notice> : null}

        <section className="task6-settings-section" data-guidance-target="settings.provider_auth" id="llm-provider" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">LLM Provider</span>
            <span className="task6-settings-section-note">管理 Coach 使用的模型服务：添加、切换当前使用、测试连接。</span>
          </div>
          <ProviderSettingsSection
            catalog={catalog}
            loading={loading}
            notify={setFeedback}
            profiles={profiles}
            refresh={refresh}
          />
        </section>

        <section className="task6-settings-section" id="profile" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">Profile</span>
            <span className="task6-settings-section-note">配置档默认值：按相关性分组的灵敏度与视野设置。</span>
          </div>
          <Panel>
            <div className="task6-profile-group">
              <h3 className="task6-profile-group-title">手动校准</h3>
              <p className="task6-muted">读取失败时才使用的默认值；不影响已完成分析。</p>
              <div className="task6-profile-fields">
                <Field label="cm/360"><FieldControl inputMode="decimal" min="0.01" onChange={(event) => setCmPer360(event.target.value)} step="any" type="number" value={cmPer360} /></Field>
                <Field label="FOV"><FieldControl inputMode="decimal" max="180" min="0.01" onChange={(event) => setFov(event.target.value)} step="any" type="number" value={fov} /></Field>
              </div>
            </div>
            <div className="task6-profile-group">
              <h3 className="task6-profile-group-title">Stats 自动读取</h3>
              <div className="task6-profile-fields">
                <div className="task6-profile-stat">
                  <span className="task6-profile-stat-label">DPI</span>
                  <span className="task6-mono">{latestStatsCalibration?.dpi ?? calibration?.dpi ?? "待读取"}</span>
                </div>
                <div className="task6-profile-stat">
                  <span className="task6-profile-stat-label">Sensitivity</span>
                  <span className="task6-mono">{latestStatsCalibration?.sensitivity ?? calibration?.sensitivity ?? "待读取"}</span>
                </div>
                <div className="task6-profile-stat">
                  <span className="task6-profile-stat-label">FOV</span>
                  <span className="task6-mono">{latestStatsCalibration?.fov ?? "待读取"}</span>
                </div>
              </div>
              <p className="task6-profile-summary">
                <span>
                  Stats 自动读取优先，此处仅在读取失败时使用。已完成分析冻结当时数值，改这里不影响历史；无法推导时显示「无法确定」，不猜值。
                </span>
                <span className="task6-info">
                  <button aria-describedby="task6-profile-help" aria-label="配置档默认值说明" className="task6-info-trigger" type="button">!</button>
                  <span className="task6-info-tooltip" id="task6-profile-help" role="tooltip">
                    Stats 自动读取优先，此处仅在读取失败时使用。已完成分析冻结当时数值，改这里不影响历史；无法推导时显示「无法确定」，不猜值。
                  </span>
                </span>
              </p>
            </div>
            <div className="task6-profile-footer">
              <div className="task6-profile-actions">
                <Button disabled={!cmPer360 && !fov} onClick={() => void saveProfileCalibration().catch(() => setFeedback("配置档未能保存，请检查数值。"))} size="compact" variant="secondary">保存</Button>
                <Button onClick={() => ask("删除配置档默认值", "之后仍会优先使用 Stats 或本局手动覆盖。", async () => { await deleteCalibrationProfile(); })} size="compact" variant="danger">删除</Button>
              </div>
            </div>
          </Panel>
        </section>

        <section className="task6-settings-section" id="theme" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">主题</span>
            <span className="task6-settings-section-note">外观模式只影响本机界面。</span>
          </div>
          <Panel>
            <div className="task6-theme-row">
              <div className="task6-theme-row-text">
                <span className="task6-theme-row-name">外观模式</span>
                <span className="task6-muted">跟随系统、浅色或深色，立即生效。</span>
              </div>
              <div className="task6-theme-segments" role="radiogroup" aria-label="外观模式">
                {themeOptions.map((mode) => (
                  <label className="task6-theme-segment" data-selected={preference === mode.value} key={mode.value}>
                    <input checked={preference === mode.value} name="theme" onChange={() => setPreference(mode.value)} type="radio" value={mode.value} />
                    <span>{mode.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div aria-hidden="true" className="task6-theme-preview">
              <span className="task6-theme-preview-chip" data-chip="primary" />
              <span className="task6-theme-preview-chip" data-chip="surface" />
              <span className="task6-theme-preview-chip" data-chip="outline" />
              <span className="task6-theme-preview-chip" data-chip="accent" />
            </div>
          </Panel>
        </section>

        <section className="task6-settings-section" data-guidance-target="desktop.capture_control" id="capture" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">自动采集与 Raw Input</span>
            <span className="task6-settings-section-note">检测到 KovaaK 对局时自动录制画面与相对鼠标输入，只保存在本机。</span>
          </div>
          <Panel>
            {!desktop ? <Notice className="task6-settings-notice" tone="warning" title="浏览器模式">自动采集、Raw Input、硬件回放缓冲和权限管理仅在 Desktop 可用。</Notice> : null}
            {desktop && capture === null ? <Loading>正在读取采集状态</Loading> : null}
            {capture?.availability === "unavailable" ? <Notice className="task6-settings-notice" tone="error" title="采集状态不可用">{capture.error?.message ?? "本地采集服务暂时不可用。"}</Notice> : null}
            {capture?.availability === "available" ? (
              <div className="task6-capture-health">
                <Status tone={runtimeHealthTone(capture.runtime_health)}>{runtimeHealthLabel(capture.runtime_health)}</Status>
                <span className="task6-muted">{captureLabel(capture.kovaak_process_present, "KovaaK 已检测到", "KovaaK 未运行")}</span>
              </div>
            ) : null}
            {capture ? (
              <div className="task6-toggle-rows">
                <div className="task6-toggle-row">
                  <div className="task6-toggle-row-text">
                    <span className="task6-toggle-row-name">自动采集</span>
                    <span className="task6-muted">检测到 KovaaK 进程后开始采集{capture.capture_enabled == null ? "" : capture.capture_enabled ? "，当前待命" : "，当前已关闭"}</span>
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
                <div className="task6-toggle-row">
                  <div className="task6-toggle-row-text">
                    <span className="task6-toggle-row-name">Raw Input 授权</span>
                    <span className="task6-muted">只采集 KovaaK 进程内的相对鼠标输入；不采集键盘与桌面坐标；只保存在本机。<a href="#">查看范围说明</a></span>
                  </div>
                  <div className="task6-toggle-row-side">{rawPermissionLabel(capture.raw_input_permission)}</div>
                </div>
                <div className="task6-toggle-row">
                  <div className="task6-toggle-row-text">
                    <span className="task6-toggle-row-name">回放缓冲</span>
                    <span className="task6-muted">仅保留最近 300 秒、仅 KovaaK 窗口画面，不录桌面与其它窗口</span>
                  </div>
                  <div className="task6-toggle-row-side">{captureLabel(capture.replay_buffer_active, "维护中", "未活动")}</div>
                </div>
                <div className="task6-toggle-row">
                  <div className="task6-toggle-row-text">
                    <span className="task6-toggle-row-name">平台支持</span>
                    <span className="task6-muted">非 Windows 提供视频兼容路径</span>
                  </div>
                  <div className="task6-toggle-row-side"><span className="task6-ok">{captureLabel(capture.platform_supported, "✓ Windows", "✗ 不支持")}</span></div>
                </div>
                <div className="task6-toggle-row">
                  <div className="task6-toggle-row-text">
                    <span className="task6-toggle-row-name">暂停局处理</span>
                    <span className="task6-muted">Stats 显示暂停的对局不生成永久录像，证据保留为部分/不可用（fail-closed）</span>
                  </div>
                  <div className="task6-toggle-row-side">{capture.pause_fail_closed ? "fail-closed" : "clear"}</div>
                </div>
              </div>
            ) : null}
            {desktop && recentRuns.length > 0 ? (
              <div className="task6-capture-events">
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
                    return (
                      <li className="task6-capture-event" data-healthy={described.healthy || undefined} key={run.run_ref}>
                        <span className="task6-capture-event-scenario">{described.scenario}</span>
                        <span className="task6-capture-event-detail">
                          视频：{described.videoLabel} · 输入轨迹：{described.traceLabel}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
            {desktop ? (
              <div className="task6-capture-diagnostics">
                <Button disabled={diagnosticExporting} onClick={() => void exportCaptureDiagnostics()} variant="secondary">
                  {diagnosticExporting ? "正在导出诊断包…" : "导出采集诊断包"}
                </Button>
                <span className="task6-settings-section-hint">给开发者排障用的：复现问题后立即导出，包含完整 native 错误、环境和采集状态，不包含 Raw 数据或 MP4。</span>
              </div>
            ) : null}
          </Panel>
        </section>

        <section className="task6-settings-section" id="kovaak-directories" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">KovaaK 本地目录</span>
            <span className="task6-settings-section-note">确认 KovaaK 的 Stats 与 Performance 文件夹，AC 据此发现训练并自动开启统计导出。</span>
          </div>
          <Panel>
            <KovaaKDirectoriesPanel context="settings" />
          </Panel>
        </section>

        <section className="task6-settings-section" id="external-telemetry" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">外部遥测导入</span>
          </div>
          <Panel>
            <ExternalTelemetryPanel />
          </Panel>
        </section>

        <section className="task6-settings-section" id="kovaak" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">KovaaK 成绩</span>
            <span className="task6-settings-section-note">连接 Steam 资料读取 S2 训练单成绩；数据只在本机展示。</span>
          </div>
          <Panel>
            <KovaaKConnectionPanel context="settings" />
          </Panel>
        </section>

        <section className="task6-settings-section" data-guidance-target="storage.incomplete" id="storage" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">存储</span>
            <span className="task6-settings-section-note">本机分析产物、录像与遥测的占用与清理。</span>
          </div>
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
                <dl className="task6-storage-kv">
                  {storageCategories.map(([label, bytes], index) => (
                    <div key={label}>
                      <dt><span className="task6-storage-swatch" style={{ background: STORAGE_COLORS[index % STORAGE_COLORS.length] }} />{label}</dt>
                      <dd><span className="task6-mono">{formatBytes(bytes)} · {totalBytes > 0 ? `${Math.round(bytes / totalBytes * 100)}%` : "0%"}</span> · {index === 0 || index === 2 ? <a href="#">管理…</a> : null}</dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : null}
            <div className="task6-storage-cleanup">
              <h3 className="task6-profile-group-title">清理</h3>
              {runs.filter((run) => run.video_artifact_ref || run.trace_quality.state === "attached").map((run) => (
                <article className="task6-storage-row" key={run.run_ref}>
                  <div>
                    <strong>{run.scenario ?? "未知场景"}</strong>
                    <p>{run.video_artifact_ref && run.trace_quality.state === "attached" ? "录像与 Raw trace" : run.video_artifact_ref ? "Run 录像" : "Raw trace"}</p>
                  </div>
                  <div className="task6-inline-actions">
                    {run.video_artifact_ref ? <Button onClick={() => ask("移除 Run 录像", "录像引用将变为 unavailable；Run metadata、Analysis 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "video"); })} size="compact" variant="danger">移除录像</Button> : null}
                    {run.trace_quality.state === "attached" ? <Button onClick={() => ask("移除 Raw trace", "依赖 Raw 的证据引用将变为 unavailable；Run metadata 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "raw"); })} size="compact" variant="danger">移除 Raw</Button> : null}
                  </div>
                </article>
              ))}
              {incomplete.map((item) => (
                <article className="task6-storage-row" key={item.item_ref}>
                  <div>
                    <strong>未完成采集</strong>
                    <p>{formatBytes(item.size_bytes)} · {incompleteReasonLabel(item.reason)}</p>
                  </div>
                  <Button disabled={!item.removable} onClick={() => ask("移除未完成采集", item.impact.message, async () => { await removeIncompleteCapture(item.item_ref); })} size="compact" variant="danger">移除</Button>
                </article>
              ))}
              {desktop && runs.filter((run) => run.video_artifact_ref || run.trace_quality.state === "attached").length === 0 && incomplete.length === 0 ? (
                <p className="task6-muted">没有可清理的录像、Raw trace 或未完成采集。</p>
              ) : null}
            </div>
          </Panel>
        </section>

        <section className="task6-settings-section" id="app-update" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">应用更新</span>
            <span className="task6-settings-section-note">检查并安装 Aiming Cookie 新版本；更新包来自官方下载源并在本机验签。</span>
          </div>
          <Panel>
            <AppUpdatePanel />
          </Panel>
        </section>
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
