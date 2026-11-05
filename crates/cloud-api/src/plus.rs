//! Plus, bought through the payment service, and the tracking packs bought alongside it.

use serde::{Deserialize, Serialize};

use crate::tracking::Quota;

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Duration {
    Days(u32),
    Months(u32),
}

/// A length of Plus on sale.
#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Tier {
    /// What an order names to buy it.
    pub item: String,
    pub duration: Duration,
    /// In fen.
    pub price: u32,
    /// When Plus would end if bought now.
    pub until: i64,
    /// False when it would take Plus past `Items::latest`.
    pub available: bool,
}

/// Emails of tracking quota on sale, for while Plus is in effect.
#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Pack {
    pub item: String,
    pub count: u32,
    /// In fen.
    pub price: u32,
    /// When a pack bought now would expire.
    pub expires_at: i64,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Items {
    pub tiers: Vec<Tier>,
    pub packs: Vec<Pack>,
    /// The latest Plus may be bought up to: a year from now.
    pub latest: i64,
    /// The account has had a refund without a reason, so it cannot buy.
    pub refunded: bool,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Method {
    Alipay,
    Wxpay,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderRequest {
    pub item: String,
    pub method: Method,
    /// The version of the Plus terms the user agreed to.
    pub terms: String,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Created {
    pub order: i64,
    /// The payment page, opened in the browser.
    pub pay_url: String,
}

#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(
    tag = "state",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum OrderState {
    Pending,
    /// With the account's Plus and tracking quota now.
    Applied {
        plus_until: Option<i64>,
        tracking: Quota,
    },
    /// Refunded or taken back after it was applied.
    Refunded,
}

/// The account's Plus.
#[cfg_attr(feature = "ts-rs", derive(ts_rs::TS))]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Plus {
    pub plus_until: Option<i64>,
}
