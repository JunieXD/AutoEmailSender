//! The log files users export to send to the developer. Nothing private is logged: no mail bodies,
//! passwords or keys.

use std::error::Error;
use std::io::Write;
use std::path::Path;

use chrono::{DateTime, Local, SecondsFormat, Utc};
use crawl::queue::{CrawlDetail, CrawlState};
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
    let names = files(dir)?;
    let mut out = environment();
    for name in names {
        out.push_str(&format!("\n== {name} ==\n"));
        out.push_str(&std::fs::read_to_string(dir.join(name))?);
    }
    crate::save::replace(&target.into_path()?, |file| file.write_all(out.as_bytes()))?;
    Ok(true)
}

fn environment() -> String {
    format!(
        "Auto Email Sender {}, {} {}\n",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    )
}

fn files(dir: &Path) -> std::io::Result<Vec<String>> {
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
    Ok(names)
}

fn crawl_lines(dir: &Path, id: i64, created_at: DateTime<Utc>) -> std::io::Result<String> {
    let start = created_at.to_rfc3339_opts(SecondsFormat::Micros, true);
    let mut out = String::new();
    let marker = format!("crawl{{id={id}}}:");
    for name in files(dir)? {
        let content = std::fs::read_to_string(dir.join(name))?;
        let mut kept = false;
        for line in content.lines() {
            let mut fields = line.split_whitespace();
            // An event's later lines, such as a panic's message, have no time of their own.
            if let Some(time) = fields
                .next()
                .filter(|f| DateTime::parse_from_rfc3339(f).is_ok())
            {
                // Fixed-width UTC RFC 3339 timestamps sort chronologically as strings.
                kept = time >= start.as_str()
                    && fields.nth(1).is_some_and(|span| span.starts_with(&marker));
            }
            if kept {
                out.push_str(line);
                out.push('\n');
            }
        }
    }
    Ok(out)
}

fn crawl_content(
    dir: &Path,
    detail: &CrawlDetail,
    created_at: DateTime<Utc>,
    started: &str,
) -> std::io::Result<String> {
    let state = match detail.summary.state {
        CrawlState::Running => "运行中",
        CrawlState::Paused => "已暂停",
        CrawlState::Done => "已完成",
        CrawlState::Failed => "失败",
        CrawlState::Canceled => "已取消",
    };
    let urls = if detail.urls.is_empty() {
        "Agent 提交".to_owned()
    } else {
        detail.urls.join("\n")
    };
    let mut out = environment();
    out.push_str(&format!(
        "抓取编号：{}
起始网址：{urls}
开始时间：{started}（本机时间）
当前状态：{state}
日志只保留最近 14 天

",
        detail.summary.id
    ));
    let lines = crawl_lines(dir, detail.summary.id, created_at)?;
    out.push_str(if lines.is_empty() {
        "没有找到这次抓取的日志。\n"
    } else {
        &lines
    });
    Ok(out)
}

fn crawl_filename(detail: &CrawlDetail, date: &str) -> Result<String, Box<dyn Error>> {
    let host = match detail.urls.first() {
        Some(url) => tauri::Url::parse(url)?
            .host_str()
            .ok_or("起始网址缺少主机名")?
            .to_owned(),
        None => "Agent 提交".into(),
    };
    // IPv6 hostnames contain colons, which Windows does not allow in filenames.
    Ok(format!("抓取日志 {} {date}.txt", host.replace(':', "_")))
}

