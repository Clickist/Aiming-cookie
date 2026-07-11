mod runtime;

use runtime::{project_root, RuntimeConnection, RuntimeProcess, RuntimeState};
use std::io;
use tauri::{Manager, State};

#[tauri::command]
fn desktop_runtime_connection(
    state: State<'_, RuntimeState>,
) -> Result<RuntimeConnection, String> {
    state.connection()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_data_dir = app.path().app_data_dir()?;
            let runtime = RuntimeProcess::start(&project_root(), &app_data_dir)
                .map_err(|message| io::Error::new(io::ErrorKind::Other, message))?;
            app.manage(RuntimeState::new(runtime));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![desktop_runtime_connection])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.app_handle().state::<RuntimeState>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Aiming Cookie desktop");
}
