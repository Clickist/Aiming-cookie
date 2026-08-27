mod capture_coordinator;
mod diag_log;
mod media_protocol;
mod raw_input;
mod runtime;
mod scenario_launch;
mod window_capture;

use capture_coordinator::{
    bounded_diagnostic_text, CaptureCoordinatorState, CaptureCoordinatorStatus,
};
use raw_input::{RawInputState, RawInputStatus};
use runtime::{runtime_layout, RuntimeConnection, RuntimeProcess, RuntimeState};
use scenario_launch::scenario_open;
use std::fs;
use std::io;
#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{Manager, State};
use window_capture::{WindowCaptureState, WindowCaptureStatus, DEFAULT_FRAME_QUEUE_CAPACITY};

// GUI 进程没有控制台；spawn 控制台程序（cmd/powershell）时若不加此标志，
// Windows 会为子进程新建控制台窗口——安装版导出诊断包时黑窗一闪，引发用户恐慌。
#[cfg(windows)]
const NO_CHILD_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct CaptureDiagnosticsBundle {
    schema_version: &'static str,
    generated_at_utc_ms: i64,
    app_version: String,
    target_os: &'static str,
    target_arch: &'static str,
    host_version: Option<String>,
    processor_identifier: Option<String>,
    processor_count: Option<String>,
    gpu_names: Vec<String>,
    capture_data_root: String,
    coordinator: CaptureCoordinatorStatus,
    raw_input: RawInputStatus,
    window_capture: WindowCaptureStatus,
    events: Vec<capture_coordinator::CaptureDiagnosticEvent>,
    // v2：一次导出要能定位「局结束后视频/轨迹/分析怎么样了」，
    // 这些事实只存在于磁盘（日志、run meta、导出回执），实时状态里没有。
    native_log_tail: Option<String>,
    backend_log_tail: Option<String>,
    recent_runs: Vec<serde_json::Value>,
    export_receipts: Vec<serde_json::Value>,
    // v3：内测诊断包定位出判死现场缺上下文——日志健康度（最后一条
    // 时间戳距今多久，判断采集/后端是否还活着）、coach-error.log 尾部、
    // backend 轮转日志尾部、分析会话现场（状态/事件尾部/文件年龄——卡住
    // 的会话无 overview 且文件不更新，一眼可见）、最近 Coach 回合的
    // stopReason/errorMessage（resolve 形态半句话不写 coach-error.log，
    // 只存在会话 jsonl 里）。缺失/解析失败给 None/空，不阻塞导出。
    log_health: LogHealth,
    coach_error_log_tail: Option<String>,
    backend_log_rotated_tail: Option<String>,
    recent_analyses: Vec<RecentAnalysis>,
    recent_coach_turns: Vec<RecentCoachTurn>,
    // v4：watcher 是独立进程，快照只在其正常落盘时可用；读取失败不能阻塞导出。
    watcher_snapshot: Option<serde_json::Value>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct LogHealth {
    backend_log_age_seconds: Option<i64>,
    native_log_age_seconds: Option<i64>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct RecentAnalysis {
    id: u64,
    overview_status: Option<String>,
    completed_at: Option<String>,
    error_fields: std::collections::BTreeMap<String, String>,
    newest_file_age_seconds: Option<i64>,
    files: Vec<String>,
    events_tail: Option<String>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct RecentCoachTurn {
    conversation_id: u64,
    title: Option<String>,
    updated_at: Option<String>,
    last_stop_reason: Option<String>,
    last_error_message: Option<String>,
    jsonl_tail: Option<String>,
}

const DIAG_LOG_TAIL_BYTES: usize = 128 * 1024;
const DIAG_ROTATED_LOG_TAIL_BYTES: usize = 64 * 1024;
const DIAG_RECENT_RUNS_LIMIT: usize = 10;
const DIAG_EXPORT_RECEIPTS_LIMIT: usize = 10;
const DIAG_RECENT_ANALYSES_LIMIT: usize = 10;
const DIAG_RECENT_COACH_TURNS_LIMIT: usize = 3;
const DIAG_EVENTS_TAIL_BYTES: usize = 4 * 1024;
const DIAG_COACH_TURN_TAIL_BYTES: usize = 8 * 1024;
static DIAGNOSTIC_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// 读取日志文件尾部；`start > 0` 时优先丢弃被截断的首行，但窗口内整段
/// 无换行（Coach jsonl 单条 assistant 消息可超窗口大小）时原样返回窗口
/// ——stopReason 等尾部字段的诊断价值高于首行完整性。
fn log_tail(path: &PathBuf, max_bytes: usize) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let start = bytes.len().saturating_sub(max_bytes);
    let text = String::from_utf8_lossy(&bytes[start..]);
    let text: String = if start > 0 {
        match text.find('\n') {
            // 只有当换行后仍有内容才丢弃截断首行；窗口本身是
            // 「超长行尾巴 + 行尾换行」（Coach jsonl 单条消息超窗口）时
            // 整窗返回——尾部字段（stopReason 等）的诊断价值优先。
            Some(newline) if newline + 1 < text.len() => text[newline + 1..].to_string(),
            _ => text.into_owned(),
        }
    } else {
        text.into_owned()
    };
    Some(text)
}

/// 日志健康自检：native.log 尾行行首是 epoch ms；backend.log 尾部最后一行
/// 是 Python asctime 本地时间（`2026-08-25 14:38:17,831`），借本地 UTC
/// 偏移换算成 epoch 后与导出时刻比对。任一环节缺失/解析失败给 None。
fn collect_log_health(data_root: &Path, now_ms: i64, local_utc_offset: Option<i64>) -> LogHealth {
    let logs_dir = data_root.join("logs");
    let native_age = log_tail(&logs_dir.join("native.log"), DIAG_LOG_TAIL_BYTES)
        .as_deref()
        .and_then(last_native_log_epoch_ms)
        .map(|ms| (now_ms - ms) / 1000);
    let backend_age = log_tail(&logs_dir.join("backend.log"), DIAG_LOG_TAIL_BYTES)
        .as_deref()
        .and_then(last_backend_log_epoch_ms)
        .zip(local_utc_offset)
        .map(|(as_utc_ms, offset_ms)| (now_ms - (as_utc_ms - offset_ms)) / 1000);
    LogHealth {
        backend_log_age_seconds: backend_age,
        native_log_age_seconds: native_age,
    }
}

/// 本地时区与 UTC 的偏移（毫秒）。chrono/time 不在依赖里，借 PowerShell
/// 读取（与 gpu_names 同一模式）；失败返回 None → backend 日志年龄给 null。
fn local_utc_offset_ms() -> Option<i64> {
    #[cfg(windows)]
    {
        let output = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-Command",
                "[int][TimeZoneInfo]::Local.GetUtcOffset([DateTimeOffset]::Now).TotalMinutes",
            ])
            .creation_flags(NO_CHILD_WINDOW)
            .output()
            .ok()?;
        let minutes: i64 = String::from_utf8_lossy(&output.stdout)
            .trim()
            .parse()
            .ok()?;
        Some(minutes * 60_000)
    }
    #[cfg(not(windows))]
    None
}

