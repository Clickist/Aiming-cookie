mod capture_coordinator;
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
use std::path::PathBuf;
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
    let window_status = window_capture
        .lock()
        .map_err(|_| "window capture state is unavailable".to_string())?
        .status();
    let bundle = CaptureDiagnosticsBundle {
        schema_version: "capture_diagnostics.v1",
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
        capture_data_root: coordinator.diagnostic_data_root(),
        coordinator: coordinator_status,
        raw_input: raw_input.status(),
        window_capture: window_status,
        events: coordinator.diagnostic_events(),
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
