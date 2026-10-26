#![cfg(test)]

use email::outcome::{self as queue, ReplyOutcome as R, ReplyProcessing};
use mentor::{ContactStatus, MentorFields, MentorQuery, ReplyOutcomeFilter as F};
use receive::outcome::Processor;
use serde_json::Value;
use sqlx::SqlitePool;
use tokio::{
    sync::mpsc,
    task::JoinHandle,
    time::{Duration, timeout},
};
#[path = "../../model/tests/common/provider.rs"]
mod provider;
use provider::{Provider, Response};

const VALID: &str = r#"{"outcome":"interested"}"#;

struct Runner {
    processor: Processor,
    changes: mpsc::UnboundedReceiver<()>,
    task: JoinHandle<()>,
}
impl Drop for Runner {
    fn drop(&mut self) {
        self.task.abort();
    }
}
impl Runner {
    fn start(db: &SqlitePool, limit: model::background::Limit) -> Self {
        let processor = Processor::default();
        let (tx, changes) = mpsc::unbounded_channel();
        let task = tokio::spawn(receive::outcome::run(
            db.clone(),
            net::Net::new(vec![]).unwrap(),
            processor.clone(),
            limit,
            move || {
                let _ = tx.send(());
            },
        ));
        Self {
            processor,
            changes,
            task,
        }
    }
    async fn until(&mut self, db: &SqlitePool, predicate: impl Fn(&ReplyProcessing) -> bool) {
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
}
async fn request(p: &mut Provider) -> provider::Request {
    timeout(Duration::from_secs(10), p.requests.recv())
        .await
        .unwrap()
        .unwrap()
}
async fn config(db: &SqlitePool, address: &str) -> i64 {
    let fields = model::ModelForm {
        name: "".into(),
        provider: "custom".into(),
        base_url: address.into(),
        model: "fixture".into(),
        input_price: None,
        output_price: None,
    }
    .check()
    .unwrap();
    let id = model::create(db, &fields, "", 1).await.unwrap();
    model::agree(db, id, "custom", Some(address)).await.unwrap();
    profile_default(db, id).await;
    id
}
async fn profile_default(db: &SqlitePool, id: i64) {
    sqlx::query("UPDATE profile SET default_model_config_id = ?")
        .bind(id)
        .execute(db)
        .await
        .unwrap();
}
async fn setup(db: &SqlitePool) -> i64 {
    test_fixtures::mail::mailbox(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
    mentor::create(
        db,
        &MentorFields {
            name: "不能发给模型的导师姓名".into(),
            research: Some("不能发给模型的研究资料".into()),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap()
}
async fn message(db: &SqlitePool, mentor: i64, kind: &str, at: i64, body: &str) -> i64 {
    let mut tx = db.begin().await.unwrap();
    let id = sqlx::query(
        r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id,
            sent_at, from_address, subject, body_text, truncated)
        VALUES (1, 'INBOX', 1, ?, ?, ?, ?, 'mentor@example.edu', '回信', ?, 0)"#,
    )
    .bind(at)
    .bind(kind)
    .bind(format!("<m{at}@example.edu>"))
    .bind(at)
    .bind(body)
    .execute(&mut *tx)
    .await
    .unwrap()
    .last_insert_rowid();
    sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
        .bind(mentor)
        .bind(id)
        .execute(&mut *tx)
        .await
        .unwrap();
    queue::enqueue(&mut tx, id, at).await.unwrap();
    tx.commit().await.unwrap();
    id
}
async fn outcome(db: &SqlitePool, id: i64) -> Option<String> {
    sqlx::query_scalar("SELECT outcome FROM message WHERE id = ?")
        .bind(id)
        .fetch_one(db)
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn switches_backfill_deduplicate_and_invalidate_deleted_claim_generations(db: SqlitePool) {
    let m = setup(&db).await;
    let first = message(&db, m, "reply", 1, "欢迎报考").await;
    message(&db, m, "auto_reply", 2, "自动回复").await;
    message(&db, m, "sent", 3, "学生的信").await;
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    queue::set_enabled(&db, true, 4).await.unwrap();
    queue::set_enabled(&db, true, 5).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 1);
    let old = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(old.id, first);
    queue::set_enabled(&db, false, 6).await.unwrap();
    queue::set_enabled(&db, true, 6).await.unwrap();
    let new = queue::next(&db).await.unwrap().unwrap();
    assert_ne!(old, new);
    assert!(!queue::complete(&db, old, R::Declined).await.unwrap());
    assert!(!queue::fail(&db, old, "旧错误").await.unwrap());
    assert!(queue::set(&db, first, R::NoSlot).await.unwrap());
    assert!(!queue::complete(&db, new, R::Interested).await.unwrap());
    assert_eq!(outcome(&db, first).await.as_deref(), Some("no_slot"));
    queue::set_enabled(&db, true, 7).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn newest_first_recovery_trash_merge_and_cascade_preserve_work(db: SqlitePool) {
    let m = setup(&db).await;
    queue::set_enabled(&db, true, 0).await.unwrap();
    let a = message(&db, m, "reply", 1, "旧").await;
    let b = message(&db, m, "reply", 2, "新").await;
    let old = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(old.id, b);
    queue::recover(&db).await.unwrap();
    assert!(!queue::complete(&db, old, R::Declined).await.unwrap());
    let new = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(new.id, b);
    mentor::trash::trash(&db, &[m], 3).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    assert!(queue::next(&db).await.unwrap().is_none());
    assert!(!queue::complete(&db, new, R::Declined).await.unwrap());
    mentor::trash::restore(&db, &[m], 4).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 2);
    let keep = mentor::create(
        &db,
        &MentorFields {
            name: "保留导师".into(),
            ..Default::default()
        },
        5,
    )
    .await
    .unwrap();
    mentor::merge::merge(&db, keep, m, 6).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.id, b);
    assert!(queue::complete(&db, claim, R::Interested).await.unwrap());
    assert_eq!(
        sqlx::query_scalar::<_, Option<String>>(
            "SELECT outcome FROM mentor_contact WHERE mentor_id=?"
        )
        .bind(keep)
        .fetch_one(&db)
        .await
        .unwrap()
        .as_deref(),
        Some("interested")
    );
    assert_eq!(queue::next(&db).await.unwrap().unwrap().id, a);
    mentor::trash::trash(&db, &[keep], 7).await.unwrap();
    mentor::trash::delete(&db, &[keep]).await.unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM reply_processing")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn latest_marked_reply_filters_all_results_and_unmarked_with_summary_in_sync(db: SqlitePool) {
    let first = setup(&db).await;
    let mut ids = vec![first];
    for i in 0..4 {
        ids.push(
            mentor::create(
                &db,
                &MentorFields {
                    name: format!("导师{i}"),
                    ..Default::default()
                },
                1,
            )
            .await
            .unwrap(),
        );
    }
    for (i, result) in [R::Interested, R::NoSlot, R::Declined, R::Unclear]
        .into_iter()
        .enumerate()
    {
        let id = message(&db, ids[i], "reply", (i + 1) as i64, "答复").await;
        queue::set(&db, id, result).await.unwrap();
    }
    let newest = message(&db, first, "reply", 20, "更新但未标记").await;
    message(&db, ids[4], "reply", 21, "未标出").await;
    for (i, filter) in [
        F::Interested,
        F::NoSlot,
        F::Declined,
        F::Unclear,
        F::Unmarked,
    ]
    .into_iter()
    .enumerate()
    {
        let query = MentorQuery {
            status: Some(ContactStatus::Replied),
            reply_outcome: Some(filter),
            keyword: None,
            unanswered_days: None,
            page: std::num::NonZeroU32::MIN,
        };
        let found = mentor::list(&db, &query, 30).await.unwrap();
        assert_eq!(found.total, 1);
        assert_eq!(found.mentors[0].id, ids[i]);
        let ignored = MentorQuery {
            status: None,
            ..query
        };
        assert_eq!(mentor::list(&db, &ignored, 30).await.unwrap().total, 5);
    }
    queue::set(&db, newest, R::Declined).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, Option<String>>(
            "SELECT outcome FROM mentor_contact WHERE mentor_id=?"
        )
        .bind(first)
        .fetch_one(&db)
        .await
        .unwrap()
        .as_deref(),
        Some("declined")
    );
    test_fixtures::contact::reconciled(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn prompt_contains_only_fresh_reply_and_referenced_student_letter_and_records_usage(
    db: SqlitePool,
) {
    let m = setup(&db).await;
    sqlx::query(
        r#"UPDATE profile
        SET name = '不能发给模型的学生姓名',
            research_interests = '不能发给模型的意向研究方向'"#,
    )
    .execute(&db)
    .await
    .unwrap();
    sqlx::query(
        r#"INSERT INTO material (file_name, size, for_matching, for_writing,
            attach_by_default, created_at, extracted_text)
        VALUES ('简历.pdf', 1, 1, 1, 0, 1, '不能发给模型的材料')"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let email = test_fixtures::mail::first_email(&db, m, "sent", 1)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE email SET body_md='学生新写的内容\n> 这行也是学生新写的',copy_message_id='<copy@example.edu>' WHERE id=?",
    )
    .bind(email)
    .execute(&db)
    .await
    .unwrap();
    let id = message(&db, m, "reply", 2, "欢迎报考。\n> 不该发送的引用").await;
    sqlx::query("UPDATE message SET in_reply_to='<copy@example.edu>' WHERE id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 3).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut p).await;
    let text = req.body["messages"][1]["content"].as_str().unwrap();
    let data: Value = serde_json::from_str(text).unwrap();
    assert_eq!(data["reply"]["text"], "欢迎报考。");
    assert_eq!(
        data["student"]["text"],
        "学生新写的内容\n> 这行也是学生新写的"
    );
    assert!(!text.contains("不能发给模型"));
    assert!(!text.contains("不该发送的引用"));
    req.respond.send(Response::answer(VALID)).ok().unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    assert_eq!(outcome(&db, id).await.as_deref(), Some("interested"));
    let usage: (String, Option<i64>, i64) =
        sqlx::query_as("SELECT purpose,email_id,input_tokens FROM usage")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(usage, ("reply_outcome".into(), Some(email), 2000));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn references_find_external_parent_strip_quotes_and_limit_total_characters(db: SqlitePool) {
    let m = setup(&db).await;
    let parent = message(&db, m, "sent", 1, &("学".repeat(7000) + "\n> 旧引用")).await;
    let id = message(&db, m, "reply", 2, "请发成绩单。\n> 不该发送的引用").await;
    sqlx::query("UPDATE message SET refs='<missing@edu> <m1@example.edu> <other@edu>' WHERE id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 3).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut p).await;
    let text = req.body["messages"][1]["content"].as_str().unwrap();
    let data: Value = serde_json::from_str(text).unwrap();
    let count = [&data["reply"], &data["student"]]
        .into_iter()
        .flat_map(|v| ["subject", "text"].map(|k| v[k].as_str().unwrap().chars().count()))
        .sum::<usize>();
    assert_eq!(count, 6000);
    assert!(data["student"]["text"].as_str().unwrap().starts_with("学"));
    assert!(!text.contains("引用"));
    req.respond.send(Response::answer(VALID)).ok().unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    let email: Option<i64> = sqlx::query_scalar("SELECT email_id FROM usage")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(email, None);
    assert_eq!(outcome(&db, parent).await, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn model_input_excludes_interleaved_quotes(db: SqlitePool) {
    assert_fresh_input(
        db,
        "在 2026年10月20日，张三 写道：\n> 可以报考吗？\n欢迎报考。\n\nOn Mon, Oct 20, 2026,\nZhang <me@example.edu> wrote:\n\n> 需要材料吗？\n请发成绩单。",
        "欢迎报考。\n\n请发成绩单。",
    )
    .await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn model_input_keeps_only_subjects_when_bodies_are_all_quotes(db: SqlitePool) {
    assert_fresh_input(db, "> 上一轮讨论中的旧内容\n> 另一段旧内容", "").await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn model_input_removes_long_quotes_before_spending_the_budget(db: SqlitePool) {
    assert_fresh_input(
        db,
        &format!("> {}\n欢迎报考，请发成绩单。", "旧".repeat(6500)),
        "欢迎报考，请发成绩单。",
    )
    .await;
}

async fn assert_fresh_input(db: SqlitePool, body: &str, fresh: &str) {
    let m = setup(&db).await;
    message(&db, m, "sent", 1, body).await;
    let id = message(&db, m, "reply", 2, body).await;
    sqlx::query("UPDATE message SET in_reply_to='<m1@example.edu>' WHERE id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 3).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut p).await;
    let data: Value =
        serde_json::from_str(req.body["messages"][1]["content"].as_str().unwrap()).unwrap();
    for letter in [&data["reply"], &data["student"]] {
        assert_eq!(letter["subject"], "回信");
        assert_eq!(letter["text"], fresh);
    }
    req.respond.send(Response::answer(VALID)).ok().unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    assert_eq!(outcome(&db, id).await.as_deref(), Some("interested"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invalid_output_retries_once_fails_then_user_retry_succeeds(db: SqlitePool) {
    let m = setup(&db).await;
    let id = message(&db, m, "reply", 1, "答复").await;
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    for _ in 0..2 {
        request(&mut p)
            .await
            .respond
            .send(Response::answer(r#"{"outcome":"unknown"}"#))
            .ok()
            .unwrap();
    }
    runner.until(&db, |p| p.failed == 1).await;
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    assert!(matches!(
        &receive::list(&db, m).await.unwrap()[0].kind,
        receive::MessageKind::Reply {
            marking: Some(queue::OutcomeMarking::Failed { reason }),
            ..
        } if reason.contains("模型")
    ));
    assert!(queue::retry(&db, id, 3).await.unwrap());
    runner.processor.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    runner
        .until(&db, |p| p.remaining == 0 && p.failed == 0)
        .await;
    assert_eq!(outcome(&db, id).await.as_deref(), Some("interested"));
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage")
            .fetch_one(&db)
            .await
            .unwrap(),
        3
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn missing_model_and_consent_pause_and_resume_without_losing_the_queue(db: SqlitePool) {
    let m = setup(&db).await;
    let id = message(&db, m, "reply", 1, "答复").await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    runner.until(&db, |p| p.pause.is_some()).await;
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 1);
    let mut p = Provider::start().await;
    let c = config(&db, &p.address).await;
    sqlx::query("UPDATE model_config SET agreed=0 WHERE id=?")
        .bind(c)
        .execute(&db)
        .await
        .unwrap();
    queue::resume(&db).await.unwrap();
    runner.processor.wake();
    runner
        .until(&db, |p| {
            p.pause.as_ref().is_some_and(|p| p.reason.contains("确认"))
        })
        .await;
    assert!(p.requests.try_recv().is_err());
    sqlx::query("UPDATE model_config SET agreed=1 WHERE id=?")
        .bind(c)
        .execute(&db)
        .await
        .unwrap();
    queue::resume(&db).await.unwrap();
    runner.processor.wake();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    assert!(outcome(&db, id).await.is_some());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn late_model_results_and_errors_cannot_override_manual_changes_or_new_work(db: SqlitePool) {
    let m = setup(&db).await;
    let id = message(&db, m, "reply", 1, "答复").await;
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let old = request(&mut p).await;
    queue::set(&db, id, R::Declined).await.unwrap();
    old.respond.send(Response::answer(VALID)).ok().unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    // The usage insert proves the delayed answer was consumed before checking the result.
    timeout(Duration::from_secs(10), async {
        loop {
            if sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage")
                .fetch_one(&db)
                .await
                .unwrap()
                == 1
            {
                break;
            }
            runner.changes.recv().await.unwrap();
        }
    })
    .await
    .unwrap();
    assert_eq!(outcome(&db, id).await.as_deref(), Some("declined"));
    let second = message(&db, m, "reply", 3, "第二封").await;
    runner.processor.wake();
    let old = request(&mut p).await;
    queue::set_enabled(&db, false, 4).await.unwrap();
    queue::set_enabled(&db, true, 4).await.unwrap();
    runner.processor.wake();
    let new = request(&mut p).await;
    old.respond
        .send(Response::Refused {
            status: 401,
            message: "bad key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    new.respond.send(Response::answer(VALID)).ok().unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    assert!(queue::progress(&db).await.unwrap().pause.is_none());
    assert_eq!(outcome(&db, second).await.as_deref(), Some("interested"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn current_config_errors_pause_old_config_errors_only_requeue(db: SqlitePool) {
    let m = setup(&db).await;
    message(&db, m, "reply", 1, "答复").await;
    let mut old_provider = Provider::start().await;
    config(&db, &old_provider.address).await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let old = request(&mut old_provider).await;
    let mut new_provider = Provider::start().await;
    config(&db, &new_provider.address).await;
    old.respond
        .send(Response::Refused {
            status: 401,
            message: "bad key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    let new = request(&mut new_provider).await;
    assert!(queue::progress(&db).await.unwrap().pause.is_none());
    new.respond
        .send(Response::Refused {
            status: 401,
            message: "bad key".into(),
            retry_after: None,
        })
        .ok()
        .unwrap();
    runner.until(&db, |p| p.pause.is_some()).await;
    let progress = queue::progress(&db).await.unwrap();
    assert_eq!((progress.remaining, progress.failed), (1, 0));
    queue::resume(&db).await.unwrap();
    runner.processor.wake();
    request(&mut new_provider)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn waiting_for_shared_capacity_reads_latest_model_and_consent(db: SqlitePool) {
    let m = setup(&db).await;
    message(&db, m, "reply", 1, "答复").await;
    let mut old = Provider::start().await;
    config(&db, &old.address).await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let limit = model::background::Limit::default();
    let permits = [
        limit.acquire().await,
        limit.acquire().await,
        limit.acquire().await,
    ];
    let mut runner = Runner::start(&db, limit);
    runner.changes.recv().await.unwrap();
    let mut new = Provider::start().await;
    let c = config(&db, &new.address).await;
    sqlx::query("UPDATE model_config SET agreed=0 WHERE id=?")
        .bind(c)
        .execute(&db)
        .await
        .unwrap();
    drop(permits);
    runner.until(&db, |p| p.pause.is_some()).await;
    assert!(old.requests.try_recv().is_err());
    assert!(new.requests.try_recv().is_err());
    sqlx::query("UPDATE model_config SET agreed=1 WHERE id=?")
        .bind(c)
        .execute(&db)
        .await
        .unwrap();
    queue::resume(&db).await.unwrap();
    runner.processor.wake();
    request(&mut new)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn rate_limit_uses_retry_after_without_polling_on_controlled_time(db: SqlitePool) {
    let m = setup(&db).await;
    message(&db, m, "reply", 1, "答复").await;
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut p).await;
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
    runner.processor.wake();
    for _ in 0..20 {
        tokio::task::yield_now().await;
    }
    assert!(p.requests.try_recv().is_err());
    tokio::time::advance(Duration::from_millis(1001)).await;
    tokio::time::resume();
    clock_guard.abort();
    request(&mut p)
        .await
        .respond
        .send(Response::answer(VALID))
        .ok()
        .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn model_work_never_exceeds_shared_capacity_and_restoration_wakes_retained_work(
    db: SqlitePool,
) {
    let m = setup(&db).await;
    for at in 1..=5 {
        message(&db, m, "reply", at, "答复").await;
    }
    let mut p = Provider::start().await;
    config(&db, &p.address).await;
    queue::set_enabled(&db, true, 6).await.unwrap();
    let mut runner = Runner::start(&db, Default::default());
    let mut requests = vec![];
    for _ in 0..3 {
        requests.push(request(&mut p).await);
    }
    assert!(p.requests.try_recv().is_err());
    mentor::trash::trash(&db, &[m], 7).await.unwrap();
    for req in requests {
        req.respond.send(Response::answer(VALID)).ok().unwrap();
    }
    timeout(Duration::from_secs(10), async {
        loop {
            if sqlx::query_scalar::<_, i64>(
                "SELECT count(*) FROM reply_processing WHERE state='running'",
            )
            .fetch_one(&db)
            .await
            .unwrap()
                == 0
            {
                break;
            }
            runner.changes.recv().await.unwrap();
        }
    })
    .await
    .unwrap();
    mentor::trash::restore(&db, &[m], 8).await.unwrap();
    runner.processor.wake();
    for _ in 0..5 {
        request(&mut p)
            .await
            .respond
            .send(Response::answer(VALID))
            .ok()
            .unwrap();
    }
    runner.until(&db, |p| p.remaining == 0).await;
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM message WHERE outcome='interested'")
            .fetch_one(&db)
            .await
            .unwrap(),
        5
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn shared_reply_stays_eligible_until_every_mentor_is_trashed(db: SqlitePool) {
    let m = setup(&db).await;
    let other = mentor::create(
        &db,
        &MentorFields {
            name: "另一位导师".into(),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    queue::set_enabled(&db, true, 1).await.unwrap();
    let id = message(&db, m, "reply", 2, "共同回信").await;
    sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
        .bind(other)
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[m], 3).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 1);
    mentor::trash::delete(&db, &[m]).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.id, id);
    assert!(queue::complete(&db, claim, R::Interested).await.unwrap());
    assert_eq!(outcome(&db, id).await.as_deref(), Some("interested"));
    test_fixtures::contact::reconciled(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn storage_rejects_half_pauses_and_failed_work_without_a_reason(db: SqlitePool) {
    let m = setup(&db).await;
    let id = message(&db, m, "reply", 1, "答复").await;
    assert!(
        sqlx::query(
            r#"INSERT INTO reply_processing (message_id, action, queued_at, state)
            VALUES (?, 'outcome', 1, 'failed')"#
        )
        .bind(id)
        .execute(&db)
        .await
        .is_err()
    );
    assert!(
        sqlx::query(
            r#"INSERT INTO reply_processing (message_id, action, queued_at, state, error)
            VALUES (?, 'outcome', 1, 'queued', '失败原因')"#
        )
        .bind(id)
        .execute(&db)
        .await
        .is_err()
    );
    assert!(
        sqlx::query("UPDATE profile SET reply_processing_paused_at=1")
            .execute(&db)
            .await
            .is_err()
    );
    assert!(
        sqlx::query("UPDATE profile SET reply_processing_pause_reason='暂停原因'")
            .execute(&db)
            .await
            .is_err()
    );
}

async fn todo_message(db: &SqlitePool, mentor: i64, at: i64, body: &str) -> i64 {
    let id = message(db, mentor, "reply", at, body).await;
    let mut tx = db.begin().await.unwrap();
    email::todos::enqueue(&mut tx, id, at).await.unwrap();
    tx.commit().await.unwrap();
    id
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todos_switch_never_backfills_and_invalidates_old_claims(db: SqlitePool) {
    let m = setup(&db).await;
    let old = message(&db, m, "reply", 1, "旧回信").await;
    email::todos::set_enabled(&db, true).await.unwrap();
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    let id = todo_message(&db, m, 2, "新回信").await;
    let claim = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.action, queue::Action::Todos);
    assert_eq!(claim.id, id);
    email::todos::set_enabled(&db, false).await.unwrap();
    email::todos::set_enabled(&db, true).await.unwrap();
    assert!(!queue::running(&db, claim).await.unwrap());
    assert!(!email::todos::complete(&db, claim, &[], 3).await.unwrap());
    assert!(!queue::fail(&db, claim, "旧错误").await.unwrap());
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    assert_eq!(outcome(&db, old).await, None);
    assert!(email::todos::for_mentor(&db, m).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todos_share_limited_material_include_reply_date_and_record_separate_usage(db: SqlitePool) {
    let m = setup(&db).await;
    let email = test_fixtures::mail::first_email(&db, m, "sent", 1)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE email SET body_md='学生新写的内容', copy_message_id='<student@edu>' WHERE id=?",
    )
    .bind(email)
    .execute(&db)
    .await
    .unwrap();
    email::todos::set_enabled(&db, true).await.unwrap();
    let id = todo_message(
        &db,
        m,
        1_792_972_800_000,
        "请周五前发成绩单。\n> 不该发送的旧引用",
    )
    .await;
    sqlx::query("UPDATE message SET in_reply_to='<student@edu>' WHERE id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    queue::set_enabled(&db, true, 2).await.unwrap();
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let mut runner = Runner::start(&db, Default::default());
    let mut materials = Vec::new();
    for _ in 0..2 {
        let req = request(&mut provider).await;
        let data: Value =
            serde_json::from_str(req.body["messages"][1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(data["reply"]["text"], "请周五前发成绩单。");
        assert_eq!(data["student"]["text"], "学生新写的内容");
        assert!(!data.to_string().contains("不能发给模型"));
        let answer = if !data["reply_date"].is_null() {
            let (date, weekday) = email::todos::reply_date(1_792_972_800_000).unwrap();
            assert_eq!(data["reply_date"], date);
            assert_eq!(data["reply_weekday"], weekday);
            r#"{"todos":[{"text":"  发成绩单  ","dueDate":null,"dueTime":"17:00"}]}"#
        } else {
            VALID
        };
        materials.push((data["reply"].clone(), data["student"].clone()));
        req.respond.send(Response::answer(answer)).ok().unwrap();
    }
    runner.until(&db, |p| p.remaining == 0).await;
    assert_eq!(materials[0], materials[1]);
    let saved = email::todos::for_mentor(&db, m).await.unwrap();
    assert_eq!(saved.len(), 1);
    assert_eq!(saved[0].text, "发成绩单");
    assert_eq!(saved[0].due_time, None);
    assert_eq!(saved[0].message_id, Some(id));
    let usage: Vec<(String, Option<i64>)> =
        sqlx::query_as("SELECT purpose,email_id FROM usage ORDER BY purpose")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(
        usage,
        [
            ("reply_outcome".into(), Some(email)),
            ("reply_todos".into(), Some(email))
        ]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todos_validate_dates_times_and_deduplicate_trimmed_text(db: SqlitePool) {
    let m = setup(&db).await;
    email::todos::set_enabled(&db, true).await.unwrap();
    let id = todo_message(&db, m, 1_792_972_800_000, "做几件事").await;
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut provider).await;
    let data: Value =
        serde_json::from_str(req.body["messages"][1]["content"].as_str().unwrap()).unwrap();
    let date = data["reply_date"].as_str().unwrap();
    let answer = serde_json::json!({"todos":[
        {"text":"  发成绩单 ","dueDate":date,"dueTime":"17:00"},
        {"text":"发成绩单","dueDate":date,"dueTime":"17:00"},
        {"text":"填表","dueDate":"2026-02-30","dueTime":"09:00"},
        {"text":"回复","dueDate":"2040-01-01","dueTime":"10:00"},
        {"text":"面谈","dueDate":date,"dueTime":"24:00"}
    ]});
    req.respond
        .send(Response::answer(&answer.to_string()))
        .ok()
        .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    let saved = email::todos::for_mentor(&db, m).await.unwrap();
    assert_eq!(saved.len(), 4);
    assert!(saved.iter().all(|todo| todo.message_id == Some(id)));
    assert_eq!(saved[0].due_date.as_deref(), Some(date));
    assert_eq!(saved[0].due_time.as_deref(), Some("17:00"));
    for todo in &saved[1..3] {
        assert_eq!(todo.due_date, None);
        assert_eq!(todo.due_time, None);
    }
    assert_eq!(saved[3].due_date.as_deref(), Some(date));
    assert_eq!(saved[3].due_time, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todos_bad_text_and_format_retry_once_fail_then_retry_without_duplicate_results(
    db: SqlitePool,
) {
    let m = setup(&db).await;
    email::todos::set_enabled(&db, true).await.unwrap();
    let id = todo_message(&db, m, 1, "发材料").await;
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let mut runner = Runner::start(&db, Default::default());
    let long =
        serde_json::json!({"todos":[{"text":"学".repeat(101),"dueDate":null,"dueTime":null}]})
            .to_string();
    for answer in [&long, "invalid JSON"] {
        request(&mut provider)
            .await
            .respond
            .send(Response::answer(answer))
            .ok()
            .unwrap();
    }
    runner.until(&db, |p| p.failed == 1).await;
    assert!(email::todos::for_mentor(&db, m).await.unwrap().is_empty());
    assert!(matches!(
        &receive::list(&db, m).await.unwrap()[0].kind,
        receive::MessageKind::Reply {
            todo_extraction: Some(email::todos::TodoExtraction::Failed { .. }),
            ..
        }
    ));
    queue::retry_action(&db, id, queue::Action::Todos, 2)
        .await
        .unwrap();
    runner.processor.wake();
    request(&mut provider)
        .await
        .respond
        .send(Response::answer(
            r#"{"todos":[{"text":"发材料","dueDate":null,"dueTime":null}]}"#,
        ))
        .ok()
        .unwrap();
    runner
        .until(&db, |p| p.remaining == 0 && p.failed == 0)
        .await;
    assert_eq!(email::todos::for_mentor(&db, m).await.unwrap().len(), 1);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage WHERE purpose='reply_todos'")
            .fetch_one(&db)
            .await
            .unwrap(),
        3
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn shared_reply_todos_choose_the_sender_address_then_lowest_mentor_id(db: SqlitePool) {
    let low = setup(&db).await;
    let high = mentor::create(
        &db,
        &MentorFields {
            name: "实际发件人".into(),
            emails: vec!["mentor@example.edu".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    email::todos::set_enabled(&db, true).await.unwrap();
    for (at, owner) in [(1, high), (2, low)] {
        let id = todo_message(&db, low, at, "发材料").await;
        sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
            .bind(high)
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        if at == 2 {
            sqlx::query("UPDATE message SET from_address='unknown@example.edu' WHERE id=?")
                .bind(id)
                .execute(&db)
                .await
                .unwrap();
        }
        let claim = queue::next(&db).await.unwrap().unwrap();
        let fields = email::todos::TodoForm {
            text: "发材料".into(),
            due_date: None,
            due_time: None,
        }
        .check()
        .unwrap();
        assert!(
            email::todos::complete(&db, claim, &[fields], 3)
                .await
                .unwrap()
        );
        assert!(!email::todos::complete(&db, claim, &[], 4).await.unwrap());
        let actual: i64 = sqlx::query_scalar("SELECT mentor_id FROM todo WHERE message_id=?")
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap();
        assert_eq!(actual, owner);
    }
    mentor::merge::merge(&db, low, high, 5).await.unwrap();
    assert_eq!(email::todos::for_mentor(&db, low).await.unwrap().len(), 2);
    mentor::trash::trash(&db, &[low], 6).await.unwrap();
    mentor::trash::delete(&db, &[low]).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM todo")
            .fetch_one(&db)
            .await
            .unwrap(),
        0
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todos_disabling_while_model_is_answering_discards_results_but_keeps_usage(db: SqlitePool) {
    let m = setup(&db).await;
    email::todos::set_enabled(&db, true).await.unwrap();
    todo_message(&db, m, 1, "发材料").await;
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let mut runner = Runner::start(&db, Default::default());
    let req = request(&mut provider).await;
    email::todos::set_enabled(&db, false).await.unwrap();
    email::todos::set_enabled(&db, true).await.unwrap();
    req.respond
        .send(Response::answer(
            r#"{"todos":[{"text":"发材料","dueDate":null,"dueTime":null}]}"#,
        ))
        .ok()
        .unwrap();
    timeout(Duration::from_secs(10), async {
        loop {
            if sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage")
                .fetch_one(&db)
                .await
                .unwrap()
                == 1
            {
                break;
            }
            runner.changes.recv().await.unwrap();
        }
    })
    .await
    .unwrap();
    runner.until(&db, |p| p.remaining == 0).await;
    assert!(email::todos::for_mentor(&db, m).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todo_queue_recovers_trash_and_atomic_storage_failure_without_partial_todos(
    db: SqlitePool,
) {
    let m = setup(&db).await;
    email::todos::set_enabled(&db, true).await.unwrap();
    todo_message(&db, m, 1, "发材料和报名").await;
    let old = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    assert!(!email::todos::complete(&db, old, &[], 2).await.unwrap());
    let claim = queue::next(&db).await.unwrap().unwrap();
    mentor::trash::trash(&db, &[m], 3).await.unwrap();
    assert!(!email::todos::complete(&db, claim, &[], 4).await.unwrap());
    assert_eq!(queue::progress(&db).await.unwrap().remaining, 0);
    mentor::trash::restore(&db, &[m], 5).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let fields = |text: &str| {
        email::todos::TodoForm {
            text: text.into(),
            due_date: None,
            due_time: None,
        }
        .check()
        .unwrap()
    };
    let items = [fields("发材料"), fields("报名")];
    sqlx::query(
        r#"CREATE TRIGGER fail_todo BEFORE INSERT ON todo WHEN NEW.text = '报名' BEGIN
        SELECT RAISE(ABORT, 'disk failure fixture');
        END"#,
    )
    .execute(&db)
    .await
    .unwrap();
    assert!(email::todos::complete(&db, claim, &items, 6).await.is_err());
    assert!(email::todos::for_mentor(&db, m).await.unwrap().is_empty());
    assert!(queue::running(&db, claim).await.unwrap());
    sqlx::query("DROP TRIGGER fail_todo")
        .execute(&db)
        .await
        .unwrap();
    assert!(email::todos::complete(&db, claim, &items, 7).await.unwrap());
    assert_eq!(email::todos::for_mentor(&db, m).await.unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn disabling_one_reply_action_preserves_the_other_actions_pause(db: SqlitePool) {
    email::todos::set_enabled(&db, true).await.unwrap();
    queue::set_enabled(&db, true, 1).await.unwrap();
    let mut conn = db.acquire().await.unwrap();
    queue::pause_if_unpaused(&mut conn, 2, "模型配置需要修正")
        .await
        .unwrap();
    drop(conn);
    queue::set_enabled(&db, false, 3).await.unwrap();
    assert!(queue::progress(&db).await.unwrap().pause.is_some());
    email::todos::set_enabled(&db, false).await.unwrap();
    assert!(queue::progress(&db).await.unwrap().pause.is_none());
    queue::set_enabled(&db, true, 4).await.unwrap();
    email::todos::set_enabled(&db, true).await.unwrap();
    let mut conn = db.acquire().await.unwrap();
    queue::pause_if_unpaused(&mut conn, 5, "模型配置需要修正")
        .await
        .unwrap();
    drop(conn);
    email::todos::set_enabled(&db, false).await.unwrap();
    assert!(queue::progress(&db).await.unwrap().pause.is_some());
    queue::set_enabled(&db, false, 6).await.unwrap();
    assert!(queue::progress(&db).await.unwrap().pause.is_none());
}
