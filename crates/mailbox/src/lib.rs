//! The user's own mailboxes: the provider table, adding and editing them, and logging in to send
//! and receive (docs/design.md#邮箱).

use std::borrow::Cow;

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

pub mod cloud;
mod imap;
pub mod pause;
pub mod providers;

pub use imap::{Head, Imap, Opened, check_receiving};
use transport::{Security, Sender, Server};

/// A mailbox as the settings show it. Its secret never leaves the core.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Mailbox {
    pub id: i64,
    pub address: String,
    pub sender_name: String,
    /// A provider's id, or `providers::CUSTOM`.
    pub provider: String,
    pub smtp: Server,
    pub imap: Server,
    pub daily_limit: u32,
    /// The shortest wait between two emails, in seconds.
    pub min_interval_s: u32,
    /// Why the last round of receiving failed.
    pub receive_error: Option<String>,
    pub pause: Option<transport::pause::Pause>,
    pub needs_secret: bool,
    /// Emails the official server is to send from it.
    pub cloud_emails: u32,
    /// Its sending paused on the official server, as last synced.
    pub cloud_pause: Option<transport::pause::Pause>,
}

/// A mailbox as the user entered it, without its secret; `check` validates it.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MailboxForm {
    pub address: String,
    pub sender_name: String,
    pub provider: String,
    pub smtp: Server,
    pub imap: Server,
    pub daily_limit: u32,
    pub min_interval_s: u32,
}

/// A checked `MailboxForm`.
#[derive(Debug)]
pub struct MailboxFields {
    pub address: lettre::Address,
    pub sender_name: String,
    pub provider: String,
    pub smtp: Server,
    pub imap: Server,
    pub daily_limit: u32,
    pub min_interval_s: u32,
}

impl MailboxForm {
    /// Trims the text, folds a full-width address and checks every field; the error says what
    /// to fix.
    pub fn check(self) -> Result<MailboxFields, String> {
        let address = cloud_api::tidy_email(self.address.trim())
            .parse()
            .map_err(|_| "邮箱地址格式不正确")?;
        let sender_name = self.sender_name.trim();
        if sender_name.is_empty() {
            return Err("请填写发件人名称".into());
        }
        if self.provider != providers::CUSTOM {
            match providers::find(&self.provider).map(|p| &p.setup) {
                Some(providers::Setup::Password { .. }) => {}
                Some(providers::Setup::Unsupported { reason }) => return Err((*reason).into()),
                None => return Err("请选择服务商".into()),
            }
        }
        if self.daily_limit == 0 {
            return Err("每日上限至少是 1 封".into());
        }
        Ok(MailboxFields {
            address,
            sender_name: sender_name.to_owned(),
            provider: self.provider,
            smtp: checked_server(self.smtp, "发信")?,
            imap: checked_server(self.imap, "收信")?,
            daily_limit: self.daily_limit,
            min_interval_s: self.min_interval_s,
        })
    }
}

fn checked_server(server: Server, role: &str) -> Result<Server, String> {
    let host = server.host.trim();
    if host.is_empty() || host.contains(char::is_whitespace) {
        return Err(format!("{role}服务器地址不正确"));
    }
    if server.port == 0 {
        return Err(format!("{role}端口不正确"));
    }
    Ok(Server {
        host: Cow::Owned(host.to_owned()),
        ..server
    })
}

/// A password or authorization code as the user typed it, trimmed; None when left blank.
pub fn checked_secret(secret: &str) -> Option<String> {
    Some(secret.trim())
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
}

/// What logging in to a mailbox takes: sending, and receiving with the same address and secret.
#[derive(Debug, PartialEq, Eq)]
pub struct Account {
    pub sender: Sender,
    pub imap: Server,
}

impl MailboxFields {
    pub fn account(&self, secret: String) -> Account {
        Account {
            sender: Sender {
                address: self.address.clone(),
                sender_name: self.sender_name.clone(),
                smtp: self.smtp.clone(),
                secret,
            },
            imap: self.imap.clone(),
        }
    }
}

/// Why a mailbox could not be saved or deleted.
#[derive(Debug)]
pub enum SaveError {
    AddressTaken,
    /// This many emails are to go from it on the official server, which knows it by its address.
    InCloud(u32),
    Db(sqlx::Error),
}

impl From<sqlx::Error> for SaveError {
    fn from(e: sqlx::Error) -> Self {
        match e {
            sqlx::Error::Database(d) if d.is_unique_violation() => SaveError::AddressTaken,
            e => SaveError::Db(e),
        }
    }
}

