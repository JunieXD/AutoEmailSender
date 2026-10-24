// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

mod common;

use common::names;
use mentor::merge::{self, MergeError};
use mentor::{Affiliation, MentorFields};
use sqlx::SqlitePool;
use test_fixtures::{
    contact::reconciled,
    mail::{first_email, mailbox},
};

async fn mentor(db: &SqlitePool, name: &str, emails: &[&str], university: &str) -> i64 {
    let fields = MentorFields {
        name: name.into(),
        emails: emails.iter().map(|e| (*e).into()).collect(),
        affiliations: vec![Affiliation {
            university: Some(university.into()),
            ..Affiliation::default()
        }],
        ..MentorFields::default()
    };
    mentor::create(db, &fields, 0).await.unwrap()
}

/// A reply seen in the mailbox, about these mentors.
async fn reply(db: &SqlitePool, mentors: &[i64], uid: i64) -> i64 {
    let id = sqlx::query(
        "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, sent_at, from_address,
           truncated)
         VALUES (1, 'INBOX', 1, ?, 'reply', ?, 'x@edu.cn', 0)",
    )
    .bind(uid)
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

async fn exec(db: &SqlitePool, sql: &'static str, id: i64) {
    sqlx::query(sql).bind(id).execute(db).await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mentor_listed_twice_becomes_one(db: SqlitePool) {
    mailbox(&mut db.acquire().await.unwrap()).await.unwrap();
    let li = mentor(&db, "李明", &["liming@gmail.com"], "北京大学").await;
    let ming = mentor(&db, "Ming Li", &["ming@pku.edu.cn"], "北京大学").await;
    sqlx::query(
        r#"UPDATE mentor_detail SET note = '周三有空', papers = '["论文甲"]', match_reason = '方向契合',
             match_directions = '[]', match_highlights = '[]', match_risks = '[]' WHERE mentor_id = ?"#,
    )
    .bind(ming)
    .execute(&db)
    .await
    .unwrap();
    exec(
        &db,
        "UPDATE mentor SET match_state = 'done', match_score = 80, matched_at = 1, match_model = 'm'
         WHERE id = ?",
        ming,
    )
    .await;
    first_email(&db, li, "sent", 100).await.unwrap();
    first_email(&db, ming, "sent", 200).await.unwrap();
    let drafts = [
        first_email(&db, li, "review", 0).await.unwrap(),
        first_email(&db, ming, "review", 0).await.unwrap(),
    ];
    reply(&db, &[li], 1).await;
    reply(&db, &[li, ming], 2).await;
    // Sent from elsewhere to both addresses, it came back from one.
    let both = reply(&db, &[li, ming], 3).await;
    exec(&db, "UPDATE message SET kind = 'sent' WHERE id = ?", both).await;
    sqlx::query(
        "UPDATE message_mentor SET bounce_reason = '收件地址不存在' WHERE message_id = ? AND mentor_id = ?",
    )
    .bind(both)
    .bind(li)
    .execute(&db)
    .await
    .unwrap();

    merge::merge(&db, li, ming, 10).await.unwrap();
    let merged = mentor::get(&db, li).await.unwrap().unwrap();
    assert!(mentor::get(&db, ming).await.unwrap().is_none());
    assert_eq!(merged.aliases, ["Ming Li"]);
    // The school's address goes first, so it sends from now on.
    assert_eq!(merged.emails, ["ming@pku.edu.cn", "liming@gmail.com"]);
    assert_eq!(merged.affiliations.len(), 1);
    assert_eq!(
        (merged.note.as_deref(), merged.papers.as_slice()),
        (Some("周三有空"), ["论文甲".to_owned()].as_slice())
    );
    assert!(matches!(
        merged.analysis,
        mentor::MatchResult::Done { score: 80, .. }
    ));
    assert!(matches!(
        merged.contact,
        mentor::Contact::Replied { count: 3, .. }
    ));
    let mail: (i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM email WHERE mentor_id = ?1),
                (SELECT count(*) FROM message_mentor WHERE mentor_id = ?1)",
    )
    .bind(li)
    .fetch_one(&db)
    .await
    .unwrap();
    let bounces: i64 =
        sqlx::query_scalar("SELECT count(*) FROM message_mentor WHERE bounce_reason IS NOT NULL")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(bounces, 0);
    // One draft each can be kept: the kept mentor's.
    assert_eq!(mail, (3, 3));
    let kept: Vec<i64> = sqlx::query_scalar("SELECT id FROM email WHERE state = 'review'")
        .fetch_all(&db)
        .await
        .unwrap();
    assert_eq!(kept, [drafts[0]]);
    // Found under either name, once.
    assert_eq!(names(&db, Some("Ming")).await, ["李明"]);
    reconciled(&mut db.acquire().await.unwrap()).await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn mentors_with_mail_to_go_or_in_the_trash_are_not_merged(db: SqlitePool) {
    let li = mentor(&db, "李明", &[], "北京大学").await;
    let ming = mentor(&db, "李明", &[], "北京大学").await;
    let approved = first_email(&db, ming, "approved", 0).await.unwrap();
    assert!(matches!(
        merge::merge(&db, li, ming, 10).await,
        Err(MergeError::Busy { count: 1, .. })
    ));
    exec(
        &db,
        "UPDATE email SET state = 'canceled', error = '取消' WHERE id = ?",
        approved,
    )
    .await;
    exec(&db, "UPDATE mentor SET trashed_at = 1 WHERE id = ?", ming).await;
    assert!(matches!(
        merge::merge(&db, li, ming, 10).await,
        Err(MergeError::Gone)
    ));
    assert!(matches!(
        merge::merge(&db, li, li, 10).await,
        Err(MergeError::Gone)
    ));
    assert!(mentor::get(&db, ming).await.unwrap().is_some());
}
