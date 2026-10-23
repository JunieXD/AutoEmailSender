//! The log files users export to send to the developer. Nothing private is logged: no mail bodies,
//! passwords or keys.

use std::error::Error;
use std::path::Path;

use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;
use tracing_appender::rolling::{RollingFileAppender, Rotation};
use tracing_subscriber::filter::LevelFilter;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

/// One file per day, kept for two weeks.
const DAYS_KEPT: usize = 14;

/// Sends logs and panics to daily files in `dir`, and in development builds to the terminal too.
pub fn init(dir: &Path) -> Result<(), Box<dyn Error>> {
    // The appender prunes old files as it is built, before it would create the directory.
    std::fs::create_dir_all(dir)?;
    let file = RollingFileAppender::builder()
        .rotation(Rotation::DAILY)
        .filename_prefix("app")
        .filename_suffix("log")
        .max_log_files(DAYS_KEPT)
        .build(dir)?;
    tracing_subscriber::registry()
        .with(LevelFilter::INFO)
        .with(
            tracing_subscriber::fmt::layer()
                .with_writer(file)
                .with_ansi(false),
        )
        .with(cfg!(debug_assertions).then(tracing_subscriber::fmt::layer))
        .try_init()?;
    let report = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        tracing::error!("{info}");
        report(info);
    }));
    Ok(())
}

/// Asks where to save, then writes every log file in `dir` there as one text file. False when the
/// user cancels.
pub fn export(app: &AppHandle, dir: &Path) -> Result<bool, Box<dyn Error>> {
    let Some(target) = app
        .dialog()
        .file()
        .set_file_name("Auto Email Sender 日志.txt")
        .blocking_save_file()
    else {
        return Ok(false);
    };
    let mut files = std::fs::read_dir(dir)?.collect::<Result<Vec<_>, _>>()?;
    // Daily file names sort by date.
    files.sort_by_key(|f| f.file_name());
    let mut out = format!(
        "Auto Email Sender {}, {} {}\n",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    for file in files {
        out.push_str(&format!("\n== {} ==\n", file.file_name().to_string_lossy()));
        out.push_str(&std::fs::read_to_string(file.path())?);
    }
    std::fs::write(target.into_path()?, out)?;
    Ok(true)
}
