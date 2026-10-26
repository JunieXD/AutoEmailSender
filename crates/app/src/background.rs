use std::sync::atomic::{AtomicBool, Ordering};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Window, WindowEvent};

use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};

#[derive(Default)]
struct CloseInProgress(AtomicBool);

pub fn init(app: &AppHandle) -> tauri::Result<()> {
    app.manage(CloseInProgress::default());
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
        if app
            .state::<CloseInProgress>()
            .0
            .swap(true, Ordering::SeqCst)
        {
            return;
        }
        let window = window.clone();
        tauri::async_runtime::spawn(async move {
            close_to_background(&window).await;
            window
                .app_handle()
                .state::<CloseInProgress>()
                .0
                .store(false, Ordering::SeqCst);
        });
    }
}

async fn close_to_background(window: &Window) {
    let app = window.app_handle();
    let ctx = app.try_state::<api::Context>();
    let shown = if let Some(ctx) = &ctx {
        match profile::background_notice_shown(&ctx.db).await {
            Ok(shown) => shown,
            Err(e) => {
                tracing::error!("read background notice: {e}");
                false
            }
        }
    } else {
        // A failed startup has no profile, but closing still needs an explanation.
        false
    };
    if !shown {
        let icon = if cfg!(target_os = "macos") {
            "菜单栏图标"
        } else {
            "托盘图标"
        };
        let text = format!(
            "关闭窗口后，Auto Email Sender 还会在后台运行，照常定时发信和收信。\
            可以从{icon}打开或退出。"
        );
        let dialog = app
            .dialog()
            .message(text)
            .title("Auto Email Sender")
            .parent(window)
            .buttons(MessageDialogButtons::OkCustom("知道了".into()));
        if let Err(e) = tauri::async_runtime::spawn_blocking(move || dialog.blocking_show()).await {
            tracing::error!("background notice dialog: {e}");
            return;
        }
    }
    if let Err(e) = window.hide() {
        tracing::error!("hide window: {e}");
    }
    if !shown
        && let Some(ctx) = &ctx
        && let Err(e) = profile::mark_background_notice_shown(&ctx.db).await
    {
        tracing::error!("write background notice: {e}");
    }
}
