#![cfg(test)]

use api::{Context, ErrorCode, call};
use serde_json::json;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_deleted_mailbox_is_rejected_without_changing_the_responsible_mailbox(db: SqlitePool) {
    test_fixtures::mail::mailbox(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
    let mentor = mentor::create(
        &db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    mentor::assign_mailbox(&mut db.acquire().await.unwrap(), mentor, 1)
        .await
        .unwrap();
    let ctx = Context {
        agent_access: Default::default(),
        writes: db::Writes::default(),
        db,
        net: net::Net::new(vec![]).unwrap(),
        materials: Default::default(),
        sender: Default::default(),
        receiver: Default::default(),
        matcher: Default::default(),
        crawler: Default::default(),
        generator: Default::default(),
        model_limit: Default::default(),
        reply_processor: Default::default(),
        todo_reminders: Default::default(),
    };
    let mailbox = call(
        &ctx,
        "addMailbox",
        json!({
            "address": "other@example.invalid",
            "senderName": "学生",
            "provider": "custom",
            "smtp": {"host": "localhost", "port": 1, "security": "tls"},
            "imap": {"host": "localhost", "port": 1, "security": "tls"},
            "dailyLimit": 50,
            "minIntervalS": 60,
            "secret": "fixture"
        }),
    )
    .await
    .unwrap();
    call(&ctx, "deleteMailbox", mailbox.clone()).await.unwrap();

    let error = call(
        &ctx,
        "setMentorMailbox",
        json!({"id": mentor, "mailboxId": mailbox["id"]}),
    )
    .await
    .unwrap_err();
    assert!(matches!(error.code, ErrorCode::Invalid));
    let detail = call(&ctx, "getMentor", json!({"id": mentor}))
        .await
        .unwrap();
    assert_eq!(detail["mailboxId"], 1);
}
