//! Updates stay in the application process, including when all windows are hidden.

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

use api::updater::{UpdateInfo, UpdateStatus};

const CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);
const PROGRESS_INTERVAL: Duration = Duration::from_millis(250);

#[derive(Default)]
pub struct Updater {
    phase: Mutex<Phase<Update>>,
    operation: tokio::sync::Mutex<()>,
}

#[derive(Default)]
enum Phase<U> {
    #[default]
    Current,
    Available {
        update: U,
        error: Option<String>,
    },
    Downloading {
        update: U,
        downloaded: u64,
        total: Option<u64>,
    },
    Ready {
        update: U,
        bytes: Arc<[u8]>,
    },
}

impl<U: Clone> Phase<U> {
    fn begin_download(&mut self) -> Result<U, String> {
        let Self::Available { update, .. } = self else {
            return Err("当前没有可下载的更新，或更新已经在下载。".into());
        };
        let update = update.clone();
        *self = Self::Downloading {
            update: update.clone(),
            downloaded: 0,
            total: None,
        };
        Ok(update)
    }

    fn finish_download(&mut self, update: U, result: Result<Vec<u8>, String>) {
        *self = match result {
            Ok(bytes) => Self::Ready {
                update,
                bytes: bytes.into(),
            },
            Err(error) => Self::Available {
                update,
                error: Some(error),
            },
        };
    }

    fn installation(&self) -> Result<(U, Arc<[u8]>), String> {
        match self {
            Self::Ready { update, bytes } => Ok((update.clone(), bytes.clone())),
            _ => Err("更新还没有下载完成，请先下载再重启。".into()),
        }
    }
}

impl Updater {
    fn phase(&self) -> MutexGuard<'_, Phase<Update>> {
        self.phase.lock().unwrap_or_else(|_| {
            unreachable!("update state changes cannot panic while holding the lock")
        })
    }

    fn status(&self) -> UpdateStatus {
        let info = |update: &Update| UpdateInfo {
            version: update.version.clone(),
            published_at: update.date.map(|date| date.unix_timestamp() * 1000),
            notes: update.body.clone(),
        };
        match &*self.phase() {
            Phase::Current => UpdateStatus::Current,
            Phase::Available { update, error } => UpdateStatus::Available {
                update: info(update),
                error: error.clone(),
            },
            Phase::Downloading {
                update,
                downloaded,
                total,
            } => UpdateStatus::Downloading {
                update: info(update),
                downloaded: *downloaded,
                total: *total,
            },
            Phase::Ready { update, .. } => UpdateStatus::Ready {
                update: info(update),
            },
        }
    }
}

fn changed(app: &AppHandle) {
    if let Err(e) = app.emit("update-changed", ()) {
        tracing::warn!("refresh update: {e}");
    }
}

pub fn start(app: &AppHandle) {
    if !cfg!(debug_assertions) {
        let app = app.clone();
        tauri::async_runtime::spawn(schedule(async move || {
            if let Err(error) = check(&app).await {
                tracing::warn!("automatic update check: {error}");
            }
        }));
    }
}

async fn schedule(mut check: impl AsyncFnMut()) {
    let mut ticks = tokio::time::interval(CHECK_INTERVAL);
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        ticks.tick().await;
        check().await;
    }
}

#[tauri::command]
pub fn update_status(app: AppHandle) -> UpdateStatus {
    app.state::<Updater>().status()
}

#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<UpdateStatus, String> {
    check(&app).await
}

async fn check(app: &AppHandle) -> Result<UpdateStatus, String> {
    let state = app.state::<Updater>();
    if cfg!(debug_assertions) {
        return Ok(state.status());
    }
    let Ok(_operation) = state.operation.try_lock() else {
        return Err("正在检查、下载或安装更新，请稍后再试。".into());
    };
    // Keep a downloaded package and its release together until the user installs it.
    if matches!(*state.phase(), Phase::Ready { .. }) {
        return Ok(state.status());
    }
    let net = app
        .try_state::<Arc<api::Context>>()
        .ok_or("应用尚未就绪，无法检查更新，请先处理启动错误。")?
        .net
        .clone();
    let exit_app = app.clone();
    let result = app
        .updater_builder()
        .timeout(Duration::from_secs(30))
        .configure_client(move |builder| net.configure_http(builder))
        // Replaces the plugin's own hook, so its cleanup runs here too.
        .on_before_exit(move || {
            crate::before_exit(&exit_app);
            exit_app.cleanup_before_exit();
        })
        .build()
        .map_err(check_error)?
        .check()
        .await
        .map_err(check_error)?;
    *state.phase() = match result {
        Some(update) => Phase::Available {
            update,
            error: None,
        },
        None => Phase::Current,
    };
    changed(app);
    Ok(state.status())
}

