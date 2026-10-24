//! The log files users export to send to the developer. Nothing private is logged: no mail bodies,
//! passwords or keys.

use std::error::Error;
use std::io::Write;
use std::path::Path;

use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;
use tracing_appender::rolling::{RollingFileAppender, Rotation};
use tracing_subscriber::filter::LevelFilter;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

/// Files are named app.<date>.log, one for each day the app runs; the newest are kept.
const PREFIX: &str = "app";
const SUFFIX: &str = "log";
const FILES_KEPT: usize = 14;

/// Sends logs and panics to daily files in `dir`, and in development builds to the terminal too.
pub fn init(dir: &Path) -> Result<(), Box<dyn Error>> {
    // The appender prunes old files as it is built, before it would create the directory.
    std::fs::create_dir_all(dir)?;
    let file = RollingFileAppender::builder()
        .rotation(Rotation::DAILY)
        .filename_prefix(PREFIX)
        .filename_suffix(SUFFIX)
        .max_log_files(FILES_KEPT)
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

/// Asks where to save, then writes the log files in `dir` there as one text file. False when the
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
    let mut names = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let name = entry?.file_name().to_string_lossy().into_owned();
        // Finder and others leave their own files here, such as .DS_Store.
        if name.starts_with(PREFIX) && name.ends_with(SUFFIX) {
            names.push(name);
        }
    }
    // Daily file names sort by date.
    names.sort();
    let mut out = format!(
        "Auto Email Sender {}, {} {}\n",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    for name in names {
        out.push_str(&format!("\n== {name} ==\n"));
        out.push_str(&std::fs::read_to_string(dir.join(name))?);
    }
    crate::save::replace(&target.into_path()?, |file| file.write_all(out.as_bytes()))?;
    Ok(true)
}
