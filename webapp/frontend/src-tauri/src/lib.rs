mod raw_input;
mod runtime;

use raw_input::{RawInputState, RawInputStatus};
use runtime::{project_root, RuntimeConnection, RuntimeProcess, RuntimeState};
use std::io;
use tauri::{Manager, State};

#[tauri::command]
fn desktop_runtime_connection(state: State<'_, RuntimeState>) -> Result<RuntimeConnection, String> {
    state.connection()
}

#[tauri::command]
fn desktop_raw_input_status(state: State<'_, RawInputState>) -> RawInputStatus {
    state.status()
}

#[tauri::command]
fn desktop_raw_input_set_enabled(
    enabled: bool,
    state: State<'_, RawInputState>,
) -> Result<RawInputStatus, String> {
    state.set_enabled(enabled)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_data_dir = app.path().app_data_dir()?;
            let runtime =
                RuntimeProcess::start(&project_root(), &app_data_dir).map_err(io::Error::other)?;
            app.manage(RuntimeState::new(runtime));
            let raw_input_path = app_data_dir.join("raw-input").join("buffer.bin");
            let raw_input = RawInputState::new(raw_input_path);
            if std::env::var("AIMING_COOKIE_RAW_INPUT_ENABLED")
                .map(|value| matches!(value.as_str(), "1" | "true" | "yes"))
                .unwrap_or(false)
            {
                raw_input.set_enabled(true).map_err(io::Error::other)?;
            }
            app.manage(raw_input);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_runtime_connection,
            desktop_raw_input_status,
            desktop_raw_input_set_enabled,
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.app_handle().state::<RuntimeState>().shutdown();
                window.app_handle().state::<RawInputState>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Aiming Cookie desktop");
}
