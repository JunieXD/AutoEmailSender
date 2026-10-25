use super::*;
use crate::{
    extract,
    review::{CandidateFilter, CandidateQuery, ReviewInput},
};
use mentor::{MentorFields, import::Decisions};
use serde_json::{Value, json};
use std::sync::Mutex;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    sync::mpsc,
};
#[path = "../../../model/tests/common/provider.rs"]
mod provider;
use provider::{Provider, Response};
struct Site {
    port: u16,
    pages: Arc<Mutex<HashMap<String, (u16, String)>>>,
    visits: Arc<Mutex<Vec<String>>>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for Site {
    fn drop(&mut self) {
        self.task.abort();
    }
}
impl Site {
    async fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let pages = Arc::new(Mutex::new(HashMap::<String, (u16, String)>::new()));
        let visits = Arc::new(Mutex::new(Vec::new()));
        let (p, v) = (pages.clone(), visits.clone());
        let task = tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let (p, v) = (p.clone(), v.clone());
                tokio::spawn(async move {
                    let mut io = BufReader::new(stream);
                    let mut line = String::new();
                    io.read_line(&mut line).await.unwrap();
                    let path = line.split_whitespace().nth(1).unwrap().to_owned();
                    loop {
                        let mut h = String::new();
                        io.read_line(&mut h).await.unwrap();
                        if h == "\r\n" {
                            break;
                        }
                    }
                    v.lock().unwrap().push(path.clone());
                    let (status, body) = p
                        .lock()
                        .unwrap()
                        .get(&path)
                        .cloned()
                        .unwrap_or((404, "not found".into()));
                    let response = format!(
                        "HTTP/1.1 {status} Reply\r\nConnection: close\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = io.write_all(response.as_bytes()).await;
                });
            }
        });
        Self {
            port,
            pages,
            visits,
            task,
        }
    }
    fn page(&self, path: &str, body: &str) {
        self.pages
            .lock()
            .unwrap()
            .insert(path.into(), (200, body.into()));
    }
    fn url(&self, path: &str) -> PageUrl {
        PageUrl::parse(&format!("http://school.example.edu:{}{path}", self.port)).unwrap()
    }
}
async fn config(db: &SqlitePool, address: &str) -> i64 {
    let f = model::ModelForm {
        name: "假模型".into(),
        provider: "custom".into(),
        base_url: address.into(),
        model: "fixture".into(),
        input_price: None,
        output_price: None,
    }
    .check()
    .unwrap();
    let id = model::create(db, &f, "fixture", 1).await.unwrap();
    model::agree(db, id, "custom", Some(address)).await.unwrap();
    id
}
fn listed(name: &str, profile: Option<usize>, emails: Vec<&str>) -> Value {
    json!({"name":name,"title":"教授","profile":profile,"emails":emails,"research":null})
}
fn person(name: &str, email: &str) -> Value {
    json!({"name":name,"aliases":[],"emails":[email,"invented@example.edu"],"affiliations":[],"homepages":[],"research":"机器学习","papers":["论文一"]})
}
async fn changed_until(
    db: &SqlitePool,
    id: i64,
    events: &mut mpsc::UnboundedReceiver<()>,
    state: &str,
) {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let actual = sqlx::query_scalar::<_, String>("SELECT state FROM crawl WHERE id=?")
                .bind(id)
                .fetch_one(db)
                .await
                .unwrap();
            if actual == state {
                break;
            }
            events.recv().await.unwrap();
        }
    })
    .await
    .unwrap();
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn home_categories_pagination_profiles_evidence_and_retry_end_to_end(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/",
        "<title>学院</title><a href='/list'>师资队伍</a><a href='/news'>学院新闻</a>",
    );
    site.page("/list", &format!("<title>教师名单</title><article><p><a href='/alice'>张三</a> a@school.example.edu</p><p><a href='http://outside.example.org:{}/bob'>李四</a> b@school.example.edu</p></article><a href='/category'>副教授</a><nav><a href='/list?page=1'>1</a><a href='/list?page=2'>2</a></nav><a href='/missing'>讲师名单</a><a href='/browser'>动态名单</a>",site.port));
    site.page(
        "/category",
        "<title>副教授</title><p><a href='/alternate-alice'>张三</a> a@school.example.edu</p>",
    );
    site.page(
        "/list?page=1",
        "<title>教师名单一</title><p><a href='/alice'>张三</a></p>",
    );
    site.page(
        "/list?page=2",
        "<title>教师名单二</title><p>王五 c@school.example.edu</p>",
    );
    site.page(
        "/alice",
        "<title>张三</title><p>张三 a@school.example.edu 机器学习</p>",
    );
    site.page("/bob","<title>李四</title><p>李四 b@school.example.edu 机器学习</p><a href='/must-not-visit'>下一页</a>");
    site.page(
        "/browser",
        "<div id='app'></div><script type='module' src='app.js'></script>",
    );
    let mut provider = Provider::start().await;
    let id = config(&db, &provider.address).await;
    let model = tokio::spawn(async move {
        while let Some(r) = provider.requests.recv().await {
            let system = r.body["messages"][0]["content"].as_str().unwrap();
            let data: Value =
                serde_json::from_str(r.body["messages"][1]["content"].as_str().unwrap()).unwrap();
            let title = data["title"].as_str().unwrap_or("");
            let answer = if system.starts_with("你整理学院") {
                let next: Vec<_> = data["links"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|l| {
                        ["师资队伍", "副教授", "讲师名单", "动态名单"]
                            .contains(&l["text"].as_str().unwrap())
                    })
                    .map(|l| l["id"].clone())
                    .chain(std::iter::once(json!(99999)))
                    .collect();
                json!({"kind":if title=="学院"{"entry"}else{"list"},"next":next})
            } else if system.starts_with("你从教师") {
                if title == "教师名单二" {
                    json!({"people":[listed("王五",None,vec!["c@school.example.edu"])]})
                } else if title == "教师名单" {
                    json!({"people":[listed("张三",Some(1),vec!["a@school.example.edu","b@school.example.edu","invented@example.edu"]),listed("李四",Some(2),vec!["a@school.example.edu"])]})
                } else {
                    json!({"people":[listed("张三",Some(1),vec!["a@school.example.edu"])]})
                }
            } else {
                person(
                    title,
                    if title == "张三" {
                        "a@school.example.edu"
                    } else {
                        "b@school.example.edu"
                    },
                )
            };
            r.respond.send(Response::answer(&answer.to_string())).ok();
        }
    });
    let crawl = queue::start(&db, &[site.url("/")], id, 1).await.unwrap();
    let runner = Crawler::default();
    let (tx, mut events) = mpsc::unbounded_channel();
    let (done, mut finished) = mpsc::unbounded_channel();
    let task = tokio::spawn(run_with_fetcher(
        db.clone(),
        net::Net::new(vec![]).unwrap(),
        Arc::new(Fetcher::local(site.port)),
        runner.clone(),
        Limit::default(),
        move || {
            tx.send(()).ok();
        },
        move |r| {
            done.send(r).ok();
        },
    ));
    changed_until(&db, crawl, &mut events, "done").await;
    let result = finished.recv().await.unwrap();
    assert_eq!(result.candidates, 3);
    assert_eq!(result.failed, 2);
    let candidates = review::list(
        &db,
        &CandidateQuery {
            crawl_id: Some(crawl),
            processed: false,
            filter: CandidateFilter::All,
            page: 1,
        },
    )
    .await
    .unwrap();
    assert_eq!(candidates.total, 3);
    for c in candidates.candidates {
        assert!(!c.emails.contains(&"invented@example.edu".into()));
        if c.name == "张三" {
            let detail = review::detail(&db, c.id).await.unwrap().unwrap();
            let list = detail
                .evidence
                .iter()
                .find(|e| e.url.ends_with("/list"))
                .unwrap();
            assert_eq!(list.emails, vec!["a@school.example.edu"]);
            assert_eq!(c.emails, vec!["a@school.example.edu"]);
        }
    }
    assert!(
        !site
            .visits
            .lock()
            .unwrap()
            .iter()
            .any(|p| p == "/news" || p == "/must-not-visit" || p == "/alternate-alice")
    );
    let detail = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
    assert!(detail.pages.iter().any(|p| p.status == "needs_browser"));
    site.page(
        "/missing",
        "<title>教师名单二</title><p>王五 c@school.example.edu</p>",
    );
    let failed = detail
        .pages
        .iter()
        .find(|p| p.url.ends_with("/missing"))
        .unwrap()
        .id;
    assert_eq!(queue::retry(&db, crawl, Some(failed), 2).await.unwrap(), 1);
    runner.wake();
    changed_until(&db, crawl, &mut events, "done").await;
    let detail = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
    assert_eq!(
        detail.pages.iter().find(|p| p.id == failed).unwrap().status,
        "success"
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM candidate WHERE crawl_id=?")
            .bind(crawl)
            .fetch_one(&db)
            .await
            .unwrap(),
        3
    );
    assert!(
        sqlx::query_scalar::<_, i64>(
            "SELECT sum(input_tokens+output_tokens) FROM usage WHERE crawl_id=?"
        )
        .bind(crawl)
        .fetch_one(&db)
        .await
        .unwrap()
            > 0
    );
    task.abort();
    model.abort();
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn claims_pause_resume_cancel_restart_and_limits(db: SqlitePool) {
    let mut provider = Provider::start().await;
    let config = config(&db, &provider.address).await;
    let site = Site::new().await;
    site.page("/", "<title>张三</title><p>张三 a@school.example.edu</p>");
    let id = queue::start(&db, &[site.url("/")], config, 1)
        .await
        .unwrap();
    assert!(
        crate::store::visited_page(&db, id, &site.url("/"), &[])
            .await
            .unwrap()
            .is_none()
    );
    let old = queue::next(&db).await.unwrap().unwrap();
    queue::control(&db, id, "pause", 2).await.unwrap();
    assert!(
        !save(&db, &old, &Outcome::Failed("旧错误".into()))
            .await
            .unwrap()
    );
    queue::control(&db, id, "resume", 3).await.unwrap();
    let interrupted = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    assert!(
        !save(&db, &interrupted, &Outcome::Failed("旧错误".into()))
            .await
            .unwrap()
    );
    let next = queue::next(&db).await.unwrap().unwrap();
    queue::control(&db, id, "cancel", 4).await.unwrap();
    assert!(
        !save(&db, &next, &Outcome::Failed("旧错误".into()))
            .await
            .unwrap()
    );
    let id = queue::start(&db, &[site.url("/")], config, 5)
        .await
        .unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let process = tokio::spawn({
        let db = db.clone();
        let port = site.port;
        let c = c.clone();
        async move {
            super::process(
                &db,
                &net::Net::new(vec![]).unwrap(),
                &Fetcher::local(port),
                &Limit::default(),
                &c,
            )
            .await
            .unwrap()
        }
    });
    let r = provider.requests.recv().await.unwrap();
    r.respond
        .send(Response::Refused {
            status: 401,
            message: "invalid key".into(),
            retry_after: None,
        })
        .ok();
    let result = process.await.unwrap();
    save(&db, &c, &result).await.unwrap();
    assert_eq!(queue::list(&db, 1).await.unwrap()[0].state, "paused");
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for n in 0..70 {
        queue::enqueue(
            &mut tx,
            id,
            &site.url(&format!("/category/{n}")),
            &site.url("/"),
            1,
            "entry",
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM crawl_page WHERE crawl_id=?")
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap(),
        60
    );
    assert!(
        !queue::detail(&db, id, 1)
            .await
            .unwrap()
            .unwrap()
            .limits
            .is_empty()
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn candidate_identity_person_limit_and_review_transactions(db: SqlitePool) {
    let site = Site::new().await;
    let config = config(&db, "http://127.0.0.1:1").await;
    let crawl = queue::start(&db, &[site.url("/")], config, 1)
        .await
        .unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let existing = mentor::create(
        &db,
        &MentorFields {
            name: "李四".into(),
            emails: vec!["li@example.edu".into()],
            research: Some("本地方向".into()),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    let other = mentor::create(
        &db,
        &MentorFields {
            name: "王五".into(),
            emails: vec!["wang@example.edu".into()],
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    let third = mentor::create(
        &db,
        &MentorFields {
            name: "赵六".into(),
            emails: vec!["zhao@example.edu".into()],
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    let _ = third;
    let same = mentor::create(
        &db,
        &MentorFields {
            name: "张三".into(),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for (name, emails, profile) in [
        ("新导师", vec!["new@example.edu"], Some("/new")),
        (
            "李四",
            vec!["li@example.edu", "extra@school.example.edu"],
            Some("/li"),
        ),
        ("张三", vec![], None),
        ("冲突", vec!["zhao@example.edu", "wang@example.edu"], None),
    ] {
        review::save(
            &mut tx,
            crawl,
            claim.id,
            "profile",
            site.url("/").as_str(),
            Person {
                fields: MentorFields {
                    name: name.into(),
                    emails: emails.into_iter().map(Into::into).collect(),
                    research: Some("抓取方向".into()),
                    ..Default::default()
                },
                papers: vec!["论文".into()],
                profile: profile.map(|p| site.url(p)),
            },
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    let q = CandidateQuery {
        crawl_id: Some(crawl),
        processed: false,
        filter: CandidateFilter::All,
        page: 1,
    };
    let page = review::list(&db, &q).await.unwrap();
    assert_eq!(page.total, 4);
    let new = page.candidates.iter().find(|c| c.name == "新导师").unwrap();
    assert_eq!(new.category, CandidateFilter::New);
    let supplement = page.candidates.iter().find(|c| c.name == "李四").unwrap();
    assert_eq!(supplement.category, CandidateFilter::Supplement);
    let maybe = page.candidates.iter().find(|c| c.name == "张三").unwrap();
    assert_eq!(maybe.category, CandidateFilter::Maybe);
    let conflict = page.candidates.iter().find(|c| c.name == "冲突").unwrap();
    assert!(conflict.issue.is_some());
    let input = ReviewInput {
        ids: vec![new.id, supplement.id, maybe.id, conflict.id],
        decisions: Decisions::default(),
    };
    let preview = review::preview(&db, &input).await.unwrap();
    assert_eq!(preview.maybe_same[0].selected, Some(same));
    let result = review::accept(&db, &input, 2).await.unwrap();
    assert_eq!(result.accepted, 3);
    assert_eq!(result.skipped, 1);
    let updated = mentor::get(&db, existing).await.unwrap().unwrap();
    assert_eq!(updated.research.as_deref(), Some("本地方向"));
    assert!(updated.emails.contains(&"extra@school.example.edu".into()));
    assert_eq!(review::accept(&db, &input, 3).await.unwrap().accepted, 0);
    assert_eq!(
        review::detail(&db, maybe.id)
            .await
            .unwrap()
            .unwrap()
            .candidate
            .mentor_id,
        Some(same)
    );
    assert_eq!(review::reject(&db, &[conflict.id]).await.unwrap(), 1);
    assert!(mentor::get(&db, other).await.unwrap().is_some());
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for n in 0..1001 {
        review::save(
            &mut tx,
            crawl,
            claim.id,
            "profile",
            site.url("/").as_str(),
            Person {
                fields: MentorFields {
                    name: format!("候选{n}"),
                    ..Default::default()
                },
                papers: vec![],
                profile: Some(site.url(&format!("/limit/{n}"))),
            },
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM candidate WHERE crawl_id=?")
            .bind(crawl)
            .fetch_one(&db)
            .await
            .unwrap(),
        1000
    );
    queue::delete(&db, crawl).await.unwrap();
    assert!(review::detail(&db, new.id).await.unwrap().is_some());
    assert_eq!(
        review::detail(&db, new.id)
            .await
            .unwrap()
            .unwrap()
            .candidate
            .crawl_id,
        None
    );
}
#[test]
fn segments_preserve_blocks_and_external_output_is_checked() {
    assert_eq!(
        queue::starts(
            "HTTPS://SCHOOL.EXAMPLE.EDU:443/list#one\nhttps://school.example.edu/list#two"
        )
        .unwrap()
        .len(),
        1
    );
    assert!(queue::starts("https://school.example.edu/list\nhttp://127.0.0.1/").is_err());
    let url = PageUrl::parse("https://school.example.edu/list").unwrap();
    let m = crate::parse(
        &url,
        &format!(
            "<p>{}</p><p>{}</p><p>李四 a@example.edu</p>",
            "甲".repeat(4000),
            "乙".repeat(4000)
        ),
    )
    .unwrap();
    let segments = extract::segments(&m);
    assert_eq!(segments.len(), 2);
    assert!(
        extract::parse::<extract::Route>(r#"{"kind":"list","next":["https://evil.example"]}"#)
            .is_err()
    );
    let people = extract::listed(
        &m,
        vec![extract::Listed {
            name: "李四".into(),
            title: None,
            profile: Some(999),
            emails: vec!["invented@example.edu".into()],
            research: None,
        }],
        &Scope::new(&url),
    );
    assert_eq!(people.len(), 1);
    assert!(people[0].fields.emails.is_empty());
    assert!(people[0].profile.is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn crawling_matching_and_drafting_share_three_actual_calls(db: SqlitePool) {
    let mut p = Provider::start().await;
    let config = config(&db, &p.address).await;
    let site = Site::new().await;
    for n in 0..3 {
        site.page(
            &format!("/p{n}"),
            "<title>张三</title><p>张三 a@school.example.edu 机器学习</p>",
        );
    }
    let urls: Vec<_> = (0..3).map(|n| site.url(&format!("/p{n}"))).collect();
    queue::start(&db, &urls, config, 1).await.unwrap();
    let mut ids = Vec::new();
    for n in 0..6 {
        ids.push(
            mentor::create(
                &db,
                &MentorFields {
                    name: format!("导师{n}"),
                    emails: vec![format!("m{n}@example.edu")],
                    research: Some("机器学习".into()),
                    ..Default::default()
                },
                1,
            )
            .await
            .unwrap(),
        );
    }
    sqlx::query("UPDATE profile SET research_interests='机器学习'")
        .execute(&db)
        .await
        .unwrap();
    mentor::matching::enqueue(&db, &ids, false, true, 1)
        .await
        .unwrap();
    task::create(
        &db,
        &task::NewTask {
            name: "写信".into(),
            ids,
            writer: task::Writer::Ai,
            template_id: None,
            model_config_id: Some(config),
            references: vec![],
            attachments: vec![],
        },
        1,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let limit = Limit::default();
    let crawl = tokio::spawn(run_with_fetcher(
        db.clone(),
        net.clone(),
        Arc::new(Fetcher::local(site.port)),
        Crawler::default(),
        limit.clone(),
        || {},
        |_| {},
    ));
    let matching = tokio::spawn(matching::run(
        db.clone(),
        net.clone(),
        matching::Matcher::default(),
        limit.clone(),
        || {},
        |_| {},
    ));
    let writing = tokio::spawn(task::generation::run(
        db.clone(),
        net,
        task::generation::Generator::default(),
        limit,
        || {},
        |_| {},
    ));
    let mut active = Vec::new();
    for _ in 0..3 {
        active.push(p.requests.recv().await.unwrap());
    }
    tokio::time::pause();
    assert!(
        tokio::time::timeout(Duration::from_secs(1), p.requests.recv())
            .await
            .is_err()
    );
    tokio::time::resume();
    let (mut matches, mut drafts, mut routes, mut profiles) = (0, 0, 0, 0);
    for _ in 0..18 {
        // A route's next call needs its response first; do not hold that response while
        // waiting for a replacement call after the other two queues have drained.
        let r = if active.is_empty() {
            tokio::time::timeout(Duration::from_secs(10), p.requests.recv())
                .await
                .unwrap()
                .unwrap()
        } else {
            active.remove(0)
        };
        let system = r.body["messages"][0]["content"].as_str().unwrap();
        let answer = if system.starts_with("你整理学院") {
            routes += 1;
            json!({"kind":"profile","next":[]}).to_string()
        } else if system.starts_with("你整理一位") {
            profiles += 1;
            person("张三", "a@school.example.edu").to_string()
        } else if system.contains("分数不是录取") {
            matches += 1;
            r#"{"score":80,"reason":"方向相关","directions":[],"highlights":[],"risks":[]}"#.into()
        } else {
            drafts += 1;
            "<subject>申请</subject><body>老师：您好，申请读研。</body>".into()
        };
        r.respond.send(Response::answer(&answer)).ok().unwrap();
    }
    assert_eq!((matches, drafts, routes, profiles), (6, 6, 3, 3));
    crawl.abort();
    matching.abort();
    writing.abort();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn late_results_restart_and_retry_after_preserve_work(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/", include_str!("../../tests/pages/toronto.html"));
    let mut p = Provider::start().await;
    let config = config(&db, &p.address).await;
    let id = queue::start(&db, &[site.url("/")], config, 1)
        .await
        .unwrap();
    let handle = Crawler::default();
    let (tx, mut events) = mpsc::unbounded_channel();
    let start = |tx: mpsc::UnboundedSender<()>| {
        tokio::spawn(run_with_fetcher(
            db.clone(),
            net::Net::new(vec![]).unwrap(),
            Arc::new(Fetcher::local(site.port)),
            handle.clone(),
            Limit::default(),
            move || {
                tx.send(()).ok();
            },
            |_| {},
        ))
    };
    let task = start(tx.clone());
    let old = p.requests.recv().await.unwrap();
    queue::control(&db, id, "pause", 2).await.unwrap();
    handle.wake();
    old.respond
        .send(Response::answer(r#"{"kind":"profile","next":[]}"#))
        .ok();
    queue::control(&db, id, "resume", 3).await.unwrap();
    handle.wake();
    let interrupted = p.requests.recv().await.unwrap();
    task.abort();
    let task = start(tx);
    let current = p.requests.recv().await.unwrap();
    interrupted
        .respond
        .send(Response::answer(r#"{"kind":"profile","next":[]}"#))
        .ok();
    let guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    while events.try_recv().is_ok() {}
    current
        .respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(60),
        })
        .ok();
    loop {
        events.recv().await.unwrap();
        let queued = sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM crawl_page WHERE crawl_id=? AND work_state='queued'",
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
        if queued == 1 {
            break;
        }
    }
    tokio::time::advance(Duration::from_secs(59)).await;
    assert!(p.requests.try_recv().is_err());
    tokio::time::advance(Duration::from_secs(2)).await;
    tokio::time::resume();
    guard.abort();
    let current = tokio::time::timeout(Duration::from_secs(10), p.requests.recv())
        .await
        .unwrap()
        .unwrap();
    current
        .respond
        .send(Response::answer(r#"{"kind":"profile","next":[]}"#))
        .ok();
    let profile = p.requests.recv().await.unwrap();
    profile
        .respond
        .send(Response::answer(
            &person("Geoffrey Hinton", "geoffrey.hinton@gmail.com").to_string(),
        ))
        .ok();
    changed_until(&db, id, &mut events, "done").await;
    let candidates = review::list(
        &db,
        &CandidateQuery {
            crawl_id: Some(id),
            processed: false,
            filter: CandidateFilter::All,
            page: 1,
        },
    )
    .await
    .unwrap();
    assert_eq!(candidates.total, 1);
    assert_eq!(
        candidates.candidates[0].emails,
        vec!["geoffrey.hinton@gmail.com"]
    );
    let detail = queue::detail(&db, id, 1).await.unwrap().unwrap();
    assert!(detail.runs.len() >= 3);
    assert!(
        crate::store::page_text(&db, detail.pages[0].id)
            .await
            .unwrap()
            .unwrap()
            .contains("Hinton")
    );
    task.abort();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_explicit_targets_differences_and_trash_follow_the_shared_rules(db: SqlitePool) {
    let site = Site::new().await;
    let config = config(&db, "http://127.0.0.1:1").await;
    let crawl = queue::start(&db, &[site.url("/")], config, 1)
        .await
        .unwrap();
    let page = queue::next(&db).await.unwrap().unwrap();
    let mut locals = Vec::new();
    for research in ["本地方向甲", "本地方向乙"] {
        locals.push(
            mentor::create(
                &db,
                &MentorFields {
                    name: "同名导师".into(),
                    research: Some(research.into()),
                    ..Default::default()
                },
                1,
            )
            .await
            .unwrap(),
        );
    }
    let trash = mentor::create(
        &db,
        &MentorFields {
            name: "回收站导师".into(),
            emails: vec!["trash@example.edu".into()],
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    mentor::trash::trash(&db, &[trash], 2).await.unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for (name, emails) in [
        ("同名导师", vec![]),
        ("回收站导师", vec!["trash@example.edu".into()]),
    ] {
        review::save(
            &mut tx,
            crawl,
            page.id,
            "profile",
            site.url("/").as_str(),
            Person {
                fields: MentorFields {
                    name: name.into(),
                    emails,
                    research: Some("新的研究方向".into()),
                    ..Default::default()
                },
                papers: vec![],
                profile: None,
            },
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    let ids = sqlx::query_scalar::<_, i64>("SELECT id FROM candidate ORDER BY id")
        .fetch_all(&db)
        .await
        .unwrap();
    let mut input = ReviewInput {
        ids: ids.clone(),
        decisions: Decisions::default(),
    };
    let p = review::preview(&db, &input).await.unwrap();
    assert_eq!(p.maybe_same[0].candidates.len(), 2);
    assert_eq!(p.maybe_same[0].selected, None);
    assert_eq!(p.trashed.len(), 1);
    input.decisions.matches.push(mentor::import::GroupChoice {
        row: 1,
        id: Some(locals[1]),
    });
    input.decisions.take = review::preview(&db, &input).await.unwrap().differences;
    let result = review::accept(&db, &input, 3).await.unwrap();
    assert_eq!((result.accepted, result.skipped), (1, 1));
    assert_eq!(
        mentor::get(&db, locals[1])
            .await
            .unwrap()
            .unwrap()
            .research
            .as_deref(),
        Some("新的研究方向")
    );
    assert_eq!(
        mentor::get(&db, locals[0])
            .await
            .unwrap()
            .unwrap()
            .research
            .as_deref(),
        Some("本地方向甲")
    );
    input.decisions.restore.push(trash);
    assert_eq!(review::accept(&db, &input, 4).await.unwrap().accepted, 1);
    assert!(!mentor::get(&db, trash).await.unwrap().unwrap().trashed);
    // Removing an earlier selected candidate cannot redirect a saved group choice to another row.
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for n in 0..2 {
        review::save(
            &mut tx,
            crawl,
            page.id,
            "profile",
            site.url("/").as_str(),
            Person {
                fields: MentorFields {
                    name: "同名导师".into(),
                    research: Some(format!("不同{n}")),
                    ..Default::default()
                },
                papers: vec![],
                profile: Some(site.url(&format!("/same/{n}"))),
            },
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    let pending =
        sqlx::query_scalar::<_, i64>("SELECT id FROM candidate WHERE review='pending' ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    let input = ReviewInput {
        ids: pending.clone(),
        decisions: Decisions {
            matches: vec![mentor::import::GroupChoice {
                row: 1,
                id: Some(locals[0]),
            }],
            ..Default::default()
        },
    };
    sqlx::query("DELETE FROM candidate WHERE id=?")
        .bind(pending[0])
        .execute(&db)
        .await
        .unwrap();
    review::accept(&db, &input, 5).await.unwrap();
    assert_ne!(
        review::detail(&db, pending[1])
            .await
            .unwrap()
            .unwrap()
            .candidate
            .mentor_id,
        Some(locals[0])
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_run_with_only_failed_pages_can_retry_without_losing_its_page(db: SqlitePool) {
    let url = PageUrl::parse("https://school.example.edu/").unwrap();
    let model = config(&db, "http://127.0.0.1:1").await;
    let id = queue::start(&db, std::slice::from_ref(&url), model, 1)
        .await
        .unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    save(&db, &claim, &Outcome::Failed("读取网页超时，请重试".into()))
        .await
        .unwrap();
    assert_eq!(finish(&db).await.unwrap()[0].failed, 1);
    assert_eq!(queue::list(&db, 1).await.unwrap()[0].state, "failed");
    assert_eq!(queue::retry(&db, id, None, 2).await.unwrap(), 1);
    let retried = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.id, retried.id);
    assert_ne!(claim.generation, retried.generation);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn later_profile_facts_need_review_and_keep_the_user_approved_identity(db: SqlitePool) {
    let url = PageUrl::parse("https://school.example.edu/profile").unwrap();
    let config = config(&db, "http://127.0.0.1:1").await;
    let crawl = queue::start(&db, std::slice::from_ref(&url), config, 1)
        .await
        .unwrap();
    let page = queue::next(&db).await.unwrap().unwrap();
    let fields = MentorFields {
        name: "张三".into(),
        ..Default::default()
    };
    let person = |papers| Person {
        fields: fields.clone(),
        papers,
        profile: Some(url.clone()),
    };
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    review::save(
        &mut tx,
        crawl,
        page.id,
        "profile",
        url.as_str(),
        person(vec![]),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let id = sqlx::query_scalar::<_, i64>("SELECT id FROM candidate")
        .fetch_one(&db)
        .await
        .unwrap();
    let input = ReviewInput {
        ids: vec![id],
        decisions: Decisions::default(),
    };
    review::accept(&db, &input, 2).await.unwrap();
    let mentor = review::detail(&db, id)
        .await
        .unwrap()
        .unwrap()
        .candidate
        .mentor_id
        .unwrap();
    mentor::update(
        &db,
        mentor,
        &MentorFields {
            name: "用户修改的姓名".into(),
            ..Default::default()
        },
        3,
    )
    .await
    .unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    review::save(
        &mut tx,
        crawl,
        page.id,
        "profile",
        url.as_str(),
        person(vec!["新补到的论文".into()]),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    queue::delete(&db, crawl).await.unwrap();
    let detail = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(detail.candidate.crawl_id, None);
    assert_eq!(detail.candidate.review, "pending");
    assert_eq!(detail.candidate.mentor_id, Some(mentor));
    let preview = review::preview(&db, &input).await.unwrap();
    assert_eq!(preview.created, 0);
    assert_eq!(preview.updated, 1);
    review::accept(&db, &input, 4).await.unwrap();
    let teacher = mentor::get(&db, mentor).await.unwrap().unwrap();
    assert_eq!(teacher.name, "用户修改的姓名");
    assert_eq!(teacher.papers, vec!["新补到的论文"]);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM mentor")
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
    assert_eq!(review::accept(&db, &input, 5).await.unwrap().accepted, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn combining_duplicate_pages_keeps_an_already_accepted_candidate(db: SqlitePool) {
    let url = PageUrl::parse("https://school.example.edu/one").unwrap();
    let two = url.join("/two").unwrap();
    let config = config(&db, "http://127.0.0.1:1").await;
    let crawl = queue::start(&db, std::slice::from_ref(&url), config, 1)
        .await
        .unwrap();
    let page = queue::next(&db).await.unwrap().unwrap();
    let person = |url: PageUrl, emails: Vec<String>| Person {
        fields: MentorFields {
            name: "张三".into(),
            emails,
            ..Default::default()
        },
        papers: vec![],
        profile: Some(url),
    };
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for u in [&url, &two] {
        review::save(
            &mut tx,
            crawl,
            page.id,
            "profile",
            u.as_str(),
            person(u.clone(), vec![]),
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    let accepted = sqlx::query_scalar::<_, i64>("SELECT max(id) FROM candidate")
        .fetch_one(&db)
        .await
        .unwrap();
    let input = ReviewInput {
        ids: vec![accepted],
        decisions: Decisions::default(),
    };
    review::accept(&db, &input, 2).await.unwrap();
    let mentor = review::detail(&db, accepted)
        .await
        .unwrap()
        .unwrap()
        .candidate
        .mentor_id
        .unwrap();
    mentor::update(
        &db,
        mentor,
        &MentorFields {
            name: "修改后的姓名".into(),
            ..Default::default()
        },
        3,
    )
    .await
    .unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    for u in [&url, &two] {
        review::save(
            &mut tx,
            crawl,
            page.id,
            "profile",
            u.as_str(),
            person(u.clone(), vec!["zhang@example.edu".into()]),
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM candidate")
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        review::detail(&db, accepted)
            .await
            .unwrap()
            .unwrap()
            .candidate
            .mentor_id,
        Some(mentor)
    );
    review::accept(&db, &input, 4).await.unwrap();
    assert_eq!(
        mentor::get(&db, mentor).await.unwrap().unwrap().emails,
        vec!["zhang@example.edu"]
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM mentor")
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn retrying_one_failed_page_after_cancel_does_not_restart_other_planned_pages(
    db: SqlitePool,
) {
    let first = PageUrl::parse("https://school.example.edu/one").unwrap();
    let second = first.join("/two").unwrap();
    let model = config(&db, "http://127.0.0.1:1").await;
    let id = queue::start(&db, &[first, second], model, 1).await.unwrap();
    let failed = queue::next(&db).await.unwrap().unwrap();
    save(&db, &failed, &Outcome::Failed("读取超时".into()))
        .await
        .unwrap();
    queue::control(&db, id, "cancel", 2).await.unwrap();
    assert_eq!(queue::retry(&db, id, Some(failed.id), 3).await.unwrap(), 1);
    let retry = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(retry.id, failed.id);
    save(&db, &retry, &Outcome::Failed("仍然超时".into()))
        .await
        .unwrap();
    assert!(queue::next(&db).await.unwrap().is_none());
}
