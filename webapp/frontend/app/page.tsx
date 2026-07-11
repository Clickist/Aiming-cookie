"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChangeEvent,
  DragEvent,
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { importDesktopPaths, uploadVideo } from "@/lib/api";
import {
  isDesktopRuntime,
  pickDesktopCsvPath,
  pickDesktopVideoPath,
} from "@/lib/desktop";
import { parseKovaaKConfig, type KovaaKConfigExtract } from "@/lib/csv";

/* ---- constants ---- */

/** Backend hard-limit on video size (matches routes.py MAX_VIDEO_BYTES). */
const MAX_VIDEO_BYTES = 100 * 1024 * 1024; // 100 MB
/** Client-side cap on CSV size (Stats export is tiny — 10 MB is generous). */
const MAX_CSV_BYTES = 10 * 1024 * 1024; // 10 MB

const DEFAULT_CM_PER_360 = 48;
const DEFAULT_FOV = 103;

/* ---- page ---- */

export default function HomePage() {
  const router = useRouter();
  const [desktopMode, setDesktopMode] = useState(false);

  // Browser files retain the existing multipart import flow.
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  // Desktop keeps native-selected absolute paths for path import.
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [csvPath, setCsvPath] = useState<string | null>(null);
  // KovaaK CSV config extract (FOV / DPI / Sens) — auto-read on CSV select,
  // used to fill the FOV field + show "已从 CSV 读取" indicator.
  const [csvExtracted, setCsvExtracted] = useState<KovaaKConfigExtract | null>(null);

  // numeric config
  const [cmPer360, setCmPer360] = useState<string>(String(DEFAULT_CM_PER_360));
  const [fov, setFov] = useState<string>(String(DEFAULT_FOV));

  // submission state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submissionRef = useRef(false);

  useEffect(() => {
    setDesktopMode(isDesktopRuntime());
  }, []);

  const clearError = () => setError(null);

  /* ---- validation ---- */

  const validateVideo = useCallback((file: File): string | null => {
    // Accept mp4 by MIME or extension. Browsers sometimes mislabel mp4 as
    // video/quicktime, so we don't require the MIME to be exactly video/mp4.
    const looksLikeMp4 =
      /\.mp4$/i.test(file.name) || file.type === "video/mp4";
    if (!looksLikeMp4) {
      return "视频必须是 mp4 文件";
    }
    if (file.size > MAX_VIDEO_BYTES) {
      return `视频超过 100MB 限制（当前 ${(file.size / 1024 / 1024).toFixed(1)} MB）`;
    }
    return null;
  }, []);

  const validateCsv = useCallback((file: File): string | null => {
    if (file.size > MAX_CSV_BYTES) {
      return `CSV 超过 10MB 限制（当前 ${(file.size / 1024 / 1024).toFixed(1)} MB）`;
    }
    return null;
  }, []);

  /* ---- handlers ---- */

  const handleVideoSelected = useCallback(
    (file: File | null | undefined) => {
      clearError();
      if (!file) return;
      const err = validateVideo(file);
      if (err) {
        setError(err);
        return;
      }
      setVideoFile(file);
    },
    [validateVideo],
  );

  const handleCsvChange = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      clearError();
      const file = e.target.files?.[0];
      if (!file) return;
      const err = validateCsv(file);
      if (err) {
        setError(err);
        return;
      }
      setCsvFile(file);
      // Auto-read FOV/DPI/Sens from CSV config block; fill FOV input on success.
      // cm/360 is NOT in the CSV (only DPI + Sens components; computed value
      // would be wrong for KovaaK's Valorant/other sens scales) — leave manual.
      try {
        const text = await file.text();
        const extract = parseKovaaKConfig(text);
        setCsvExtracted(extract);
        if (extract.fov !== undefined) {
          setFov(String(extract.fov));
        }
      } catch {
        // Parsing failure is non-fatal — user can still input manually.
        setCsvExtracted(null);
      }
    },
    [validateCsv],
  );

  const handleDesktopVideoPick = useCallback(async () => {
    clearError();
    try {
      const selected = await pickDesktopVideoPath();
      if (selected) setVideoPath(selected);
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  const handleDesktopCsvPick = useCallback(async () => {
    clearError();
    try {
      const selected = await pickDesktopCsvPath();
      if (selected) setCsvPath(selected);
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submissionRef.current) return;
    clearError();

    if (desktopMode) {
      if (!videoPath) {
        setError("请先选择视频文件");
        return;
      }
      if (!csvPath) {
        setError("请上传 KovaaK Stats CSV（必填）");
        return;
      }
    } else {
      if (!videoFile) {
        setError("请先选择视频文件");
        return;
      }
      if (!csvFile) {
        setError("请上传 KovaaK Stats CSV（必填）");
        return;
      }
    }

    submissionRef.current = true;
    setSubmitting(true);
    try {
      const res = desktopMode
        ? await importDesktopPaths({
            videoPath: videoPath!,
            csvPath: csvPath!,
            cmPer360: parseNumber(cmPer360),
            fov: parseNumber(fov),
          })
        : await uploadVideo(videoFile!, {
            csv: csvFile!,
            cmPer360: parseNumber(cmPer360),
            fov: parseNumber(fov),
          });
      router.push(`/sessions/${res.session_id}`);
    } catch (err) {
      submissionRef.current = false;
      setError(formatError(err));
      setSubmitting(false);
    }
  };

  /* ---- render ---- */

  return (
    <div className="min-h-dvh flex flex-col">
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline">
        <div className="flex items-center gap-sm">
          <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
            Aiming Cookie
          </span>
          <div className="h-4 w-px bg-outline mx-xs" />
          <span className="text-label-md text-on-surface-variant">
            Flicking Tension Analyzer
          </span>
        </div>
        <nav className="flex items-center gap-md">
          <Link
            href="/history"
            className="text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            历史记录
          </Link>
          <Link
            href="https://github.com"
            target="_blank"
            rel="noreferrer"
            className="text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            GitHub
          </Link>
        </nav>
      </header>

      <main className="flex-1 w-full max-w-[var(--spacing-container-max)] mx-auto px-md pt-xl pb-xl">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
          {/* Left column: hero + drop zone */}
          <section className="lg:col-span-8 flex flex-col gap-lg">
            <div>
              <h1 className="font-display text-display-lg text-on-surface tracking-tight">
                Analyze your flicking
              </h1>
              <p className="mt-xs text-body-lg text-on-surface-variant max-w-[36rem]">
                上传你的 KovaaK&apos;s 录像。系统会解析减速段张力、目标获取速度与微校正模式，并给出个性化诊断。
              </p>
            </div>

            {desktopMode ? (
              <DesktopVideoPicker
                path={videoPath}
                onPick={handleDesktopVideoPick}
              />
            ) : (
              <DropZone
                file={videoFile}
                onFile={handleVideoSelected}
                onError={clearError}
              />
            )}
          </section>

          {/* Right column: config + CTA */}
          <aside className="lg:col-span-4 flex flex-col gap-md">
            <form
              onSubmit={handleSubmit}
              className="bg-surface-container-low border border-outline rounded-lg p-md flex flex-col gap-md"
            >
              <div className="border-b border-outline pb-md">
                <h2 className="font-display text-headline-sm text-on-surface">
                  Analysis config
                </h2>
                <p className="mt-xs text-label-sm text-on-surface-variant">
                  校准参数以获得精确结果
                </p>
              </div>

              {/* KovaaK Stats CSV (required — backend hard-requires it) */}
              {desktopMode ? (
                <DesktopFilePicker
                  label="KovaaK Stats CSV（必填）"
                  hint={csvPath ? fileName(csvPath) : "未选择文件"}
                  onPick={handleDesktopCsvPick}
                />
              ) : (
                <FileField
                  id="csv"
                  label="KovaaK Stats CSV（必填）"
                  hint={csvFile ? csvFile.name : "未选择文件"}
                  accept=".csv,text/csv"
                  onChange={handleCsvChange}
                />
              )}

              {/* cm/360 — 用户填实测最准(公式对 KovaaK's Horiz Sens 单位敏感)。
                  后端 fallback:用户没填时 csv_parser 从 DPI + Horiz Sens + Sens Scale
                  用 yaw 表算(精度取决于 game yaw 已知)。接通到 worker → analyze_flicking_fair_summary。 */}
              <NumberField
                id="cm360"
                label="cm/360"
                value={cmPer360}
                onChange={setCmPer360}
                placeholder="e.g. 48"
              />
              <p className="text-label-sm text-on-surface-variant/70 -mt-xs">
                后端从 CSV 自动算(DPI + Horiz Sens + game yaw 表)。想最准:KovaaK&apos;s 里选 &quot;cm/360&quot; scale,Horiz Sens 直接是 cm/360。也可手填覆盖。
              </p>

              {/* FOV — auto-filled from CSV config block on CSV select */}
              <NumberField
                id="fov"
                label="FOV (Field of View)"
                value={fov}
                onChange={setFov}
                placeholder="e.g. 103"
              />
              {!desktopMode && csvExtracted && (
                <p className="text-label-sm text-on-surface-variant -mt-xs">
                  {csvExtracted.fov !== undefined ? (
                    <>
                      <span className="text-primary">✓ CSV 已读：</span>
                      FOV={csvExtracted.fov}
                      {csvExtracted.dpi !== undefined ? ` · DPI=${csvExtracted.dpi}` : ""}
                      {csvExtracted.horizSens !== undefined ? ` · Horiz=${csvExtracted.horizSens}` : ""}
                    </>
                  ) : (
                    <span className="text-on-surface-variant/70">
                      CSV 未含 FOV 字段，请手动填写
                    </span>
                  )}
                </p>
              )}

              {error && (
                <div
                  role="alert"
                  className="border border-outline-variant bg-surface-container-high text-on-surface rounded-md px-sm py-xs text-label-md"
                >
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={
                  submitting ||
                  (desktopMode ? !videoPath || !csvPath : !videoFile || !csvFile)
                }
                className="w-full bg-primary text-on-primary font-label-md py-md mt-sm rounded-md transition-colors hover:brightness-110 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting ? "提交中…" : "开始分析"}
              </button>
            </form>
          </aside>
        </div>
      </main>

      <footer className="bg-surface-container-low border-t border-outline-variant mt-auto">
        <div className="w-full py-xl px-md flex flex-col md:flex-row justify-between items-center gap-md max-w-[var(--spacing-container-max)] mx-auto">
          <div className="flex flex-col gap-xs items-center md:items-start">
            <span className="font-display text-headline-sm text-on-surface font-bold">
              Aiming Cookie
            </span>
            <p className="text-label-sm text-on-surface-variant">
              © 2026 Aiming Cookie. All rights reserved.
            </p>
          </div>
          <div className="flex gap-md">
            <button type="button" disabled className="text-label-sm text-on-surface-variant/60 cursor-not-allowed" title="待接通">
              Privacy Policy
            </button>
            <button type="button" disabled className="text-label-sm text-on-surface-variant/60 cursor-not-allowed" title="待接通">
              Terms of Service
            </button>
            <button type="button" disabled className="text-label-sm text-on-surface-variant/60 cursor-not-allowed" title="待接通">
              Contact
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}

/* ---- subcomponents (kept in-file — single-use, no third-party deps) ---- */

function DropZone({
  file,
  onFile,
  onError,
}: {
  file: File | null;
  onFile: (file: File | null | undefined) => void;
  onError: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const openPicker = () => inputRef.current?.click();

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    onError();
    setDragging(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    onFile(dropped);
  };

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    onFile(f);
    // allow re-selecting same file
    e.target.value = "";
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={openPicker}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openPicker();
        }
      }}
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={[
        "relative group border-2 border-dashed rounded-lg p-xl",
        "flex flex-col items-center justify-center min-h-[360px] cursor-pointer",
        "transition-colors",
        dragging
          ? "border-primary bg-surface-container-high"
          : "border-outline bg-surface-container-low hover:border-primary",
      ].join(" ")}
      aria-label="拖放视频文件或点击选择"
    >
      <input
        ref={inputRef}
        type="file"
        accept="video/mp4,video/*"
        onChange={handleChange}
        className="hidden"
      />

      <div className="flex flex-col items-center gap-md text-center">
        {file ? (
          <>
            <span className="text-headline-sm text-on-surface font-mono">
              {file.name}
            </span>
            <span className="text-label-md text-on-surface-variant">
              {(file.size / 1024 / 1024).toFixed(1)} MB · 点击替换
            </span>
          </>
        ) : (
          <>
            <span
              className={[
                "material-symbols-outlined text-[64px] leading-none transition-colors",
                dragging ? "text-primary" : "text-on-surface-variant",
              ].join(" ")}
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden
            >
              upload
            </span>
            <div>
              <p className="text-headline-sm text-on-surface">
                拖入 MP4 录像，或点击选择文件
              </p>
              <p className="mt-xs text-body-md text-on-surface-variant">
                上限 100MB · 推荐 1080p / 60fps
              </p>
            </div>
            <span className="border border-outline px-md py-xs text-label-md text-on-surface hover:bg-surface-container-high transition-colors">
              Select file
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  placeholder,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-xs">
      <label
        htmlFor={id}
        className="text-label-md text-on-surface"
      >
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        step="0.01"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="bg-transparent border-0 border-b border-outline py-xs text-on-surface focus:outline-none focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed"
      />
    </div>
  );
}

function DesktopVideoPicker({
  path,
  onPick,
}: {
  path: string | null;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      className="relative group border-2 border-dashed rounded-lg p-xl flex flex-col items-center justify-center min-h-[360px] cursor-pointer transition-colors border-outline bg-surface-container-low hover:border-primary"
      aria-label="选择 MP4 视频文件"
    >
      <div className="flex flex-col items-center gap-md text-center">
        {path ? (
          <>
            <span className="text-headline-sm text-on-surface font-mono break-all">
              {fileName(path)}
            </span>
            <span className="text-label-md text-on-surface-variant">
              已选择本地路径 · 点击替换
            </span>
          </>
        ) : (
          <>
            <span
              className="material-symbols-outlined text-[64px] leading-none text-on-surface-variant"
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden
            >
              upload
            </span>
            <div>
              <p className="text-headline-sm text-on-surface">
                选择 MP4 录像
              </p>
              <p className="mt-xs text-body-md text-on-surface-variant">
                原始文件只会复制到 Aiming Cookie 管理目录
              </p>
            </div>
            <span className="border border-outline px-md py-xs text-label-md text-on-surface group-hover:bg-surface-container-high transition-colors">
              Select file
            </span>
          </>
        )}
      </div>
    </button>
  );
}

function DesktopFilePicker({
  label,
  hint,
  onPick,
}: {
  label: string;
  hint: string;
  onPick: () => void;
}) {
  return (
    <div className="flex flex-col gap-xs">
      <span className="text-label-md text-on-surface">{label}</span>
      <button
        type="button"
        onClick={onPick}
        className="border border-outline px-sm py-xs flex items-center justify-between cursor-pointer hover:bg-surface-container-high transition-colors"
      >
        <span className="text-label-sm text-on-surface-variant truncate">{hint}</span>
        <span className="material-symbols-outlined text-label-md text-on-surface-variant">attach_file</span>
      </button>
    </div>
  );
}

function FileField({
  id,
  label,
  hint,
  accept,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  accept: string;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="flex flex-col gap-xs">
      <label
        htmlFor={id}
        className="text-label-md text-on-surface"
      >
        {label}
      </label>
      <label
        htmlFor={id}
        className="border border-outline px-sm py-xs flex items-center justify-between cursor-pointer hover:bg-surface-container-high transition-colors"
      >
        <span className="text-label-sm text-on-surface-variant truncate">
          {hint}
        </span>
        <span className="material-symbols-outlined text-label-md text-on-surface-variant">attach_file</span>
      </label>
      <input
        id={id}
        type="file"
        accept={accept}
        onChange={onChange}
        className="hidden"
      />
    </div>
  );
}

/* ---- helpers ---- */

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function parseNumber(s: string): number | undefined {
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
}

function formatError(err: unknown): string {
  // api.ts throws Errors whose .name is `ApiError_<status>` and .message is the
  // backend's JSON `detail` (or `<status> <statusText>` fallback). We just need
  // the human-readable message either way.
  if (err instanceof Error) {
    return err.message;
  }
  return "提交失败，请稍后重试";
}
