#![cfg(test)]

use std::borrow::Cow;

use api::{Context, call};
use fakemail::{ADDRESS, SECRET};
use mailbox::{MailboxForm, Security, Server};
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::provider;
use tokio::{
    sync::mpsc,
    time::{Duration, timeout},
};

const T: i64 = 1_800_000_000_000;
const DAY: i64 = 86_400_000;
const LETTER: &str = "<subject>申请</subject><body>王老师：您好，我希望申请读研。</body>";

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn today_work_counts_generated_drafts_retained_matches_and_confirmed_sends(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let pki = fakemail::pki();
    let (port, _) = fakemail::smtp(&pki, false).await;
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security: Security::Tls,
    };
    let form = MailboxForm {
        address: ADDRESS.into(),
        sender_name: "林晓".into(),
        provider: mailbox::providers::CUSTOM.into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 50,
        min_interval_s: 60,
    }
    .check()
    .unwrap();
    mailbox::create(&db, &form, SECRET, T).await.unwrap();
    let ctx = Context {
        writes: db::Writes::default(),
        db,
        net: net::Net::new(vec![pki.root]).unwrap(),
        materials: dir.path().to_owned(),
        sender: Default::default(),
        receiver: Default::default(),
        matcher: Default::default(),
        crawler: Default::default(),
        generator: Default::default(),
        model_limit: Default::default(),
        reply_processor: Default::default(),
        todo_reminders: Default::default(),
    };
    let (address, _) = provider::scripted(vec![(LETTER, "stop"); 3]).await.unwrap();
    sqlx::query(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed, created_at)
        VALUES ('custom', ?, 'fixture', '', 1, ?)"#,
    )
    .bind(address)
    .bind(T)
    .execute(&ctx.db)
    .await
    .unwrap();
    profile::set_ai_fact_check(&ctx.db, false).await.unwrap();
    let mut ids = vec![];
    for address in [
        "wang@example.com",
        "hangup@example.com",
        "li@example.com",
        "wu@example.com",
    ] {
        ids.push(
            mentor::create(
                &ctx.db,
                &mentor::MentorFields {
                    name: "王伟".into(),
                    emails: vec![address.into()],
                    research: Some("机器学习".into()),
                    ..Default::default()
                },
                T,
            )
            .await
            .unwrap(),
        );
    }
    let task = task::create(
        &ctx.db,
        &task::NewTask {
            name: "今天的草稿".into(),
            kind: task::Kind::First,
            writer: task::Writer::Ai,
            ids: ids[..2].to_vec(),
            template_id: None,
            template_source_id: None,
            source_task_id: None,
            model_config_id: None,
            references: vec![],
            attachments: vec![],
            schedule: email::schedule::Schedule::Now,
        },
        T,
    )
    .await
    .unwrap();
    let (done, mut finished) = mpsc::unbounded_channel();
    let worker = tokio::spawn(task::generation::run(
        ctx.db.clone(),
        ctx.writes.clone(),
        ctx.net.clone(),
        ctx.generator.clone(),
        ctx.model_limit.clone(),
        || {},
        move |result| {
            done.send(result).unwrap();
        },
    ));
    assert_eq!(
        timeout(Duration::from_secs(10), finished.recv())
            .await
            .unwrap()
            .unwrap()
            .generated,
        2
    );
    let drafts: Vec<(i64, Option<i64>)> = sqlx::query_as(
        r#"SELECT id, generated_at
        FROM email
        WHERE task_id = ?
        ORDER BY id"#,
    )
    .bind(task)
    .fetch_all(&ctx.db)
    .await
    .unwrap();
    assert!(drafts.iter().all(|(_, at)| at.is_some()));
    sqlx::query("UPDATE email SET generated_at = 1 WHERE id = ?")
        .bind(drafts[0].0)
        .execute(&ctx.db)
        .await
        .unwrap();
    task::regenerate(&ctx.db, task, Some(drafts[0].0), false, T)
        .await
        .unwrap();
    ctx.generator.wake();
    assert_eq!(
        timeout(Duration::from_secs(10), finished.recv())
            .await
            .unwrap()
            .unwrap()
            .generated,
        2
    );
    worker.abort();
    let regenerated: Option<i64> = sqlx::query_scalar(
        r#"SELECT generated_at
        FROM email
        WHERE id = ?"#,
    )
    .bind(drafts[0].0)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    assert!(regenerated.is_some_and(|at| at > 1));
    // Pin persisted times because the background runner uses the wall clock.
    for ((id, _), at) in drafts.iter().zip([T - 1, T]) {
        sqlx::query("UPDATE email SET generated_at = ? WHERE id = ?")
            .bind(at)
            .bind(id)
            .execute(&ctx.db)
            .await
            .unwrap();
        assert!(
            email::tasks::approve(
                &mut ctx.db.acquire().await.unwrap(),
                task,
                *id,
                "<p>申请</p>",
                "申请",
                T,
            )
            .await
            .unwrap()
        );
    }
    mentor::matching::enqueue(&ctx.db, &ids[2..], false, true, T)
        .await
        .unwrap();
    for _ in 0..2 {
        let claim = mentor::matching::next(&ctx.db).await.unwrap().unwrap();
        assert!(
            mentor::matching::complete(
                &ctx.db,
                claim,
                &mentor::matching::MatchAnswer {
                    score: 80,
                    reason: "方向相近".into(),
                    directions: vec![],
                    highlights: vec![],
                    risks: vec![],
                },
                "fixture",
                T - 1,
                T,
            )
            .await
            .unwrap()
        );
    }
    mentor::trash::trash(&ctx.db, &ids[3..], T).await.unwrap();
    for at in [T, T + 60_000] {
        send::step(&ctx.db, &ctx.writes, &ctx.net, &ctx.materials, || at)
            .await
            .unwrap();
    }
    let states: Vec<String> = sqlx::query_scalar("SELECT state FROM email ORDER BY id")
        .fetch_all(&ctx.db)
        .await
        .unwrap();
    assert_eq!(states, ["sent", "maybe_sent"]);
    let expected = json!({
        "drafts": [{"id": task, "name": "今天的草稿", "generated": 1}],
        "matched": 1,
        "sent": 1,
    });
    assert_eq!(
        call(&ctx, "todayWork", json!({"start": T, "end": T + DAY}))
            .await
            .unwrap(),
        expected
    );
    let message_id: String = sqlx::query_scalar(
        r#"SELECT message_id
        FROM email
        WHERE id = ?"#,
    )
    .bind(drafts[0].0)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    assert!(
        email::bounced(
            &mut ctx.db.acquire().await.unwrap(),
            &message_id,
            &[],
            "退信",
            T + 120_000,
        )
        .await
        .unwrap()
    );
    assert_eq!(
        call(&ctx, "todayWork", json!({"start": T, "end": T + DAY}))
            .await
            .unwrap(),
        expected
    );
    assert_eq!(
        call(&ctx, "todayWork", json!({"start": T - 1, "end": T}))
            .await
            .unwrap(),
        json!({
            "drafts": [{"id": task, "name": "今天的草稿", "generated": 1}],
            "matched": 0,
            "sent": 0,
        })
    );
}
