//! Each render owns one isolated window and closes it when the claim is dropped.
use std::{
    future::Future,
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

use crawl::{
    CrawlError, PageTarget, PageUrl,
    browser::{Browser as Render, BrowserError, RenderedPage},
    store::PageStep,
};
use serde_json::{Value, json};
use tauri::{
    AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    webview::{NewWindowResponse, PageLoadEvent},
};

const PREFIX: &str = "crawl-browser-";
const TIMEOUT: Duration = Duration::from_secs(20);
const POLL: Duration = Duration::from_millis(500);
const STABLE: Duration = Duration::from_millis(1500);
const SNAPSHOT: &str = "({state:document.readyState,url:location.href,length:document.body?.innerText.length??0,elements:document.getElementsByTagName('*').length})";
const MATERIAL_SNAPSHOT: &str = r#"({
    text: document.body?.innerText ?? '',
    links: [...document.querySelectorAll('a[href]')].map(element => element.href),
    images: [...document.querySelectorAll('img[alt]')].map(element => element.getAttribute('alt')),
})"#;
const CLICK_CONTROL: &str = r#"(label => {
    const alternative = element => {
        const attributes = element.tagName === 'IMG' ? ['alt', 'title', 'aria-label'] : ['aria-label', 'title', 'alt'];
        return attributes.map(name => element.getAttribute(name)).find(value => value?.trim());
    };
    const controlText = element => {
        if (element.tagName === 'INPUT' && element.getAttribute('value')?.trim()) return element.getAttribute('value');
        const text = element.innerText?.trim();
        if (text) return text;
        return alternative(element) || [...element.querySelectorAll('img')].map(alternative).find(Boolean) || '无文字链接';
    };
    const control = [...document.querySelectorAll('a,button,input,[role="button"],[onclick]')].find(element => {
        const style = getComputedStyle(element);
        return !element.disabled && !element.closest('[disabled],[aria-disabled="true"],.disabled')
            && element.getClientRects().length && style.visibility !== 'hidden'
            && controlText(element).trim().replace(/\s+/g, ' ') === label;
    });
    if (!control) return false;
    control.click();
    return true;
})"#;

static NEXT: AtomicU64 = AtomicU64::new(0);

pub struct Browser {
    app: AppHandle,
    available: tauri::async_runtime::Mutex<tauri::async_runtime::Receiver<()>>,
    returned: tauri::async_runtime::Sender<()>,
}

impl Browser {
    pub fn new(app: AppHandle) -> Self {
        let (returned, available) = tauri::async_runtime::channel(2);
        for _ in 0..2 {
            returned
                .try_send(())
                .unwrap_or_else(|_| unreachable!("two empty browser slots"));
        }
        Self {
            app,
            available: tauri::async_runtime::Mutex::new(available),
            returned,
        }
    }
}

struct Slot(tauri::async_runtime::Sender<()>);
impl Drop for Slot {
    fn drop(&mut self) {
        self.0
            .try_send(())
            .unwrap_or_else(|_| unreachable!("only acquired browser slots are returned"));
    }
}

#[derive(Default)]
struct State {
    stopped: AtomicBool,
    close_requested: AtomicBool,
    loaded: AtomicBool,
    error: Mutex<Option<String>>,
}
impl State {
    fn fail(&self, message: String) {
        let mut error = self
            .error
            .lock()
            .unwrap_or_else(|_| unreachable!("browser state updates cannot panic"));
        if error.is_none() {
            *error = Some(message);
        }
    }
    fn check(&self) -> Result<(), BrowserError> {
        if self.stopped.load(Ordering::Relaxed) {
            return Err(failed("浏览器页面已关闭，请重试"));
        }
        if let Some(error) = self
            .error
            .lock()
            .unwrap_or_else(|_| unreachable!("browser state updates cannot panic"))
            .as_ref()
        {
            return Err(BrowserError::Failed(error.clone()));
        }
        Ok(())
    }
}

struct Window {
    app: AppHandle,
    label: String,
    state: Arc<State>,
}
impl Drop for Window {
    fn drop(&mut self) {
        self.state.stopped.store(true, Ordering::Relaxed);
        if let Some(window) = self.app.get_webview_window(&self.label)
            && !self.state.close_requested.swap(true, Ordering::Relaxed)
            && let Err(error) = window.close()
        {
            tracing::warn!("close crawl browser: {error}");
        }
    }
}

