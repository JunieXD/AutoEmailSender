use serde::{Deserialize, Serialize};
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum PageKind {
    Entry,
    List,
    Profile,
    EnrichProfile,
    EnrichChild,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
pub enum PageMethod {
    Http,
    Webview,
}

/// Raw replayable control labels and page numbers; their serialized values identify the page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageStep {
    pub page: u32,
    pub control: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum PageStatus {
    Success,
    Failed,
    NeedsBrowser,
}
