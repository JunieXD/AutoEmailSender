use sqlx::{SqliteConnection, SqlitePool};

/// Adds the mailbox the tests' mail is in, as id 1.
pub async fn mailbox(conn: &mut SqliteConnection) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO mailbox (id, address, sender_name, provider, smtp_host, smtp_port, smtp_security,
           imap_host, imap_port, imap_security, auth, secret, daily_limit, min_interval_s,
           rotation_order, created_at)
         VALUES (1, 'me@qq.com', '我', 'qq', 'smtp.qq.com', 465, 'tls',
           'imap.qq.com', 993, 'tls', 'password', 'secret', 50, 60, 1, 0)",
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// A first email to the mentor in this state; a sent one went out at `sent_at`.
pub async fn first_email(
    db: &SqlitePool,
    mentor: i64,
    state: &str,
    sent_at: i64,
) -> Result<i64, sqlx::Error> {
    let out = state == "sent";
    Ok(sqlx::query(
        "INSERT INTO email (mentor_id, kind, state, planned_at, from_address, to_address, message_id,
           sent_at, created_at, updated_at, subject, body_md, html, text)
         VALUES (?, 'first', ?, 1, ?, ?, ?, ?, 0, 0, '申请', '信', '<p>信</p>', '信')",
    )
    .bind(mentor)
    .bind(state)
    .bind(out.then_some("me@qq.com"))
    .bind(out.then_some("li@pku.edu.cn"))
    .bind(out.then(|| format!("<{mentor}-{sent_at}@aes>")))
    .bind(out.then_some(sent_at))
    .execute(db)
    .await?
    .last_insert_rowid())
}