impl Render for Browser {
    fn render<'a>(
        &'a self,
        target: &'a PageTarget,
        steps: &'a [PageStep],
    ) -> Pin<Box<dyn Future<Output = Result<RenderedPage, BrowserError>> + Send + 'a>> {
        Box::pin(async move {
            self.available
                .lock()
                .await
                .recv()
                .await
                .ok_or_else(|| failed("浏览器已停止，请重启应用"))?;
            let slot = Slot(self.returned.clone());
            let state = Arc::new(State::default());
            let window = Window {
                app: self.app.clone(),
                label: format!("{PREFIX}{}", NEXT.fetch_add(1, Ordering::Relaxed)),
                state,
            };
            let (app, label, state, target, steps) = (
                window.app.clone(),
                window.label.clone(),
                window.state.clone(),
                target.clone(),
                steps.to_vec(),
            );
            let result = tauri::async_runtime::spawn_blocking(move || {
                let _slot = slot;
                // This second guard also covers cancellation during native window creation.
                let _window = Window {
                    app: app.clone(),
                    label: label.clone(),
                    state: state.clone(),
                };
                render(&app, &label, target, &steps, &state)
            })
            .await
            .map_err(|error| {
                tracing::error!("crawl browser worker: {error}");
                failed("浏览器读取意外中断，请重试")
            })?;
            drop(window);
            result
        })
    }
}

fn failed(message: &str) -> BrowserError {
    BrowserError::Failed(message.into())
}

fn navigation_url(
    target: &PageTarget,
    current: &PageUrl,
    url: &str,
) -> Result<Option<PageUrl>, CrawlError> {
    // These documents stay in memory and must not replace the last network URL.
    if matches!(url, "about:blank" | "about:srcdoc") {
        return Ok(None);
    }
    target.redirect(current, url).map(Some)
}

fn render(
    app: &AppHandle,
    label: &str,
    target: PageTarget,
    steps: &[PageStep],
    state: &Arc<State>,
) -> Result<RenderedPage, BrowserError> {
    state.check()?;
    let nav_state = state.clone();
    let load_state = state.clone();
    let download_state = state.clone();
    let popup_state = state.clone();
    let navigation = Arc::new(Mutex::new((target.url().clone(), false)));
    let nav_navigation = navigation.clone();
    let redirects = Arc::new(AtomicU64::new(0));
    let nav_redirects = redirects.clone();
    let url = target
        .url()
        .as_str()
        .parse()
        .map_err(|_| failed("网页地址无法使用"))?;
    let window = WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
        .visible(false)
        .focused(false)
        .incognito(true)
        .on_navigation(move |url| {
            let mut navigation = nav_navigation
                .lock()
                .unwrap_or_else(|_| unreachable!("navigation updates cannot panic"));
            let (current, started) = &mut *navigation;
            let result = navigation_url(&target, current, url.as_str()).and_then(|next| {
                let Some(next) = next else {
                    return Ok(());
                };
                if *started
                    && nav_redirects.fetch_add(1, Ordering::Relaxed)
                        >= crawl::fetch::MAX_REDIRECTS as u64
                {
                    return Err(crawl::CrawlError::Redirect);
                }
                *started = true;
                *current = next;
                Ok(())
            });
            match result {
                Ok(()) => true,
                Err(error) => {
                    nav_state.fail(error.message());
                    false
                }
            }
        })
        .on_new_window(move |_, _| {
            popup_state.fail("网页尝试打开新窗口，已阻止，请改用具体的名单网址".into());
            NewWindowResponse::Deny
        })
        .on_download(move |_, _| {
            download_state.fail("网页尝试下载文件，已阻止，请改用网页地址".into());
            false
        })
        .on_page_load(move |_, payload| {
            load_state.loaded.store(
                matches!(payload.event(), PageLoadEvent::Finished),
                Ordering::Relaxed,
            );
        })
        .build()
        .map_err(|error| {
            tracing::error!("create crawl browser: {error}");
            failed("无法打开浏览器页面，请重试或重启应用")
        })?;
    state.check()?;
    window.on_window_event({
        let state = state.clone();
        move |event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                state.stopped.store(true, Ordering::Relaxed);
            }
        }
    });
    wait(&window, state, None)?;
    for step in steps {
        let deadline = Instant::now() + TIMEOUT;
        let before = evaluate(&window, state, MATERIAL_SNAPSHOT, deadline)?;
        let control =
            serde_json::to_string(&step.control).map_err(|_| failed("无法读取翻页步骤"))?;
        redirects.store(0, Ordering::Relaxed);
        navigation
            .lock()
            .unwrap_or_else(|_| unreachable!("navigation updates cannot panic"))
            .1 = false;
        let clicked = evaluate(
            &window,
            state,
            &format!("{CLICK_CONTROL}({control})"),
            deadline,
        )?;
        if clicked != json!(true) {
            return Err(failed(&format!("找不到可点击的翻页控件“{}”", step.control)));
        }
        wait(&window, state, Some(&before))?;
    }
    let value = evaluate(
        &window,
        state,
        &format!(
            r#"(() => {{
        const html = document.documentElement.outerHTML;
        if (new TextEncoder().encode(html).length > {}) return {{tooLarge:true}};
        return {{url:location.href,html}};
    }})()"#,
            crawl::fetch::MAX_PAGE_BYTES
        ),
        Instant::now() + TIMEOUT,
    )?;
    if value["tooLarge"] == true {
        return Err(BrowserError::Failed(crawl::CrawlError::TooLarge.message()));
    }
    let url = value["url"]
        .as_str()
        .ok_or_else(|| failed("浏览器未能读取网页地址"))?
        .to_owned();
    let html = value["html"]
        .as_str()
        .ok_or_else(|| failed("浏览器未能读取网页内容"))?
        .to_owned();
    state.check()?;
    Ok(RenderedPage { url, html })
}

