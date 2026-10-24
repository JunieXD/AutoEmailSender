#![cfg(test)]

use std::num::NonZeroU32;

use mentor::conversations::{self, ConversationPage, ConversationQuery};
use mentor::{MentorFields, PAGE_SIZE};
use sqlx::SqlitePool;
use test_fixtures::mail::{first_email, mailbox};

async fn add(db: &SqlitePool, name: &str) -> i64 {
    mentor::create(
        db,
        &MentorFields {
            name: name.into(),
            ..MentorFields::default()
        },
        0,
    )
    .await
    .unwrap()
}

async fn list(db: &SqlitePool, unread: bool, page: u32) -> ConversationPage {
    conversations::list(
        db,
        &ConversationQuery {
            unread,
            page: NonZeroU32::new(page).unwrap(),
        },
    )
    .await
    .unwrap()
}

async fn message(db: &SqlitePool, mentor: i64, kind: &str, at: i64, subject: Option<&str>) {
    let id: i64 = sqlx::query_scalar(
        "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, sent_at, from_address, subject, truncated)
         VALUES (1, 'INBOX', 1, ?, ?, ?, 'li@example.com', ?, 0) RETURNING id",
    ).bind(at).bind(kind).bind(at).bind(subject).fetch_one(db).await.unwrap();
    sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, ?)")
        .bind(mentor)
        .bind(id)
        .execute(db)
        .await
        .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn latest_correspondence_sorts_and_pages(db: SqlitePool) {
    mailbox(&mut db.acquire().await.unwrap()).await.unwrap();
    let mut ids = Vec::new();
    for n in 0..PAGE_SIZE + 3 {
        let id = add(&db, &format!("导师{n}")).await;
        first_email(&db, id, "sent", 10).await.unwrap();
        ids.push(id);
    }
    // A reply can be newer than the last outgoing email.
    message(&db, ids[0], "reply", 20, Some("回信")).await;
    let first = list(&db, false, 1).await;
    let second = list(&db, false, 2).await;
    assert_eq!(first.total, i64::from(PAGE_SIZE + 3));
    assert_eq!(first.conversations.len(), PAGE_SIZE as usize);
    assert_eq!(second.total, first.total);
    assert_eq!(second.conversations.len(), 3);
    let sorted: Vec<i64> = first
        .conversations
        .iter()
        .chain(&second.conversations)
        .map(|c| c.mentor_id)
        .collect();
    let expected: Vec<i64> = std::iter::once(ids[0])
        .chain(ids[1..].iter().rev().copied())
        .collect();
    assert_eq!(sorted, expected);
    assert_eq!(first.conversations[0].at, 20);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn unread_filter_and_count_exclude_trash_and_automatic_replies(db: SqlitePool) {
    mailbox(&mut db.acquire().await.unwrap()).await.unwrap();
    let li = add(&db, "李明").await;
    let wang = add(&db, "王伟").await;
    let trashed = add(&db, "回收站导师").await;
    let automatic = add(&db, "自动回复导师").await;
    add(&db, "没有往来").await;
    assert_eq!(conversations::unread_count(&db).await.unwrap(), 0);
    message(&db, li, "reply", 10, None).await;
    message(&db, li, "reply", 11, None).await;
    message(&db, wang, "reply", 12, None).await;
    message(&db, trashed, "reply", 13, None).await;
    message(&db, automatic, "auto_reply", 14, None).await;
    sqlx::query("UPDATE mentor SET trashed_at = 20 WHERE id = ?")
        .bind(trashed)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(conversations::unread_count(&db).await.unwrap(), 2);
    assert_eq!(list(&db, false, 1).await.total, 2);
    // A reply sent elsewhere clears that mentor's new-reply badge.
    message(&db, wang, "sent", 15, None).await;
    let unread = list(&db, true, 1).await;
    assert_eq!(unread.total, 1);
    assert_eq!(unread.conversations[0].name, "李明");
    assert_eq!(unread.conversations[0].unread, 1);
    assert_eq!(conversations::unread_count(&db).await.unwrap(), 1);
    sqlx::query("UPDATE message SET read_at = 30 WHERE kind = 'reply'")
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(list(&db, true, 1).await.total, 0);
    assert_eq!(conversations::unread_count(&db).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn subject_is_from_the_latest_nonautomatic_mail(db: SqlitePool) {
    mailbox(&mut db.acquire().await.unwrap()).await.unwrap();
    let li = add(&db, "李明").await;
    let email = first_email(&db, li, "sent", 10).await.unwrap();
    assert_eq!(
        list(&db, false, 1).await.conversations[0]
            .subject
            .as_deref(),
        Some("申请")
    );
    for state in ["maybe_sent", "bounced"] {
        sqlx::query("UPDATE email SET state = ?, error = '连接中断' WHERE id = ?")
            .bind(state)
            .bind(email)
            .execute(&db)
            .await
            .unwrap();
        assert_eq!(
            list(&db, false, 1).await.conversations[0]
                .subject
                .as_deref(),
            Some("申请")
        );
    }
    message(&db, li, "reply", 20, Some("欢迎报考")).await;
    assert_eq!(
        list(&db, false, 1).await.conversations[0]
            .subject
            .as_deref(),
        Some("欢迎报考")
    );
    message(&db, li, "sent", 30, Some("补充材料")).await;
    assert_eq!(
        list(&db, false, 1).await.conversations[0]
            .subject
            .as_deref(),
        Some("补充材料")
    );
    message(&db, li, "reply", 40, None).await;
    message(&db, li, "auto_reply", 50, Some("自动回复")).await;
    first_email(&db, li, "review", 60).await.unwrap();
    let page = list(&db, false, 1).await;
    assert_eq!(page.conversations[0].subject, None);
    assert_eq!(page.conversations[0].at, 40);
}
