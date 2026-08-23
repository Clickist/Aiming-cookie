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
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
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
}

const DIAG_LOG_TAIL_BYTES: usize = 128 * 1024;
const DIAG_RECENT_RUNS_LIMIT: usize = 10;
const DIAG_EXPORT_RECEIPTS_LIMIT: usize = 10;

/// 读取日志文件尾部；`start > 0` 时丢弃可能被截断的首行，保证 UTF-8 合法。
fn log_tail(path: &PathBuf, max_bytes: usize) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let start = bytes.len().saturating_sub(max_bytes);
    let text = String::from_utf8_lossy(&bytes[start..]);
    let text: String = if start > 0 {
        match text.find('\n') {
            Some(newline) => text[newline + 1..].to_string(),
            None => return Some(String::new()),
        }
    } else {
        text.into_owned()
    };
    Some(text)
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
    let bundle = CaptureDiagnosticsBundle {
        schema_version: "capture_diagnostics.v2",
        generated_at_utc_ms: diagnostic_now_ms(),
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
    };
    let payload =
        serde_json::to_vec_pretty(&bundle).map_err(|error| format!("诊断包序列化失败: {error}"))?;
    fs::write(&path, payload).map_err(|error| format!("诊断包写入失败: {error}"))?;
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
        }
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
