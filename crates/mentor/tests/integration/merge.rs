// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use super::common::names;
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
        "UPDATE mentor SET match_state = NULL, match_score = 80, matched_at = 1, match_model = 'm'
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

    let preview = merge::preview(&db, li, ming).await.unwrap();
    assert_eq!((preview.emails, preview.affiliations), (2, 1));
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
        merged.analysis.result,
        Some(mentor::MatchResult { score: 80, .. })
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
        "UPDATE email SET state = 'canceled', cancel_reason = '取消' WHERE id = ?",
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn compatible_affiliations_fill_blanks_and_keep_local_title(db: SqlitePool) {
    let a = mentor(&db, "王明", &[], "北京大学").await;
    let b = mentor(&db, "Ming Wang", &[], "北京大学").await;
    sqlx::query("UPDATE mentor_affiliation SET title = '副教授' WHERE mentor_id = ?")
        .bind(a)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE mentor_affiliation SET school = '计算机学院', title = '教授' WHERE mentor_id = ?",
    )
    .bind(b)
    .execute(&db)
    .await
    .unwrap();
    let preview = merge::preview(&db, a, b).await.unwrap();
    assert_eq!((preview.emails, preview.affiliations), (0, 1));
    assert!(mentor::get(&db, b).await.unwrap().is_some());
    merge::merge(&db, a, b, 1).await.unwrap();
    let m = mentor::get(&db, a).await.unwrap().unwrap();
    assert_eq!(
        m.affiliations,
        [Affiliation {
            university: Some("北京大学".into()),
            school: Some("计算机学院".into()),
            title: Some("副教授".into()),
            department: None
        }]
    );
}

async fn task_pair(db: &SqlitePool, states: [&str; 2]) -> (i64, [i64; 2], [i64; 2]) {
    let mentors = [
        mentor(db, "李明", &[], "北京大学").await,
        mentor(db, "Ming Li", &[], "北京大学").await,
    ];
    let task = sqlx::query(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
        VALUES ('首封任务', 'first', 'ai', 'now', 'stopped', 0)"#,
    )
    .execute(db)
    .await
    .unwrap()
    .last_insert_rowid();
    let mut emails = [0; 2];
    for i in 0..2 {
        emails[i] = first_email(
            db,
            mentors[i],
            if states[i] == "sent" {
                "sent"
            } else {
                "review"
            },
            i as i64 + 1,
        )
        .await
        .unwrap();
        sqlx::query(
            r#"UPDATE email SET task_id = ?, state = ?,
                 cancel_reason = CASE WHEN ? = 'canceled' THEN '任务停止' END
               WHERE id = ?"#,
        )
        .bind(task)
        .bind(states[i])
        .bind(states[i])
        .bind(emails[i])
        .execute(db)
        .await
        .unwrap();
    }
    (task, mentors, emails)
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_two_canceled_task_members_keeps_only_the_retained_mentors_email(db: SqlitePool) {
    let (task, mentors, emails) = task_pair(&db, ["canceled", "canceled"]).await;
    merge::merge(&db, mentors[0], mentors[1], 10).await.unwrap();
    let rows: Vec<(i64, i64, Option<i64>)> =
        sqlx::query_as("SELECT id,mentor_id,task_id FROM email ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(rows, [(emails[0], mentors[0], Some(task))]);
    reconciled(&mut db.acquire().await.unwrap()).await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_sent_and_canceled_task_members_keeps_the_sent_email_in_either_direction(
    db: SqlitePool,
) {
    for states in [["sent", "canceled"], ["canceled", "sent"]] {
        let (task, mentors, emails) = task_pair(&db, states).await;
        let sent = if states[0] == "sent" {
            emails[0]
        } else {
            emails[1]
        };
        merge::merge(&db, mentors[0], mentors[1], 10).await.unwrap();
        let rows: Vec<(i64, i64, String)> =
            sqlx::query_as("SELECT id,mentor_id,state FROM email WHERE task_id=?")
                .bind(task)
                .fetch_all(&db)
                .await
                .unwrap();
        assert_eq!(rows, [(sent, mentors[0], "sent".into())]);
        let count: i64 = sqlx::query_scalar("SELECT count(*) FROM email WHERE id IN (?,?)")
            .bind(emails[0])
            .bind(emails[1])
            .fetch_one(&db)
            .await
            .unwrap();
        assert_eq!(count, 1);
    }
    reconciled(&mut db.acquire().await.unwrap()).await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_two_sent_task_members_preserves_both_letters_and_only_one_membership(
    db: SqlitePool,
) {
    let (task, mentors, emails) = task_pair(&db, ["sent", "sent"]).await;
    let before: Vec<(i64, String, String, i64)> =
        sqlx::query_as("SELECT id,message_id,body_md,sent_at FROM email ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    merge::merge(&db, mentors[0], mentors[1], 10).await.unwrap();
    let after: Vec<(i64, String, String, i64)> =
        sqlx::query_as("SELECT id,message_id,body_md,sent_at FROM email ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(after, before);
    let rows: Vec<(i64, i64, Option<i64>)> =
        sqlx::query_as("SELECT id,mentor_id,task_id FROM email ORDER BY id")
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(
        rows,
        [
            (emails[0], mentors[0], Some(task)),
            (emails[1], mentors[0], None)
        ]
    );
    reconciled(&mut db.acquire().await.unwrap()).await.unwrap();
}
