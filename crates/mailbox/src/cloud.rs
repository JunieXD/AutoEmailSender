//! What the official server has of a mailbox it sends emails from (docs/design.md#云端定时发送):
//! its login and limits as last uploaded, and its pause there as last synced.

use cloud_api::cloud_send;
use sqlx::SqliteConnection;
use transport::Sender;

/// What the server is given of a mailbox.
#[derive(Debug, PartialEq, Eq)]
pub struct ForCloud {
    pub sender: Sender,
    pub daily_limit: u32,
    pub min_interval_s: u32,
}

impl ForCloud {
    pub fn upload(&self) -> cloud_send::MailboxUpload {
        cloud_send::MailboxUpload {
            address: self.sender.address.to_string(),
            sender_name: self.sender.sender_name.clone(),
            smtp: self.sender.smtp.clone(),
            secret: self.sender.secret.clone(),
            daily_limit: self.daily_limit,
            min_interval_seconds: self.min_interval_s,
        }
    }
}

/// None when the mailbox is gone or has no secret.
pub async fn for_cloud(
    conn: &mut SqliteConnection,
    id: i64,
) -> Result<Option<ForCloud>, sqlx::Error> {
    let Some(account) = crate::account(&mut *conn, id).await? else {
        return Ok(None);
    };
    let limits = sqlx::query!(
        r#"SELECT daily_limit AS "daily_limit: u32", min_interval_s AS "min_interval_s: u32"
           FROM mailbox WHERE id = ?"#,
        id
    )
    .fetch_one(conn)
    .await?;
    Ok(Some(ForCloud {
        sender: account.sender,
        daily_limit: limits.daily_limit,
        min_interval_s: limits.min_interval_s,
    }))
}

/// The mailboxes `account`'s emails on the server go from that changed since they went up.
pub async fn stale(conn: &mut SqliteConnection, account: &str) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT id AS "id!" FROM mailbox
           WHERE cloud_stale = 1 AND id IN (SELECT mailbox_id FROM email
             WHERE state = 'cloud' AND cloud_account = ?)
           ORDER BY id"#,
        account
    )
    .fetch_all(conn)
    .await
}

pub async fn is_stale(conn: &mut SqliteConnection, id: i64) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT cloud_stale AS "stale!: bool" FROM mailbox WHERE id = ?"#,
        id
    )
    .fetch_one(conn)
    .await
}

/// The server was given `copy` of mailbox `id`, whether or not it could log in with it: no longer
/// stale, unless it changed again meanwhile. In the caller's transaction, so that nothing changes
/// in between.
pub async fn given(
    conn: &mut SqliteConnection,
    id: i64,
    copy: &ForCloud,
) -> Result<(), sqlx::Error> {
    if for_cloud(&mut *conn, id).await?.as_ref() != Some(copy) {
        return Ok(());
    }
    sqlx::query!("UPDATE mailbox SET cloud_stale = 0 WHERE id = ?", id)
        .execute(conn)
        .await?;
    Ok(())
}

/// The server's pauses of the mailboxes `account`'s emails there go from, as it reports
/// `on_server`. True when one changed.
pub async fn synced(
    conn: &mut SqliteConnection,
    account: &str,
    on_server: &[cloud_send::Mailbox],
) -> Result<bool, sqlx::Error> {
    let mine = sqlx::query!(
        r#"SELECT DISTINCT m.id, m.address FROM mailbox m JOIN email e ON e.mailbox_id = m.id
           WHERE e.state = 'cloud' AND e.cloud_account = ?"#,
        account
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut changed = false;
    for m in mine {
        let pause = on_server
            .iter()
            .find(|s| s.address == m.address)
            .and_then(|s| s.pause.as_ref());
        let (until, reason) = (pause.map(|p| p.until), pause.map(|p| p.reason.as_str()));
        changed |= sqlx::query!(
            "UPDATE mailbox SET cloud_paused_until = ?1, cloud_pause_reason = ?2
             WHERE id = ?3 AND (cloud_paused_until IS NOT ?1 OR cloud_pause_reason IS NOT ?2)",
            until,
            reason,
            m.id,
        )
        .execute(&mut *conn)
        .await?
        .rows_affected()
            > 0;
    }
    Ok(changed)
}