pub fn export_crawl(
    app: &AppHandle,
    dir: &Path,
    detail: &CrawlDetail,
    created_at: DateTime<Utc>,
) -> Result<bool, Box<dyn Error>> {
    let local = created_at.with_timezone(&Local);
    let date = local.format("%Y-%m-%d").to_string();
    let started = local.format("%Y-%m-%d %H:%M:%S").to_string();
    let Some(target) = app
        .dialog()
        .file()
        .set_file_name(crawl_filename(detail, &date)?)
        .blocking_save_file()
    else {
        return Ok(false);
    };
    let out = crawl_content(dir, detail, created_at, &started)?;
    crate::save::replace(&target.into_path()?, |file| file.write_all(out.as_bytes()))?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crawl::queue::{CrawlStage, CrawlSummary};
    use sqlx::types::Json;

    fn detail() -> CrawlDetail {
        CrawlDetail {
            summary: CrawlSummary {
                university: None,
                school: None,
                id: 5,
                url: Some("https://school.example.edu/list".into()),
                model_config_id: None,
                state: CrawlState::Paused,
                error: None,
                created_at: 946_684_800_000,
                finished_at: None,
                pages: 2,
                remaining: 1,
                enrich_remaining: 0,
                model_ids: Json(vec![]),
                profiles: 0,
                candidates: 1,
                failed: 1,
                tokens: Some(1200),
                stage: CrawlStage::Paused,
            },
            urls: vec!["https://school.example.edu/list".into()],
            limits: vec![],
            pages: vec![],
            runs: vec![],
            page_total: 3,
        }
    }

    #[test]
    fn filters_crawl_lines_across_daily_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("app.2026-10-26.log"),
            concat!(
                "2026-10-26T12:00:00.000000Z  INFO crawl{id=50}: crawl::runner: other crawl\n",
                "2026-10-26T12:01:00.000000Z  INFO crawl{id=5}: crawl::runner: resumed\n",
                "2026-10-26T12:02:00.000000Z  WARN crawl{id=5}: crawl: failed\n",
                "2026-10-26T12:03:00.000000Z ERROR crawl{id=5}: crawl: panicked at src/x.rs:1:1:\n",
                "the reason\n",
                "2026-10-26T12:04:00.000000Z  INFO crawl{id=15}: crawl: wrong id\n",
                "its own reason\n",
                "2026-10-26T12:05:00.000000Z  INFO app: text crawl{id=5}\n",
                "2026-10-26T12:06:00.000000Z  INFO othercrawl{id=5}: app: wrong span\n",
            ),
        )
        .unwrap();
        std::fs::write(
            dir.path().join("app.2026-10-25.log"),
            concat!(
                "2026-10-25T11:59:59.999999Z  INFO crawl{id=5}: crawl: previous crawl\n",
                "2026-10-25T12:00:00.000000Z  INFO crawl{id=5}: crawl: started\n",
            ),
        )
        .unwrap();
        std::fs::write(dir.path().join(".DS_Store"), "unrelated").unwrap();
        let created_at = DateTime::from_timestamp_millis(1_792_929_600_000).unwrap();
        let lines = crawl_lines(dir.path(), 5, created_at).unwrap();
        assert!(!lines.contains("previous crawl"));
        assert_eq!(lines.lines().count(), 5);
        assert!(lines.starts_with("2026-10-25"));
        assert!(lines.contains("resumed"));
        assert!(lines.contains("failed"));
        assert!(lines.ends_with("panicked at src/x.rs:1:1:\nthe reason\n"));
        assert!(!lines.contains("other crawl"));
    }

    #[test]
    fn export_has_context_and_keeps_an_empty_crawl_useful() {
        let dir = tempfile::tempdir().unwrap();
        let mut detail = detail();
        let created_at = DateTime::from_timestamp_millis(detail.summary.created_at).unwrap();
        let started = "2000-01-01 08:00:00";
        let empty = crawl_content(dir.path(), &detail, created_at, started).unwrap();
        for value in [
            env!("CARGO_PKG_VERSION"),
            std::env::consts::OS,
            std::env::consts::ARCH,
            "抓取编号：5",
            &detail.urls[0],
            started,
            "已暂停",
            "最近 14 天",
            "没有找到",
        ] {
            assert!(empty.contains(value), "missing {value}");
        }
        assert_eq!(
            crawl_filename(&detail, "2026-10-26").unwrap(),
            "抓取日志 school.example.edu 2026-10-26.txt"
        );
        let file = std::fs::File::create(dir.path().join("app.2026-10-26.log")).unwrap();
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .with_writer(std::sync::Mutex::new(file))
            .finish();
        tracing::subscriber::with_default(subscriber, || {
            crawl::span(5).in_scope(|| tracing::info!("crawl started"));
        });
        let content = crawl_content(dir.path(), &detail, created_at, started).unwrap();
        assert!(content.contains("crawl{id=5}"));
        assert!(content.contains("crawl started"));
        assert!(!content.contains("没有找到"));
        detail.urls.clear();
        assert_eq!(
            crawl_filename(&detail, "2026-10-26").unwrap(),
            "抓取日志 Agent 提交 2026-10-26.txt"
        );
        assert!(
            crawl_content(dir.path(), &detail, created_at, started)
                .unwrap()
                .contains("Agent 提交")
        );
    }
}
