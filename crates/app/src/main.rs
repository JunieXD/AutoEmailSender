// Release builds on Windows run without a console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod logs;

use std::error::Error;
use std::fs::{File, TryLockError};
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

/// The UI's one entry point into the core; see `api::call`.
#[tauri::command]
async fn call(
    ctx: tauri::State<'_, api::Context>,
    op: String,
    input: serde_json::Value,
) -> Result<serde_json::Value, api::CallError> {
    api::call(&ctx, &op, input).await
}

/// What the UI shows in place of the app when the core could not start.
struct StartupFailure(String);

/// The reason the core could not start, or None when it is running.
#[tauri::command]
fn startup_failure(app: AppHandle) -> Option<String> {
    app.try_state::<StartupFailure>().map(|f| f.0.clone())
}

/// Saves the logs where the user picks; false when they cancel.
#[tauri::command]
async fn export_logs(app: AppHandle) -> Result<bool, String> {
    let dir = data_dir(&app)
        .map_err(|e| format!("日志未能导出：{e}"))?
        .join("logs");
    logs::export(&app, &dir).map_err(|e| format!("日志未能导出：{e}"))
}

/// Where the app keeps its data, for the about page.
#[tauri::command]
fn data_location(app: AppHandle) -> Result<String, String> {
    data_dir(&app)
        .map(|dir| dir.display().to_string())
        .map_err(|e| e.to_string())
}

/// Opens the data directory in Finder or Explorer. The UI may not open paths itself.
#[tauri::command]
fn open_data_location(app: AppHandle) -> Result<(), String> {
    let dir = data_dir(&app).map_err(|e| e.to_string())?;
    app.opener()
        .open_path(dir.display().to_string(), None::<&str>)
        .map_err(|e| format!("未能打开数据位置：{e}"))
}

fn main() -> tauri::Result<()> {
    tauri::Builder::default()
        // First, so that launching the app again only brings the running one forward.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            match tauri::async_runtime::block_on(start(app.handle())) {
                Ok(ctx) => app.manage(ctx),
                Err(e) => {
                    tracing::error!("startup failed: {e}");
                    app.manage(StartupFailure(e.message()))
                }
            };
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            call,
            startup_failure,
            export_logs,
            data_location,
            open_data_location
        ])
        .run(tauri::generate_context!())
}

/// Holds the data directory for the life of the process.
struct InstanceLock(#[expect(dead_code, reason = "only kept open")] File);

/// Opens the data directory, the logs and the database.
async fn start(app: &AppHandle) -> Result<api::Context, StartupError> {
    let dir = data_dir(app).map_err(|e| StartupError::DataDir(e.into()))?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| StartupError::DataDir(format!("{}: {e}", dir.display()).into()))?;
    // The single-instance plugin can miss a second copy launched at the same moment, and two
    // copies sharing the database could send the same mail twice.
    let lock = File::options()
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.join("app.lock"))
        .map_err(|e| StartupError::DataDir(e.into()))?;
    match lock.try_lock() {
        Ok(()) => app.manage(InstanceLock(lock)),
        Err(TryLockError::WouldBlock) => std::process::exit(0),
        Err(TryLockError::Error(e)) => return Err(StartupError::DataDir(e.into())),
    };
    logs::init(&dir.join("logs")).map_err(StartupError::Logs)?;
    tracing::info!(
        "starting {} on {} {}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    let db = db::open(&dir.join("app.db"))
        .await
        .map_err(StartupError::Database)?;
    let net = mailbox::Net::new(Vec::new()).map_err(|e| StartupError::Tls(e.into()))?;
    Ok(api::Context { db, net })
}

#[derive(Debug)]
enum StartupError {
    DataDir(Box<dyn Error>),
    Logs(Box<dyn Error>),
    Database(db::OpenError),
    Tls(Box<dyn Error>),
}

impl std::fmt::Display for StartupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StartupError::DataDir(e) => write!(f, "data directory: {e}"),
            StartupError::Logs(e) => write!(f, "logs: {e}"),
            StartupError::Database(e) => write!(f, "{e}"),
            StartupError::Tls(e) => write!(f, "tls: {e}"),
        }
    }
}

impl StartupError {
    /// What the user reads: what happened, and what was kept safe.
    fn message(&self) -> String {
        use db::OpenError;
        match self {
            StartupError::DataDir(e) => format!("数据目录无法使用：{e}"),
            StartupError::Logs(e) => format!("日志无法写入：{e}"),
            StartupError::Tls(e) => format!("加密连接无法初始化：{e}"),
            StartupError::Database(OpenError::Open(e)) => format!("数据库无法打开：{e}"),
            StartupError::Database(OpenError::Backup(e)) => {
                format!("升级数据库前未能备份，为保护数据未做升级：{e}")
            }
            StartupError::Database(OpenError::Upgrade {
                error,
                backup: Some(path),
            }) => format!(
                "数据库升级失败：{error}。升级前的数据库已备份到 {}。",
                path.display()
            ),
            StartupError::Database(OpenError::Upgrade {
                error,
                backup: None,
            }) => format!("数据库创建失败：{error}"),
        }
    }
}

/// Brings the main window forward when the app is launched again.
fn show(app: &AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if let Err(e) = window
        .unminimize()
        .and_then(|()| window.show())
        .and_then(|()| window.set_focus())
    {
        tracing::warn!("bring window forward: {e}");
    }
}

/// Development builds keep their data in the repository, away from an installed app's data.
fn data_dir(app: &AppHandle) -> tauri::Result<PathBuf> {
    if cfg!(debug_assertions) {
        // crates/app is two levels below the repository.
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().nth(2);
        Ok(repo.ok_or(tauri::Error::UnknownPath)?.join(".dev-data"))
    } else {
        app.path().app_data_dir()
    }
}
