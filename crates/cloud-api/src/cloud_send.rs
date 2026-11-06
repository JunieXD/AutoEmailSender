//! Cloud sending (docs/design.md#云端定时发送): the server sends a planned first email from the
//! user's own mailbox at the time the app planned, with the mailbox the app chose. The app puts
//! the whole message together; the server dates it as it goes out.

use serde::{Deserialize, Serialize};

pub const MAILBOXES: &str = "/v1/cloud/mailboxes";
/// Followed by `/{id}`.
pub const EMAILS: &str = "/v1/cloud/emails";
pub const RESULTS: &str = "/v1/cloud/results";
pub const RESULTS_ACK: &str = "/v1/cloud/results/ack";

/// Ids are UUID v4s chosen by the app, as 32 lowercase hex digits; uploading or withdrawing the
/// same id again changes nothing.
pub fn valid_id(value: &str) -> bool {
    crate::backup::valid_id(value)
}

/// The most ids one request for results, or their acknowledgement, carries.
pub const MAX_IDS: usize = 500;
/// The most emails an account has waiting on the server.
pub const MAX_WAITING: usize = 500;
/// The most an account's waiting emails come to, compressed.
pub const MAX_WAITING_SIZE: usize = 200 * 1024 * 1024;
/// The most one email comes to, compressed.
pub const MAX_SIZE: usize = 50 * 1024 * 1024;
/// The most one email comes to uncompressed, so that a small upload cannot unpack into a huge one.
pub const MAX_MESSAGE: usize = 96 * 1024 * 1024;
/// How long before its time the app hands an email over, in milliseconds.
pub const LEAD: i64 = 2 * 60 * 1000;
/// How long the server keeps a result the app has not acknowledged, and an id withdrawn, in
/// milliseconds.
pub const RETENTION: i64 = 30 * 24 * 60 * 60 * 1000;

/// A mailbox the server keeps the secret of, while it has emails waiting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Mailbox {
    pub address: String,
    /// Its sending is paused on the server, after its mail server limited or refused it.
    pub pause: Option<transport::pause::Pause>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Mailboxes {
    pub mailboxes: Vec<Mailbox>,
}

/// A mailbox's settings and secret; the server logs in with them before keeping them. Uploading a
/// mailbox the server has replaces it.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MailboxUpload {
    pub address: String,
    pub sender_name: String,
    pub smtp: transport::Server,
    pub secret: String,
    pub daily_limit: u32,
    pub min_interval_seconds: u32,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MailboxSaved {
    /// Why the server could not log in, so it did not keep the mailbox.
    pub login_error: Option<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EmailUpload {
    /// The address of a mailbox the account uploaded.
    pub mailbox: String,
    pub recipient: String,
    /// No later than the end of Plus. One that arrives after it, uploaded slowly, goes at once.
    pub planned_at: i64,
    /// Returned if not sent by then; after `planned_at`.
    pub latest_at: i64,
    /// Registered for the account when it goes out, as the app does after sending.
    pub tracking_id: Option<String>,
    /// The whole message without a Date header, zstd-compressed, in base64url without padding.
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "state",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum Outcome {
    /// Handed to the mailbox's server at `at`.
    Sent { at: i64 },
    /// The mail server turned this email down.
    Failed { at: i64, reason: String },
    /// The connection broke off while it was being handed over.
    MaybeSent { at: i64, reason: String },
    /// Given back unsent: past its latest time, or Plus was taken back.
    Returned { at: i64 },
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Finished {
    pub id: String,
    pub outcome: Outcome,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ids {
    pub ids: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Results {
    /// Of the ids asked about, those with an outcome.
    pub results: Vec<Finished>,
    /// All the account's mailboxes on the server.
    pub mailboxes: Vec<Mailbox>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum Withdrawal {
    /// Taken back unsent, now or when asked before.
    Withdrawn,
    /// The server has no record of it: it never came, or its outcome was acknowledged or kept
    /// past `RETENTION`. Either way, an upload of it still on its way is refused from now on.
    Unknown,
    /// Being handed to the mail server; its outcome comes with the results.
    Sending,
    /// Too late: it has an outcome, which stays until acknowledged.
    Finished { outcome: Outcome },
}
