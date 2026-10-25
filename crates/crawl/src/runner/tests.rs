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
                &Cooldown::default(),
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
    )
    .unwrap();
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

async fn observation(
    db: &SqlitePool,
    c: &Claim,
    site: &Site,
    path: &str,
    kind: &str,
    fields: MentorFields,
) {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    if kind == "profile" {
        sqlx::query("UPDATE crawl_page SET work_state='done' WHERE crawl_id=? AND url=?")
            .bind(c.crawl)
            .bind(site.url(path).as_str())
            .execute(&mut *tx)
            .await
            .unwrap();
    }
    review::save(
        &mut tx,
        c.crawl,
        c.id,
        kind,
        site.url(path).as_str(),
        Person {
            fields,
            papers: vec![],
            profile: Some(site.url(path)),
        },
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
}
fn observed(name: &str, emails: &[&str], research: Option<&str>) -> MentorFields {
    MentorFields {
        name: name.into(),
        emails: emails.iter().map(|s| (*s).into()).collect(),
        research: research.map(Into::into),
        ..Default::default()
    }
}
async fn candidate_ids(db: &SqlitePool) -> Vec<i64> {
    sqlx::query_scalar("SELECT id FROM candidate ORDER BY id")
        .fetch_all(db)
        .await
        .unwrap()
}
async fn review_fixture(db: &SqlitePool) -> (Site, Claim) {
    let site = Site::new().await;
    let model = config(db, "http://127.0.0.1:1").await;
    queue::start(db, &[site.url("/")], model, 1).await.unwrap();
    let claim = queue::next(db).await.unwrap().unwrap();
    (site, claim)
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_distinct_confirmed_targets_survive_new_shared_email(db: SqlitePool) {
    let (site, c) = review_fixture(&db).await;
    for path in ["/one", "/two"] {
        observation(&db, &c, &site, path, "list", observed("张三", &[], None)).await;
        let id = *candidate_ids(&db).await.last().unwrap();
        review::accept(
            &db,
            &ReviewInput {
                ids: vec![id],
                decisions: Decisions {
                    matches: vec![mentor::import::GroupChoice { row: 1, id: None }],
                    ..Default::default()
                },
            },
            2,
        )
        .await
        .unwrap();
    }
    let ids = candidate_ids(&db).await;
    let targets: Vec<_> =
        sqlx::query_scalar::<_, Option<i64>>("SELECT mentor_id FROM candidate ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_ne!(targets[0], targets[1]);
    for path in ["/one", "/two"] {
        observation(
            &db,
            &c,
            &site,
            path,
            "profile",
            observed("张三", &["z@example.edu"], None),
        )
        .await;
    }
    assert_eq!(candidate_ids(&db).await, ids);
    assert_eq!(
        sqlx::query_scalar::<_, Option<i64>>("SELECT mentor_id FROM candidate ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap(),
        targets
    );
    for id in &ids {
        let input = ReviewInput {
            ids: vec![*id],
            decisions: Decisions::default(),
        };
        let preview = review::preview(&db, &input).await.unwrap();
        assert_eq!(
            preview.errors.len(),
            1,
            "a single candidate must expose both prior targets"
        );
        assert_eq!(review::accept(&db, &input, 3).await.unwrap().accepted, 0);
    }
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_merged_profile_identity_routes_late_results_and_removes_queued_work(
    db: SqlitePool,
) {
    let (site, c) = review_fixture(&db).await;
    observation(&db, &c, &site, "/one", "list", observed("张三", &[], None)).await;
    observation(
        &db,
        &c,
        &site,
        "/two",
        "list",
        observed("张三", &["z@example.edu"], None),
    )
    .await;
    observation(
        &db,
        &c,
        &site,
        "/one",
        "profile",
        observed("张三", &["z@example.edu"], None),
    )
    .await;
    assert_eq!(candidate_ids(&db).await.len(), 1);
    assert_eq!(
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM crawl_page WHERE kind='profile' AND work_state='queued'"
        )
        .fetch_one(&db)
        .await
        .unwrap(),
        0,
        "unstarted duplicate profiles must be removed at merge time"
    );
    observation(
        &db,
        &c,
        &site,
        "/two",
        "profile",
        observed("San Zhang", &[], None),
    )
    .await;
    let ids = candidate_ids(&db).await;
    assert_eq!(ids.len(), 1);
    let detail = review::detail(&db, ids[0]).await.unwrap().unwrap();
    assert!(
        detail
            .candidate
            .fields
            .aliases
            .contains(&"San Zhang".into())
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM crawl_page WHERE kind='profile' AND work_state='queued'"
        )
        .fetch_one(&db)
        .await
        .unwrap(),
        0
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_profile_scalars_replace_list_snippets_and_reopen_accepted_review(
    db: SqlitePool,
) {
    let (site, c) = review_fixture(&db).await;
    let fields = |research: &str, title: &str| MentorFields {
        affiliations: vec![mentor::Affiliation {
            title: Some(title.into()),
            ..Default::default()
        }],
        ..observed("张三", &["z@example.edu"], Some(research))
    };
    observation(&db, &c, &site, "/one", "list", fields("机器学习", "讲师")).await;
    let id = candidate_ids(&db).await[0];
    review::accept(
        &db,
        &ReviewInput {
            ids: vec![id],
            decisions: Decisions::default(),
        },
        2,
    )
    .await
    .unwrap();
    let full = "机器学习、隐私计算与大模型安全，主持可信人工智能项目";
    observation(&db, &c, &site, "/one", "profile", fields(full, "教授")).await;
    observation(
        &db,
        &c,
        &site,
        "/one",
        "list",
        fields("后到的名单片段", "副教授"),
    )
    .await;
    observation(
        &db,
        &c,
        &site,
        "/one",
        "profile",
        fields("同级后到的方向", "研究员"),
    )
    .await;
    let detail = review::detail(&db, id).await.unwrap().unwrap().candidate;
    assert_eq!(detail.fields.research.as_deref(), Some(full));
    assert_eq!(detail.fields.affiliations[0].title.as_deref(), Some("教授"));
    assert_eq!(detail.review, "pending");
    let local = mentor::get(&db, detail.mentor_id.unwrap())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(local.research.as_deref(), Some("机器学习"));
    assert_eq!(
        review::preview(
            &db,
            &ReviewInput {
                ids: vec![id],
                decisions: Decisions::default()
            }
        )
        .await
        .unwrap()
        .differences
        .len(),
        2
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_invalid_name_is_corrected_or_fails_with_page_reason(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/", "<title>教师名单</title><p>张三 z@example.edu</p>");
    let mut p = Provider::start().await;
    let config = config(&db, &p.address).await;
    for correct in [false, true] {
        let id = queue::start(&db, &[site.url("/")], config, 1)
            .await
            .unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let process = tokio::spawn({
            let db = db.clone();
            let c = c.clone();
            let port = site.port;
            async move {
                super::process(
                    &db,
                    &net::Net::new(vec![]).unwrap(),
                    &Fetcher::local(port),
                    &Limit::default(),
                    &c,
                    &Cooldown::default(),
                )
                .await
                .unwrap()
            }
        });
        let server = tokio::spawn(async move {
            let mut invalid = false;
            while let Some(r) = p.requests.recv().await {
                let system = r.body["messages"][0]["content"].as_str().unwrap();
                let answer = if system.starts_with("你整理学院") {
                    json!({"kind":"list","next":[]}).to_string()
                } else if invalid && correct {
                    json!({"people":[listed("张三", Some(999), vec!["z@example.edu", "invented@example.edu"])]}).to_string()
                } else {
                    invalid = true;
                    include_str!("../../tests/model/invalid-name.json").into()
                };
                r.respond.send(Response::answer(&answer)).ok();
            }
            p
        });
        let outcome = tokio::time::timeout(Duration::from_secs(10), process)
            .await
            .unwrap()
            .unwrap();
        if correct {
            assert!(matches!(outcome, Outcome::Done(ref page) if page.people.len()==1));
        } else {
            assert!(matches!(outcome, Outcome::Failed(ref reason) if reason.contains("姓名")));
        }
        save(&db, &c, &outcome).await.unwrap();
        finish(&db).await.unwrap();
        let detail = queue::detail(&db, id, 1).await.unwrap().unwrap();
        assert_eq!(
            detail.pages[0].status,
            if correct { "success" } else { "failed" }
        );
        server.abort();
        // A fresh fixture isolates the second run from the first server's lifetime.
        if !correct {
            p = Provider::start().await;
            sqlx::query("UPDATE model_config SET base_url=? WHERE id=?")
                .bind(&p.address)
                .bind(config)
                .execute(&db)
                .await
                .unwrap();
        } else {
            break;
        }
    }
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_deleted_or_changed_config_requeues_and_requires_current_consent(
    db: SqlitePool,
) {
    let site = Site::new().await;
    site.page("/", "<title>学院</title><p>教师名单</p>");
    let fetcher = Fetcher::local(site.port);
    for deleted in [true, false] {
        let mut old_provider = Provider::start().await;
        let mut new_provider = Provider::start().await;
        let old_id = config(&db, &old_provider.address).await;
        let fallback = config(&db, &new_provider.address).await;
        sqlx::query("UPDATE model_config SET agreed=0 WHERE id=?")
            .bind(fallback)
            .execute(&db)
            .await
            .unwrap();
        sqlx::query("UPDATE profile SET default_model_config_id=?")
            .bind(fallback)
            .execute(&db)
            .await
            .unwrap();
        let crawl = queue::start(&db, &[site.url("/")], old_id, 1)
            .await
            .unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let chosen = model::selected(&db, Some(old_id)).await.unwrap().unwrap();
        let limit = Limit::default();
        let permits = [
            limit.acquire().await,
            limit.acquire().await,
            limit.acquire().await,
        ];
        let net = net::Net::new(vec![]).unwrap();
        let cooldown = Cooldown::default();
        let reader = Reader {
            db: &db,
            net: &net,
            limit: &limit,
            claim: &c,
            chosen: &chosen,
            cooldown: &cooldown,
        };
        let ask = reader.ask::<extract::Route>(extract::ROUTE, json!({"title":"学院","links":[]}));
        tokio::pin!(ask);
        assert!(
            std::future::poll_fn(|cx| std::task::Poll::Ready(ask.as_mut().poll(cx).is_pending()))
                .await
        );
        if deleted {
            model::delete(&db, old_id).await.unwrap();
        } else {
            let fields = model::ModelForm {
                name: "修改后的模型".into(),
                provider: "custom".into(),
                base_url: new_provider.address.clone(),
                model: "fixture".into(),
                input_price: None,
                output_price: None,
            }
            .check()
            .unwrap();
            model::update(&db, old_id, &fields, "fixture")
                .await
                .unwrap();
        }
        drop(permits);
        tokio::select! { result = &mut ask => assert!(matches!(result, Err(ReadError::Gone))), _ = old_provider.requests.recv() => panic!("started a request using stale configuration") }
        save(&db, &c, &Outcome::Gone).await.unwrap();
        assert!(
            !queue::valid(&mut db.acquire().await.unwrap(), &c)
                .await
                .unwrap()
        );
        let fresh = queue::next(&db).await.unwrap().unwrap();
        let chosen = model::selected(&db, fresh.model_id).await.unwrap().unwrap();
        assert_eq!(chosen.id, if deleted { fallback } else { old_id });
        let outcome = process(&db, &net, &fetcher, &limit, &fresh, &cooldown)
            .await
            .unwrap();
        assert!(matches!(outcome, Outcome::Pause(_, _)));
        save(&db, &fresh, &outcome).await.unwrap();
        assert!(old_provider.requests.try_recv().is_err());
        assert!(new_provider.requests.try_recv().is_err());
        model::agree(&db, chosen.id, "custom", Some(&new_provider.address))
            .await
            .unwrap();
        queue::control(&db, crawl, "resume", 2).await.unwrap();
        let fresh = queue::next(&db).await.unwrap().unwrap();
        let read = process(&db, &net, &fetcher, &limit, &fresh, &cooldown);
        let answer = async {
            new_provider
                .requests
                .recv()
                .await
                .unwrap()
                .respond
                .send(Response::answer(r#"{"kind":"other","next":[]}"#))
                .ok();
        };
        let (outcome, ()) = tokio::join!(read, answer);
        save(&db, &fresh, &outcome.unwrap()).await.unwrap();
        finish(&db).await.unwrap();
    }
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_supplement_preview_names_target_and_added_content(db: SqlitePool) {
    let (site, c) = review_fixture(&db).await;
    let local = MentorFields {
        affiliations: vec![mentor::Affiliation {
            university: Some("示例大学".into()),
            ..Default::default()
        }],
        ..observed("张三", &["z@example.edu"], None)
    };
    let target = mentor::create(&db, &local, 1).await.unwrap();
    let mut fields = observed(
        "张三",
        &["z@example.edu", "extra@example.edu"],
        Some("机器学习"),
    );
    fields.homepages = vec!["https://school.example.edu/one".into()];
    fields.affiliations = vec![
        mentor::Affiliation {
            university: Some("示例大学".into()),
            school: Some("计算机学院".into()),
            title: Some("教授".into()),
            ..Default::default()
        },
        mentor::Affiliation {
            university: Some("兼任大学".into()),
            ..Default::default()
        },
    ];
    observation(&db, &c, &site, "/one", "profile", fields).await;
    let preview = review::preview(
        &db,
        &ReviewInput {
            ids: candidate_ids(&db).await,
            decisions: Decisions::default(),
        },
    )
    .await
    .unwrap();
    let value = serde_json::to_value(preview).unwrap();
    assert_eq!(value["additions"][0]["target"]["id"], target);
    assert_eq!(value["additions"][0]["target"]["profile"]["name"], "张三");
    assert_eq!(
        value["additions"][0]["fields"]["emails"],
        json!(["extra@example.edu"])
    );
    assert_eq!(
        value["additions"][0]["fields"]["homepages"],
        json!(["https://school.example.edu/one"])
    );
    assert_eq!(value["additions"][0]["fields"]["research"], "机器学习");
    assert_eq!(
        value["additions"][0]["fields"]["affiliations"][0]["before"]["university"],
        "示例大学"
    );
    let units = &value["additions"][0]["fields"]["affiliations"];
    assert_eq!(units[0]["added"]["school"], "计算机学院");
    assert_eq!(units[0]["added"]["title"], "教授");
    assert_eq!(units[0]["added"]["university"], Value::Null);
    assert_eq!(units[1]["before"], Value::Null);
    assert_eq!(units[1]["added"]["university"], "兼任大学");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_retry_after_blocks_inflight_pages_next_request(db: SqlitePool) {
    let site = Site::new().await;
    for path in ["/a", "/b"] {
        site.page(path, "<title>教师名单</title><p>张三 z@example.edu</p>");
    }
    let mut p = Provider::start().await;
    let config = config(&db, &p.address).await;
    queue::start(&db, &[site.url("/a"), site.url("/b")], config, 1)
        .await
        .unwrap();
    let cooldown = Cooldown::default();
    let limit = Limit::default();
    let start = |claim: Claim| {
        let (db, cooldown, limit, port) = (db.clone(), cooldown.clone(), limit.clone(), site.port);
        tokio::spawn(async move {
            process(
                &db,
                &net::Net::new(vec![]).unwrap(),
                &Fetcher::local(port),
                &limit,
                &claim,
                &cooldown,
            )
            .await
            .unwrap()
        })
    };
    let a = start(queue::next(&db).await.unwrap().unwrap());
    let b = start(queue::next(&db).await.unwrap().unwrap());
    let first = p.requests.recv().await.unwrap();
    let second = p.requests.recv().await.unwrap();
    let guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    first
        .respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(60),
        })
        .ok();
    // Whichever page received 429 finishes first. The other page remains in flight.
    tokio::pin!(a, b);
    let a_limited = tokio::select! { result = &mut a => { assert!(matches!(result.unwrap(), Outcome::RateLimit)); true }, result = &mut b => { assert!(matches!(result.unwrap(), Outcome::RateLimit)); false } };
    second
        .respond
        .send(Response::answer(r#"{"kind":"list","next":[]}"#))
        .ok();
    let permit = cooldown.acquire(&limit);
    tokio::pin!(permit);
    assert!(
        std::future::poll_fn(|cx| std::task::Poll::Ready(permit.as_mut().poll(cx).is_pending()))
            .await,
        "new model work must wait after the other page was limited"
    );
    // The in-flight route's usage is saved while further extraction waits.
    loop {
        if sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage")
            .fetch_one(&db)
            .await
            .unwrap()
            == 1
        {
            break;
        }
        tokio::task::yield_now().await;
    }
    tokio::time::advance(Duration::from_secs(59)).await;
    assert!(
        std::future::poll_fn(|cx| std::task::Poll::Ready(permit.as_mut().poll(cx).is_pending()))
            .await
    );
    assert!(p.requests.try_recv().is_err());
    tokio::time::advance(Duration::from_secs(2)).await;
    drop(permit.await);
    tokio::time::resume();
    guard.abort();
    let next = tokio::time::timeout(Duration::from_secs(10), p.requests.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(
        next.body["messages"][0]["content"]
            .as_str()
            .unwrap()
            .starts_with("你从教师")
    );
    next.respond.send(Response::answer(r#"{"people":[]}"#)).ok();
    let outcome = if a_limited {
        b.await.unwrap()
    } else {
        a.await.unwrap()
    };
    assert!(matches!(outcome, Outcome::Done(ref p) if p.people.is_empty()));
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regression_background_includes_old_running_paused_and_failed_crawls(db: SqlitePool) {
    let (site, c) = review_fixture(&db).await;
    let paused = queue::start(&db, &[site.url("/paused")], c.model_id.unwrap(), 2)
        .await
        .unwrap();
    queue::control(&db, paused, "pause", 3).await.unwrap();
    let failed: i64 = sqlx::query_scalar("INSERT INTO crawl(kind,state,error,created_at,finished_at) VALUES('agent','failed','打不开',3,4) RETURNING id").fetch_one(&db).await.unwrap();
    for n in 0..51 {
        sqlx::query(
            "INSERT INTO crawl(kind,state,created_at,finished_at) VALUES('agent','done',?,?)",
        )
        .bind(10 + n)
        .bind(10 + n)
        .execute(&db)
        .await
        .unwrap();
    }
    let today: i64 = sqlx::query_scalar("INSERT INTO crawl(kind,state,created_at,finished_at) VALUES('agent','done',150,150) RETURNING id").fetch_one(&db).await.unwrap();
    sqlx::query(
        "INSERT INTO crawl(kind,state,created_at,finished_at) VALUES('agent','done',200,200)",
    )
    .execute(&db)
    .await
    .unwrap();
    let work = queue::work(&db, 100, 200).await.unwrap();
    assert_eq!(
        work.iter()
            .filter(|c| c.state == "done")
            .map(|c| c.id)
            .collect::<Vec<_>>(),
        vec![today]
    );
    for id in [c.crawl, paused, failed] {
        assert!(work.iter().any(|c| c.id == id), "old work {id} is absent");
    }
    assert!(queue::detail(&db, c.crawl, 1).await.unwrap().is_some());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn candidate_classification_pages_follow_normalized_names_schools_and_mentor_edits(
    db: SqlitePool,
) {
    let (site, c) = review_fixture(&db).await;
    let affiliation = |university: &str| mentor::Affiliation {
        university: Some(university.into()),
        ..Default::default()
    };
    let mut local = MentorFields {
        name: "张三".into(),
        aliases: vec!["Ａ  B".into()],
        affiliations: vec![affiliation("ＡＢ 大学")],
        ..Default::default()
    };
    let target = mentor::create(&db, &local, 1).await.unwrap();
    for n in 0..55 {
        observation(
            &db,
            &c,
            &site,
            &format!("/person/{n}"),
            "profile",
            MentorFields {
                name: "A B".into(),
                affiliations: vec![affiliation("AB   大学")],
                ..Default::default()
            },
        )
        .await;
    }
    let mut query = CandidateQuery {
        crawl_id: Some(c.crawl),
        processed: false,
        filter: CandidateFilter::Maybe,
        page: 1,
    };
    let first = review::list(&db, &query).await.unwrap();
    assert_eq!((first.total, first.candidates.len()), (55, 50));
    query.page = 2;
    let second = review::list(&db, &query).await.unwrap();
    assert_eq!((second.total, second.candidates.len()), (55, 5));
    assert!(
        second
            .candidates
            .iter()
            .all(|c| c.id > first.candidates.last().unwrap().id)
    );
    local.affiliations = vec![affiliation("另一所大学")];
    mentor::update(&db, target, &local, 2).await.unwrap();
    assert_eq!(review::list(&db, &query).await.unwrap().total, 0);
    query.filter = CandidateFilter::New;
    let new = review::list(&db, &query).await.unwrap();
    assert_eq!((new.total, new.candidates.len()), (55, 5));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn candidate_categories_do_not_depend_on_other_rows_on_the_page(db: SqlitePool) {
    let (site, c) = review_fixture(&db).await;
    mentor::create(&db, &observed("李四", &[], None), 1)
        .await
        .unwrap();
    for (name, path) in [("张三", "/zhang"), ("李四", "/li")] {
        observation(
            &db,
            &c,
            &site,
            path,
            "profile",
            observed(name, &["shared@example.edu"], None),
        )
        .await;
    }
    let mut q = CandidateQuery {
        crawl_id: Some(c.crawl),
        processed: false,
        filter: CandidateFilter::All,
        page: 1,
    };
    let all = review::list(&db, &q).await.unwrap();
    assert_eq!(all.candidates[0].category, CandidateFilter::New);
    assert_eq!(all.candidates[1].category, CandidateFilter::Maybe);
    for filter in [CandidateFilter::New, CandidateFilter::Maybe] {
        q.filter = filter;
        let page = review::list(&db, &q).await.unwrap();
        assert_eq!(page.total, 1);
        assert_eq!(page.candidates[0].category, filter);
    }
}
