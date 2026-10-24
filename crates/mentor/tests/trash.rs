// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use std::num::NonZeroU32;

use mentor::trash::{self, TrashQuery};
use mentor::{MentorFields, MentorQuery};
use sqlx::SqlitePool;

async fn mentor(db: &SqlitePool, name: &str) -> i64 {
    let fields = MentorFields {
        name: name.into(),
        ..MentorFields::default()
    };
    mentor::create(db, &fields, 0).await.unwrap()
}

async fn email(db: &SqlitePool, mentor: i64, state: &str) -> i64 {
    let out = state == "sent";
    sqlx::query(
        "INSERT INTO email (mentor_id, kind, state, planned_at, from_address, message_id, sent_at,
           created_at, updated_at, subject, body_md, html, text)
         VALUES (?, 'first', ?, 1, ?, ?, ?, 0, 0, '申请', '信', '<p>信</p>', '信')",
    )
    .bind(mentor)
    .bind(state)
    .bind(out.then_some("me@qq.com"))
    .bind(out.then(|| format!("<{mentor}-{state}@aes>")))
    .bind(out.then_some(1))
    .execute(db)
    .await
    .unwrap()
    .last_insert_rowid()
}

async fn state_of(db: &SqlitePool, email: i64) -> String {
    sqlx::query_scalar("SELECT state FROM email WHERE id = ?")
        .bind(email)
        .fetch_one(db)
        .await
        .unwrap()
}

/// A reply seen in the mailbox, about these mentors.
async fn reply(db: &SqlitePool, mentors: &[i64], uid: i64) -> i64 {
    let id = sqlx::query(
        "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, sent_at, from_address,
           truncated)
         VALUES (1, 'INBOX', 1, ?, 'reply', 2, 'x@edu.cn', 0)",
    )
    .bind(uid)
    .execute(db)
    .await
    .unwrap()
    .last_insert_rowid();
    for mentor in mentors {
        sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, ?)")
            .bind(mentor)
            .bind(id)
            .execute(db)
            .await
            .unwrap();
    }
    id
}

async fn names(page: mentor::MentorPage) -> Vec<String> {
    page.mentors.into_iter().map(|m| m.name).collect()
}

async fn home(db: &SqlitePool, keyword: Option<&str>) -> Vec<String> {
    let query = MentorQuery {
        status: None,
        keyword: keyword.map(Into::into),
        page: NonZeroU32::MIN,
    };
    names(mentor::list(db, &query).await.unwrap()).await
}

async fn in_trash(db: &SqlitePool) -> Vec<String> {
    let query = TrashQuery {
        page: NonZeroU32::MIN,
    };
    names(trash::list(db, &query).await.unwrap()).await
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn trashing_cancels_unsent_mail_and_restoring_keeps_it_canceled(db: SqlitePool) {
    let zhang = mentor(&db, "张伟").await;
    let li = mentor(&db, "李静").await;
    let approved = email(&db, zhang, "approved").await;
    let review = email(&db, zhang, "review").await;
    let sent = email(&db, zhang, "sent").await;
    let other = email(&db, li, "approved").await;

    trash::trash(&db, &[zhang], 10).await.unwrap();
    trash::trash(&db, &[li], 20).await.unwrap();
    assert!(home(&db, None).await.is_empty());
    assert_eq!(in_trash(&db).await, ["李静", "张伟"]);
    assert!(mentor::get(&db, zhang).await.unwrap().unwrap().trashed);
    for id in [approved, review, other] {
        assert_eq!(state_of(&db, id).await, "canceled");
    }
    assert_eq!(state_of(&db, sent).await, "sent");

    trash::restore(&db, &[zhang, li], 30).await.unwrap();
    assert_eq!(home(&db, None).await, ["张伟", "李静"]);
    assert!(in_trash(&db).await.is_empty());
    assert_eq!(state_of(&db, approved).await, "canceled");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deleting_for_good_takes_only_what_is_the_mentors_alone(db: SqlitePool) {
    sqlx::query(
        "INSERT INTO mailbox VALUES (1, 'me@qq.com', '我', 'qq', 'smtp.qq.com', 465, 'tls',
           'imap.qq.com', 993, 'tls', 'password', 'secret', 50, 60, 1, NULL, NULL, NULL, NULL, 0)",
    )
    .execute(&db)
    .await
    .unwrap();
    let zhang = mentor(&db, "张伟").await;
    let li = mentor(&db, "李静").await;
    email(&db, zhang, "sent").await;
    reply(&db, &[zhang], 1).await;
    let shared = reply(&db, &[zhang, li], 2).await;

    // Only mentors in the trash can be deleted.
    trash::delete(&db, &[zhang]).await.unwrap();
    assert_eq!(home(&db, Some("张伟")).await, ["张伟"]);

    trash::trash(&db, &[zhang], 10).await.unwrap();
    trash::delete(&db, &[zhang]).await.unwrap();
    assert!(mentor::get(&db, zhang).await.unwrap().is_none());
    assert!(in_trash(&db).await.is_empty());
    let messages: Vec<i64> = sqlx::query_scalar("SELECT id FROM message ORDER BY id")
        .fetch_all(&db)
        .await
        .unwrap();
    assert_eq!(messages, [shared]);
    let emails: i64 = sqlx::query_scalar("SELECT count(*) FROM email")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(emails, 0);
    // The search index forgets the mentor, and the contact summary still matches its definition.
    let indexed: i64 = sqlx::query_scalar("SELECT count(*) FROM mentor_search WHERE rowid = ?")
        .bind(zhang)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(indexed, 0);
    let drift: i64 = sqlx::query_scalar(
        "SELECT (SELECT count(*) FROM (SELECT * FROM mentor_contact EXCEPT SELECT * FROM mentor_contact_live))
              + (SELECT count(*) FROM (SELECT * FROM mentor_contact_live EXCEPT SELECT * FROM mentor_contact))",
    )
    .fetch_one(&db)
    .await
    .unwrap();
    assert_eq!(drift, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_deleted_mentors_id_is_never_reused(db: SqlitePool) {
    let newest = mentor(&db, "张伟").await;
    trash::trash(&db, &[newest], 10).await.unwrap();
    trash::delete(&db, &[newest]).await.unwrap();
    assert!(mentor(&db, "李静").await > newest);
}