/// Every mailbox, in rotation order.
pub async fn list(db: &SqlitePool, now: i64) -> Result<Vec<Mailbox>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT id, address, sender_name, provider,
            smtp_host, smtp_port AS "smtp_port: u16",
            smtp_security AS "smtp_security: Security",
            imap_host, imap_port AS "imap_port: u16",
            imap_security AS "imap_security: Security",
            daily_limit AS "daily_limit: u32", min_interval_s AS "min_interval_s: u32",
            receive_error, paused_until, pause_reason,
            secret IS NULL AS "needs_secret!: bool",
            (SELECT count(*) FROM email WHERE mailbox_id = mailbox.id AND state = 'cloud')
              AS "cloud_emails!: u32",
            cloud_paused_until, cloud_pause_reason
        FROM mailbox
        ORDER BY rotation_order, id"#
    )
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| Mailbox {
            id: r.id,
            address: r.address,
            sender_name: r.sender_name,
            provider: r.provider,
            smtp: Server {
                host: Cow::Owned(r.smtp_host),
                port: r.smtp_port,
                security: r.smtp_security,
            },
            imap: Server {
                host: Cow::Owned(r.imap_host),
                port: r.imap_port,
                security: r.imap_security,
            },
            daily_limit: r.daily_limit,
            min_interval_s: r.min_interval_s,
            receive_error: r.receive_error,
            pause: r
                .paused_until
                .zip(r.pause_reason)
                .filter(|(until, _)| *until > now)
                .map(|(until, reason)| transport::pause::Pause { until, reason }),
            needs_secret: r.needs_secret,
            cloud_emails: r.cloud_emails,
            cloud_pause: r
                .cloud_paused_until
                .zip(r.cloud_pause_reason)
                .filter(|(until, _)| *until > now)
                .map(|(until, reason)| transport::pause::Pause { until, reason }),
        })
        .collect())
}

/// Adds a mailbox after the others in rotation order.
pub async fn create(
    db: &SqlitePool,
    fields: &MailboxFields,
    secret: &str,
    now: i64,
) -> Result<i64, SaveError> {
    let address: &str = fields.address.as_ref();
    let row = sqlx::query!(
        "INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port, smtp_security,
           imap_host, imap_port, imap_security, auth, secret, daily_limit, min_interval_s,
           rotation_order, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'password', ?, ?, ?,
           (SELECT coalesce(max(rotation_order), 0) + 1 FROM mailbox), ?)",
        address,
        fields.sender_name,
        fields.provider,
        fields.smtp.host,
        fields.smtp.port,
        fields.smtp.security,
        fields.imap.host,
        fields.imap.port,
        fields.imap.security,
        secret,
        fields.daily_limit,
        fields.min_interval_s,
        now,
    )
    .execute(db)
    .await?;
    Ok(row.last_insert_rowid())
}

