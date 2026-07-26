mod capture_coordinator;
mod media_protocol;
mod raw_input;
mod runtime;
mod window_capture;

use capture_coordinator::{CaptureCoordinatorState, CaptureCoordinatorStatus};
use raw_input::{RawInputState, RawInputStatus};
use runtime::{project_root, RuntimeConnection, RuntimeProcess, RuntimeState};
use std::io;
use std::sync::{Arc, Mutex};
use tauri::{Manager, State};
use window_capture::{WindowCaptureState, WindowCaptureStatus, DEFAULT_FRAME_QUEUE_CAPACITY};

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
    tauri::Builder::default()
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
            let runtime = match coordinator.control_connection().and_then(|connection| {
                RuntimeProcess::start(&project_root(), &app_data_dir, &connection)
            }) {
                Ok(runtime) => runtime,
                Err(error) => {
                    coordinator.shutdown();
                    return Err(io::Error::other(error).into());
                }
            };
            app.manage(RuntimeState::new(runtime));
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
            desktop_capture_coordinator_set_enabled,
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
