// LogicLoom desktop shell.

pub mod sidecar;

use std::sync::Arc;

use sidecar::{
    SidecarState, get_api_port, get_api_token, get_sidecar_error, launch, record_launch_error,
    shutdown,
};
use tauri::{Emitter, Manager, RunEvent, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state: Arc<SidecarState> = Arc::new(SidecarState::default());

    let builder = tauri::Builder::default();
    // Must be the first plugin registered. A second launch exits right away and
    // this callback runs in the first instance instead.
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
    }));

    let app = builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![get_api_port, get_api_token, get_sidecar_error])
        .setup(|app| {
            let handle = app.handle().clone();
            // Launch the sidecar in a worker thread so setup() returns
            // promptly. The frontend awaits readiness via `get_api_port`.
            std::thread::spawn(move || match launch(handle.clone()) {
                Ok(port) => log::info!("logic-loom-api ready on port {port}"),
                Err(e) => {
                    log::error!("logic-loom-api failed to start: {e}");
                    record_launch_error(&handle, format!("{e}"));
                    let _ = handle.emit("logic-loom://error", format!("{e}"));
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                shutdown(&window.app_handle().clone());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            shutdown(app_handle);
        }
    });
}