fn last_backend_log_epoch_ms(tail: &str) -> Option<i64> {
    tail.lines().rev().find_map(parse_backend_log_line_epoch_ms)
}

fn parse_backend_log_line_epoch_ms(line: &str) -> Option<i64> {
    let digits = |range: std::ops::Range<usize>| line.get(range)?.parse::<i64>().ok();
    let separators = [
        (4, b'-'),
        (7, b'-'),
        (10, b' '),
        (13, b':'),
        (16, b':'),
        (19, b','),
    ];
    if separators
        .iter()
        .any(|(index, expected)| line.as_bytes().get(*index) != Some(expected))
    {
        return None;
    }
    let (year, month, day) = (digits(0..4)?, digits(5..7)?, digits(8..10)?);
    let (hour, minute, second) = (digits(11..13)?, digits(14..16)?, digits(17..19)?);
    let millis = digits(20..23)?;
    if !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || !(0..=23).contains(&hour)
        || !(0..=59).contains(&minute)
        || !(0..=60).contains(&second)
        || !(0..=999).contains(&millis)
    {
        return None;
    }
    Some(
        ((days_from_civil(year, month, day) * 24 + hour) * 60 + minute) * 60_000
            + second * 1000
            + millis,
    )
}

/// 公历日期 → 自 1970-01-01 起的天数（Howard Hinnant 的 days_from_civil）。
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month_prime = (month + 9) % 12;
    let day_of_year = (153 * month_prime + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

fn last_native_log_epoch_ms(tail: &str) -> Option<i64> {
    tail.lines().rev().find_map(parse_native_log_line_epoch_ms)
}

fn parse_native_log_line_epoch_ms(line: &str) -> Option<i64> {
    // diag_log 行格式：`{epoch_ms} {message}`；阈值排除行首小数字（非时间戳）。
    let token = line.split_whitespace().next()?;
    let value: i64 = token.parse().ok()?;
    (value > 1_000_000_000_000).then_some(value)
}

/// 诊断白名单：只导排障需要的终态字段，不含 user_id、capture_session_id
/// 和本地文件路径。
const RUN_META_FIELDS: &[&str] = &[
    "id",
    "created_at",
    "updated_at",
    "scenario",
    "source_key",
    "alignment_state",
    "alignment_summary",
    "window_start_epoch_ms",
    "window_end_epoch_ms",
    "video_state",
    "video_error",
    "video_summary",
    "trace_state",
    "trace_error",
    "finalization_state",
    "finalization_error",
];

fn run_id_from_dir_name(name: &str) -> Option<u64> {
    name.parse::<u64>().ok().filter(|id| *id > 0)
}

/// 按_run id 降序枚举 `runs/*/meta.json`，返回白名单摘要。
fn collect_recent_runs(data_root: &Path, limit: usize) -> Vec<serde_json::Value> {
    let mut run_ids: Vec<u64> = fs::read_dir(data_root.join("runs"))
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter_map(|entry| {
                    run_id_from_dir_name(entry.file_name().to_string_lossy().trim())
                })
                .collect()
        })
        .unwrap_or_default();
    run_ids.sort_unstable_by(|a, b| b.cmp(a));
    let mut summaries = Vec::new();
    for run_id in run_ids.into_iter().take(limit) {
        let Ok(meta_bytes) = fs::read(
            data_root
                .join("runs")
                .join(run_id.to_string())
                .join("meta.json"),
        ) else {
            continue;
        };
        let Ok(serde_json::Value::Object(meta)) = serde_json::from_slice(&meta_bytes) else {
            continue;
        };
        let mut summary = serde_json::Map::new();
        for field in RUN_META_FIELDS {
            if let Some(value) = meta.get(*field) {
                summary.insert((*field).to_string(), value.clone());
            }
        }
        summaries.push(serde_json::Value::Object(summary));
    }
    summaries
}

