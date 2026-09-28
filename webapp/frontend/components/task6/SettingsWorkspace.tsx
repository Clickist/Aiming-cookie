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
import { getLocale, t, useLocale, useT, type Locale, type MessageKey } from "@/lib/i18n";
import { DIAGNOSTICS_UPLOAD_NOT_CONFIGURED, exportDesktopCaptureDiagnostics, isDesktopRuntime, pickDesktopDirectory, setDesktopCaptureEnabled, uploadDesktopCaptureDiagnostics } from "@/lib/desktop";
import { logFrontendError } from "@/lib/frontend-log";
import {
  copyTextToClipboard,
  getStorageLocationStatus,
  migrationFailureKey,
  migrationPercent,
  setStorageLocation,
  type StorageLocationStatusV1,
} from "@/lib/storage-location";
import { checkForDesktopUpdate, type DesktopUpdate } from "@/lib/updater";
import { KovaaKConnectionPanel } from "@/components/kovaak/KovaaKConnectionPanel";
import { KovaaKDirectoriesPanel } from "@/components/kovaak/KovaaKDirectoriesPanel";
import { KnowledgeSettingsSection } from "@/components/task6/KnowledgeSettingsSection";
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
  const t = useT();
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
    return <p className="task6-muted">{t("settings.update.browserOnly")}</p>;
  }
  // 线框形态（0912 完美复刻）：第一行 = 「应用更新」+ 状态 chip；
  // 第二行 = 当前版本（居左）+ 行尾「检查更新」。chip 状态词与面板状态
  // 一一对应：未检查/检查中/已是最新/有新版本/检查失败。
  const chip =
    checkState.phase === "latest" ? { tone: "ok", label: t("settings.update.chipLatest") }
    : checkState.phase === "available" ? { tone: "new", label: t("settings.update.chipAvailable") }
    : checkState.phase === "checking" ? { tone: "idle", label: t("settings.update.chipChecking") }
    : checkState.phase === "error" ? { tone: "idle", label: t("settings.update.chipError") }
    : { tone: "idle", label: t("settings.update.chipIdle") };
  return (
    <div className="task6-app-update">
      <div className="task6-app-update-head">
        <h3 className="task6-profile-group-title">{t("settings.update.title")}</h3>
        <span className="task6-update-chip" data-tone={chip.tone}>{chip.label}</span>
      </div>
      <div className="task6-app-update-row">
        <p className="task6-muted">
          {t("settings.update.currentVersion")}{appVersion ? t("settings.update.versionSuffix", { version: appVersion }) : ""}
          {checkState.phase === "checking" ? t("settings.update.checkingSuffix") : null}
          {checkState.phase === "error" ? t("settings.update.errorSuffix") : null}
          {installing ? t("settings.update.installingSuffix") : null}
        </p>
        <div className="task6-inline-actions">
          <Button
            disabled={checkState.phase === "checking" || installing}
            onClick={() => void checkNow()}
            size="compact"
            variant="secondary"
          >
            {t("settings.update.checkButton")}
          </Button>
          {checkState.phase === "available" && !installing ? (
            <Button onClick={() => void installNow()} size="compact">
              {t("settings.update.updateTo", { version: checkState.update.version })}
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

// 存储位置卡（自定义数据根，2026-09-28）：生效根由 Rust 壳解析，切换重启才生效、
// 迁移在重启时后台执行。这里只做三件事：读状态、写位置（先二次确认再写指针）、
// 迁移进行中轮询进度。位置被拒时只上屏壳的稳定码译出的本地化文案（不伪造成功）。
function StorageLocationCard({
  ask,
  notify,
}: {
  ask: (title: string, impact: string, run: () => Promise<void>) => void;
  notify: (message: string) => void;
}) {
  const t = useT();
  const [status, setStatus] = useState<StorageLocationStatusV1 | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getStorageLocationStatus());
    } catch {
      // 读取失败保留上一份已知状态，不把卡片变成错误页（下面的行按可用数据渲染）。
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 迁移只在重启时执行：记录处于 planned/running 才需要轮询（done/failed 后停止，
  // 重启提示本身是静态事实，轮询它也变不出新信息）。
  const migration = status?.migration ?? null;
  const migrationActive = migration?.phase === "planned" || migration?.phase === "running";
  useEffect(() => {
    if (!migrationActive) return undefined;
    const timer = window.setInterval(() => void load(), 1_000);
    return () => window.clearInterval(timer);
  }, [load, migrationActive]);

  const copyPath = async () => {
    if (!status) return;
    notify((await copyTextToClipboard(status.effectiveRoot)) ? t("settings.storage.location.copied") : t("settings.storage.location.copyFailed"));
  };

  // 位置被拒（盘符不存在 / 无写权限 / 空间不足 / 嵌套）时红字上屏，指针不写：
  // 文案由 lib/storage-location 把壳的稳定码译成本地语言。
  const reportFailure = (failure: unknown) => {
    setError(failure instanceof Error ? failure.message : t("settings.storage.location.errorUnknown"));
  };

  const pick = async (): Promise<string | null> => {
    setBusy(true);
    try {
      return await pickDesktopDirectory(t("settings.storage.location.pickerTitle"));
    } catch {
      setError(t("settings.storage.location.pickerFailed"));
      return null;
    } finally {
      setBusy(false);
    }
  };

  // 更改位置：选文件夹 → 二次确认（说明迁移语义）→ 写指针；重启后自动迁移。
  const changeLocation = async () => {
    const current = status?.effectiveRoot ?? "";
    const picked = await pick();
    if (!picked) return;
    ask(
      t("settings.storage.location.changeTitle"),
      t("settings.storage.location.changeImpact", { from: current, to: picked }),
      async () => {
        try {
          const next = await setStorageLocation(picked);
          setStatus(next);
          setError(null);
          notify(t("settings.storage.location.restartPending", { path: next.customRoot ?? next.defaultRoot }));
        } catch (failure) {
          reportFailure(failure);
        }
      },
    );
  };

  // 恢复默认位置：同一确认与迁移流程，方向相反。
  const restoreLocation = async () => {
    const target = status?.defaultRoot ?? "";
    ask(
      t("settings.storage.location.restoreTitle"),
      t("settings.storage.location.restoreImpact", { to: target }),
      async () => {
        try {
          const next = await setStorageLocation(null);
          setStatus(next);
          setError(null);
          notify(t("settings.storage.location.restartPending", { path: next.defaultRoot }));
        } catch (failure) {
          reportFailure(failure);
        }
      },
    );
  };

  const migrationLine = (() => {
    if (!migration) return null;
    if (migration.phase === "planned") return t("settings.storage.location.migrationPlanned");
    if (migration.phase === "running") {
      const percent = migrationPercent(migration);
      const values = {
        moved: migration.movedEntries.length,
        total: migration.movedEntries.length + migration.pendingEntries.length,
        copied: formatBytes(migration.copiedBytes),
        all: formatBytes(migration.totalBytes),
      };
      return percent == null
        ? t("settings.storage.location.migrationRunning", values)
        : t("settings.storage.location.migrationRunningPercent", { ...values, percent });
    }
    if (migration.phase === "failed") {
      return t("settings.storage.location.migrationFailed", { reason: t(migrationFailureKey(migration)) });
    }
    return t("settings.storage.location.migrationDone");
  })();

  return (
    <Panel>
      <h3 className="task6-profile-group-title">{t("settings.storage.location.title")}</h3>
      <p className="task6-card-desc">{t("settings.storage.location.desc")}</p>
      <div className="task6-storage-location">
        <span className="task6-form-row-label">{t("settings.storage.location.current")}</span>
        <span className="task6-storage-location-path">{status?.effectiveRoot ?? t("settings.storage.loading")}</span>
        <Button disabled={!status} onClick={() => void copyPath()} size="compact" variant="ghost">{t("settings.storage.location.copy")}</Button>
      </div>
      {status?.restartRequired ? (
        <p className="task6-storage-location-pending">
          {t("settings.storage.location.restartPending", { path: status.customRoot ?? status.defaultRoot })}
        </p>
      ) : null}
      {migrationLine ? (
        <p className={migration?.phase === "failed" ? "task6-storage-location-error" : "task6-muted"}>{migrationLine}</p>
      ) : null}
      {error ? <p className="task6-storage-location-error">{error}</p> : null}
      <div className="task6-storage-location-actions">
        <Button disabled={busy || migration?.phase === "running"} onClick={() => void changeLocation()} size="compact">{t("settings.storage.location.change")}</Button>
        <Button disabled={busy || !status?.customRoot || migration?.phase === "running"} onClick={() => void restoreLocation()} size="compact" variant="secondary">{t("settings.storage.location.restore")}</Button>
      </div>
    </Panel>
  );
}

/** 「8月27日」短日期；解析失败返回 null（不渲染，不硬造）。 */
function formatDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  // 日期格式跟随当前 locale（批 1 formatHistoryDate 同款改法）。
  return new Intl.DateTimeFormat(getLocale() === "en-US" ? "en-US" : "zh-CN", { month: "long", day: "numeric" }).format(date);
}

function captureLabel(value: boolean | null | undefined, yes: string, no: string): string {
  if (value == null) return t("settings.capture.unknown");
  return value ? yes : no;
}

function incompleteReasonLabel(value: IncompleteCaptureItemV1["reason"]): string {
  return value === "interrupted_finalization" ? t("settings.storage.reasonInterrupted") : t("settings.storage.reasonUncategorized");
}

// 分区切换化（点点拍板线框）：左栏切换项，点击只显示对应屏。
// 0912 点点拍板：高级屏取消——诊断包挪回自动采集，外部遥测导入界面下线。
// i18n 批 4（§2c）：label 是字典键（MessageKey），渲染时经 t() 解析。
const NAV_ITEMS = [
  { id: "general", label: "settings.nav.general" },
  { id: "llm-provider", label: "settings.nav.llmProvider" },
  { id: "knowledge", label: "settings.nav.knowledge" },
  { id: "capture", label: "settings.nav.capture" },
  { id: "kovaak", label: "settings.nav.kovaak" },
  { id: "storage", label: "settings.nav.storage" },
] as const satisfies ReadonlyArray<{ id: string; label: MessageKey }>;

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
  knowledge: "knowledge",
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
  const t = useT();
  return <IconButton className="task6-settings-back" label={t("settings.exit.label")} onClick={onExit} size="compact" title={t("history.page.backToCoach")}><IconChevronLeft /></IconButton>;
}

