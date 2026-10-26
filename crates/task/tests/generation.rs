#![cfg(test)]
use provider::{Provider, Response};
use sqlx::SqlitePool;
use task::{
    NewTask, Writer,
    generation::{self, DraftsFinished, Generator},
};
use test_fixtures::provider;
use tokio::{
    sync::mpsc,
    task::JoinHandle,
    time::{Duration, timeout},
};
const LETTER: &str = "<subject>申请</subject><body>王老师：您好，我希望申请读研。</body>";
const MATCH: &str =
    r#"{"score":80,"reason":"方向相近","directions":[],"highlights":[],"risks":[]}"#;
struct Runner {
    handle: Generator,
    writes: db::Writes,
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
        let writes = db::Writes::default();
        let worker = tokio::spawn(generation::run(
            db.clone(),
            writes.clone(),
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
            writes,
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
    profile::set_ai_fact_check(db, false).await.unwrap();
    let config = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', ?, 'fixture', '', 1, 0)
        RETURNING id"#,
    )
    .bind(address)
    .fetch_one(db)
    .await
    .unwrap();
    let mut ids = vec![];
    let offset: i64 = sqlx::query_scalar("SELECT count(*) FROM mentor")
        .fetch_one(db)
        .await
        .unwrap();
    for i in offset..offset + n {
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
            kind: task::Kind::First,
            schedule: email::schedule::Schedule::Now,
            name: "AI 任务".into(),
            ids: ids.clone(),
            writer: Writer::Ai,
            template_id: None,
            template_source_id: None,
            source_task_id: None,
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
async fn usage_write_failures_keep_the_letter_and_findings_without_calling_again(db: SqlitePool) {
    for block_check in [false, true] {
        let mut p = Provider::start().await.unwrap();
        let (id, _) = setup(&db, &p.address, 1).await;
        profile::set_ai_fact_check(&db, true).await.unwrap();
        let mut run = Runner::start(&db, Default::default());
        let writing = request(&mut p).await;
        let pending = if block_check {
            writing.respond.send(Response::answer(LETTER)).unwrap();
            request(&mut p).await
        } else {
            writing
        };
        sqlx::query(
            r#"CREATE TRIGGER block_usage BEFORE INSERT ON usage
            BEGIN
                SELECT RAISE(FAIL, 'usage storage unavailable');
            END"#,
        )
        .execute(&db)
        .await
        .unwrap();
        let guard = tokio::spawn(async {
            loop {
                tokio::task::yield_now().await;
            }
        });
        tokio::time::pause();
        pending
            .respond
            .send(Response::answer(if block_check {
                r#"[{"quote":"申请读研","reason":"材料未提到"}]"#
            } else {
                LETTER
            }))
            .unwrap();
        timeout(Duration::from_secs(10), async {
            while run.writes.failure().is_none() {
                assert!(
                    task::get(&db, id)
                        .await
                        .unwrap()
                        .unwrap()
                        .pause_reason
                        .is_none()
                );
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        assert!(p.requests.try_recv().is_err());
        sqlx::query("DROP TRIGGER block_usage")
            .execute(&db)
            .await
            .unwrap();
        tokio::time::advance(Duration::from_secs(5)).await;
        tokio::time::resume();
        guard.abort();
        if !block_check {
            request(&mut p)
                .await
                .respond
                .send(Response::answer(
                    r#"[{"quote":"申请读研","reason":"材料未提到"}]"#,
                ))
                .unwrap();
        }
        assert_eq!(run.finished().await.generated, 1);
        let drafts = task::pending(&db, id, 1).await.unwrap();
        assert_eq!(drafts[0].draft.body_md, "王老师：您好，我希望申请读研。");
        assert!(matches!(
            &drafts[0].draft.fact_check,
            Some(email::fact_check::FactCheck::Completed { findings })
                if findings.len() == 1 && findings[0].quote == "申请读研"
        ));
        let calls: i64 = sqlx::query_scalar(
            r#"SELECT count(*)
            FROM usage u
            JOIN email e ON e.id = u.email_id
            WHERE e.task_id = ?"#,
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
        assert_eq!(calls, 2);
        assert_eq!(run.writes.failure(), None);
        assert!(p.requests.try_recv().is_err());
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn generates_and_regenerates_follow_up_body_with_the_fixed_subject(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (_, ids) = setup(&db, &p.address, 1).await;
    sqlx::query(
        r#"UPDATE email
        SET state = 'sent', subject = '申请', body_md = '之前发出的申请',
            html = '<p>之前发出的申请</p>', text = '之前发出的申请', planned_at = 1,
            sent_at = 1, from_address = 'me@example.com',
            to_address = 'm0@example.com', message_id = '<first@example.com>'
        WHERE mentor_id = ?"#,
    )
    .bind(ids[0])
    .execute(&db)
    .await
    .unwrap();
    let id = task::create(
        &db,
        &NewTask {
            kind: task::Kind::FollowUp,
            name: "AI 追问".into(),
            ids,
            writer: Writer::Ai,
            template_id: None,
            template_source_id: None,
            source_task_id: None,
            model_config_id: None,
            references: vec![],
            attachments: vec![],
            schedule: email::schedule::Schedule::Now,
        },
        2,
    )
    .await
    .unwrap();
    let mut run = Runner::start(&db, Default::default());
    request(&mut p)
        .await
        .respond
        .send(Response::answer(
            "<body>王老师：想请问是否方便查看我的申请。</body>",
        ))
        .ok()
        .unwrap();
    run.until(&db, id, |t| t.progress.review == 1).await;
    let email = sqlx::query_scalar::<_, i64>("SELECT id FROM email WHERE task_id=?")
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
    let key = task::TaskEmail {
        task_id: id,
        id: email,
    };
    let draft = task::draft(&db, &key).await.unwrap().unwrap();
    assert_eq!(draft.subject, "Re: 申请");
    assert!(draft.body_md.contains("查看我的申请"));
    task::regenerate(&db, id, Some(email), false, 3)
        .await
        .unwrap();
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(
            "<body>王老师：再次请教招生安排，谢谢！</body>",
        ))
        .ok()
        .unwrap();
    run.until(&db, id, |t| t.progress.review == 1).await;
    let draft = task::draft(&db, &key).await.unwrap().unwrap();
    assert_eq!(draft.subject, "Re: 申请");
    assert!(draft.body_md.contains("再次请教"));
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn generates_and_reports_single_failures_and_finishes_once(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
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
    let mut p = Provider::start().await.unwrap();
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
    let mut p = Provider::start().await.unwrap();
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
    let mut p = Provider::start().await.unwrap();
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
    let mut p = Provider::start().await.unwrap();
    let (id, ids) = setup(&db, &p.address, 1).await;
    sqlx::query(
        r#"INSERT INTO material (id, file_name, size, for_matching, for_writing,
            attach_by_default, created_at, extracted_text)
        VALUES (1, '参考.pdf', 1, 0, 0, 0, 0, '固定资料：课程项目')"#,
    )
    .execute(&db)
    .await
    .unwrap();
    sqlx::query("INSERT INTO task_material VALUES (?,1,'reference')")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE profile SET name = '林晓'")
        .execute(&db)
        .await
        .unwrap();
    sqlx::query(
        r#"UPDATE task
        SET template_subject = '申请{导师姓名}',
            template_body_md = '{导师姓名}老师：我是{我的姓名}，原始底稿'"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let mut run = Runner::start(&db, Default::default());
    let old = request(&mut p).await;
    assert!(old.body.to_string().contains("固定资料"));
    assert!(old.body.to_string().contains("原始底稿"));
    assert!(
        old.body
            .to_string()
            .contains(&format!("申请王{}", ids[0] - 1))
    );
    let message: serde_json::Value =
        serde_json::from_str(old.body["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(message["个人资料"]["姓名"], "林晓");
    assert_eq!(message["导师"]["姓名"], format!("王{}", ids[0] - 1));
    assert_eq!(
        message["现在的信"]["正文"],
        format!("王{}老师：我是林晓，原始底稿", ids[0] - 1)
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
    let mut p = Provider::start().await.unwrap();
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
        db::Writes::default(),
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
    let mut p = Provider::start().await.unwrap();
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn fact_check_switch_controls_generation_and_token_estimates(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (id, ids) = setup(&db, &p.address, 1).await;
    let form = NewTask {
        kind: task::Kind::First,
        schedule: email::schedule::Schedule::Now,
        name: "预计用量".into(),
        ids: vec![],
        writer: Writer::Ai,
        template_id: None,
        template_source_id: None,
        source_task_id: None,
        model_config_id: None,
        references: vec![],
        attachments: vec![],
    };
    // A second eligible mentor makes both previews describe the same unsent letter.
    let eligible = mentor::create(
        &db,
        &mentor::MentorFields {
            name: "王二".into(),
            emails: vec!["second@example.com".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let form = NewTask {
        ids: vec![eligible],
        ..form
    };
    let off = task::plan(&db, &form).await.unwrap();
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let on = task::plan(&db, &form).await.unwrap();
    assert!(on.input_tokens > off.input_tokens + off.output_tokens);
    assert_eq!(on.output_tokens - off.output_tokens, 300);
    let mut run = Runner::start(&db, Default::default());
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    let check = request(&mut p).await;
    assert_eq!(
        task::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .progress
            .generating,
        1
    );
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().progress.review,
        0
    );
    let message: serde_json::Value =
        serde_json::from_str(check.body["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(message["信"]["主题"], "申请");
    check
        .respond
        .send(Response::answer(
            r#"[{"quote":"希望申请读研","reason":"资料未说明"}]"#,
        ))
        .ok()
        .unwrap();
    run.finished().await;
    let email: i64 = sqlx::query_scalar("SELECT id FROM email WHERE mentor_id=?")
        .bind(ids[0])
        .fetch_one(&db)
        .await
        .unwrap();
    let key = task::TaskEmail {
        task_id: id,
        id: email,
    };
    assert!(
        matches!(task::draft(&db,&key).await.unwrap().unwrap().fact_check,
        Some(email::fact_check::FactCheck::Completed { findings }) if findings.len()==1)
    );
    let purposes: Vec<(String, Option<i64>)> =
        sqlx::query_as("SELECT purpose,email_id FROM usage ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(
        purposes,
        vec![
            ("draft".into(), Some(email)),
            ("fact_check".into(), Some(email))
        ]
    );
    profile::set_ai_fact_check(&db, false).await.unwrap();
    task::regenerate(&db, id, Some(email), false, 2)
        .await
        .unwrap();
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    run.finished().await;
    assert!(
        task::draft(&db, &key)
            .await
            .unwrap()
            .unwrap()
            .fact_check
            .is_none()
    );
    assert!(p.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn failed_fact_check_enters_review_and_stale_check_cannot_replace_a_new_claim(
    db: SqlitePool,
) {
    let mut p = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &p.address, 1).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    request(&mut p)
        .await
        .respond
        .send(Response::Refused {
            status: 401,
            message: "wrong key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    let done = run.finished().await;
    assert_eq!((done.generated, done.failed), (1, 0));
    let email: i64 = sqlx::query_scalar("SELECT id FROM email WHERE task_id=?")
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
    let key = task::TaskEmail {
        task_id: id,
        id: email,
    };
    let draft = task::draft(&db, &key).await.unwrap().unwrap();
    assert_eq!(draft.state, email::tasks::DraftState::Review);
    assert!(matches!(
        draft.fact_check,
        Some(email::fact_check::FactCheck::Failed { reason }) if !reason.is_empty()
    ));
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().state,
        task::TaskState::Paused
    );
    task::resume(&db, id).await.unwrap();
    task::regenerate(&db, id, Some(email), false, 2)
        .await
        .unwrap();
    run.handle.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(LETTER))
        .ok()
        .unwrap();
    let stale = request(&mut p).await;
    email::tasks::recover(&db).await.unwrap();
    stale
        .respond
        .send(Response::Refused {
            status: 402,
            message: "insufficient balance".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    // The scheduler must regenerate instead of accepting the expired check and draft.
    let fresh = request(&mut p).await;
    assert_eq!(
        task::draft(&db, &key).await.unwrap().unwrap().state,
        email::tasks::DraftState::Generating
    );
    fresh
        .respond
        .send(Response::answer(
            "<subject>新主题</subject><body>王老师：新稿</body>",
        ))
        .ok()
        .unwrap();
    request(&mut p)
        .await
        .respond
        .send(Response::answer("[]"))
        .ok()
        .unwrap();
    run.finished().await;
    let draft = task::draft(&db, &key).await.unwrap().unwrap();
    assert_eq!(draft.subject, "新主题");
    assert_eq!(
        draft.fact_check,
        Some(email::fact_check::FactCheck::Completed { findings: vec![] })
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn fact_check_refreshes_configuration_and_consent_after_writing(db: SqlitePool) {
    let mut writing = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &writing.address, 1).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut checking = Provider::start().await.unwrap();
    sqlx::query(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', ?, 'replacement', '', 1, 1)"#,
    )
    .bind(&checking.address)
    .execute(&db)
    .await
    .unwrap();
    let mut run = Runner::start(&db, Default::default());
    let old = request(&mut writing).await;
    sqlx::query("DELETE FROM model_config WHERE model='fixture'")
        .execute(&db)
        .await
        .unwrap();
    old.respond.send(Response::answer(LETTER)).ok().unwrap();
    request(&mut checking)
        .await
        .respond
        .send(Response::answer("[]"))
        .ok()
        .unwrap();
    run.finished().await;
    let email: i64 = sqlx::query_scalar("SELECT id FROM email WHERE task_id=?")
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
    let usage: (String, Option<i64>) =
        sqlx::query_as("SELECT model,email_id FROM usage WHERE purpose='fact_check'")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(usage, ("replacement".into(), Some(email)));
    task::regenerate(&db, id, Some(email), false, 2)
        .await
        .unwrap();
    run.handle.wake();
    let rewrite = request(&mut checking).await;
    sqlx::query("UPDATE model_config SET agreed=0")
        .execute(&db)
        .await
        .unwrap();
    rewrite.respond.send(Response::answer(LETTER)).ok().unwrap();
    run.finished().await;
    let draft = task::draft(
        &db,
        &task::TaskEmail {
            task_id: id,
            id: email,
        },
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(draft.state, email::tasks::DraftState::Review);
    assert!(matches!(
        draft.fact_check,
        Some(email::fact_check::FactCheck::Failed { .. })
    ));
    assert!(checking.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn disabling_fact_checks_while_writing_skips_the_second_call(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &p.address, 1).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    let writing = request(&mut p).await;
    profile::set_ai_fact_check(&db, false).await.unwrap();
    writing.respond.send(Response::answer(LETTER)).ok().unwrap();
    assert_eq!(run.finished().await.generated, 1);
    let drafts = task::pending(&db, id, 1).await.unwrap();
    assert_eq!(drafts[0].draft.body_md, "王老师：您好，我希望申请读研。");
    assert_eq!(drafts[0].draft.fact_check, None);
    assert!(p.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stopped_or_paused_tasks_skip_checks_after_writing(db: SqlitePool) {
    for stop in [true, false] {
        let mut p = Provider::start().await.unwrap();
        let (id, _) = setup(&db, &p.address, 1).await;
        profile::set_ai_fact_check(&db, true).await.unwrap();
        let mut run = Runner::start(&db, Default::default());
        let writing = request(&mut p).await;
        if stop {
            task::stop(&db, id, 2).await.unwrap();
        } else {
            task::pause(&db, id, 2).await.unwrap();
        }
        while run.changes.try_recv().is_ok() {}
        writing.respond.send(Response::answer(LETTER)).ok().unwrap();
        timeout(Duration::from_secs(10), async {
            tokio::select! {
                changed = run.changes.recv() => {
                    changed.unwrap();
                }
                request = p.requests.recv() => {
                    request
                        .unwrap()
                        .respond
                        .send(Response::answer("[]"))
                        .ok()
                        .unwrap();
                    panic!("invalidated work started a fact check");
                }
            }
        })
        .await
        .unwrap();
        assert!(p.requests.try_recv().is_err());
        let row: (String, Option<String>, Option<i64>) = sqlx::query_as(
            "SELECT state,fact_check,generation_queued_at FROM email WHERE task_id=?",
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
        assert_eq!(row.0, if stop { "canceled" } else { "generating" });
        assert_eq!(row.1, None);
        if !stop {
            assert!(row.2.is_some());
            task::resume(&db, id).await.unwrap();
            run.handle.wake();
            request(&mut p)
                .await
                .respond
                .send(Response::answer(LETTER))
                .ok()
                .unwrap();
            request(&mut p)
                .await
                .respond
                .send(Response::answer("[]"))
                .ok()
                .unwrap();
            assert_eq!(run.finished().await.generated, 1);
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn expired_generation_claim_skips_the_old_letters_check(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &p.address, 1).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    let writing = request(&mut p).await;
    email::tasks::recover(&db).await.unwrap();
    writing.respond.send(Response::answer(LETTER)).ok().unwrap();
    let fresh = request(&mut p).await;
    let message: serde_json::Value =
        serde_json::from_str(fresh.body["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(message["导师"]["姓名"], "王0");
    fresh.respond.send(Response::answer(LETTER)).ok().unwrap();
    request(&mut p)
        .await
        .respond
        .send(Response::answer("[]"))
        .ok()
        .unwrap();
    assert_eq!(run.finished().await.generated, 1);
    assert_eq!(
        task::get(&db, id).await.unwrap().unwrap().progress.review,
        1
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn fact_check_rate_limit_cools_the_queue_without_regenerating_the_letter(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &p.address, 4).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    let first = request(&mut p).await;
    let second = request(&mut p).await;
    let third = request(&mut p).await;
    first.respond.send(Response::answer(LETTER)).ok().unwrap();
    let check = request(&mut p).await;
    let guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    while run.changes.try_recv().is_ok() {}
    check
        .respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(60),
        })
        .ok()
        .unwrap();
    loop {
        run.changes.recv().await.unwrap();
        if task::get(&db, id).await.unwrap().unwrap().progress.review == 1 {
            break;
        }
    }
    let drafts = task::pending(&db, id, 1).await.unwrap();
    assert_eq!(drafts[0].draft.body_md, "王老师：您好，我希望申请读研。");
    assert!(matches!(&drafts[0].draft.fact_check,
        Some(email::fact_check::FactCheck::Failed { reason }) if !reason.is_empty()));
    profile::set_ai_fact_check(&db, false).await.unwrap();
    second.respond.send(Response::answer(LETTER)).ok().unwrap();
    third.respond.send(Response::answer(LETTER)).ok().unwrap();
    run.until(&db, id, |t| t.progress.review == 3).await;
    tokio::time::advance(Duration::from_secs(59)).await;
    let queued: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM email WHERE task_id=? AND generation_queued_at IS NOT NULL",
    )
    .bind(id)
    .fetch_one(&db)
    .await
    .unwrap();
    assert_eq!(queued, 1);
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
    let done = run.finished().await;
    assert_eq!((done.generated, done.failed), (4, 0));
    let calls: i64 = sqlx::query_scalar("SELECT count(*) FROM usage WHERE purpose='draft'")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(calls, 4);
    assert!(p.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn fact_check_account_errors_pause_remaining_work_and_keep_the_draft(db: SqlitePool) {
    let mut p = Provider::start().await.unwrap();
    let (id, _) = setup(&db, &p.address, 4).await;
    profile::set_ai_fact_check(&db, true).await.unwrap();
    let mut run = Runner::start(&db, Default::default());
    let first = request(&mut p).await;
    let _second = request(&mut p).await;
    let _third = request(&mut p).await;
    first.respond.send(Response::answer(LETTER)).ok().unwrap();
    request(&mut p)
        .await
        .respond
        .send(Response::Refused {
            status: 402,
            message: "insufficient balance".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    run.until(&db, id, |t| t.progress.review == 1).await;
    let t = task::get(&db, id).await.unwrap().unwrap();
    assert_eq!(t.state, task::TaskState::Paused);
    assert!(t.pause_reason.unwrap().contains("额度"));
    assert_eq!(
        (
            t.progress.review,
            t.progress.generating,
            t.progress.generation_failed
        ),
        (1, 3, 0)
    );
    let drafts = task::pending(&db, id, 1).await.unwrap();
    assert_eq!(drafts[0].draft.body_md, "王老师：您好，我希望申请读研。");
    assert!(matches!(&drafts[0].draft.fact_check,
        Some(email::fact_check::FactCheck::Failed { reason }) if !reason.is_empty()));
    assert!(p.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn fact_check_pause_compares_its_own_configuration_with_the_current_one(db: SqlitePool) {
    for field in ["unchanged", "name", "model", "base_url", "api_key", "id"] {
        let mut p = Provider::start().await.unwrap();
        let (id, _) = setup(&db, &p.address, 1).await;
        profile::set_ai_fact_check(&db, true).await.unwrap();
        let mut run = Runner::start(&db, Default::default());
        let writing = request(&mut p).await;
        // The check can use a configuration different from the writing request.
        sqlx::query(
            r#"UPDATE model_config
            SET model = 'check-model'
            WHERE id = (SELECT model_config_id FROM task WHERE id = ?)"#,
        )
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
        writing.respond.send(Response::answer(LETTER)).ok().unwrap();
        let check = request(&mut p).await;
        assert_eq!(check.body["model"], "check-model");
        let config: i64 = sqlx::query_scalar("SELECT model_config_id FROM task WHERE id=?")
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap();
        if field == "id" {
            let replacement: i64 = sqlx::query_scalar(
                r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
                    created_at)
                VALUES ('custom', ?, 'check-model', '', 1, 0)
                RETURNING id"#,
            )
            .bind(&p.address)
            .fetch_one(&db)
            .await
            .unwrap();
            sqlx::query("UPDATE task SET model_config_id=? WHERE id=?")
                .bind(replacement)
                .bind(id)
                .execute(&db)
                .await
                .unwrap();
        } else if field != "unchanged" {
            sqlx::query(
                r#"UPDATE model_config
                SET name = CASE WHEN ?1 = 'name' THEN 'changed'
                ELSE name END,
                    model = CASE WHEN ?1 = 'model' THEN 'changed'
                ELSE model END, base_url = CASE WHEN ?1 = 'base_url' THEN
                    'http://127.0.0.1:1/v1' ELSE base_url END, api_key = CASE
                WHEN ?1 = 'api_key' THEN 'changed' ELSE api_key END
                WHERE id = ?2"#,
            )
            .bind(field)
            .bind(config)
            .execute(&db)
            .await
            .unwrap();
        }
        check
            .respond
            .send(Response::Refused {
                status: 402,
                message: "insufficient balance".into(),
                retry_after: None,
            })
            .ok()
            .unwrap();
        run.finished().await;
        let t = task::get(&db, id).await.unwrap().unwrap();
        assert_eq!(
            t.state,
            if matches!(field, "unchanged" | "name") {
                task::TaskState::Paused
            } else {
                task::TaskState::Active
            },
            "changed {field}"
        );
        assert_eq!((t.progress.review, t.progress.generation_failed), (1, 0));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restored_key_pauses_generation_without_failing_each_letter(db: SqlitePool) {
    let (id, _) = setup(&db, "http://127.0.0.1:1/v1", 4).await;
    sqlx::query("UPDATE model_config SET api_key = NULL")
        .execute(&db)
        .await
        .unwrap();
    let mut runner = Runner::start(&db, Default::default());
    runner
        .until(&db, id, |t| t.state == task::TaskState::Paused)
        .await;
    let task = task::get(&db, id).await.unwrap().unwrap();
    assert!(task.pause_reason.unwrap().contains("重新填写"));
    assert_eq!(
        (task.progress.generating, task.progress.generation_failed),
        (4, 0)
    );
}
