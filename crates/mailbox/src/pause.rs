//! Pauses a mailbox's sending (docs/design.md#多邮箱轮转); what a failure means is in
//! `transport::pause`.

use sqlx::SqliteConnection;
use transport::pause::Pause;

/// In the caller's transaction, so the pause and its email outcome take effect together.
pub async fn apply(conn: &mut SqliteConnection, id: i64, pause: &Pause) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE mailbox SET paused_until = ?1, pause_reason = ?2
         WHERE id = ?3 AND (paused_until IS NULL OR paused_until < ?1)",
        pause.until,
        pause.reason,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// A saved configuration must not inherit the failure of a request using its old settings.
pub async fn apply_for_account(
    conn: &mut SqliteConnection,
    id: i64,
    account: &crate::Account,
    pause: &Pause,
) -> Result<(), sqlx::Error> {
    let address: &str = account.sender.address.as_ref();
    sqlx::query!(
        "UPDATE mailbox SET paused_until = ?1, pause_reason = ?2
         WHERE id = ?3 AND (paused_until IS NULL OR paused_until < ?1)
           AND address = ?4 AND smtp_host = ?5 AND smtp_port = ?6 AND smtp_security = ?7
           AND imap_host = ?8 AND imap_port = ?9 AND imap_security = ?10 AND secret = ?11",
        pause.until,
        pause.reason,
        id,
        address,
        account.sender.smtp.host,
        account.sender.smtp.port,
        account.sender.smtp.security,
        account.imap.host,
        account.imap.port,
        account.imap.security,
        account.sender.secret,
    )
    .execute(conn)
    .await?;
    Ok(())
}
