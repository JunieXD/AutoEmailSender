#![cfg(test)]
use matching::{MatchFinished, Matcher};
use mentor::{MatchState, MentorFields, matching as queue};
use net::Net;
use sqlx::SqlitePool;
use tokio::{
    sync::mpsc,
    task::JoinHandle,
    time::{Duration, timeout},
};
#[path = "../../model/tests/common/provider.rs"]
mod provider;
use provider::{Provider, Response};
const VALID: &str = r#"{"score":88,"reason":"研究方向相近，有可迁移的项目经历。","directions":["视觉"],"highlights":["课程项目"],"risks":[]}"#;

struct Runner {
    matcher: Matcher,
    changes: mpsc::UnboundedReceiver<()>,
    done: mpsc::UnboundedReceiver<MatchFinished>,
    task: JoinHandle<()>,
}
impl Drop for Runner {
    fn drop(&mut self) {
        self.task.abort();
    }
}
impl Runner {
    fn start(db: &SqlitePool) -> Self {
        let matcher = Matcher::default();
        let (change_tx, changes) = mpsc::unbounded_channel();
        let (done_tx, done) = mpsc::unbounded_channel();
        let task = tokio::spawn(matching::run(
            db.clone(),
            Net::new(Vec::new()).unwrap(),
            matcher.clone(),
            move || {
                let _ = change_tx.send(());
            },
            move |r| {
                let _ = done_tx.send(r);
            },
        ));
        Self {
            matcher,
            changes,
            done,
            task,
        }
    }
    async fn progress(
        &mut self,
        db: &SqlitePool,
        predicate: impl Fn(&queue::MatchProgress) -> bool,
    ) {
        timeout(Duration::from_secs(10), async {
            loop {
                if predicate(&queue::progress(db).await.unwrap()) {
                    break;
                }
                self.changes.recv().await.unwrap();
            }
        })
        .await
        .unwrap();
    }
    async fn finished(&mut self) -> MatchFinished {
        timeout(Duration::from_secs(10), self.done.recv())
            .await
            .unwrap()
            .unwrap()
    }
}
async fn request(p: &mut Provider) -> provider::Request {
    timeout(Duration::from_secs(10), p.requests.recv())
        .await
        .unwrap()
        .unwrap()
}
async fn setup(db: &SqlitePool, address: &str, n: usize) -> Vec<i64> {
    let fields = model::ModelForm {
        name: "".into(),
        provider: "custom".into(),
        base_url: address.into(),
        model: "m".into(),
        input_price: Some(1.0),
        output_price: Some(2.0),
    }
    .check()
    .unwrap();
    let config = model::create(db, &fields, "", 1).await.unwrap();
    model::agree(db, config, "custom", Some(address))
        .await
        .unwrap();
    profile::update(
        db,
        &profile::ProfileForm {
            name: "学生".into(),
            research_interests: "视觉".into(),
            rewrite_preference: "".into(),
        },
        1,
    )
    .await
    .unwrap();
    let mut ids = vec![];
    for i in 0..n {
        ids.push(
            mentor::create(
                db,
                &MentorFields {
                    name: format!("导师{i}"),
                    research: Some("视觉研究".into()),
                    ..Default::default()
                },
                1,
            )
            .await
            .unwrap(),
        );
    }
    queue::enqueue(db, &ids, false, false, 2).await.unwrap();
    ids
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn normal_analysis_records_result_usage_and_estimate_with_student_prefix(db: SqlitePool) {
    let mut p = Provider::start().await;
    let ids = setup(&db, &p.address, 2).await;
    let text = "中".repeat(10_000) + "不应读取尾部";
    sqlx::query("INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at, extracted_text) VALUES ('简历.pdf', 1, 1, 1, 0, 1, ?)").bind(text).execute(&db).await.unwrap();
    queue::cancel(&db).await.unwrap();
    let estimate = matching::plan(&db, &ids, false).await.unwrap();
    assert_eq!(estimate.plan.analyze, 2);
    assert!(estimate.input_tokens > 20_000);
    assert_eq!(estimate.output_tokens, 2 * matching::OUTPUT_TOKENS);
    assert_eq!(
        estimate.cost,
        Some((estimate.input_tokens as f64 + 2.0 * estimate.output_tokens as f64) / 1_000_000.0)
    );
    queue::enqueue(&db, &ids, false, false, 2).await.unwrap();
    let mut runner = Runner::start(&db);
    let mut prefixes = vec![];
    for _ in 0..2 {
        let req = request(&mut p).await;
        let msg = req.body["messages"][1]["content"].as_str().unwrap();
        let value: serde_json::Value = serde_json::from_str(msg).unwrap();
        assert_eq!(
            value["学生"]["材料"][0]["文字"]
                .as_str()
                .unwrap()
                .chars()
                .count(),
            10_000
        );
        assert!(!msg.contains("不应读取尾部"));
        let (prefix, _) = msg.split_once(",\"导师\":").unwrap();
        prefixes.push(prefix.to_owned());
        assert!(msg.starts_with("{\"学生\":"));
        req.respond
            .send(Response::answer(&format!(
                "<think>思考</think>\n```json\n{VALID}\n```"
            )))
            .ok()
            .unwrap();
    }
    let done = runner.finished().await;
    assert_eq!((done.analyzed, done.failed), (2, 0));
    assert_eq!(prefixes[0], prefixes[1]);
    for id in ids {
        let m = mentor::get(&db, id).await.unwrap().unwrap();
        assert_eq!(m.analysis.result.unwrap().score, 88);
        assert!(m.analysis.state.is_none());
    }
    let usage: Vec<(String, i64, i64)> =
        sqlx::query_as("SELECT purpose, input_tokens, output_tokens FROM usage")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(
        usage,
        [("match".into(), 2000, 400), ("match".into(), 2000, 400)]
    );
    runner.matcher.wake();
    runner.matcher.wake();
    tokio::task::yield_now().await;
    assert!(runner.done.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invalid_output_retries_once_then_succeeds_or_fails(db: SqlitePool) {
    let mut p = Provider::start().await;
    let ids = setup(&db, &p.address, 1).await;
    let mut runner = Runner::start(&db);
    request(&mut p)
        .await
        .respond
        .send(Response::answer(r#"{"score":101}"#))
        .ok()
        .unwrap();
    let retry = request(&mut p).await;
    assert!(
        retry.body["messages"][0]["content"]
            .as_str()
            .unwrap()
            .contains("校验错误")
    );
    retry.respond.send(Response::answer(VALID)).ok().unwrap();
    assert_eq!(runner.finished().await.failed, 0);
    queue::enqueue(&db, &ids, true, true, 5).await.unwrap();
    runner.matcher.wake();
    for _ in 0..2 {
        request(&mut p)
            .await
            .respond
            .send(Response::answer("不是 JSON"))
            .ok()
            .unwrap();
    }
    assert_eq!(runner.finished().await.failed, 1);
    let m = mentor::get(&db, ids[0]).await.unwrap().unwrap();
    assert!(m.analysis.result.is_some());
    assert!(
        matches!(m.analysis.state, Some(MatchState::Failed { error }) if error == "模型没有按要求回答，可以重试或换个模型")
    );
    let calls: i64 = sqlx::query_scalar("SELECT count(*) FROM usage")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(calls, 4);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn unauthorized_pauses_and_requeues_without_failure_then_resumes(db: SqlitePool) {
    let mut p = Provider::start().await;
    let ids = setup(&db, &p.address, 1).await;
    let mut runner = Runner::start(&db);
    request(&mut p)
        .await
        .respond
        .send(Response::Refused {
            status: 401,
            message: "bad key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    runner.progress(&db, |p| p.pause.is_some()).await;
    let progress = queue::progress(&db).await.unwrap();
    assert_eq!((progress.remaining, progress.failed), (1, 0));
    assert!(progress.pause.unwrap().reason.unwrap().contains("API Key"));
    assert!(matches!(
        mentor::get(&db, ids[0])
            .await
            .unwrap()
            .unwrap()
            .analysis
            .state,
        Some(MatchState::Queued)
    ));
    assert!(p.requests.try_recv().is_err());
    queue::resume(&db).await.unwrap();
    runner.matcher.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    assert_eq!(runner.finished().await.analyzed, 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn rate_limit_waits_for_retry_after_on_controlled_time(db: SqlitePool) {
    let mut p = Provider::start().await;
    setup(&db, &p.address, 1).await;
    let mut runner = Runner::start(&db);
    let req = request(&mut p).await;
    // Keep the runtime busy so SQLite I/O cannot auto-advance the paused clock.
    let clock_guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    while runner.changes.try_recv().is_ok() {}
    req.respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(10),
        })
        .ok()
        .unwrap();
    runner.changes.recv().await.unwrap();
    tokio::time::advance(Duration::from_secs(9)).await;
    runner.matcher.wake();
    for _ in 0..20 {
        tokio::task::yield_now().await;
    }
    assert!(p.requests.try_recv().is_err());
    // Tokio timers round deadlines up to the next millisecond.
    tokio::time::advance(Duration::from_millis(1001)).await;
    tokio::time::resume();
    clock_guard.abort();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    assert_eq!(runner.finished().await.analyzed, 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn startup_recovers_running_concurrency_is_three_and_pause_stops_claiming(db: SqlitePool) {
    let mut p = Provider::start().await;
    setup(&db, &p.address, 5).await;
    queue::next(&db).await.unwrap();
    let mut runner = Runner::start(&db);
    let mut requests = vec![];
    for _ in 0..3 {
        requests.push(request(&mut p).await);
    }
    let running: i64 =
        sqlx::query_scalar("SELECT count(*) FROM mentor WHERE match_state = 'running'")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(running, 3);
    assert!(p.requests.try_recv().is_err());
    queue::pause(&db, 10, None).await.unwrap();
    runner.matcher.wake();
    for req in requests {
        req.respond.send(Response::answer(VALID)).ok().unwrap();
    }
    runner.progress(&db, |p| p.remaining == 2).await;
    assert!(p.requests.try_recv().is_err());
    assert!(runner.done.try_recv().is_err());
    let fields = model::ModelForm {
        name: "".into(),
        provider: "custom".into(),
        base_url: p.address.clone(),
        model: "new-default".into(),
        input_price: None,
        output_price: None,
    }
    .check()
    .unwrap();
    let config = model::create(&db, &fields, "", 20).await.unwrap();
    model::agree(&db, config, "custom", Some(&p.address))
        .await
        .unwrap();
    profile::set_default_model(&db, config).await.unwrap();
    queue::resume(&db).await.unwrap();
    runner.matcher.wake();
    for _ in 0..2 {
        let req = request(&mut p).await;
        assert_eq!(req.body["model"], "new-default");
        req.respond.send(Response::answer(VALID)).ok().unwrap();
    }
    let done = runner.finished().await;
    assert_eq!((done.analyzed, done.failed), (5, 0));
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn absent_model_consent_and_student_basis_are_explained_before_calling(db: SqlitePool) {
    assert!(matches!(
        matching::ready(&db).await,
        Err(matching::Error::NoModel)
    ));
    let mut p = Provider::start().await;
    setup(&db, &p.address, 1).await;
    sqlx::query("UPDATE model_config SET agreed = 0")
        .execute(&db)
        .await
        .unwrap();
    assert!(matches!(
        matching::ready(&db).await,
        Err(matching::Error::NotAgreed)
    ));
    let mut runner = Runner::start(&db);
    runner.progress(&db, |p| p.pause.is_some()).await;
    assert_eq!(queue::progress(&db).await.unwrap().failed, 0);
    assert!(p.requests.try_recv().is_err());
    sqlx::query("UPDATE model_config SET agreed = 1")
        .execute(&db)
        .await
        .unwrap();
    profile::update(
        &db,
        &profile::ProfileForm {
            name: "".into(),
            research_interests: "".into(),
            rewrite_preference: "".into(),
        },
        30,
    )
    .await
    .unwrap();
    assert!(matches!(
        matching::ready(&db).await,
        Err(matching::Error::NoBasis)
    ));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn research_removed_while_waiting_is_not_sent_to_the_model(db: SqlitePool) {
    let mut p = Provider::start().await;
    let ids = setup(&db, &p.address, 1).await;
    sqlx::query("UPDATE mentor_detail SET research = NULL WHERE mentor_id = ?")
        .bind(ids[0])
        .execute(&db)
        .await
        .unwrap();
    let mut runner = Runner::start(&db);
    let done = runner.finished().await;
    assert_eq!((done.analyzed, done.failed), (1, 1));
    assert!(p.requests.try_recv().is_err());
    let state = mentor::get(&db, ids[0])
        .await
        .unwrap()
        .unwrap()
        .analysis
        .state;
    assert!(matches!(state, Some(MatchState::Failed { error }) if error.contains("资料不足")));
}
