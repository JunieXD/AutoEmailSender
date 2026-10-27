//! Desktop update state returned by the application commands.

use serde::Serialize;
use ts_rs::TS;

#[derive(Clone, Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    /// Unix milliseconds, or null when the release has no publication date.
    pub published_at: Option<i64>,
    pub notes: Option<String>,
}

#[derive(Clone, Debug, Serialize, TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum UpdateStatus {
    Current,
    Available {
        update: UpdateInfo,
        error: Option<String>,
    },
    Downloading {
        update: UpdateInfo,
        downloaded: u64,
        total: Option<u64>,
    },
    Ready {
        update: UpdateInfo,
    },
}
