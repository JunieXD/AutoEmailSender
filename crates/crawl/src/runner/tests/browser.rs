use super::*;
use crate::browser::{BrowserError, RenderedPage};
use std::{
    collections::VecDeque,
    future::Future,
    pin::Pin,
    sync::atomic::{AtomicUsize, Ordering},
};

#[derive(Default)]
struct FakeBrowser {
    results: Mutex<VecDeque<Result<RenderedPage, BrowserError>>>,
    steps: Mutex<Vec<Vec<PageStep>>>,
    entered: Notify,
    release: Notify,
    active: AtomicUsize,
}
impl FakeBrowser {
    fn page(&self, url: &PageUrl, html: &str) {
        self.results.lock().unwrap().push_back(Ok(RenderedPage {
            url: url.as_str().into(),
            html: html.into(),
        }));
    }
}
impl Browser for FakeBrowser {
    fn render<'a>(
        &'a self,
        _: &'a PageTarget,
        steps: &'a [PageStep],
    ) -> Pin<Box<dyn Future<Output = Result<RenderedPage, BrowserError>> + Send + 'a>> {
        Box::pin(async move {
            struct Active<'a>(&'a AtomicUsize);
            impl Drop for Active<'_> {
                fn drop(&mut self) {
                    self.0.fetch_sub(1, Ordering::SeqCst);
                }
            }
            self.active.fetch_add(1, Ordering::SeqCst);
            let _active = Active(&self.active);
            self.steps.lock().unwrap().push(steps.to_vec());
            self.entered.notify_one();
            let result = self.results.lock().unwrap().pop_front();
            if let Some(result) = result {
                return result;
            }
            self.release.notified().await;
            self.results.lock().unwrap().pop_front().unwrap()
        })
    }
}

