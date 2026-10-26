// Release builds on Windows run without a console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod autostart;
mod background;
mod backup;
mod logs;
mod save;

use std::error::Error;
use std::io::Write;
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
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

/// Saves the exported table through the native file picker, away from the async executor.
#[tauri::command]
async fn export_mentors(
    app: AppHandle,
    ctx: tauri::State<'_, api::Context>,
    input: api::ExportMentors,
    file_name: String,
) -> Result<bool, String> {
    let extension = match input.format {
        api::TableFormat::Xlsx => "xlsx",
        api::TableFormat::Csv => "csv",
    };
    let data = api::export_mentors(&ctx, input)
        .await
        .map_err(|e| e.message)?;
    tauri::async_runtime::spawn_blocking(move || {
        let Some(target) = app
            .dialog()
            .file()
            .add_filter("导师表格", &[extension])
            .set_file_name(format!("{file_name}.{extension}"))
            .blocking_save_file()
        else {
            return Ok(false);
        };
        let path = target
            .into_path()
            .map_err(|e| format!("无法保存到这个位置：{e}，请选择其他位置"))?;
        save::replace(&path, |file| file.write_all(&data))
            .map_err(|e| format!("表格未能保存：{e}，请检查保存位置是否可写后重试"))?;
        Ok(true)
    })
    .await
    .map_err(|e| format!("表格未能导出：{e}，请重试"))?
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
    let context = tauri::generate_context!();
    let app = tauri::Builder::default()
        // First, so that launching the app again only brings the running one forward.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name(context.config().identifier.clone())
                .arg(autostart::ARG)
                .build(),
        )
        .manage(backup::Commands::default())
        .setup(|app| {
            background::init(app.handle())?;
            // A login launch stays in the tray unless there is something the user must see.
            let must_show = match tauri::async_runtime::block_on(start(app.handle())) {
                Ok((ctx, restored)) => {
                    app.manage(ctx);
                    restored
                }
                Err(e) => {
                    tracing::error!("startup failed: {e}");
                    app.manage(StartupFailure(e.message()));
                    true
                }
            };
            if must_show || !std::env::args_os().any(|arg| arg == autostart::ARG) {
                show(app.handle());
            }
            Ok(())
        })
        .on_window_event(background::on_window_event)
        .invoke_handler(tauri::generate_handler![
            call,
            startup_failure,
            export_logs,
            export_mentors,
            backup::export_backup,
            backup::prepare_restore,
            backup::cancel_restore,
            backup::restore_backup,
            data_location,
            open_data_location,
            autostart::autostart_enabled,
            autostart::set_autostart
        ])
        .build(context)?;
    app.run(|_app, _event| {
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Reopen { .. } = _event {
            show(_app);
        }
    });
    Ok(())
}

/// Opens the data directory, the logs and the database.
async fn start(app: &AppHandle) -> Result<(api::Context, bool), StartupError> {
    let dir = data_dir(app).map_err(|e| StartupError::DataDir(e.into()))?;
    let lock = match api::startup::DataLock::acquire(&dir) {
        Ok(lock) => lock,
        // The single-instance plugin can miss simultaneous launches.
        Err(api::startup::Error::InUse) => std::process::exit(0),
        Err(e) => return Err(StartupError::Core(e)),
    };
    app.manage(lock);
    logs::init(&dir.join("logs")).map_err(StartupError::Logs)?;
    tracing::info!(
        "starting {} on {} {}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    if let Err(e) = autostart::refresh(app) {
        tracing::error!("refresh autostart: {e}");
    }
    let (ctx, restored) = api::startup::open(&dir).await.map_err(StartupError::Core)?;
    let api::Context {
        db,
        net,
        materials,
        sender,
        receiver,
        matcher,
        reply_processor,
        crawler,
        generator,
        model_limit,
    } = &ctx;
    let ui = app.clone();
    tauri::async_runtime::spawn(send::run(
        db.clone(),
        net.clone(),
        materials.clone(),
        sender.clone(),
        move || {
            if let Err(e) = ui.emit("sending-changed", ()) {
                tracing::warn!("refresh sending: {e}");
            }
        },
    ));
    let ui = app.clone();
    let notifications_db = db.clone();
    let received_processor = reply_processor.clone();
    tauri::async_runtime::spawn(receive::run(
        db.clone(),
        net.clone(),
        receiver.clone(),
        // The UI reads again what new mail may have changed.
        move |replies| {
            received_processor.wake();
            if let Err(e) = ui.emit("received", ()) {
                tracing::warn!("tell the window about new mail: {e}");
            }
            if !replies.is_empty() {
                let db = notifications_db.clone();
                let app = ui.clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = notify_replies(&app, &db, replies).await {
                        tracing::warn!("reply notification: {e}");
                    }
                });
            }
        },
    ));
    let ui = app.clone();
    tauri::async_runtime::spawn(receive::outcome::run(
        db.clone(),
        net.clone(),
        reply_processor.clone(),
        model_limit.clone(),
        move || {
            if let Err(e) = ui.emit("reply-processing-changed", ()) {
                tracing::warn!("refresh reply processing: {e}");
            }
        },
    ));
    let (ui, done_ui) = (app.clone(), app.clone());
    let matching_notifications_db = db.clone();
    tauri::async_runtime::spawn(matching::run(
        db.clone(),
        net.clone(),
        matcher.clone(),
        model_limit.clone(),
        move || {
            if let Err(e) = ui.emit("matching-changed", ()) {
                tracing::warn!("refresh matching: {e}");
            }
        },
        move |result| {
            let app = done_ui.clone();
            let db = matching_notifications_db.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = finish_matching(&app, &db, result).await {
                    tracing::warn!("match completion notification: {e}");
                }
            });
        },
    ));
    let (ui, done_ui) = (app.clone(), app.clone());
    let drafts_db = db.clone();
    tauri::async_runtime::spawn(task::generation::run(
        db.clone(),
        net.clone(),
        generator.clone(),
        model_limit.clone(),
        move || {
            if let Err(e) = ui.emit("tasks-changed", ()) {
                tracing::warn!("refresh tasks: {e}");
            }
        },
        move |result| {
            let (app, db) = (done_ui.clone(), drafts_db.clone());
            tauri::async_runtime::spawn(async move {
                if let Err(e) = finish_drafts(&app, &db, result).await {
                    tracing::warn!("draft completion notification: {e}");
                }
            });
        },
    ));
    let (ui, done_ui, crawl_db) = (app.clone(), app.clone(), db.clone());
    tauri::async_runtime::spawn(crawl::runner::run(
        db.clone(),
        net.clone(),
        crawler.clone(),
        model_limit.clone(),
        move || {
            if let Err(e) = ui.emit("crawl-changed", ()) {
                tracing::warn!("refresh crawl: {e}");
            }
        },
        move |result| {
            let (app, db) = (done_ui.clone(), crawl_db.clone());
            tauri::async_runtime::spawn(async move {
                if let Err(e) = finish_crawl(&app, &db, result).await {
                    tracing::warn!("crawl completion notification: {e}");
                }
            });
        },
    ));
    Ok((ctx, restored))
}

