// Release builds on Windows run without a console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};

use tauri::Manager;

/// The UI's one entry point into the core; see `api::call`.
#[tauri::command]
async fn call(
    ctx: tauri::State<'_, api::Context>,
    op: String,
    input: serde_json::Value,
) -> Result<serde_json::Value, api::CallError> {
    api::call(&ctx, &op, input).await
}

fn main() -> tauri::Result<()> {
    tracing_subscriber::fmt::init();
    tauri::Builder::default()
        .setup(|app| {
            let dir = data_dir(app.handle())?;
            std::fs::create_dir_all(&dir)?;
            let db = tauri::async_runtime::block_on(db::open(&dir.join("app.db")))?;
            app.manage(api::Context { db });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![call])
        .run(tauri::generate_context!())
}

/// Development builds keep their data in the repository, away from an installed app's data.
fn data_dir(app: &tauri::AppHandle) -> tauri::Result<PathBuf> {
    if cfg!(debug_assertions) {
        Ok(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.dev-data"))
    } else {
        app.path().app_data_dir()
    }
}
