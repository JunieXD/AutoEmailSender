use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Window, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;

pub const AUTOSTART_ARG: &str = "--autostart";

#[tauri::command]
pub async fn autostart_enabled(app: AppHandle) -> Result<bool, String> {
    app.autolaunch()
        .is_enabled()
        .map_err(|e| format!("无法读取开机自启状态：{e}，请稍后重试"))
}

#[tauri::command]
pub async fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let autostart = app.autolaunch();
    if enabled {
        autostart.enable()
    } else {
        autostart.disable()
    }
    .map_err(|e| format!("未能更改开机自启设置：{e}，请检查系统登录项设置后重试"))
}

pub fn init(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开 Auto Email Sender", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    #[cfg(target_os = "macos")]
    let icon = tauri::include_image!("icons/tray.png");
    #[cfg(not(target_os = "macos"))]
    let icon = tauri::include_image!("icons/32x32.png");
    let tray = TrayIconBuilder::new()
        .icon(icon)
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip("Auto Email Sender")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => crate::show(app),
            "quit" => app.exit(0),
            _ => {}
        });
    #[cfg(target_os = "windows")]
    let tray = tray
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up,
                ..
            } = event
            {
                crate::show(tray.app_handle());
            }
        });
    tray.build(app)?;
    Ok(())
}

pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if window.label() != "main" {
        return;
    }
    if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        // Left open, the window shows the user it did not close.
        if let Err(e) = window.hide() {
            tracing::error!("hide window: {e}");
            return;
        }
        let app = window.app_handle().clone();
        tauri::async_runtime::spawn(async move {
            if let Err(e) = tell_still_running(&app).await {
                tracing::error!("background notice: {e}");
            }
        });
    }
}

/// Tells the user once that closing the window left the app running.
async fn tell_still_running(app: &AppHandle) -> Result<(), sqlx::Error> {
    // Startup failures have no profile to read; the tray still lets the user reopen or quit.
    let Some(ctx) = app.try_state::<api::Context>() else {
        return Ok(());
    };
    if profile::background_notice_shown(&ctx.db).await? {
        return Ok(());
    }
    let icon = if cfg!(target_os = "macos") {
        "菜单栏图标"
    } else {
        "托盘图标"
    };
    let text = format!("Auto Email Sender 还在后台运行，可以从{icon}打开或退出。");
    let shown = app
        .notification()
        .builder()
        .title("Auto Email Sender")
        .body(&text)
        .show();
    if let Err(e) = shown {
        // Without notifications the user still has to learn where the app went.
        tracing::warn!("background notification: {e}");
        app.dialog()
            .message(text)
            .title("Auto Email Sender")
            .show(|_| {});
    }
    profile::mark_background_notice_shown(&ctx.db).await
}
