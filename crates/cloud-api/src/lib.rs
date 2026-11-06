//! The official server's wire format. Times are UTC milliseconds; secrets use base64url without
//! padding. No desktop dependencies or user-facing messages belong here.

use serde::{Deserialize, Serialize};

pub mod account;
pub mod backup;
pub mod cloud_send;
pub mod library;
pub mod plus;
pub mod relay;
pub mod tracking;

pub mod path {
    pub const REGISTER_CODE: &str = "/v1/account/register-code";
    pub const REGISTER: &str = "/v1/account/register";
    pub const LOGIN: &str = "/v1/account/login";
    pub const LOGOUT: &str = "/v1/account/logout";
    pub const RESET_CODE: &str = "/v1/account/reset-code";
    pub const RESET_PASSWORD: &str = "/v1/account/reset-password";
    pub const ACCOUNT: &str = "/v1/account";
    pub const ACCOUNT_TERMS: &str = "/v1/account/terms";
    pub const PLUS_ITEMS: &str = "/v1/plus/items";
    /// Followed by `/{order}` to read one.
    pub const PLUS_ORDERS: &str = "/v1/plus/orders";
    pub const INVITE: &str = "/v1/invite";
    pub const REDEEM: &str = "/v1/redeem";
    pub const LIBRARY_ORGS: &str = "/v1/library/orgs";
    pub const LIBRARY_MENTORS: &str = "/v1/library/mentors";
    pub const LIBRARY_QUOTA: &str = "/v1/library/quota";
    pub const LIBRARY_FETCH: &str = "/v1/library/fetch";
    pub const LIBRARY_CHANGES: &str = "/v1/library/changes";
    pub const BACKUP: &str = "/v1/backup";
    pub const BACKUP_DISCARD: &str = "/v1/backup/discard";
    /// Followed by `/{id}`.
    pub const BACKUP_OBJECTS: &str = "/v1/backup/objects";
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EmailRequest {
    pub email: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LoginRequest {
    pub email: String,
    pub login_key: String,
    /// Cancels the account's deletion during its cooling-off period; without it such a login
    /// fails with `account_deleting`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub restore: bool,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RegisterRequest {
    pub email: String,
    pub code: String,
    pub login_key: String,
    /// The version of the terms of service and privacy policy the user agreed to.
    pub terms: String,
}

/// Resets the password.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VerifyRequest {
    pub email: String,
    pub code: String,
    pub login_key: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteRequest {
    pub login_key: String,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum Invite {
    Ineligible,
    Eligible {
        code: String,
        invited: u32,
        fill_until: Option<i64>,
    },
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CodeRequest {
    pub code: String,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Redeemed {
    pub days: u32,
    pub plus_until: i64,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Account {
    pub email: String,
    pub plus_until: Option<i64>,
    pub tracking: tracking::Quota,
    /// The version of the terms of service and privacy policy the account agreed to; none for
    /// accounts from before the terms.
    pub terms: Option<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Session {
    pub token: String,
    pub account: Account,
}

#[derive(Serialize, Deserialize)]
pub struct Empty {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    InvalidInput,
    EmailRegistered,
    EmailNotRegistered,
    InvalidCode,
    InvalidCredentials,
    RateLimited,
    SessionExpired,
    MailUnavailable,
    Internal,
    NotFound,
    MethodNotAllowed,
    QuotaExceeded,
    /// The account is in its cooling-off period after asking to be deleted; `delete_at` says
    /// when it goes.
    AccountDeleting,
    OrderNotFound,
    /// Payment is not configured, or the payment service did not take the order.
    PlusUnavailable,
    /// The account has had a refund without a reason and cannot buy again.
    PlusRefunded,
    /// Needs Plus in effect.
    PlusRequired,
    /// The tier would take Plus past a year from now.
    PlusTooLong,
    InviteNotFound,
    InviteFull,
    InviteOwn,
    InviteExpired,
    InviteUsed,
    CodeNotFound,
    CodeUsedUp,
    CodeExpired,
    CodeUsed,
    EmailIneligible,
    BackupNotFound,
    BackupIncomplete,
    BackupChanged,
    BackupTooLarge,
    /// The relay's room belongs to another account.
    RoomTaken,
    /// The server has no key to keep emails for cloud sending with.
    CloudUnavailable,
    /// The email, or the account's waiting emails with it, would be over the limit.
    CloudTooLarge,
    /// The email's mailbox has not been uploaded.
    UnknownMailbox,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApiError {
    pub code: ErrorCode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retry_after_seconds: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delete_at: Option<i64>,
}

/// This is part of the key derivation protocol; changing it would lock existing accounts out.
pub fn normalize_email(value: &str) -> String {
    tidy_email(value.trim()).to_lowercase()
}

// Formats of a mentor's fields. The desktop checks every mentor with these and the server
// checks library records with them, so the server never hands out one the desktop refuses.

/// Folds the full-width letters, digits, "＠" and "．" that Chinese input methods type.
pub fn tidy_email(value: &str) -> String {
    value
        .chars()
        .map(|c| match c {
            'Ａ'..='Ｚ' | 'ａ'..='ｚ' | '０'..='９' | '＠' | '．' => {
                (c as u32 - 0xfee0) as u8 as char
            }
            _ => c,
        })
        .collect()
}

/// A plain address as people write them: `local@domain`, where the local part is dot-separated
/// runs of letters, digits and the symbols addresses allow, and the domain is two or more labels.
/// Catches what copying from a web page drags along, such as a trailing "；" or "/people".
pub fn is_email(s: &str) -> bool {
    let Some((local, domain)) = s.split_once('@') else {
        return false;
    };
    let atom = |part: &str| {
        !part.is_empty()
            && part
                .chars()
                .all(|c| c.is_alphanumeric() || "!#$%&'*+/=?^_`{|}~-".contains(c))
    };
    let label = |part: &str| {
        !part.is_empty()
            && !part.starts_with('-')
            && !part.ends_with('-')
            && part.chars().all(|c| c.is_alphanumeric() || c == '-')
    };
    local.split('.').all(atom) && domain.split('.').count() > 1 && domain.split('.').all(label)
}

/// An http or https address with a host, in any letter case.
pub fn is_web_page(s: &str) -> bool {
    url::Url::parse(s).is_ok_and(|u| {
        matches!(u.scheme(), "http" | "https")
            && u.host().is_some()
            && !s.contains(char::is_whitespace)
    })
}

/// One alias, not several run together.
pub fn is_alias(s: &str) -> bool {
    !s.contains([';', '；', '、'])
}

pub fn normalize_code(value: &str) -> String {
    value
        .trim()
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-')
        .map(|c| match c {
            'Ａ'..='Ｚ' | 'ａ'..='ｚ' | '０'..='９' => (c as u32 - 0xfee0) as u8 as char,
            _ => c,
        })
        .map(|c| c.to_ascii_uppercase())
        .collect()
}
