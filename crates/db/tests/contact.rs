//! The contact summary kept by triggers must always equal the view that defines it, and follow the
//! rules in docs/data-model.md#联系状态.
// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use sqlx::{SqliteConnection, SqlitePool};

type Summary = (i64, String, Option<i64>, Option<i64>, i64, i64);

async fn summary(conn: &mut SqliteConnection, table: &str) -> Vec<Summary> {
    let sql = format!(
        "SELECT mentor_id, status, last_out_at, last_reply_at, contact_count, unread FROM {table} ORDER BY mentor_id"
    );
    sqlx::query_as(sqlx::AssertSqlSafe(sql))
        .fetch_all(conn)
        .await
        .unwrap()
}

/// Asserts the stored summary matches the view, and returns it.
async fn reconciled(conn: &mut SqliteConnection) -> Vec<Summary> {
    let stored = summary(conn, "mentor_contact").await;
    assert_eq!(stored, summary(conn, "mentor_contact_live").await);
    stored
}

async fn mentor(conn: &mut SqliteConnection, name: &str) -> i64 {
    sqlx::query("INSERT INTO mentor (name, created_at, updated_at) VALUES (?, 0, 0)")
        .bind(name)
        .execute(conn)
        .await
        .unwrap()
        .last_insert_rowid()
}

/// An email the app sent; follow-ups reply to an earlier message.
async fn sent(conn: &mut SqliteConnection, mentor: i64, kind: &str, at: i64) -> i64 {
    let reply_to = (kind != "first").then_some("<earlier@aes>");
    sqlx::query(
        "INSERT INTO email (mentor_id, kind, reply_to, state, planned_at, from_address, message_id,
           sent_at, created_at, updated_at, subject, body_md, html, text)
         VALUES (?, ?, ?, 'sent', ?, 'me@qq.com', ?, ?, 0, 0, '申请', '信', '<p>信</p>', '信')",
    )
    .bind(mentor)
    .bind(kind)
    .bind(reply_to)
    .bind(at)
    .bind(format!("<{mentor}-{at}@aes>"))
    .bind(at)
    .execute(conn)
    .await
    .unwrap()
    .last_insert_rowid()
}

/// A message seen in the mailbox, linked to the given mentors.
async fn seen(
    conn: &mut SqliteConnection,
    mentors: &[i64],
    kind: &str,
    in_reply_to: Option<&str>,
    at: i64,
) -> i64 {
    let id = sqlx::query(
        "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, in_reply_to, sent_at,
           from_address, recipients)
         VALUES (1, 'INBOX', 1, ?, ?, ?, ?, 'x@edu.cn', '{}')",
    )
    .bind(at)
    .bind(kind)
    .bind(in_reply_to)
    .bind(at)
    .execute(&mut *conn)
    .await
    .unwrap()
    .last_insert_rowid();
    for mentor in mentors {
        sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, ?)")
            .bind(mentor)
            .bind(id)
            .execute(&mut *conn)
            .await
            .unwrap();
    }
    id
}

async fn exec(conn: &mut SqliteConnection, sql: &'static str, id: i64) {
    sqlx::query(sql).bind(id).execute(conn).await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn summary_follows_mail(pool: SqlitePool) {
    let conn = &mut pool.acquire().await.unwrap();
    sqlx::query(
        "INSERT INTO mailbox VALUES (1, 'me@qq.com', '我', 'qq', 'smtp.qq.com', 465, 'tls',
           'imap.qq.com', 993, 'tls', 'password', 'secret', 50, 60, 1, NULL, NULL, 0)",
    )
    .execute(&mut **conn)
    .await
    .unwrap();
    let a = mentor(conn, "甲").await;
    let b = mentor(conn, "乙").await;
    let c = mentor(conn, "丙").await;

    sent(conn, a, "first", 100).await;
    assert_eq!(
        reconciled(conn).await,
        [(a, "contacted".into(), Some(100), None, 1, 0)]
    );

    // A reply shows a red dot until it is read.
    let reply = seen(conn, &[a], "reply", Some("<a-100@aes>"), 200).await;
    assert_eq!(
        reconciled(conn).await[0],
        (a, "replied".into(), Some(100), Some(200), 1, 1)
    );
    exec(conn, "UPDATE message SET read_at = 300 WHERE id = ?", reply).await;
    assert_eq!(reconciled(conn).await[0].5, 0);

    // Answering from another mail client also clears the dot, and replies don't count as contacts.
    seen(conn, &[a], "reply", Some("<x@edu.cn>"), 400).await;
    assert_eq!(reconciled(conn).await[0].5, 1);
    seen(conn, &[a], "sent", Some("<y@edu.cn>"), 500).await;
    assert_eq!(
        reconciled(conn).await[0],
        (a, "replied".into(), Some(500), Some(400), 1, 0)
    );

    // Bounced while it is the latest mail out; a later follow-up that went through is contacted.
    let first_b = sent(conn, b, "first", 100).await;
    exec(
        conn,
        "UPDATE email SET state = 'bounced', error = '地址不存在' WHERE id = ?",
        first_b,
    )
    .await;
    assert_eq!(
        reconciled(conn).await[1],
        (b, "bounced".into(), Some(100), None, 1, 0)
    );
    let follow_up = sent(conn, b, "follow_up", 300).await;
    assert_eq!(
        reconciled(conn).await[1],
        (b, "contacted".into(), Some(300), None, 1, 0)
    );

    // Auto-replies are not contact; a new thread sent elsewhere is.
    seen(conn, &[c], "auto_reply", None, 40).await;
    assert_eq!(reconciled(conn).await.len(), 2);
    seen(conn, &[c], "sent", None, 50).await;
    assert_eq!(
        reconciled(conn).await[2],
        (c, "contacted".into(), Some(50), None, 1, 0)
    );

    // One message can belong to several mentors.
    let shared = seen(conn, &[a, c], "reply", Some("<z@edu.cn>"), 600).await;
    assert_eq!(reconciled(conn).await[2].1, "replied");

    // Moving, editing and deleting mail recomputes every mentor it touched.
    sqlx::query("UPDATE email SET mentor_id = ? WHERE id = ?")
        .bind(c)
        .bind(follow_up)
        .execute(&mut **conn)
        .await
        .unwrap();
    reconciled(conn).await;
    exec(
        conn,
        "UPDATE message SET kind = 'auto_reply', read_at = NULL WHERE id = ?",
        shared,
    )
    .await;
    reconciled(conn).await;
    // Merging duplicate mentors moves their messages over.
    sqlx::query("UPDATE message_mentor SET mentor_id = ? WHERE message_id = ?")
        .bind(b)
        .bind(reply)
        .execute(&mut **conn)
        .await
        .unwrap();
    assert_eq!(reconciled(conn).await[1].1, "replied");
    exec(
        conn,
        "DELETE FROM message_mentor WHERE message_id = ?",
        reply,
    )
    .await;
    reconciled(conn).await;
    exec(conn, "DELETE FROM email WHERE id = ?", first_b).await;
    let rows = reconciled(conn).await;
    assert!(rows.iter().all(|row| row.0 != b));
}