// Plugin errors can include remote JSON strings; only log the category and safe HTTP details.
fn check_error(error: tauri_plugin_updater::Error) -> String {
    let reason = match error {
        tauri_plugin_updater::Error::Reqwest(error) => {
            tracing::warn!(
                timeout = error.is_timeout(),
                status = ?error.status(),
                "update request failed"
            );
            if error.is_timeout() {
                "连接超时"
            } else {
                "无法连接更新服务或读取更新清单"
            }
        }
        _ => "更新配置或更新清单不正确",
    };
    format!("检查更新失败：{reason}，请检查网络和系统代理后重试。")
}

#[tauri::command]
pub async fn download_update(app: AppHandle) -> Result<(), String> {
    let state = app.state::<Updater>();
    let _operation = state
        .operation
        .try_lock()
        .map_err(|_| "正在检查、下载或安装更新，请稍后再试。".to_owned())?;
    let mut update = state.phase().begin_download()?;
    update.timeout = Some(Duration::from_secs(30 * 60));
    changed(&app);
    let mut downloaded = 0;
    let mut last_event = Instant::now();
    let result = update
        .download(
            |chunk, total| {
                downloaded += chunk as u64;
                if let Phase::Downloading {
                    downloaded: current,
                    total: size,
                    ..
                } = &mut *state.phase()
                {
                    *current = downloaded;
                    *size = total;
                }
                if last_event.elapsed() >= PROGRESS_INTERVAL {
                    changed(&app);
                    last_event = Instant::now();
                }
            },
            || {},
        )
        .await
        .map_err(download_error);
    let response = result.as_ref().map(|_| ()).map_err(Clone::clone);
    state.phase().finish_download(update, result);
    changed(&app);
    response
}

fn download_error(error: tauri_plugin_updater::Error) -> String {
    use tauri_plugin_updater::Error;
    let (category, reason) = match error {
        Error::Minisign(_)
        | Error::Base64(_)
        | Error::SignatureUtf8(_)
        | Error::SignedVersionMismatch { .. }
        | Error::MissingSignedVersion => (
            "signature",
            "更新包签名校验失败，未安装，请重新下载；仍失败时请从官网下载。",
        ),
        Error::Reqwest(error) if error.is_timeout() => {
            ("timeout", "更新下载超时，请检查网络和系统代理后重试。")
        }
        Error::Reqwest(_) | Error::Network(_) => {
            ("network", "更新下载失败，请检查网络和系统代理后重试。")
        }
        _ => ("package", "更新包无法读取或校验，未安装，请重新下载。"),
    };
    tracing::warn!(category, "update download failed");
    reason.into()
}

#[tauri::command]
pub async fn restart_update(app: AppHandle) -> Result<(), String> {
    let install_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = install_app.state::<Updater>();
        let _operation = state
            .operation
            .try_lock()
            .map_err(|_| "正在检查、下载或安装更新，请稍后再试。".to_owned())?;
        let (update, bytes) = state.phase().installation()?;
        update.install(&*bytes).map_err(|error| {
            tracing::error!("update installation failed: {error}");
            "更新未能安装，请检查应用所在目录的权限后重试。".to_owned()
        })?;
        install_app.restart();
    })
    .await
    .map_err(|error| format!("更新处理未能完成：{error}，请重试。"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn download_failure_can_retry_and_only_verified_bytes_are_installable() -> Result<(), String> {
        let mut phase = Phase::Current;
        assert!(phase.installation().is_err());
        phase = Phase::Available {
            update: "3.0.1",
            error: None,
        };
        let update = phase.begin_download()?;
        assert!(phase.begin_download().is_err());
        assert!(phase.installation().is_err());
        phase.finish_download(update, Err("bad signature".into()));
        assert!(matches!(&phase, Phase::Available { error: Some(_), .. }));
        let update = phase.begin_download()?;
        phase.finish_download(update, Ok(vec![1, 2, 3]));
        let (version, bytes) = phase.installation()?;
        assert_eq!(version, "3.0.1");
        assert_eq!(&*bytes, &[1, 2, 3]);
        assert!(phase.begin_download().is_err());
        Ok(())
    }

    #[tokio::test(start_paused = true)]
    async fn checks_at_startup_and_every_six_hours() {
        let (sent, mut received) = tokio::sync::mpsc::unbounded_channel();
        let task = tokio::spawn(schedule(async move || {
            sent.send(tokio::time::Instant::now()).unwrap();
        }));
        let first = received.recv().await.unwrap();
        tokio::time::advance(CHECK_INTERVAL - Duration::from_secs(1)).await;
        assert!(received.try_recv().is_err());
        tokio::time::advance(Duration::from_secs(1)).await;
        assert_eq!(received.recv().await.unwrap() - first, CHECK_INTERVAL);
        tokio::time::advance(CHECK_INTERVAL).await;
        assert_eq!(received.recv().await.unwrap() - first, CHECK_INTERVAL * 2);
        task.abort();
    }
}