export function SettingsWorkspace() {
  const router = useRouter();
  const t = useT();
  const { preference, setPreference } = useTheme();
  const { locale, setLocale: applyLocale } = useLocale();
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
  const [diagnosticUploading, setDiagnosticUploading] = useState(false);
  const [diagnosticUploadId, setDiagnosticUploadId] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);

  const desktop = isDesktopRuntime();

  const exportCaptureDiagnostics = async () => {
    setDiagnosticExporting(true);
    try {
      const path = await exportDesktopCaptureDiagnostics();
      if (path) setFeedback(t("settings.feedback.logsExported", { path }));
    } catch (error) {
      logFrontendError("capture-diagnostics-export", error instanceof Error ? error.message : String(error));
      setFeedback(t("settings.feedback.logsExportFailed"));
    } finally {
      setDiagnosticExporting(false);
    }
  };

  // 一键上传诊断包到 logs.aimingcookie.com；成功回显编号给开发者对账，
  // 失败不阻塞用户——提示改用「导出运行日志」走本地文件降级。
  // 「未配置」与「上传失败」分开提示：前者是构建缺 token（重试无用，找开发者），
  // 后者是网络/服务问题（可重试）。两者混用会让构建问题被读成网络问题。
  const uploadCaptureDiagnostics = async () => {
    setDiagnosticUploading(true);
    try {
      const id = await uploadDesktopCaptureDiagnostics();
      setDiagnosticUploadId(id);
      setFeedback(t("settings.feedback.uploadDone", { id }));
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      logFrontendError("capture-diagnostics-upload", code || String(error));
      if (code === DIAGNOSTICS_UPLOAD_NOT_CONFIGURED) {
        setFeedback(t("settings.feedback.uploadNotConfigured"));
      } else if (code === "UPLOAD_RATE_LIMITED") {
        setFeedback(t("settings.feedback.uploadRateLimited"));
      } else if (code === "UPLOAD_QUOTA_EXCEEDED") {
        setFeedback(t("settings.feedback.uploadQuotaExceeded"));
      } else {
        setFeedback(t("settings.feedback.uploadFailed"));
      }
    } finally {
      setDiagnosticUploading(false);
    }
  };

  // 「打开文件位置」：只发条目 id/kind，本地路径由后端解析（path-free 合同）。
  const reveal = async (request: Parameters<typeof revealStorageItem>[0]) => {
    try {
      await revealStorageItem(request);
    } catch {
      setFeedback(t("settings.feedback.revealFailed"));
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
      setFeedback(t("settings.feedback.opIncomplete"));
    }
  };

  const saveProfileCalibration = async () => {
    const result = await saveCalibrationProfile({
      cm_per_360: cmPer360 ? Number(cmPer360) : null,
      fov: fov ? Number(fov) : null,
    });
    setCalibration(result);
    setFeedback(t("settings.feedback.profileSaved"));
  };

  const deleteProfileCalibration = async () => {
    const result = await deleteCalibrationProfile();
    setCalibration(result);
    setCmPer360(result.values.cm_per_360?.toString() ?? "");
    setFov(result.values.fov?.toString() ?? "");
    setFeedback(t("settings.feedback.profileDeleted"));
  };

  const latestStatsCalibration = runs.find((run) => run.stats_calibration)?.stats_calibration ?? null;
  // Profile 卡输入框的灰字占位 = Stats 已读取值；没有读取值才是「未设置」。
  const statsCm360Placeholder = latestStatsCalibration?.cm_per_360 != null ? String(latestStatsCalibration.cm_per_360) : t("settings.profile.unsetPlaceholder");
  const statsFovPlaceholder = latestStatsCalibration?.fov != null ? String(latestStatsCalibration.fov) : t("settings.profile.unsetPlaceholder");
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
    { value: "light", label: t("settings.theme.light") },
    { value: "dark", label: t("settings.theme.dark") },
    { value: "system", label: t("settings.theme.system") },
  ] as const;

  // 界面语言两档（i18n 收尾批）：applyLocale = setLocale，切换立即生效
  // （订阅通知触发所有 useT 组件重渲染）并持久化到 localStorage。
  const languageOptions = [
    { value: "zh-CN", label: t("settings.language.zh") },
    { value: "en-US", label: t("settings.language.en") },
  ] as const satisfies ReadonlyArray<{ value: Locale; label: string }>;

  // 渐进渲染：页面框架常驻，不再整页 return Loading。各分区（Provider /
  // 采集 / 存储）在各自数据到达前显示局部 skeleton，数据先到先显示。
  if (loadError && !catalog && profiles.length === 0) {
    return <div className="task6-settings-page"><div className="task6-settings-state-header" onMouseDown={startWindowDraggingOnBackground}><SettingsExit onExit={() => router.push("/")} /><span>{t("settings.page.title")}</span></div><ErrorState title={t("settings.error.title")}><Button onClick={() => void refresh(true)} variant="secondary">{t("common.retry")}</Button></ErrorState></div>;
  }

  return (
    <div className="task6-settings-page">
      <aside className="task6-settings-nav" aria-label={t("settings.page.navAria")}>
        {/* 背景随内容拉满整高；sticky 放内层，滚动时导航仍跟随。 */}
        <div className="task6-settings-nav-inner">
          {/* 标题行兼作窗口拖拽区（左键空白处）：横跨顶栏拆除后的拖拽补偿。 */}
          <div className="task6-settings-nav-title-row" onMouseDown={startWindowDraggingOnBackground}>
            <SettingsExit onExit={() => router.push("/")} />
            <div className="task6-settings-nav-title">{t("settings.page.title")}</div>
          </div>
          <nav aria-label={t("settings.page.navLabel")}>
            {NAV_ITEMS.map((item) => (
              <button
                aria-current={item.id === activeNav ? "true" : undefined}
                className="task6-settings-nav-link"
                data-current={item.id === activeNav || undefined}
                key={item.id}
                onClick={() => selectSection(item.id)}
                type="button"
              >
                {t(item.label)}
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
        {loadError ? <Notice className="task6-settings-notice" tone="warning" title={t("settings.notice.partialRefreshTitle")}>{t("settings.notice.partialRefreshBody")}</Notice> : null}

        {/* 通用（置顶）：主题 / Profile 默认值 / 应用更新 三小节合一屏。 */}
        <section className="task6-settings-section" hidden={activeNav !== "general"} id="general" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">{t("settings.nav.general")}</span>
            <span className="task6-settings-section-note">{t("settings.section.generalNote")}</span>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <h3 className="task6-profile-group-title">{t("settings.theme.title")}</h3>
              <p className="task6-card-desc">{t("settings.theme.desc")}</p>
              {/* 线框形态：三张横排主题预览卡——上半是目标主题配色预览块，
                  下方是名称；选中卡描边 accent。预览块用字面色表达目标主题。 */}
              <div className="task6-theme-cards" role="radiogroup" aria-label={t("settings.theme.modeAria")}>
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
              <h3 className="task6-profile-group-title">{t("settings.language.title")}</h3>
              <p className="task6-card-desc">{t("settings.language.desc")}</p>
              {/* 语言两档单选：视觉复用知识库向导来源选择的 task6-mode-card
                  单选卡（i18n 收尾批，零新增 CSS）；语言名固定各自语言书写。 */}
              <div className="task6-theme-cards" role="radiogroup" aria-label={t("settings.language.choiceAria")}>
                {languageOptions.map((option) => (
                  <label className="task6-mode-card" data-selected={locale === option.value || undefined} key={option.value}>
                    <input checked={locale === option.value} name="ui-language" onChange={() => applyLocale(option.value)} type="radio" value={option.value} />
                    <span className="task6-mode-card-name">{option.label}</span>
                  </label>
                ))}
              </div>
            </Panel>
          </div>
          <div className="task6-settings-subsection">
            <Panel>
              <h3 className="task6-profile-group-title">{t("settings.profile.defaultsTitle")}</h3>
              <p className="task6-card-desc">{t("settings.profile.defaultsDesc")}</p>
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
                  <Button onClick={() => void deleteProfileCalibration().catch(() => setFeedback(t("settings.feedback.profileDeleteFailed")))} size="compact" variant="primary">{t("settings.profile.delete")}</Button>
                ) : (
                  <Button disabled={!profileDirty || !profileHasInput} onClick={() => void saveProfileCalibration().catch(() => setFeedback(t("settings.feedback.profileSaveFailed")))} size="compact" variant="primary">{t("settings.profile.save")}</Button>
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
            <span className="task6-settings-section-title">{t("settings.nav.llmProvider")}</span>
            <span className="task6-settings-section-note">{t("settings.providerSection.note")}</span>
          </div>
          <ProviderSettingsSection
            catalog={catalog}
            loading={loading}
            notify={setFeedback}
            profiles={profiles}
            refresh={refresh}
          />
        </section>

        {/* 知识库（kb-sdk WP-12，线框 2026-09-20）：官方档置顶 + 已装包列表 + 导入向导。
            与 Provider 相邻——两者同属「Coach 回答的来源」（Provider 定模型、知识库定口径）。 */}
        <section className="task6-settings-section" hidden={activeNav !== "knowledge"} id="knowledge" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">{t("settings.nav.knowledge")}</span>
            <span className="task6-settings-section-note">{t("settings.knowledgeSection.note")}</span>
          </div>
          <KnowledgeSettingsSection notify={setFeedback} />
        </section>

        <section className="task6-settings-section" data-guidance-target="desktop.capture_control" hidden={activeNav !== "capture"} id="capture" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">{t("settings.nav.capture")}</span>
            <span className="task6-settings-section-note">{t("settings.capture.note")}</span>
          </div>
          {/* 0912 点点拍板：只留开关行（合并 KovaaK 状态）与最近采集事件；细节行退役。 */}
          <div className="task6-settings-subsection">
            <Panel>
              <div className="task6-settings-section-header">
                <h3 className="task6-profile-group-title">{t("settings.capture.title")}</h3>
                <span
                  aria-hidden="true"
                  className="task6-capture-status-dot"
                  data-issue={captureIssue || undefined}
                  title={captureIssue ? t("settings.capture.dotIssue") : undefined}
                />
              </div>
              <p className="task6-card-desc">{t("settings.capture.desc")}</p>
              {!desktop ? <Notice className="task6-settings-notice" tone="warning" title={t("settings.capture.browserTitle")}>{t("settings.capture.browserBody")}</Notice> : null}
              {desktop && capture === null ? <Loading>{t("settings.capture.loading")}</Loading> : null}
              {capture?.availability === "unavailable" ? <Notice className="task6-settings-notice" tone="error" title={t("settings.capture.unavailableTitle")}>{capture.error?.message ?? t("settings.capture.unavailableBody")}</Notice> : null}
              {capture ? (
                <div className="task6-toggle-rows">
                  <div className="task6-toggle-row">
                    <div className="task6-toggle-row-text">
                      <span className="task6-muted">
                        {t("settings.capture.togglePrefix")}{capture.capture_enabled == null ? "" : capture.capture_enabled ? t("settings.capture.standbySuffix") : t("settings.capture.offSuffix")}
                        {" · "}{captureLabel(capture.kovaak_process_present, t("settings.capture.processPresent"), t("settings.capture.processAbsent"))}
                      </span>
                    </div>
                    <div className="task6-toggle-row-side">
                      {desktop && capture.capture_enabled != null ? (
                        <Button
                          disabled={!capture.capture_enabled && !captureConsent}
                          onClick={() => void setDesktopCaptureEnabled(!capture.capture_enabled).then(() => refresh(true))}
                          variant="secondary"
                        >
                          {capture.capture_enabled ? t("settings.capture.turnOff") : t("settings.capture.enable")}
                        </Button>
                      ) : (
                        <span className={capture.capture_enabled ? "task6-ok" : undefined}>{captureLabel(capture.capture_enabled, t("settings.capture.standby"), t("settings.capture.off"))}</span>
                      )}
                    </div>
                  </div>
                  {desktop && capture.capture_enabled === false ? (
                    <label className="task6-consent">
                      <input checked={captureConsent} onChange={(event) => setCaptureConsent(event.target.checked)} type="checkbox" />
                      <span>{t("settings.capture.consent")}</span>
                    </label>
                  ) : null}
                </div>
              ) : null}
            </Panel>
          </div>
          {desktop && recentRuns.length > 0 ? (
            <div className="task6-settings-subsection">
              <Panel>
                <h3 className="task6-profile-group-title">{t("settings.capture.recentTitle")}</h3>
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
                    // i18n 批 1 解耦：状态归并吃结构化状态枚举，显示词只做显示。
                    const status = summarizeCaptureRunStatus(described.videoStatus, described.traceStatus);
                    return (
                      <li
                        className="task6-capture-event"
                        data-tone={status.tone}
                        key={run.run_ref}
                        title={t("settings.capture.runHoverTitle", { video: described.videoLabel, trace: described.traceLabel })}
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
              <Button disabled={diagnosticUploading} onClick={() => void uploadCaptureDiagnostics()} variant="primary">
                {diagnosticUploading ? t("settings.capture.uploading") : t("settings.capture.uploadButton")}
              </Button>
              <Button disabled={diagnosticExporting} onClick={() => void exportCaptureDiagnostics()} variant="secondary">
                {diagnosticExporting ? t("settings.capture.packingLogs") : t("settings.capture.exportLogs")}
              </Button>
              {diagnosticUploadId ? (
                <span className="task6-settings-section-hint">{t("settings.capture.lastUploadId", { id: diagnosticUploadId })}</span>
              ) : null}
              <span className="task6-settings-section-hint">{t("settings.capture.diagnosticsHint")}</span>
            </div>
          ) : null}
        </section>

        {/* KovaaK：本地目录 + KovaaKs 在线成绩 两张自包含卡（0912 去嵌套拍板）。 */}
        <section className="task6-settings-section" hidden={activeNav !== "kovaak"} id="kovaak" tabIndex={-1}>
          <div className="task6-settings-section-header">
            <span className="task6-settings-section-title">{t("settings.nav.kovaak")}</span>
            <span className="task6-settings-section-note">{t("settings.kovaakSection.note")}</span>
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
            <span className="task6-settings-section-title">{t("settings.nav.storage")}</span>
            <span className="task6-settings-section-note">{t("settings.storage.note")}</span>
          </div>
          {/* 0912 线框拍板：总占用与按条目清理拆成两张自包含卡。
              2026-09-28 在总占用卡之后加一张「存储位置」卡：数据根可迁到非系统盘。 */}
          <div className="task6-settings-subsection">
            <Panel>
            {!desktop ? <Notice className="task6-settings-notice" tone="warning" title={t("settings.storage.desktopOnlyTitle")}>{t("settings.storage.desktopOnlyBody")}</Notice> : null}
            {desktop && storage === null ? <Loading>{t("settings.storage.loading")}</Loading> : null}
            {storage ? (
              <>
                <div className="task6-storage-total">
                  <span className="task6-storage-total-number">{formatBytes(totalBytes)}</span>
                  <span className="task6-muted">{t("settings.storage.total")}</span>
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
          {desktop ? (
            <div className="task6-settings-subsection">
              <StorageLocationCard ask={ask} notify={setFeedback} />
            </div>
          ) : null}
          <div className="task6-settings-subsection">
            <Panel>
              <div className="task6-storage-cleanup">
                <h3 className="task6-profile-group-title">{t("settings.storage.cleanupTitle")}</h3>
                {cleanupRows.map(({ run, kinds, sizeBytes }) => {
                  const evidenceLabel =
                    kinds.length === 2 ? t("settings.storage.evidenceVideoRaw") : kinds[0] === "video" ? t("settings.storage.evidenceVideo") : t("settings.storage.evidenceRaw");
                  const facts = [evidenceLabel];
                  if (sizeBytes != null) facts.push(formatBytes(sizeBytes));
                  const dateText = formatDay(run.training_at ?? run.created_at);
                  if (dateText) facts.push(dateText);
                  const removalTitle =
                    kinds.length === 2 ? t("settings.storage.removeVideoRaw") : kinds[0] === "video" ? t("settings.storage.removeVideo") : t("settings.storage.removeRaw");
                  const removalImpact =
                    kinds.length === 2
                      ? t("settings.storage.impactVideoRaw")
                      : kinds[0] === "video"
                        ? t("settings.storage.impactVideo")
                        : t("settings.storage.impactRaw");
                  return (
                    <article className="task6-storage-row" key={run.run_ref}>
                      <div>
                        <strong>{run.scenario ?? t("settings.storage.unknownScenario")}</strong>
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
                          {t("settings.storage.openLocation")}
                        </Button>
                        <Button
                          className="task6-btn-danger-ghost"
                          onClick={() => ask(removalTitle, removalImpact, async () => {
                            for (const kind of kinds) await removeRunEvidence(run.id, kind);
                          })}
                          size="compact"
                          variant="ghost"
                        >
                          {t("settings.storage.remove")}
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
                        <strong>{t("settings.storage.incomplete")}</strong>
                        <p>{facts.join(" · ")}</p>
                      </div>
                      <div className="task6-inline-actions">
                        <Button
                          onClick={() => void reveal({ kind: "incomplete_capture", item_ref: item.item_ref })}
                          size="compact"
                          variant="ghost"
                        >
                          {t("settings.storage.openLocation")}
                        </Button>
                        <Button
                          className="task6-btn-danger-ghost"
                          disabled={!item.removable}
                          onClick={() => ask(t("settings.storage.removeIncompleteTitle"), item.impact.message, async () => { await removeIncompleteCapture(item.item_ref); })}
                          size="compact"
                          variant="ghost"
                        >
                          {t("settings.storage.remove")}
                        </Button>
                      </div>
                    </article>
                  );
                })}
                {desktop && cleanupRows.length === 0 && incomplete.length === 0 ? (
                  <p className="task6-muted">{t("settings.storage.emptyCleanup")}</p>
                ) : null}
              </div>
            </Panel>
          </div>
        </section>
        </div>
      </div>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">{t("settings.dialog.cancel")}</Button><Button onClick={() => void confirm()} variant="danger">{t("settings.dialog.confirm")}</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? t("settings.dialog.confirmTitle")}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
      {feedback ? <Toast onClose={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
