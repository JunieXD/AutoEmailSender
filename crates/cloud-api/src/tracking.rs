//! Email tracking: the server counts the opens of each tracked email by a random id the app put in
//! its image address, without knowing who the email went to or what it says.

use serde::{Deserialize, Serialize};

pub const REGISTER: &str = "/v1/tracking";
pub const OPENS: &str = "/v1/tracking/opens";

/// The address of an email's tracking image, under the server's address.
pub fn image_path(id: &str) -> String {
    format!("/t/{id}.gif")
}

/// Ids are UUID v4s chosen by the app, 122 random bits, as 32 lowercase hex digits.
pub fn valid_id(value: &str) -> bool {
    crate::backup::valid_id(value)
}

/// The most ids one registration carries.
pub const MAX_IDS: usize = 100;
/// The most opens one page carries.
pub const PAGE: usize = 500;
/// How long after registration the server counts opens, in milliseconds; it forgets the email
/// then.
pub const RETENTION: i64 = 180 * 24 * 60 * 60 * 1000;
/// How long after an email went out the app goes on trying to register it. Well within
/// `RETENTION`, so that the server still knows an email whose registration it took though the
/// answer was lost, and does not charge for it again.
pub const REGISTER_WITHIN: i64 = 30 * 24 * 60 * 60 * 1000;

/// Emails tracked in each rolling 30 days while Plus is in effect, before packs are used.
pub const ALLOWANCE: u32 = 500;
/// The rolling window of `ALLOWANCE`, in milliseconds.
pub const WINDOW: i64 = 30 * 24 * 60 * 60 * 1000;
/// How long a pack lasts from when it is added, in milliseconds.
pub const PACK_LIFETIME: i64 = 365 * 24 * 60 * 60 * 1000;

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Quota {
    /// Of the allowance, in the last 30 days.
    pub used: u32,
    pub limit: u32,
    /// Left in the packs that have not expired.
    pub pack_left: u32,
    /// The earliest expiry among packs with some left.
    pub pack_expires_at: Option<i64>,
}

/// Emails handed to the mail server with these ids; each new one uses one email of the quota.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Register {
    pub ids: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Registered {
    pub accepted: Vec<String>,
    /// Over the quota, or registered by another account.
    pub rejected: Vec<String>,
    /// The quota afterwards.
    pub tracking: Quota,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpensQuery {
    /// The revision the app has opens up to.
    pub after: i64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Opens {
    pub opens: Vec<Open>,
    /// Of the page's last open, or `after` when there is none.
    pub revision: i64,
    pub more: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Open {
    pub id: String,
    pub opens: u32,
    pub first_at: i64,
    pub last_at: i64,
}
