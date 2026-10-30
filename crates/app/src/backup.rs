//! Native dialogs and serialization of the local backup workflow.

use std::sync::Mutex;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

#[derive(Default)]
pub struct Commands(Mutex<()>);

async fn blocking<T: Send + 'static>(
    app: AppHandle,
    work: impl FnOnce(&AppHandle) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let commands = app.state::<Commands>();
        let _held = commands
            .0
            .lock()
            .map_err(|_| "备份处理已中断，请重启应用后重试。".to_owned())?;
        work(&app)
    })
    .await
    .map_err(|e| format!("备份处理未能完成：{e}，请重试。"))?
}

fn failure(e: api::backup::Error) -> String {
    tracing::error!(error = ?e, "backup failed");
    e.message()
}

#[tauri::command]
pub async fn export_backup(
    app: AppHandle,
    ctx: tauri::State<'_, std::sync::Arc<api::Context>>,
) -> Result<bool, String> {
    let db = ctx.db.clone();
    blocking(app, move |app| {
        let dir = crate::data_dir(app).map_err(|e| format!("数据目录无法使用：{e}"))?;
        let now = now();
        let date: String = tauri::async_runtime::block_on(
            sqlx::query_scalar("SELECT date(? / 1000, 'unixepoch', 'localtime')")
                .bind(now)
                .fetch_one(&db),
        )
        .map_err(|e| {
            tracing::error!("backup date: {e}");
            "无法读取备份日期，请重试。".to_owned()
        })?;
        let Some(target) = app
            .dialog()
            .file()
            .add_filter("Auto Email Sender 备份", &["zip"])
            .set_file_name(format!("Auto Email Sender 备份 {date}.zip"))
            .blocking_save_file()
        else {
            return Ok(false);
        };
        let target = target
            .into_path()
            .map_err(|e| format!("无法保存到这个位置：{e}，请选择其他位置。"))?;
        tauri::async_runtime::block_on(api::backup::export(&db, &dir, &target, now))
            .map_err(failure)?;
        Ok(true)
    })
    .await
}

#[tauri::command]
pub async fn prepare_restore(app: AppHandle) -> Result<Option<api::backup::RestoreInfo>, String> {
    blocking(app, |app| {
        let dir = crate::data_dir(app).map_err(|e| format!("数据目录无法使用：{e}"))?;
        // Opening a second picker abandons the previous unconfirmed choice, including on cancel.
        api::backup::cancel(&dir).map_err(failure)?;
        let Some(source) = app
            .dialog()
            .file()
            .add_filter("Auto Email Sender 备份", &["zip"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let source = source
            .into_path()
            .map_err(|e| format!("无法读取这个文件：{e}，请选择本地备份文件。"))?;
        tauri::async_runtime::block_on(api::backup::prepare(&dir, &source))
            .map(Some)
            .map_err(failure)
    })
    .await
}

/// Downloads the cloud backup into the same staged restore that `restore_backup` confirms.
#[tauri::command]
pub async fn prepare_cloud_restore(
    app: AppHandle,
    ctx: tauri::State<'_, std::sync::Arc<api::Context>>,
) -> Result<api::backup::RestoreInfo, String> {
    let ctx = ctx.inner().clone();
    blocking(app, move |app| {
        let dir = crate::data_dir(app).map_err(|e| format!("数据目录无法使用：{e}"))?;
        tauri::async_runtime::block_on(api::backup::cloud::prepare_restore(&ctx, &dir)).map_err(
            |e| {
                tracing::error!(error = %e, "cloud restore failed");
                e.message()
            },
        )
    })
    .await
}

#[tauri::command]
pub async fn cancel_restore(app: AppHandle) -> Result<(), String> {
    blocking(app, |app| {
        let dir = crate::data_dir(app).map_err(|e| format!("数据目录无法使用：{e}"))?;
        api::backup::cancel(&dir).map_err(failure)
    })
    .await
}

#[tauri::command]
pub async fn restore_backup(
    app: AppHandle,
    ctx: tauri::State<'_, std::sync::Arc<api::Context>>,
    exported_at: i64,
) -> Result<(), String> {
    let db = ctx.db.clone();
    blocking(app.clone(), move |app| {
        let dir = crate::data_dir(app).map_err(|e| format!("数据目录无法使用：{e}"))?;
        tauri::async_runtime::block_on(api::backup::confirm(&db, &dir, exported_at, now()))
            .map_err(failure)
    })
    .await?;
    app.restart();
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}
