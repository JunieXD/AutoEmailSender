#![cfg(test)]
use sqlx::SqlitePool;
use task::{
    NewTask, Writer,
    generation::{self, DraftsFinished, Generator},
};
use tokio::{
    sync::mpsc,
    task::JoinHandle,
    time::{Duration, timeout},
};
#[path = "../../model/tests/common/provider.rs"]
mod provider;
use provider::{Provider, Response};
const LETTER: &str = "<subject>申请</subject><body>王老师：您好，我希望申请读研。</body>";
const MATCH: &str =
    r#"{"score":80,"reason":"方向相近","directions":[],"highlights":[],"risks":[]}"#;
struct Runner {
    handle: Generator,
    changes: mpsc::UnboundedReceiver<()>,
    done: mpsc::UnboundedReceiver<DraftsFinished>,
    worker: JoinHandle<()>,
}
impl Drop for Runner {
    fn drop(&mut self) {
        self.worker.abort();
    }
}
impl Runner {
    fn start(db: &SqlitePool, limit: model::background::Limit) -> Self {
        let handle = Generator::default();
        let (tx, changes) = mpsc::unbounded_channel();
        let (done_tx, done) = mpsc::unbounded_channel();
        let worker = tokio::spawn(generation::run(
            db.clone(),
            net::Net::new(vec![]).unwrap(),
            handle.clone(),
            limit,
            move || {
                let _ = tx.send(());
            },
            move |r| {
                let _ = done_tx.send(r);
            },
        ));
        Self {
            handle,
            changes,
            done,
            worker,
        }
    }
    async fn until(&mut self, db: &SqlitePool, id: i64, p: impl Fn(&task::TaskSummary) -> bool) {
        timeout(Duration::from_secs(10), async {
            loop {
                if p(&task::get(db, id).await.unwrap().unwrap()) {
                    return;
                }
                self.changes.recv().await.unwrap();
            }
        })
        .await
        .unwrap();
    }
    async fn finished(&mut self) -> DraftsFinished {
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
async fn setup(db: &SqlitePool, address: &str, n: i64) -> (i64, Vec<i64>) {
    let config=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES('custom',?,'fixture','',1,0) RETURNING id").bind(address).fetch_one(db).await.unwrap();
    let mut ids = vec![];
    for i in 0..n {
        ids.push(
            mentor::create(
                db,
                &mentor::MentorFields {
                    name: format!("王{i}"),
                    emails: vec![format!("m{i}@example.com")],
                    research: Some("机器学习".into()),
                    ..Default::default()
                },
                0,
            )
            .await
            .unwrap(),
        );
    }
    let task = task::create(
        db,
        &NewTask {
            name: "AI 任务".into(),
            ids: ids.clone(),
            writer: Writer::Ai,
            template_id: None,
            model_config_id: Some(config),
            references: vec![],
            attachments: vec![],
        },
        1,
    )
    .await
    .unwrap();
    (task, ids)
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn generates_and_reports_single_failures_and_finishes_once(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, _) = setup(&db, &p.address, 4).await;
    let mut run = Runner::start(&db, Default::default());
    let first = request(&mut p).await;
    let second = request(&mut p).await;
    let third = request(&mut p).await;
    first.respond.send(Response::answer(LETTER)).ok().unwrap();
    let fourth = request(&mut p).await;
    second
        .respond
        .send(Response::answer("不是邮件"))
        .ok()
        .unwrap();
    third.respond.send(Response::answer(LETTER)).ok().unwrap();
    fourth.respond.send(Response::answer(LETTER)).ok().unwrap();
    let done = run.finished().await;
    assert_eq!((done.generated, done.failed), (3, 1));
    run.until(&db, id, |t| t.progress.generating == 0).await;
    run.handle.wake();
    assert!(run.done.try_recv().is_err());
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().progress.review,
        3
    );
    assert_eq!(task::regenerate(&db, id, None, true, 2).await.unwrap(), 1);
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    assert_eq!(run.finished().await.failed, 0);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn failed_regeneration_is_not_counted_as_a_finished_draft(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, _) = setup(&db, &p.address, 1).await;
    let mut run = Runner::start(&db, Default::default());
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    assert_eq!(run.finished().await.generated, 1);
    let email = task::pending(&db, id, 1).await.unwrap().remove(0).draft.id;
    task::regenerate(&db, id, Some(email), false, 2)
        .await
        .unwrap();
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer("不是邮件"))
        .ok()
        .unwrap();
    let result = run.finished().await;
    assert_eq!((result.generated, result.failed), (0, 1));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn account_errors_pause_only_the_task_and_old_configuration_errors_do_not(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, _) = setup(&db, &p.address, 1).await;
    let mut run = Runner::start(&db, Default::default());
    let r = request(&mut p).await;
    sqlx::query("UPDATE model_config SET api_key='changed'")
        .execute(&db)
        .await
        .unwrap();
    r.respond
        .send(Response::Refused {
            status: 401,
            message: "wrong key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    let r = request(&mut p).await;
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().state,
        task::TaskState::Active
    );
    r.respond
        .send(Response::Refused {
            status: 429,
            message: "insufficient_quota".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    run.until(&db, id, |t| t.state == task::TaskState::Paused)
        .await;
    let t = task::get(&db, id).await.unwrap().unwrap();
    assert!(t.pause_reason.unwrap().contains("额度"));
    assert_eq!(t.progress.generation_failed, 0);
    assert_eq!(t.progress.generating, 1);
    task::resume(&db, id).await.unwrap();
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    assert_eq!(run.finished().await.generated, 1);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn retry_after_uses_controlled_time_and_does_not_mark_a_failure(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, _) = setup(&db, &p.address, 1).await;
    let mut run = Runner::start(&db, Default::default());
    let r = request(&mut p).await;
    let guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    while run.changes.try_recv().is_ok() {}
    r.respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(60),
        })
        .ok()
        .unwrap();
    // Wait for the response's persisted requeue and the scheduler's change event.
    loop {
        run.changes.recv().await.unwrap();
        let queued = sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM email WHERE generation_queued_at IS NOT NULL",
        )
        .fetch_one(&db)
        .await
        .unwrap();
        if queued == 1 {
            break;
        }
    }
    assert_eq!(
        task::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .progress
            .generation_failed,
        0
    );
    tokio::time::advance(Duration::from_secs(59)).await;
    assert!(p.requests.try_recv().is_err());
    tokio::time::advance(Duration::from_millis(1001)).await;
    tokio::time::resume();
    guard.abort();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    assert_eq!(run.finished().await.generated, 1);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_recovers_running_claims_and_fixed_references_and_template_are_used(
    db: SqlitePool,
) {
    let mut p = Provider::start().await;
    let (id, ids) = setup(&db, &p.address, 1).await;
    sqlx::query("INSERT INTO material(id,file_name,size,for_matching,for_writing,attach_by_default,created_at,extracted_text) VALUES (1,'参考.pdf',1,0,0,0,0,'固定资料：课程项目')").execute(&db).await.unwrap();
    sqlx::query("INSERT INTO task_material VALUES (?,1,'reference')")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE task SET template_subject='申请{导师姓名}',template_body_md='{导师姓名}老师：原始底稿'").execute(&db).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    let old = request(&mut p).await;
    assert!(old.body.to_string().contains("固定资料"));
    assert!(old.body.to_string().contains("原始底稿"));
    assert!(
        old.body
            .to_string()
            .contains(&format!("申请王{}", ids[0] - 1))
    );
    drop(run);
    run = Runner::start(&db, Default::default());
    let new = request(&mut p).await;
    old.respond
        .send(Response::answer(
            "<subject>旧稿</subject><body>迟到的旧内容</body>",
        ))
        .ok();
    new.respond.send(Response::answer(LETTER)).ok().unwrap();
    assert_eq!(run.finished().await.generated, 1);
    let drafts = task::pending(&db, id, 1).await.unwrap();
    assert_eq!(drafts[0].draft.subject, "申请");
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn matching_and_writing_share_three_actual_model_calls(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, ids) = setup(&db, &p.address, 6).await;
    sqlx::query("UPDATE profile SET research_interests='机器学习'")
        .execute(&db)
        .await
        .unwrap();
    mentor::matching::enqueue(&db, &ids, false, true, 0)
        .await
        .unwrap();
    let limit = model::background::Limit::default();
    let _drafts = Runner::start(&db, limit.clone());
    let matcher = matching::Matcher::default();
    let match_worker = tokio::spawn(matching::run(
        db.clone(),
        net::Net::new(vec![]).unwrap(),
        matcher,
        limit,
        || {},
        |_| {},
    ));
    let mut active = vec![];
    for _ in 0..3 {
        active.push(request(&mut p).await);
    }
    // Hold all three provider responses while both queues have work. A fourth call would violate
    // the user-visible shared bound, regardless of which queue claimed it.
    tokio::time::pause();
    assert!(
        timeout(Duration::from_secs(1), p.requests.recv())
            .await
            .is_err()
    );
    tokio::time::resume();
    let mut matching = 0;
    let mut writing = 0;
    for _ in 0..12 {
        let r = active.remove(0);
        let system = r.body["messages"][0]["content"].as_str().unwrap();
        let answer = if system.contains("分数不是录取") {
            matching += 1;
            MATCH
        } else {
            writing += 1;
            LETTER
        };
        r.respond.send(Response::answer(answer)).ok().unwrap();
        if matching + writing <= 9 {
            active.push(request(&mut p).await);
        }
    }
    assert_eq!((matching, writing), (6, 6));
    assert_eq!(id, 1);
    match_worker.abort();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pause_keeps_completed_drafts_and_stop_discards_in_flight_results(db: SqlitePool) {
    let mut p = Provider::start().await;
    let (id, _) = setup(&db, &p.address, 5).await;
    let mut run = Runner::start(&db, Default::default());
    let mut requests = vec![];
    for _ in 0..3 {
        requests.push(request(&mut p).await);
    }
    task::pause(&db, id, 2).await.unwrap();
    run.handle.wake();
    for r in requests {
        r.respond.send(Response::answer(LETTER)).ok().unwrap();
    }
    run.until(&db, id, |t| t.progress.review == 3).await;
    assert_eq!(
        task::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .progress
            .generating,
        2
    );
    assert!(p.requests.try_recv().is_err());
    task::resume(&db, id).await.unwrap();
    run.handle.wake();
    let a = request(&mut p).await;
    let b = request(&mut p).await;
    task::stop(&db, id, 3).await.unwrap();
    run.handle.wake();
    a.respond.send(Response::answer(LETTER)).ok();
    b.respond.send(Response::answer(LETTER)).ok();
    run.until(&db, id, |t| t.progress.canceled == 5).await;
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().progress.review,
        0
    );
}
