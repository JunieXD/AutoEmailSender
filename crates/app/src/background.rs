use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Window, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_notification::NotificationExt;

pub const AUTOSTART_ARG: &str = "--autostart";

// Close requests can arrive again before the first notice has been persisted.
struct BackgroundNotice(tauri::async_runtime::Mutex<()>);

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
    app.manage(BackgroundNotice(tauri::async_runtime::Mutex::new(())));
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
        let app = window.app_handle();
        if let Err(e) = window.hide() {
            report_error(app, format!("窗口未能隐藏：{e}，请重试"));
            return;
        }
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            if let Err(e) = notify_background(&app).await {
                report_error(&app, e);
            }
        });
    }
}

async fn notify_background(app: &AppHandle) -> Result<(), String> {
    // Startup failures have no profile to read; the tray still lets the user reopen or quit.
    let Some(ctx) = app.try_state::<api::Context>() else {
        return Ok(());
    };
    let notice = app.state::<BackgroundNotice>();
    let _guard = notice.0.lock().await;
    if profile::background_notice_shown(&ctx.db)
        .await
        .map_err(|e| format!("无法读取后台运行提示状态：{e}，请重新打开应用后重试"))?
    {
        return Ok(());
    }
    let icon = if cfg!(target_os = "macos") {
        "菜单栏图标"
    } else {
        "托盘图标"
    };
    app.notification()
        .builder()
        .title("Auto Email Sender")
        .body(format!(
            "Auto Email Sender 还在后台运行，可以从{icon}打开或退出。"
        ))
        .show()
        .map_err(|e| {
            format!("后台运行通知未能显示：{e}，应用仍在后台运行，请从{icon}打开或退出")
        })?;
    profile::mark_background_notice_shown(&ctx.db)
        .await
        .map_err(|e| format!("后台运行提示状态未能保存：{e}，下次关闭窗口时可能会再次提示"))
}

fn report_error(app: &AppHandle, message: String) {
    tracing::error!("background window handling: {message}");
    app.dialog()
        .message(message)
        .title("Auto Email Sender")
        .kind(MessageDialogKind::Error)
        .show(|_| {});
}