async fn finish_crawl(
    app: &AppHandle,
    db: &sqlx::SqlitePool,
    result: crawl::runner::CrawlFinished,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    if result.message.is_empty() {
        return Ok(());
    }
    if let Some(window) = app.get_webview_window("main")
        && window.is_focused()?
    {
        app.emit("crawl-finished", result)?;
    } else if profile::background_notifications(db).await? {
        app.notification().builder().title(&result.message).show()?;
    }
    Ok(())
}
async fn finish_drafts(
    app: &AppHandle,
    db: &sqlx::SqlitePool,
    result: task::generation::DraftsFinished,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    if let Some(window) = app.get_webview_window("main")
        && window.is_focused()?
    {
        app.emit("drafts-finished", result)?;
    } else if profile::background_notifications(db).await? {
        app.notification()
            .builder()
            .title(format!(
                "任务“{}”的草稿写好了：{} 封，失败 {} 封",
                result.name, result.generated, result.failed
            ))
            .show()?;
    }
    Ok(())
}

async fn finish_matching(
    app: &AppHandle,
    db: &sqlx::SqlitePool,
    result: matching::MatchFinished,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    if let Some(window) = app.get_webview_window("main")
        && window.is_focused()?
    {
        app.emit("matching-finished", result)?;
    } else if profile::background_notifications(db).await? {
        let failed = if result.failed > 0 {
            format!("，失败 {} 位", result.failed)
        } else {
            String::new()
        };
        app.notification()
            .builder()
            .title(format!(
                "匹配度分析完成：分析了 {} 位{failed}",
                result.analyzed
            ))
            .show()?;
    }
    Ok(())
}

async fn notify_replies(
    app: &AppHandle,
    db: &sqlx::SqlitePool,
    replies: Vec<receive::NewReply>,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    if !profile::reply_notifications(db).await? {
        return Ok(());
    }
    if let Some(window) = app.get_webview_window("main")
        && window.is_focused()?
    {
        return Ok(());
    }
    let (title, body) = if replies.len() == 1 && replies[0].mentors.len() == 1 {
        (
            format!("{}回信了", replies[0].mentors[0]),
            replies[0]
                .subject
                .clone()
                .unwrap_or_else(|| "（无主题）".into()),
        )
    } else {
        let mut names = Vec::new();
        for name in replies.iter().flat_map(|r| &r.mentors) {
            if !names.contains(name) {
                names.push(name.clone());
            }
        }
        (format!("{} 封新回信", replies.len()), names.join("、"))
    };
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()?;
    Ok(())
}

#[derive(Debug)]
enum StartupError {
    DataDir(Box<dyn Error>),
    Logs(Box<dyn Error>),
    Core(api::startup::Error),
}

impl std::fmt::Display for StartupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StartupError::DataDir(e) => write!(f, "data directory: {e}"),
            StartupError::Logs(e) => write!(f, "logs: {e}"),
            StartupError::Core(e) => write!(f, "{e}"),
        }
    }
}

impl StartupError {
    /// What the user reads: what happened, and what was kept safe.
    fn message(&self) -> String {
        match self {
            StartupError::DataDir(e) => format!("数据目录无法使用：{e}"),
            StartupError::Logs(e) => format!("日志无法写入：{e}"),
            StartupError::Core(e) => e.message(),
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
