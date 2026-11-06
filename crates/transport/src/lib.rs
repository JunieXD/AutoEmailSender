//! Talking to a mailbox's servers: connecting through the proxy with the app's TLS settings,
//! logging in to SMTP and handing an email over, and what a failure means. The official server's
//! cloud sending uses this too (docs/development.md#云端定时发送), so it has no database.

use std::borrow::Cow;

use serde::{Deserialize, Serialize};

pub mod connect;
mod error;
pub mod pause;
mod smtp;

pub use error::MailError;
pub use smtp::{
    File, Letter, Outgoing, SendError, SendStatus, Smtp, check_sending, compose, compose_undated,
    login, message_id, send_test,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
pub struct Server {
    #[cfg_attr(feature = "ts-rs", ts(type = "string"))]
    pub host: Cow<'static, str>,
    pub port: u16,
    pub security: Security,
}

/// How the connection is encrypted; mail is never sent unencrypted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[cfg_attr(feature = "sqlx", derive(sqlx::Type))]
#[serde(rename_all = "snake_case")]
#[cfg_attr(feature = "sqlx", sqlx(rename_all = "snake_case"))]
pub enum Security {
    /// Encrypted from the start, usually ports 465 and 993.
    Tls,
    /// Plain at first, then upgraded, usually ports 587 and 143.
    Starttls,
}

/// What sending from a mailbox takes.
#[derive(Debug, PartialEq, Eq)]
pub struct Sender {
    pub address: lettre::Address,
    pub sender_name: String,
    pub smtp: Server,
    pub secret: String,
}