/// 按修改时间降序枚举 `runs/*/video-*.receipt.json`，抹除 capture_session_id
/// 并附上对应 mp4 的存在性与大小——「导出成功但 mp4 丢了」靠它区分。
fn collect_export_receipts(data_root: &Path, limit: usize) -> Vec<serde_json::Value> {
    let runs_root = data_root.join("runs");
    let mut receipts: Vec<(std::time::SystemTime, PathBuf)> = fs::read_dir(&runs_root)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .flat_map(|entry| {
                    let run_dir = entry.path();
                    if !run_dir.is_dir() {
                        return Vec::new();
                    }
                    fs::read_dir(&run_dir)
                        .map(|files| {
                            files
                                .filter_map(Result::ok)
                                .filter_map(|file| {
                                    let name = file.file_name().to_string_lossy().into_owned();
                                    if name.starts_with("video-") && name.ends_with(".receipt.json")
                                    {
                                        let mtime = file.metadata().ok()?.modified().ok()?;
                                        Some((mtime, run_dir.join(name)))
                                    } else {
                                        None
                                    }
                                })
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default()
                })
                .collect()
        })
        .unwrap_or_default();
    receipts.sort_unstable_by_key(|(mtime, _)| std::cmp::Reverse(*mtime));
    receipts
        .into_iter()
        .take(limit)
        .filter_map(|(_, path)| {
            let mut value: serde_json::Value =
                serde_json::from_slice(&fs::read(&path).ok()?).ok()?;
            let object = value.as_object_mut()?;
            object.remove("captureSessionId");
            let mp4 = path.with_file_name(
                path.file_name()?
                    .to_string_lossy()
                    .replace(".receipt.json", ".mp4"),
            );
            let mp4_meta = fs::metadata(&mp4).ok();
            object.insert(
                "mp4Exists".to_string(),
                serde_json::Value::Bool(mp4_meta.is_some()),
            );
            if let Some(meta) = mp4_meta {
                object.insert(
                    "mp4Bytes".to_string(),
                    serde_json::Value::Number(meta.len().into()),
                );
            }
            Some(value)
        })
        .collect()
}

fn diagnostic_now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}

