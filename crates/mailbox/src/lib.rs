//! The user's own mailboxes: the provider table, adding and editing them, and logging in to send
//! and receive (docs/design.md#邮箱).

use std::borrow::Cow;

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

mod connect;
mod error;
mod imap;
pub mod providers;
mod smtp;

pub use error::MailError;
pub use imap::check_receiving;
pub use smtp::{check_sending, send_test};

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
}

#[derive(Debug, Clone, Serialize, Deserialize, TS)]
pub struct Server {
    #[ts(type = "string")]
    pub host: Cow<'static, str>,
    pub port: u16,
    pub security: Security,
}

/// How the connection is encrypted; mail is never sent unencrypted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum Security {
    /// Encrypted from the start, usually ports 465 and 993.
    Tls,
    /// Plain at first, then upgraded, usually ports 587 and 143.
    Starttls,
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
    /// Trims the text and checks every field; the error says what to fix.
    pub fn check(self) -> Result<MailboxFields, String> {
        let address = self
            .address
            .trim()
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

/// What logging in to a mailbox takes.
#[derive(Debug)]
pub struct Account {
    pub address: lettre::Address,
    pub sender_name: String,
    pub smtp: Server,
    pub imap: Server,
    pub secret: String,
}

impl MailboxFields {
    pub fn account(&self, secret: String) -> Account {
        Account {
            address: self.address.clone(),
            sender_name: self.sender_name.clone(),
            smtp: self.smtp.clone(),
            imap: self.imap.clone(),
            secret,
        }
    }
}

#[derive(Debug)]
pub enum SaveError {
    AddressTaken,
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
pub async fn list(db: &SqlitePool) -> Result<Vec<Mailbox>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT id, address, sender_name, provider,
             smtp_host, smtp_port AS "smtp_port: u16", smtp_security AS "smtp_security: Security",
             imap_host, imap_port AS "imap_port: u16", imap_security AS "imap_security: Security",
             daily_limit AS "daily_limit: u32", min_interval_s AS "min_interval_s: u32"
           FROM mailbox ORDER BY rotation_order, id"#
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

/// False when there is no such mailbox.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &MailboxFields,
    secret: &str,
) -> Result<bool, SaveError> {
    let address: &str = fields.address.as_ref();
    let row = sqlx::query!(
        "UPDATE mailbox SET address = ?, sender_name = ?, provider = ?, smtp_host = ?, smtp_port = ?,
           smtp_security = ?, imap_host = ?, imap_port = ?, imap_security = ?,
           secret = ?, daily_limit = ?, min_interval_s = ?
         WHERE id = ?",
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
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// Deletes a mailbox with its folders' progress and the messages seen in it; emails sent from it
/// keep their sender address, and its mentors are left without one (docs/data-model.md#删除).
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM mailbox WHERE id = ?", id)
        .execute(db)
        .await?;
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
}

/// What logging in to a saved mailbox takes.
pub async fn account(db: &SqlitePool, id: i64) -> Result<Option<Account>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT address, sender_name, secret,
             smtp_host, smtp_port AS "smtp_port: u16", smtp_security AS "smtp_security: Security",
             imap_host, imap_port AS "imap_port: u16", imap_security AS "imap_security: Security"
           FROM mailbox WHERE id = ?"#,
        id
    )
    .fetch_optional(db)
    .await?
    else {
        return Ok(None);
    };
    Ok(Some(Account {
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
        imap: Server {
            host: Cow::Owned(r.imap_host),
            port: r.imap_port,
            security: r.imap_security,
        },
        secret: r.secret,
    }))
}