fn wait(window: &WebviewWindow, state: &State, before: Option<&Value>) -> Result<(), BrowserError> {
    let deadline = Instant::now() + TIMEOUT;
    let mut previous = Value::Null;
    let mut unchanged = Instant::now();
    loop {
        state.check()?;
        if Instant::now() >= deadline {
            return Err(BrowserError::TimedOut);
        }
        if state.loaded.load(Ordering::Relaxed) {
            let snapshot = evaluate(window, state, SNAPSHOT, deadline)?;
            let changed = if let Some(before) = before {
                evaluate(window, state, MATERIAL_SNAPSHOT, deadline)? != *before
            } else {
                true
            };
            if snapshot["state"] == "complete" && changed && snapshot == previous {
                if unchanged.elapsed() >= STABLE {
                    return Ok(());
                }
            } else {
                unchanged = Instant::now();
                previous = snapshot;
            }
        }
        std::thread::sleep(POLL.min(deadline.saturating_duration_since(Instant::now())));
    }
}

fn evaluate(
    window: &WebviewWindow,
    state: &State,
    script: &str,
    deadline: Instant,
) -> Result<Value, BrowserError> {
    state.check()?;
    let (send, receive) = mpsc::sync_channel(1);
    window
        .eval_with_callback(script, move |value| {
            let _ = send.send(value);
        })
        .map_err(|error| {
            tracing::debug!("evaluate crawl page: {error}");
            failed("浏览器页面已关闭或无法读取，请重试")
        })?;
    loop {
        state.check()?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(BrowserError::TimedOut);
        }
        match receive.recv_timeout(POLL.min(remaining)) {
            Ok(value) => {
                return serde_json::from_str(&value)
                    .map_err(|_| failed("浏览器返回的网页内容无法读取，请重试"));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(failed("浏览器页面已关闭，请重试"));
            }
        }
    }
}

pub fn close_all(app: &AppHandle) {
    for (label, window) in app.webview_windows() {
        if label.starts_with(PREFIX)
            && let Err(error) = window.close()
        {
            tracing::warn!("close crawl browser on exit: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_allows_only_exact_in_memory_documents_without_relaxing_network_scope() {
        let url = PageUrl::parse("https://school.example.edu/list").unwrap();
        let target = crawl::Scope::new(&url).page(url.clone()).unwrap();
        for document in ["about:blank", "about:srcdoc"] {
            assert_eq!(navigation_url(&target, &url, document).unwrap(), None);
        }
        for blocked in [
            "about:blank#fragment",
            "about:blank?query",
            "about:srcdoc#fragment",
            "about:srcdoc?query",
            "about:blank/",
            "about:config",
            "javascript:void(0)",
            "data:text/html,hello",
            "file:///tmp/page.html",
            "http://127.0.0.1/",
            "http://192.168.1.1/",
        ] {
            assert!(
                matches!(navigation_url(&target, &url, blocked), Err(CrawlError::Url)),
                "{blocked}"
            );
        }
        assert!(matches!(
            navigation_url(&target, &url, "https://other.example.org/"),
            Err(CrawlError::OutsideSite)
        ));
        let next = url.join("/teachers").unwrap();
        assert_eq!(
            navigation_url(&target, &url, next.as_str()).unwrap(),
            Some(next)
        );
    }
}
