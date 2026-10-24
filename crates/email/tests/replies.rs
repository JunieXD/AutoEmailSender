#![cfg(test)]

use email::{Approval, Approved};
use sqlx::SqlitePool;

fn approval(files: &[i64]) -> Approval<'_> {
    Approval {
        subject: "ignored",
        body_md: "王老师：谢谢。",
        html: "<p>王老师：谢谢。</p>",
        text: "王老师：谢谢。",
        attachments: files,
    }
}

async fn setup(db: &SqlitePool) -> i64 {
    sqlx::query("INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port, smtp_security,
      imap_host, imap_port, imap_security, auth, secret, daily_limit, min_interval_s, rotation_order, created_at)
      VALUES ('me@example.com', '我', 'custom', 'localhost', 465, 'tls', 'localhost', 993, 'tls', 'password', '', 50, 0, 0, 0),
      ('other@example.com', '我', 'custom', 'localhost', 465, 'tls', 'localhost', 993, 'tls', 'password', '', 50, 0, 1, 0)")
        .execute(db).await.unwrap();
    let mentor = mentor::create(
        db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["wang@pku.edu.cn".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    sqlx::query("INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id, sent_at,
      from_address, from_name, subject, refs, body_text, truncated)
      VALUES (1, 'INBOX', 1, 1, 'reply', '<parent@pku>', 1792821600000, 'secretary@pku.edu.cn', '王伟',
        '申请', '<root@example.com>', '欢迎报考。', 0)")
        .execute(db).await.unwrap();
    sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, 1)")
        .bind(mentor)
        .execute(db)
        .await
        .unwrap();
    mentor
}

async fn draft(db: &SqlitePool, mentor: i64) -> email::ReplyDraft {
    email::save_reply_draft(db, mentor, "<parent@pku>", "王老师：谢谢。", &[], 1)
        .await
        .unwrap()
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn reply_draft_saves_the_target_and_files_and_clears_only_when_both_are_empty(
    db: SqlitePool,
) {
    let mentor = setup(&db).await;
    let file = sqlx::query("INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
      VALUES ('成绩单.pdf', 100, 0, 0, 0, 0)").execute(&db).await.unwrap().last_insert_rowid();
    let first = draft(&db, mentor).await;
    let saved = email::save_reply_draft(&db, mentor, "<parent@pku>", "", &[file], 2)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(saved.id, first.id);
    assert_eq!(saved.attachments, [file]);
    let loaded = email::reply_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(loaded.reply_to, "<parent@pku>");
    assert_eq!(loaded.target.unwrap().from_name.as_deref(), Some("王伟"));
    assert!(
        email::save_reply_draft(&db, mentor, "<parent@pku>", " \n", &[], 3)
            .await
            .unwrap()
            .is_none()
    );
    assert!(email::reply_draft(&db, mentor).await.unwrap().is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn approval_freezes_the_quote_and_thread_and_consumes_the_draft_once(db: SqlitePool) {
    let mentor = setup(&db).await;
    let d = draft(&db, mentor).await;
    let approval = approval(&[]);
    let (first, repeated) = tokio::join!(
        email::approve_reply(&db, mentor, d.id, &approval, 2),
        email::approve_reply(&db, mentor, d.id, &approval, 2),
    );
    let results = [first.unwrap(), repeated.unwrap()];
    assert_eq!(results.iter().filter(|r| **r == Approved::Yes).count(), 1);
    assert_eq!(
        results.iter().filter(|r| **r == Approved::Refused).count(),
        1
    );
    sqlx::query("UPDATE message SET body_text = 'changed' WHERE id = 1")
        .execute(&db)
        .await
        .unwrap();
    let row: (String, String, String, String, i64) =
        sqlx::query_as("SELECT subject, html, text, refs, mailbox_id FROM email WHERE id = ?")
            .bind(d.id)
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(row.0, "Re: 申请");
    assert!(row.1.contains("<blockquote"));
    assert!(
        row.2
            .ends_with("王伟 <secretary@pku.edu.cn> 写道：\n> 欢迎报考。")
    );
    assert_eq!(row.3, "<root@example.com> <parent@pku>");
    assert_eq!(row.4, 1);
    assert_eq!(email::list(&db, mentor).await.unwrap().len(), 1);
    assert!(email::reply_draft(&db, mentor).await.unwrap().is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn approval_uses_the_responsible_mailbox_before_the_receiving_mailbox(db: SqlitePool) {
    let mentor = setup(&db).await;
    sqlx::query("UPDATE mentor SET mailbox_id = 2 WHERE id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    let d = draft(&db, mentor).await;
    assert_eq!(
        email::approve_reply(&db, mentor, d.id, &approval(&[]), 2)
            .await
            .unwrap(),
        Approved::Yes
    );
    let chosen: i64 = sqlx::query_scalar("SELECT mailbox_id FROM email WHERE id = ?")
        .bind(d.id)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(chosen, 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn missing_target_mailbox_and_address_leave_the_reply_as_a_draft(db: SqlitePool) {
    let mentor = setup(&db).await;
    let d = draft(&db, mentor).await;
    sqlx::query("DELETE FROM mentor_email WHERE mentor_id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::approve_reply(&db, mentor, d.id, &approval(&[]), 2)
            .await
            .unwrap(),
        Approved::NoAddress
    );
    sqlx::query("DELETE FROM message_mentor WHERE mentor_id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::approve_reply(&db, mentor, d.id, &approval(&[]), 2)
            .await
            .unwrap(),
        Approved::ReplyGone
    );
    sqlx::query("DELETE FROM mailbox")
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::approve_reply(&db, mentor, d.id, &approval(&[]), 2)
            .await
            .unwrap(),
        Approved::NoMailbox
    );
    assert!(email::reply_draft(&db, mentor).await.unwrap().is_some());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn replies_can_be_taken_back_without_overwriting_another_draft(db: SqlitePool) {
    let mentor = setup(&db).await;
    let d = draft(&db, mentor).await;
    assert_eq!(
        email::approve_reply(&db, mentor, d.id, &approval(&[]), 2)
            .await
            .unwrap(),
        Approved::Yes
    );
    let newer = draft(&db, mentor).await;
    assert!(email::withdraw(&db, d.id, 3).await.is_err());
    assert_eq!(
        email::reply_draft(&db, mentor).await.unwrap().unwrap().id,
        newer.id
    );
    email::save_reply_draft(&db, mentor, "<parent@pku>", "", &[], 4)
        .await
        .unwrap();
    assert!(email::withdraw(&db, d.id, 5).await.unwrap());
    let restored = email::reply_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(restored.id, d.id);
    assert_eq!(restored.reply_to, "<parent@pku>");
    assert_eq!(restored.body_md.as_deref(), Some("王老师：谢谢。"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn only_this_mentors_nonautomatic_reply_can_be_selected(db: SqlitePool) {
    let mentor = setup(&db).await;
    sqlx::query("INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id, sent_at, from_address, truncated)
      VALUES (1, 'INBOX', 1, 2, 'auto_reply', '<automatic@pku>', 9999999999999, 'wang@pku.edu.cn', 0)").execute(&db).await.unwrap();
    sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, 2)")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::reply_target(&db, mentor, None)
            .await
            .unwrap()
            .unwrap()
            .id,
        1
    );
    assert!(
        email::reply_target(&db, mentor, Some(2))
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        email::save_reply_draft(&db, mentor, "<automatic@pku>", "正文", &[], 1)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        email::reply_target(&db, mentor + 1, Some(1))
            .await
            .unwrap()
            .is_none()
    );
}