fn challenge(site: &Site) {
    site.pages.lock().unwrap().insert(
        "/".into(),
        (
            412,
            "<p>HTTP 已读文字</p><script>challenge()</script>".into(),
        ),
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn browser_extracts_people_and_replays_each_list_page(db: SqlitePool) {
    let site = Site::new().await;
    challenge(&site);
    let browser = Arc::new(FakeBrowser::default());
    browser.page(
        &site.url("/"),
        "<title>名单</title><p>张三 a@school.example.edu</p><nav><button onclick='next()'>\
        下一页</button></nav>",
    );
    browser.page(
        &site.url("/"),
        "<title>名单</title><p>李四 b@school.example.edu</p><nav><button onclick='next()'>\
        下一页</button></nav>",
    );
    browser.page(
        &site.url("/"),
        "<title>名单</title><p>王五 c@school.example.edu</p><nav><button disabled>\
        下一页</button></nav>",
    );
    let mut provider = Provider::start().await.unwrap();
    let model = config(&db, &provider.address).await;
    let responder = tokio::spawn(async move {
        while let Some(request) = provider.requests.recv().await {
            let system = request.body["messages"][0]["content"].as_str().unwrap();
            let data = request.body["messages"][1]["content"].as_str().unwrap();
            let output = if system.starts_with("你整理学院") {
                json!({"kind":"list","next":[]})
            } else {
                let (name, email) = if data.contains("张三") {
                    ("张三", "a@school.example.edu")
                } else if data.contains("李四") {
                    ("李四", "b@school.example.edu")
                } else {
                    ("王五", "c@school.example.edu")
                };
                json!({"people":[listed(name, None, vec![email])]})
            };
            assert!(
                request
                    .respond
                    .send(Response::answer(&output.to_string()))
                    .is_ok()
            );
        }
    });
    let id = queue::start(&db, &[site.url("/")], model, 1).await.unwrap();
    let pages = Pages {
        writes: db::Writes::default(),
        http: Fetcher::local(site.port),
        browser: Some(browser.clone()),
    };
    let net = net::Net::new(vec![]).unwrap();
    let limit = Limit::default();
    for _ in 0..3 {
        let claim = queue::next(&db).await.unwrap().unwrap();
        let outcome = process(&db, &net, &pages, &limit, &claim, &Cooldown::default())
            .await
            .unwrap();
        assert!(matches!(outcome, Outcome::Done(_)));
        assert!(save(&db, &claim, &outcome).await.unwrap());
    }
    assert!(queue::next(&db).await.unwrap().is_none());
    let records: Vec<(String, String)> =
        sqlx::query_as("SELECT mode,status FROM crawl_page WHERE crawl_id=? ORDER BY id")
            .bind(id)
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(records, vec![("webview".into(), "success".into()); 3]);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM candidate WHERE crawl_id=?")
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap(),
        3
    );
    assert_eq!(
        *browser.steps.lock().unwrap(),
        vec![
            vec![],
            vec![PageStep {
                page: 2,
                control: "下一页".into()
            }],
            vec![
                PageStep {
                    page: 2,
                    control: "下一页".into()
                },
                PageStep {
                    page: 3,
                    control: "下一页".into()
                }
            ]
        ]
    );
    responder.abort();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn browser_failures_preserve_http_text_and_preview_still_needs_browser(db: SqlitePool) {
    let site = Site::new().await;
    challenge(&site);
    let provider = Provider::start().await.unwrap();
    let model = config(&db, &provider.address).await;
    let id = queue::start(&db, &[site.url("/")], model, 1).await.unwrap();
    let mut claim = queue::next(&db).await.unwrap().unwrap();
    let browser = Arc::new(FakeBrowser::default());
    let pages = Pages {
        writes: db::Writes::default(),
        http: Fetcher::local(site.port),
        browser: Some(browser.clone()),
    };
    let target = Scope::new(&claim.scope).page(claim.url.clone()).unwrap();
    for (result, reason) in [
        (
            Ok(RenderedPage {
                url: site.url("/").as_str().into(),
                html: include_str!("../../../tests/pages/bupt.html").into(),
            }),
            "验证",
        ),
        (Err(BrowserError::TimedOut), "20 秒"),
        (
            Err(BrowserError::Failed(
                "网站的证书有问题，浏览器打不开".into(),
            )),
            "证书",
        ),
        (
            Ok(RenderedPage {
                url: site.url("/").as_str().into(),
                html: "<div id='app'></div><script type='module' src='app.js'></script>".into(),
            }),
            "仍未取得",
        ),
        (
            Ok(RenderedPage {
                url: site.url("/").as_str().into(),
                html: String::new(),
            }),
            "验证",
        ),
        (
            Ok(RenderedPage {
                url: "http://127.0.0.1/private".into(),
                html: "<p>private</p>".into(),
            }),
            "公开",
        ),
        (
            Ok(RenderedPage {
                url: "https://outside.example.org/".into(),
                html: "<p>outside</p>".into(),
            }),
            "允许",
        ),
        (
            Ok(RenderedPage {
                url: site.url("/").as_str().into(),
                html: "x".repeat(crate::fetch::MAX_PAGE_BYTES + 1),
            }),
            "过大",
        ),
    ] {
        browser.results.lock().unwrap().push_back(result);
        let Err(ReadError::Failed(error)) = pages.fetch(&db, &claim, &target).await else {
            panic!("browser should fail");
        };
        assert!(error.contains(reason), "{error}");
        assert!(save(&db, &claim, &Outcome::Failed(error)).await.unwrap());
        let row: (String, String, String) =
            sqlx::query_as("SELECT status,mode,text FROM crawl_page WHERE id=?")
                .bind(claim.id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(
            row,
            ("failed".into(), "webview".into(), "HTTP 已读文字".into())
        );
        queue::retry(&db, id, Some(claim.id), 2).await.unwrap();
        claim = queue::next(&db).await.unwrap().unwrap();
    }
    let m = Pages::local(site.port)
        .fetch(&db, &claim, &target)
        .await
        .unwrap_or_else(|_| panic!("preview HTTP material"));
    assert_eq!(m.needs_browser, Some(crate::BrowserReason::Challenge));
    assert_eq!(m.text(), "HTTP 已读文字");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pause_and_cancel_drop_browser_and_reject_late_results(db: SqlitePool) {
    let site = Site::new().await;
    challenge(&site);
    let provider = Provider::start().await.unwrap();
    let model = config(&db, &provider.address).await;
    for action in ["pause", "cancel"] {
        let id = queue::start(&db, &[site.url("/")], model, 1).await.unwrap();
        let claim = queue::next(&db).await.unwrap().unwrap();
        let browser = Arc::new(FakeBrowser::default());
        let pages = Pages {
            writes: db::Writes::default(),
            http: Fetcher::local(site.port),
            browser: Some(browser.clone()),
        };
        let target = Scope::new(&claim.scope).page(claim.url.clone()).unwrap();
        let read = pages.fetch(&db, &claim, &target);
        tokio::pin!(read);
        tokio::select! {
            _ = browser.entered.notified() => {},
            _ = &mut read => panic!("browser must remain in flight"),
        }
        queue::control(&db, id, action, 2).await.unwrap();
        tokio::time::pause();
        tokio::time::advance(Duration::from_millis(501)).await;
        assert!(matches!(read.await, Err(ReadError::Gone)));
        tokio::time::resume();
        assert_eq!(browser.active.load(Ordering::SeqCst), 0);
        let before: (String, Option<String>, String) =
            sqlx::query_as("SELECT status,error,text FROM crawl_page WHERE id=?")
                .bind(claim.id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert!(
            !save(&db, &claim, &Outcome::Failed("迟到的浏览器结果".into()))
                .await
                .unwrap()
        );
        let after: (String, Option<String>, String) =
            sqlx::query_as("SELECT status,error,text FROM crawl_page WHERE id=?")
                .bind(claim.id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(before, after);
        assert_eq!(after.2, "HTTP 已读文字");
        if action == "pause" {
            queue::control(&db, id, "cancel", 3).await.unwrap();
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn page_failures_and_browser_takeover_log_the_crawl_without_private_content(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("crawl.log");
    let file = std::fs::File::create(&log_path).unwrap();
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_ansi(false)
        .with_writer(Mutex::new(file))
        .finish();
    // Two scoped dispatchers avoid tracing-core caching callsites from unsubscribed test threads.
    let _quiet = tracing::subscriber::set_default(tracing::subscriber::NoSubscriber::default());
    let guard = tracing::subscriber::set_default(subscriber);

    let site = Site::new().await;
    let provider = Provider::start().await.unwrap();
    let model = config(&db, &provider.address).await;
    let id = queue::start(&db, &[site.url("/missing")], model, 1)
        .await
        .unwrap();
    let browser = Arc::new(FakeBrowser::default());
    browser
        .results
        .lock()
        .unwrap()
        .push_back(Err(BrowserError::TimedOut));
    let pages = Pages {
        writes: db::Writes::default(),
        http: Fetcher::local(site.port),
        browser: Some(browser),
    };
    let net = net::Net::new(vec![]).unwrap();
    let limit = Limit::default();
    for challenge in [false, true] {
        if challenge {
            site.pages.lock().unwrap().insert(
                "/missing".into(),
                (
                    412,
                    "<p>PRIVATE PAGE 张三 secret@school.example.edu</p><script>challenge()</script>"
                        .into(),
                ),
            );
            queue::retry(&db, id, None, 2).await.unwrap();
        }
        let claim = queue::next(&db).await.unwrap().unwrap();
        let outcome = process(&db, &net, &pages, &limit, &claim, &Cooldown::default())
            .await
            .unwrap();
        assert!(matches!(outcome, Outcome::Failed(_)));
        save(&db, &claim, &outcome).await.unwrap();
    }
    drop(guard);
    let log = std::fs::read_to_string(log_path).unwrap();
    let marker = format!("crawl{{id={id}}}:");
    for diagnostic in ["page returned HTTP 404", "Challenge", "crawl pages retried"] {
        assert!(
            log.lines()
                .any(|line| line.contains(&marker) && line.contains(diagnostic)),
            "missing crawl-scoped {diagnostic}: {log}"
        );
    }
    for private in [
        "PRIVATE PAGE",
        "张三",
        "secret@school.example.edu",
        "fixture",
    ] {
        assert!(!log.contains(private));
    }
}
