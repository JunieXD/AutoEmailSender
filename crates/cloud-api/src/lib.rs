//! The official server's wire format. Times are UTC milliseconds; secrets use base64url without
//! padding. No desktop dependencies or user-facing messages belong here.

use serde::{Deserialize, Serialize};

pub mod backup;
pub mod library;

pub mod path {
    pub const REGISTER_CODE: &str = "/v1/account/register-code";
    pub const REGISTER: &str = "/v1/account/register";
    pub const LOGIN: &str = "/v1/account/login";
    pub const LOGOUT: &str = "/v1/account/logout";
    pub const RESET_CODE: &str = "/v1/account/reset-code";
    pub const RESET_PASSWORD: &str = "/v1/account/reset-password";
    pub const ACCOUNT: &str = "/v1/account";
    pub const SPONSOR_TIERS: &str = "/v1/sponsor/tiers";
    pub const SPONSOR_CLAIM: &str = "/v1/sponsor/claim";
    pub const INVITE: &str = "/v1/invite";
    pub const REDEEM: &str = "/v1/redeem";
    pub const LIBRARY_ORGS: &str = "/v1/library/orgs";
    pub const LIBRARY_MENTORS: &str = "/v1/library/mentors";
    pub const LIBRARY_QUOTA: &str = "/v1/library/quota";
    pub const LIBRARY_FETCH: &str = "/v1/library/fetch";
    pub const LIBRARY_CHANGES: &str = "/v1/library/changes";
    pub const BACKUP: &str = "/v1/backup";
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
}

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

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ClaimRequest {
    pub order_number: String,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Duration {
    Days(u32),
    Months(u32),
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
pub struct SponsorTier {
    pub duration: Duration,
    pub price: u32,
    pub url: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SponsorTiers {
    pub tiers: Vec<SponsorTier>,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Sponsor {
    pub sponsor_until: Option<i64>,
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
    pub sponsor_until: i64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Account {
    pub email: String,
    pub sponsor_until: Option<i64>,
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
    OrderNotFound,
    OrderClaimed,
    OrderNotSponsorship,
    SponsorUnavailable,
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
    SponsorRequired,
    BackupNotFound,
    BackupIncomplete,
    BackupChanged,
    BackupTooLarge,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApiError {
    pub code: ErrorCode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retry_after_seconds: Option<u64>,
}

/// This is part of the key derivation protocol; changing it would lock existing accounts out.
pub fn normalize_email(value: &str) -> String {
    value
        .trim()
        .chars()
        .map(|c| match c {
            'Ａ'..='Ｚ' | 'ａ'..='ｚ' | '０'..='９' | '＠' | '．' => {
                (c as u32 - 0xfee0) as u8 as char
            }
            _ => c,
        })
        .collect::<String>()
        .to_lowercase()
}

// Formats of a mentor's fields. The desktop checks every mentor with these and the server
// checks library records with them, so the server never hands out one the desktop refuses.

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