/// False when there is no such mailbox. Receiving is tried again at once, as the change may have
/// fixed what failed. With emails on the official server, its address stays, and what changed of
/// what the server has is marked to go up.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &MailboxFields,
    secret: &str,
) -> Result<bool, SaveError> {
    let address: &str = fields.address.as_ref();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let in_cloud = sqlx::query_scalar!(
        r#"SELECT count(*) AS "count!: u32" FROM email e JOIN mailbox m ON m.id = e.mailbox_id
           WHERE m.id = ? AND e.state = 'cloud' AND m.address <> ?"#,
        id,
        address,
    )
    .fetch_one(&mut *tx)
    .await?;
    if in_cloud > 0 {
        return Err(SaveError::InCloud(in_cloud));
    }
    let row = sqlx::query!(
        r#"UPDATE mailbox
        SET address = ?1, sender_name = ?2, provider = ?3, smtp_host = ?4, smtp_port = ?5,
            smtp_security = ?6, imap_host = ?7, imap_port = ?8, imap_security = ?9,
            secret = ?10, daily_limit = ?11, min_interval_s = ?12, receive_error = NULL,
            receive_retry_at = NULL, paused_until = NULL, pause_reason = NULL,
            cloud_stale = cloud_stale OR (
              EXISTS (SELECT 1 FROM email WHERE mailbox_id = mailbox.id AND state = 'cloud')
              AND (sender_name IS NOT ?2 OR smtp_host IS NOT ?4 OR smtp_port IS NOT ?5
                OR smtp_security IS NOT ?6 OR secret IS NOT ?10 OR daily_limit IS NOT ?11
                OR min_interval_s IS NOT ?12))
        WHERE id = ?13"#,
        address,
        fields.sender_name,
        fields.provider,
        fields.smtp.host,
        fields.smtp.port,
        fields.smtp.security,
        fields.imap.host,
        fields.imap.port,
        fields.imap.security,
        secret,
        fields.daily_limit,
        fields.min_interval_s,
        id,
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(row.rows_affected() > 0)
}

/// Deletes a mailbox with its folders' progress and the messages seen in it; emails sent from it
/// keep their sender address, and its mentors are left without one (docs/data-model.md#删除). Not
/// while emails are to go from it on the official server: the schema refuses that too.
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), SaveError> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let in_cloud = sqlx::query_scalar!(
        r#"SELECT count(*) AS "count!: u32" FROM email WHERE mailbox_id = ? AND state = 'cloud'"#,
        id
    )
    .fetch_one(&mut *tx)
    .await?;
    if in_cloud > 0 {
        return Err(SaveError::InCloud(in_cloud));
    }
    sqlx::query!("DELETE FROM mailbox WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}

/// The saved secret of a mailbox, for an edit that leaves it blank; None when the edit changed
/// its servers, which must not get this secret.
pub async fn secret(
    db: &SqlitePool,
    id: i64,
    fields: &MailboxFields,
) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar!(
        "SELECT secret FROM mailbox
         WHERE id = ? AND smtp_host = ? COLLATE NOCASE AND imap_host = ? COLLATE NOCASE",
        id,
        fields.smtp.host,
        fields.imap.host,
    )
    .fetch_optional(db)
    .await
    .map(Option::flatten)
}

/// What logging in to a saved mailbox takes.
pub async fn account(
    db: impl sqlx::SqliteExecutor<'_>,
    id: i64,
) -> Result<Option<Account>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT address, sender_name, secret AS "secret!",
             smtp_host, smtp_port AS "smtp_port: u16", smtp_security AS "smtp_security: Security",
             imap_host, imap_port AS "imap_port: u16", imap_security AS "imap_security: Security"
           FROM mailbox WHERE id = ? AND secret IS NOT NULL"#,
        id
    )
    .fetch_optional(db)
    .await?
    else {
        return Ok(None);
    };
    Ok(Some(Account {
        sender: Sender {
            address: r
                .address
                .parse()
                .map_err(|e| sqlx::Error::Decode(Box::new(e)))?,
            sender_name: r.sender_name,
            smtp: Server {
                host: Cow::Owned(r.smtp_host),
                port: r.smtp_port,
                security: r.smtp_security,
            },
            secret: r.secret,
        },
        imap: Server {
            host: Cow::Owned(r.imap_host),
            port: r.imap_port,
            security: r.imap_security,
        },
    }))
}

/// The mailboxes to receive from at `now`, in rotation order: all but those whose server lately
/// refused.
pub async fn receivable(db: &SqlitePool, now: i64) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        "SELECT id FROM mailbox WHERE secret IS NOT NULL
           AND (receive_retry_at IS NULL OR receive_retry_at <= ?)
         ORDER BY rotation_order, id",
        now
    )
    .fetch_all(db)
    .await
}

/// Records a round of receiving that went through; true when it failed before.
pub async fn received(db: &SqlitePool, id: i64) -> Result<bool, sqlx::Error> {
    let cleared = sqlx::query!(
        "UPDATE mailbox SET receive_error = NULL, receive_retry_at = NULL
         WHERE id = ? AND receive_error IS NOT NULL",
        id
    )
    .execute(db)
    .await?;
    Ok(cleared.rows_affected() == 1)
}

/// Records why a round of receiving failed, and when to try again if not at the next round.
pub async fn receive_failed(
    db: &SqlitePool,
    id: i64,
    error: &str,
    retry_at: Option<i64>,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE mailbox SET receive_error = ?, receive_retry_at = ? WHERE id = ?",
        error,
        retry_at,
        id
    )
    .execute(db)
    .await?;
    Ok(())
}

/// Clears a pause, including one that already expired. False when the mailbox was deleted.
pub async fn resume(db: &SqlitePool, id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query!(
        "UPDATE mailbox SET paused_until = NULL, pause_reason = NULL WHERE id = ?",
        id,
    )
    .execute(db)
    .await?;
    Ok(result.rows_affected() == 1)
}