/// 分析会话现场：analyses/<id>/ 与 sessions/<id>/ 的并集（只有 sessions
/// 目录、没有 overview 的即进行中/卡住的会话），按 id 降序取最近 N 个。
/// 「卡在第一步」类故障靠 newestFileAgeSeconds + 无 overview 直接可见。
fn collect_recent_analyses(data_root: &Path, limit: usize, now_ms: i64) -> Vec<RecentAnalysis> {
    let numeric_dir_ids = |dir: &Path| -> Vec<u64> {
        fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .filter_map(|entry| {
                        run_id_from_dir_name(entry.file_name().to_string_lossy().trim())
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    let mut ids: Vec<u64> = numeric_dir_ids(&data_root.join("analyses"));
    ids.extend(numeric_dir_ids(&data_root.join("sessions")));
    ids.sort_unstable_by(|a, b| b.cmp(a));
    ids.dedup();
    let mut summaries = Vec::new();
    for id in ids.into_iter().take(limit) {
        let analyses_dir = data_root.join("analyses").join(id.to_string());
        let sessions_dir = data_root.join("sessions").join(id.to_string());
        if !analyses_dir.is_dir() && !sessions_dir.is_dir() {
            continue;
        }
        let overview = fs::read(analyses_dir.join("overview.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
        let overview_object = overview.as_ref().and_then(|value| value.as_object());
        let str_field = |key: &str| -> Option<String> {
            overview_object
                .and_then(|object| object.get(key))
                .and_then(|value| value.as_str())
                .map(|text| text.to_string())
        };
        let mut error_fields = std::collections::BTreeMap::new();
        if let Some(object) = overview_object {
            for (key, value) in object {
                if key.contains("error") && value.is_string() {
                    error_fields
                        .insert(key.clone(), value.as_str().unwrap_or_default().to_string());
                }
            }
        }
        let mut files = Vec::new();
        let mut newest_mtime_ms: Option<i64> = None;
        for (prefix, dir) in [("", &sessions_dir), ("analyses/", &analyses_dir)] {
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.filter_map(Result::ok) {
                    let name = entry.file_name().to_string_lossy().into_owned();
                    files.push(format!("{prefix}{name}"));
                    if let Ok(mtime) = entry.metadata().and_then(|meta| meta.modified()) {
                        let ms = mtime
                            .duration_since(UNIX_EPOCH)
                            .map(|duration| duration.as_millis() as i64)
                            .unwrap_or(0);
                        newest_mtime_ms =
                            Some(newest_mtime_ms.map_or(ms, |prev: i64| prev.max(ms)));
                    }
                }
            }
        }
        files.sort();
        summaries.push(RecentAnalysis {
            id,
            overview_status: str_field("status"),
            completed_at: str_field("completed_at"),
            error_fields,
            newest_file_age_seconds: newest_mtime_ms.map(|ms| (now_ms - ms) / 1000),
            files,
            events_tail: log_tail(&analyses_dir.join("events.json"), DIAG_EVENTS_TAIL_BYTES),
        });
    }
    summaries
}

/// 最近 Coach 回合：会话 meta + 对应 jsonl 尾部的 stopReason/errorMessage。
/// resolve 形态的「半句话当完整答案」只记录在 jsonl 的 stopReason 里，
/// 不写 coach-error.log——没有它，Coach 类报障只能去用户机器翻会话文件。
fn collect_recent_coach_turns(data_root: &Path, limit: usize) -> Vec<RecentCoachTurn> {
    let conversations_dir = data_root.join("conversations");
    let mut entries: Vec<(u64, serde_json::Value)> = fs::read_dir(&conversations_dir)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter_map(|entry| {
                    let name = entry.file_name().to_string_lossy().into_owned();
                    let id = name.strip_suffix(".meta.json")?.parse::<u64>().ok()?;
                    let value =
                        serde_json::from_slice::<serde_json::Value>(&fs::read(entry.path()).ok()?)
                            .ok()?;
                    Some((id, value))
                })
                .collect()
        })
        .unwrap_or_default();
    entries.sort_unstable_by(|(a, _), (b, _)| b.cmp(a));
    let coach_dir = conversations_dir.join("--coach--");
    let mut turns = Vec::new();
    for (conversation_id, meta) in entries.into_iter().take(limit) {
        let str_field = |key: &str| -> Option<String> {
            meta.get(key)
                .and_then(|value| value.as_str())
                .map(|text| text.to_string())
        };
        let jsonl_name_suffix = format!("_{conversation_id}.jsonl");
        let jsonl_path = fs::read_dir(&coach_dir).ok().and_then(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .find(|path| {
                    path.file_name()
                        .map(|name| name.to_string_lossy().ends_with(&jsonl_name_suffix))
                        .unwrap_or(false)
                })
        });
        let jsonl_tail = jsonl_path
            .as_ref()
            .and_then(|path| log_tail(path, DIAG_COACH_TURN_TAIL_BYTES));
        turns.push(RecentCoachTurn {
            conversation_id,
            title: str_field("title"),
            updated_at: str_field("updated_at"),
            last_stop_reason: jsonl_tail
                .as_deref()
                .and_then(|tail| last_coach_json_string_field(tail, "stopReason")),
            last_error_message: jsonl_tail
                .as_deref()
                .and_then(|tail| last_coach_json_string_field(tail, "errorMessage")),
            jsonl_tail,
        });
    }
    turns
}

/// Coach jsonl 的尾部可能从半行开始，或包含被截断/恶意内容。只接受完整的
/// 单行 JSON 记录，按时间倒序取最后一个 assistant message 的字段。
fn last_coach_json_string_field(tail: &str, key: &str) -> Option<String> {
    tail.lines().rev().find_map(|line| {
        let record = serde_json::from_str::<serde_json::Value>(line).ok()?;
        record.get("message")?.get(key)?.as_str().map(str::to_owned)
    })
}

fn collect_watcher_snapshot(data_root: &Path) -> Option<serde_json::Value> {
    fs::read(data_root.join("diagnostics").join("kovaak-watcher.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
}

fn diagnostic_temp_path(path: &Path) -> PathBuf {
    let file_name = path.file_name().unwrap_or_default().to_string_lossy();
    path.with_file_name(format!(
        ".{file_name}.tmp-{}-{}-{}",
        std::process::id(),
        diagnostic_now_ms(),
        DIAGNOSTIC_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ))
}

fn atomic_replace(source: &Path, destination: &Path) -> io::Result<()> {
    #[cfg(windows)]
    {
        use winapi::um::winbase::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};

        let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
        let destination_wide: Vec<u16> = destination
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        if unsafe {
            MoveFileExW(
                source_wide.as_ptr(),
                destination_wide.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        fs::rename(source, destination)
    }
}

fn atomic_write_diagnostic_bundle(path: &Path, payload: &[u8]) -> io::Result<()> {
    let temporary_path = diagnostic_temp_path(path);
    fs::write(&temporary_path, payload)?;
    match atomic_replace(&temporary_path, path) {
        Ok(()) => Ok(()),
        Err(error) => {
            let _ = fs::remove_file(&temporary_path);
            Err(error)
        }
    }
}

fn host_version() -> Option<String> {
    #[cfg(windows)]
    let output = std::process::Command::new("cmd")
        .args(["/C", "ver"])
        .creation_flags(NO_CHILD_WINDOW)
        .output()
        .ok()?;
    #[cfg(target_os = "macos")]
    let output = std::process::Command::new("sw_vers")
        .arg("-productVersion")
        .output()
        .ok()?;
    #[cfg(all(not(windows), not(target_os = "macos")))]
    let output = return None;

    let value = String::from_utf8_lossy(&output.stdout);
    let value = bounded_diagnostic_text(value.trim());
    (!value.is_empty()).then_some(value)
}

// WGC 视频采集失败的常见根因是显卡/驱动，诊断包需要 GPU 型号。
// wmic 在 Win11 24H2 起被移除，走 PowerShell CIM（Win10/11 通用）；
// 双卡（核显+独显）机型每个适配器一行，全列。
fn gpu_names() -> Vec<String> {
    #[cfg(windows)]
    {
        let output = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; (Get-CimInstance Win32_VideoController).Name",
            ])
            .creation_flags(NO_CHILD_WINDOW)
            .output();
        match output {
            Ok(output) => String::from_utf8_lossy(&output.stdout)
                .lines()
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .map(bounded_diagnostic_text)
                .collect(),
            Err(_) => Vec::new(),
        }
    }
    #[cfg(not(windows))]
    Vec::new()
}

#[tauri::command]
fn desktop_runtime_connection(state: State<'_, RuntimeState>) -> Result<RuntimeConnection, String> {
    state.connection()
}

#[tauri::command]
fn desktop_raw_input_status(state: State<'_, Arc<RawInputState>>) -> RawInputStatus {
    state.status()
}

#[tauri::command]
fn desktop_window_capture_status(
    state: State<'_, Arc<Mutex<WindowCaptureState>>>,
) -> Result<WindowCaptureStatus, String> {
    state
        .lock()
        .map_err(|_| "window capture state is unavailable".to_string())
        .map(|state| state.status())
}

#[tauri::command]
fn desktop_capture_coordinator_status(
    state: State<'_, Arc<CaptureCoordinatorState>>,
) -> CaptureCoordinatorStatus {
    state.status()
}

#[tauri::command]
fn desktop_export_capture_diagnostics(
    app: tauri::AppHandle,
    path: String,
    coordinator: State<'_, Arc<CaptureCoordinatorState>>,
    raw_input: State<'_, Arc<RawInputState>>,
    window_capture: State<'_, Arc<Mutex<WindowCaptureState>>>,
) -> Result<String, String> {
    let path = PathBuf::from(path.trim());
    if !path.is_absolute() {
        return Err("诊断包保存路径必须是绝对路径".to_string());
    }
    let mut coordinator_status = coordinator.status();
    // The session id is an internal correlation secret and is not needed by support.
    coordinator_status.capture_session_id = None;
    let data_root = PathBuf::from(coordinator.diagnostic_data_root());
    let window_status = window_capture
        .lock()
        .map_err(|_| "window capture state is unavailable".to_string())?
        .status();
    let now_ms = diagnostic_now_ms();
    let bundle = CaptureDiagnosticsBundle {
        schema_version: "capture_diagnostics.v4",
        generated_at_utc_ms: now_ms,
        app_version: app.package_info().version.to_string(),
        target_os: std::env::consts::OS,
        target_arch: std::env::consts::ARCH,
        host_version: host_version(),
        processor_identifier: std::env::var("PROCESSOR_IDENTIFIER")
            .ok()
            .map(|value| bounded_diagnostic_text(&value)),
        processor_count: std::env::var("NUMBER_OF_PROCESSORS")
            .ok()
            .map(|value| bounded_diagnostic_text(&value)),
        gpu_names: gpu_names(),
        capture_data_root: data_root.to_string_lossy().into_owned(),
        coordinator: coordinator_status,
        raw_input: raw_input.status(),
        window_capture: window_status,
        events: coordinator.diagnostic_events(),
        native_log_tail: log_tail(
            &data_root.join("logs").join("native.log"),
            DIAG_LOG_TAIL_BYTES,
        ),
        backend_log_tail: log_tail(
            &data_root.join("logs").join("backend.log"),
            DIAG_LOG_TAIL_BYTES,
        ),
        recent_runs: collect_recent_runs(&data_root, DIAG_RECENT_RUNS_LIMIT),
        export_receipts: collect_export_receipts(&data_root, DIAG_EXPORT_RECEIPTS_LIMIT),
        log_health: collect_log_health(&data_root, now_ms, local_utc_offset_ms()),
        coach_error_log_tail: log_tail(&data_root.join("coach-error.log"), DIAG_LOG_TAIL_BYTES),
        backend_log_rotated_tail: log_tail(
            &data_root.join("logs").join("backend.log.1"),
            DIAG_ROTATED_LOG_TAIL_BYTES,
        ),
        recent_analyses: collect_recent_analyses(&data_root, DIAG_RECENT_ANALYSES_LIMIT, now_ms),
        recent_coach_turns: collect_recent_coach_turns(&data_root, DIAG_RECENT_COACH_TURNS_LIMIT),
        watcher_snapshot: collect_watcher_snapshot(&data_root),
    };
    let payload =
        serde_json::to_vec_pretty(&bundle).map_err(|error| format!("诊断包序列化失败: {error}"))?;
    atomic_write_diagnostic_bundle(&path, &payload)
        .map_err(|error| format!("诊断包写入失败: {error}"))?;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
fn desktop_capture_coordinator_set_enabled(
    enabled: bool,
    state: State<'_, Arc<CaptureCoordinatorState>>,
) -> Result<CaptureCoordinatorStatus, String> {
    state.set_enabled(enabled)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let managed_media = Arc::new(media_protocol::ManagedMediaProtocol::default());
    let media_handler = Arc::clone(&managed_media);
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }
    builder
        .register_uri_scheme_protocol("aiming-cookie-media", move |_context, request| {
            media_handler.response(request)
        })
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let app_data_dir = app.path().app_data_dir()?;
            diag_log::init(app_data_dir.join("logs"));
            managed_media
                .configure(app_data_dir.clone())
                .map_err(io::Error::other)?;
            let raw_input_path = app_data_dir.join("raw-input").join("buffer.bin");
            let raw_input = Arc::new(RawInputState::new(raw_input_path));
            let window_capture = Arc::new(Mutex::new(
                WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).map_err(io::Error::other)?,
            ));
            let coordinator = CaptureCoordinatorState::new(
                app_data_dir.clone(),
                Arc::clone(&raw_input),
                Arc::clone(&window_capture),
            )
            .map_err(io::Error::other)?;
            let runtime_layout =
                runtime_layout(&app.path().resource_dir()?).map_err(io::Error::other)?;
            let capture_control = coordinator.control_connection().map_err(io::Error::other)?;
            let runtime =
                match RuntimeProcess::start(&runtime_layout, &app_data_dir, &capture_control) {
                    Ok(runtime) => runtime,
                    Err(error) => {
                        coordinator.shutdown();
                        return Err(io::Error::other(error).into());
                    }
                };
            app.manage(RuntimeState::new(
                runtime,
                runtime_layout,
                app_data_dir,
                capture_control,
            ));
            if std::env::var("AIMING_COOKIE_RAW_INPUT_ENABLED")
                .map(|value| matches!(value.as_str(), "1" | "true" | "yes"))
                .unwrap_or(false)
            {
                raw_input.set_enabled(true).map_err(io::Error::other)?;
            }
            app.manage(raw_input);
            app.manage(window_capture);
            app.manage(coordinator);
            // 无边框窗口（decorations:false）在 Windows 上默认是直角；显式请求
            // DWM 画圆角（Win11+，dwmapi.dll）。失败（如 Win10 不支持该属性）
            // 静默忽略——圆角是渐进增强，不能阻塞启动。
            #[cfg(target_os = "windows")]
            {
                use windows::Win32::Graphics::Dwm::{
                    DwmSetWindowAttribute, DWMWA_WINDOW_CORNER_PREFERENCE,
                    DWM_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
                };
                if let Some(window) = app.get_webview_window("main") {
                    if let Ok(hwnd) = window.hwnd() {
                        let preference = DWMWCP_ROUND;
                        unsafe {
                            let _ = DwmSetWindowAttribute(
                                hwnd,
                                DWMWA_WINDOW_CORNER_PREFERENCE,
                                &preference as *const DWM_WINDOW_CORNER_PREFERENCE as *const _,
                                std::mem::size_of::<DWM_WINDOW_CORNER_PREFERENCE>() as u32,
                            );
                        }
                    }
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_runtime_connection,
            desktop_raw_input_status,
            desktop_window_capture_status,
            desktop_capture_coordinator_status,
            desktop_export_capture_diagnostics,
            desktop_capture_coordinator_set_enabled,
            scenario_open,
        ])
        .on_window_event(|window, event| {
            if matches!(
                event,
                tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed
            ) {
                window.app_handle().state::<RuntimeState>().shutdown();
                window
                    .app_handle()
                    .state::<Arc<CaptureCoordinatorState>>()
                    .shutdown();
                window.app_handle().state::<Arc<RawInputState>>().shutdown();
                if let Ok(mut capture) = window
                    .app_handle()
                    .state::<Arc<Mutex<WindowCaptureState>>>()
                    .lock()
                {
                    capture.stop();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Aiming Cookie desktop");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch_data_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-diag-bundle-{}-{name}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        root
    }

    #[test]
    fn log_tail_returns_last_bytes_and_drops_partial_first_line() {
        let root = scratch_data_root("tail");
        fs::create_dir_all(&root).expect("mkdir");
        let log = root.join("native.log");
        fs::write(&log, "aaaa\nbbbb\ncccc\ndddd\n").expect("write");
        let tail = log_tail(&log, 15).expect("tail");
        assert_eq!(tail, "cccc\ndddd\n");
        assert_eq!(log_tail(&log, 10).as_deref(), Some("dddd\n"));
        assert_eq!(
            log_tail(&log, usize::MAX).as_deref(),
            Some("aaaa\nbbbb\ncccc\ndddd\n")
        );
        assert_eq!(log_tail(&root.join("missing.log"), 10), None);
        // 窗口内无换行（超长单行，如 Coach jsonl 的大消息）→ 原样返回窗口而非空串。
        fs::write(&log, format!("{}\n{}", "h".repeat(30), "x".repeat(20))).expect("write");
        assert_eq!(log_tail(&log, 10).as_deref(), Some("xxxxxxxxxx"));
        // 窗口 = 超长行尾巴 + 行尾换行（首个换行在窗口头部）→ 切完仍是整行。
        fs::write(&log, format!("{}\n{}", "h".repeat(30), "x".repeat(29))).expect("write");
        assert_eq!(log_tail(&log, 30).as_deref(), Some(&"x".repeat(29)[..]));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn collect_recent_runs_sorts_by_id_desc_and_whitelists_fields() {
        let root = scratch_data_root("runs");
        for (run_id, video_state) in [(3u64, "attached"), (1, "pending"), (10, "unavailable")] {
            let run_dir = root.join("runs").join(run_id.to_string());
            fs::create_dir_all(&run_dir).expect("mkdir");
            fs::write(
                run_dir.join("meta.json"),
                serde_json::json!({
                    "id": run_id,
                    "video_state": video_state,
                    "video_error": "video_capture_unavailable",
                    "window_start_epoch_ms": 100,
                    "window_end_epoch_ms": 200,
                    "capture_session_id": "secret",
                    "user_id": "user-1",
                    "mouse_trace_path": "C:/trace.bin",
                    "scenario": "test scenario",
                    "alignment_summary": {
                        "timebase_version": "time_alignment.v2",
                        "error_code": "anchor_conflict",
                        "stats_challenge_start": "01:46:41.321",
                        "performance_challenge_start_utc": 1_699_897_600_000i64,
                    },
                })
                .to_string(),
            )
            .expect("write");
        }
        let runs = collect_recent_runs(&root, 2);
        assert_eq!(runs.len(), 2);
        assert_eq!(runs[0]["id"], serde_json::json!(10));
        assert_eq!(runs[1]["id"], serde_json::json!(3));
        for run in &runs {
            let object = run.as_object().expect("object");
            assert!(object.contains_key("video_state"));
            assert!(!object.contains_key("capture_session_id"));
            assert!(!object.contains_key("user_id"));
            assert!(!object.contains_key("mouse_trace_path"));
            // v3：判死 run 的对齐摘要（error_code + 两个锚点原始值）进包。
            assert_eq!(
                run["alignment_summary"]["error_code"],
                serde_json::json!("anchor_conflict")
            );
            assert_eq!(
                run["alignment_summary"]["performance_challenge_start_utc"],
                serde_json::json!(1_699_897_600_000i64)
            );
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn collect_recent_analyses_reports_status_files_and_inflight_sessions() {
        let root = scratch_data_root("analyses");
        // 完成的会话：overview + events + 工作区文件
        for (dir, file, content) in [
            (
                root.join("analyses/7"),
                "overview.json",
                r#"{"status":"done","completed_at":"2026-08-25T11:00:00Z","source_error":null}"#,
            ),
            (
                root.join("analyses/7"),
                "events.json",
                r#"{"event":"created"}{"event":"done"}"#,
            ),
            (root.join("sessions/7"), "video.mp4", "x"),
            // 卡住的会话：只有工作区、无 overview
            (root.join("sessions/8"), "video.mp4", "x"),
        ] {
            fs::create_dir_all(&dir).expect("mkdir");
            fs::write(dir.join(file), content).expect("write");
        }
        let now_ms = diagnostic_now_ms();
        let mut analyses = collect_recent_analyses(&root, 10, now_ms);
        analyses.sort_by_key(|item| item.id);
        assert_eq!(analyses.len(), 2);
        let done = &analyses[0];
        assert_eq!(done.id, 7);
        assert_eq!(done.overview_status.as_deref(), Some("done"));
        assert_eq!(done.completed_at.as_deref(), Some("2026-08-25T11:00:00Z"));
        // source_error 是 null（非字符串），不进 error_fields
        assert!(done.error_fields.is_empty());
        assert!(done.files.iter().any(|name| name == "analyses/events.json"));
        assert!(done.events_tail.as_deref().unwrap_or("").contains("done"));
        let inflight = &analyses[1];
        assert_eq!(inflight.id, 8);
        assert_eq!(inflight.overview_status, None);
        assert_eq!(inflight.events_tail, None);
        // 文件是刚写的，年龄必须是小的正数
        let age = inflight.newest_file_age_seconds.expect("age");
        assert!((0..60).contains(&age), "age {age}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn collect_recent_coach_turns_extracts_stop_reason_and_error() {
        let root = scratch_data_root("coach");
        fs::create_dir_all(root.join("conversations/--coach--")).expect("mkdir");
        fs::write(
            root.join("conversations/53.meta.json"),
            r#"{"id":53,"title":"灵敏度","updated_at":"2026-08-25T10:41:00Z"}"#,
        )
        .expect("write");
        fs::write(
            root.join("conversations/--coach--/2026-08-25T10-40-39-787Z_53.jsonl"),
            concat!(
                "{\"type\":\"session\"}\n",
                "{\"type\":\"message\",\"message\":{\"role\":\"user\",\"content\":\"hi\"}}\n",
                "{\"type\":\"message\",\"message\":{\"role\":\"assistant\",\"stopReason\":\"stop\",\"content\":[{\"type\":\"text\",\"text\":\"done\"}]}}\n",
            ),
        )
        .expect("write");
        let turns = collect_recent_coach_turns(&root, 3);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].conversation_id, 53);
        assert_eq!(turns[0].title.as_deref(), Some("灵敏度"));
        assert_eq!(turns[0].last_stop_reason.as_deref(), Some("stop"));
        assert_eq!(turns[0].last_error_message, None);
        // resolve 形态：stopReason=error + errorMessage，必须被尾部提取逮住
        fs::write(
            root.join("conversations/--coach--/2026-08-25T10-40-39-787Z_53.jsonl"),
            r#"{"type":"message","message":{"role":"assistant","stopReason":"error","errorMessage":"terminated"}}"#,
        )
        .expect("rewrite");
        let turns = collect_recent_coach_turns(&root, 3);
        assert_eq!(turns[0].last_stop_reason.as_deref(), Some("error"));
        assert_eq!(turns[0].last_error_message.as_deref(), Some("terminated"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn coach_jsonl_field_extraction_accepts_only_complete_jsonl_records() {
        let tail = concat!(
            "{\"message\":{\"stopReason\":\"old\"}}\n",
            "{\"message\":{\"stopReason\":\"new\",\"errorMessage\":\"escaped \\\"quote\\\"\"}}\n",
            r#"{"message":{"stopReason":"hostile"}"#,
        );
        assert_eq!(
            last_coach_json_string_field(tail, "stopReason"),
            Some("new".to_string())
        );
        assert_eq!(
            last_coach_json_string_field(tail, "errorMessage"),
            Some("escaped \"quote\"".to_string())
        );
        assert_eq!(
            last_coach_json_string_field("no json here", "stopReason"),
            None
        );
        assert_eq!(
            last_coach_json_string_field(r#"{"a":1}"#, "stopReason"),
            None
        );
    }

    #[test]
    fn watcher_snapshot_is_optional_and_must_be_valid_json() {
        let root = scratch_data_root("watcher-snapshot");
        let snapshot = root.join("diagnostics/kovaak-watcher.json");
        assert_eq!(collect_watcher_snapshot(&root), None);
        fs::create_dir_all(snapshot.parent().expect("parent")).expect("mkdir");
        fs::write(&snapshot, "not json").expect("write malformed");
        assert_eq!(collect_watcher_snapshot(&root), None);
        fs::write(&snapshot, r#"{"state":"watching"}"#).expect("write valid");
        assert_eq!(
            collect_watcher_snapshot(&root),
            Some(serde_json::json!({"state":"watching"}))
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn atomic_diagnostic_write_replaces_existing_bundle_without_temp_artifacts() {
        let root = scratch_data_root("atomic-write");
        fs::create_dir_all(&root).expect("mkdir");
        let destination = root.join("diagnostics.json");
        fs::write(&destination, "old bundle").expect("seed destination");
        atomic_write_diagnostic_bundle(&destination, b"new bundle").expect("atomic write");
        assert_eq!(
            fs::read(&destination).expect("read destination"),
            b"new bundle"
        );
        assert_eq!(
            fs::read_dir(&root).expect("read root").count(),
            1,
            "temporary diagnostic bundle must not remain"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn parse_backend_log_line_epoch_ms_matches_python_asctime() {
        assert_eq!(
            parse_backend_log_line_epoch_ms(
                "2026-08-25 14:38:17,831 INFO webapp.backend.kovaak_run_store message",
            ),
            Some(1_787_668_697_831)
        );
        assert_eq!(
            parse_backend_log_line_epoch_ms("2024-02-29 23:59:59,999 WARNING leap"),
            Some(1_709_251_199_999)
        );
        assert_eq!(
            parse_backend_log_line_epoch_ms("1970-01-01 00:00:00,000"),
            Some(0)
        );
        assert_eq!(parse_backend_log_line_epoch_ms("not a timestamp"), None);
        assert_eq!(
            parse_backend_log_line_epoch_ms("2026-13-01 00:00:00,000"),
            None
        );
        // 缺毫秒段（截断行）不算可解析时间戳。
        assert_eq!(parse_backend_log_line_epoch_ms("2026-08-25 14:38:17"), None);
    }

    #[test]
    fn last_line_wins_and_skips_unparseable_lines() {
        // 尾行无时间戳 → 向前找最近一条可解析行。
        let backend = "2023-11-13 17:46:40,000 WARNING old\nTRACE tail-without-ts\n";
        assert_eq!(last_backend_log_epoch_ms(backend), Some(1_699_897_600_000));
        assert_eq!(last_backend_log_epoch_ms(""), None);
        assert_eq!(last_backend_log_epoch_ms("garbage\nstill garbage\n"), None);
        let native = "1750000000000 capture-export: ok\n0 weird line\n";
        assert_eq!(last_native_log_epoch_ms(native), Some(1_750_000_000_000));
        assert_eq!(
            last_native_log_epoch_ms("1_750_000_000_000 old\nabc def\n"),
            None
        );
        assert_eq!(last_native_log_epoch_ms(""), None);
    }

    #[test]
    fn collect_log_health_computes_ages_from_scratch_data_root() {
        let root = scratch_data_root("health");
        let logs = root.join("logs");
        fs::create_dir_all(&logs).expect("mkdir");
        let now_ms = 1_787_669_000_000i64;
        fs::write(
            logs.join("native.log"),
            format!("{} native alive\n", now_ms - 5_000),
        )
        .expect("write native");
        // backend asctime 是本地时间：now-30s 的 epoch + UTC+8 偏移的墙钟。
        fs::write(
            logs.join("backend.log"),
            "2026-08-25 22:42:50,000 INFO webapp.backend alive\n",
        )
        .expect("write backend");
        let health = collect_log_health(&root, now_ms, Some(28_800_000));
        assert_eq!(health.native_log_age_seconds, Some(5));
        assert_eq!(health.backend_log_age_seconds, Some(30));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn collect_log_health_missing_logs_or_offset_yield_null() {
        let root = scratch_data_root("health-missing");
        fs::create_dir_all(root.join("logs")).expect("mkdir");
        fs::write(
            root.join("logs").join("backend.log"),
            "2026-08-25 22:42:50,000 INFO x\n",
        )
        .expect("write backend");
        // 缺本地偏移 → backend 年龄给 null；缺 native.log → null。
        let no_offset = collect_log_health(&root, 1_787_669_000_000, None);
        assert_eq!(no_offset.backend_log_age_seconds, None);
        assert_eq!(no_offset.native_log_age_seconds, None);
        let empty_root = scratch_data_root("health-empty");
        let empty = collect_log_health(&empty_root, 1_787_669_000_000, Some(0));
        assert_eq!(empty.backend_log_age_seconds, None);
        assert_eq!(empty.native_log_age_seconds, None);
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&empty_root);
    }

    #[test]
    fn v3_tail_fields_read_coach_error_and_rotated_logs() {
        let root = scratch_data_root("v3-tails");
        fs::create_dir_all(root.join("logs")).expect("mkdir");
        fs::write(root.join("coach-error.log"), "coach error line\n").expect("write");
        fs::write(root.join("logs").join("backend.log.1"), "rotated line\n").expect("write");
        assert_eq!(
            log_tail(&root.join("coach-error.log"), DIAG_LOG_TAIL_BYTES).as_deref(),
            Some("coach error line\n")
        );
        assert_eq!(
            log_tail(
                &root.join("logs").join("backend.log.1"),
                DIAG_ROTATED_LOG_TAIL_BYTES,
            )
            .as_deref(),
            Some("rotated line\n")
        );
        assert_eq!(log_tail(&root.join("missing-coach-error.log"), 10), None);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn collect_export_receipts_strips_session_id_and_checks_mp4() {
        let root = scratch_data_root("receipts");
        let run_dir = root.join("runs").join("42");
        fs::create_dir_all(&run_dir).expect("mkdir");
        let receipt_path = run_dir.join("video-req-1.receipt.json");
        fs::write(
            &receipt_path,
            serde_json::json!({
                "version": "capture_receipt.v1",
                "runId": 42,
                "captureSessionId": "secret",
                "startEpochMs": 1,
                "endEpochMs": 2,
            })
            .to_string(),
        )
        .expect("write receipt");
        fs::write(run_dir.join("video-req-1.mp4"), vec![b'x'; 7]).expect("write mp4");
        fs::write(run_dir.join("video-req-2.receipt.json"), "not json").expect("write bad receipt");

        let receipts = collect_export_receipts(&root, 10);
        assert_eq!(receipts.len(), 1);
        assert!(receipts[0].get("captureSessionId").is_none());
        assert_eq!(receipts[0]["mp4Exists"], serde_json::json!(true));
        assert_eq!(receipts[0]["mp4Bytes"], serde_json::json!(7));
        assert_eq!(receipts[0]["runId"], serde_json::json!(42));
        let _ = fs::remove_dir_all(&root);
    }
}
