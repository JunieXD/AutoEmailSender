use serde::{Deserialize, Serialize};
use sqlx::{SqlitePool, types::Json};

use crate::PageUrl;

#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
pub enum PageKind {
    Entry,
    List,
    Profile,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PageResult {
    Success {
        text: String,
    },
    Failed {
        reason: String,
    },
    NeedsBrowser {
        reason: String,
        text: Option<String>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
pub enum PageStatus {
    Success,
    Failed,
    NeedsBrowser,
}

#[derive(Debug, Clone)]
pub struct PageRecord {
    pub url: PageUrl,
    pub kind: PageKind,
    pub steps: Vec<PageStep>,
    pub method: PageMethod,
    pub fetched_at: i64,
    pub result: PageResult,
}

/// The small part needed when deciding whether to fetch a page again.
#[derive(Debug, sqlx::FromRow)]
pub struct PageVisit {
    pub id: i64,
    pub kind: PageKind,
    pub method: PageMethod,
    pub status: PageStatus,
    pub error: Option<String>,
    pub fetched_at: i64,
}

/// Atomic upsert: retrying a page replaces its result without changing its ID or duplicating it.
pub async fn save_page(
    pool: &SqlitePool,
    crawl_id: i64,
    page: &PageRecord,
) -> Result<i64, sqlx::Error> {
    let (status, text, error) = match &page.result {
        PageResult::Success { text } => (PageStatus::Success, Some(text.as_str()), None),
        PageResult::Failed { reason } => (PageStatus::Failed, None, Some(reason.as_str())),
        PageResult::NeedsBrowser { reason, text } => (
            PageStatus::NeedsBrowser,
            text.as_deref(),
            Some(reason.as_str()),
        ),
    };
    let url = page.url.as_str();
    let steps = Json(&page.steps);
    sqlx::query_scalar!(
        r#"INSERT INTO crawl_page (crawl_id, url, kind, steps, mode, status, error, fetched_at, text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (crawl_id, url, steps) DO UPDATE SET
             kind = excluded.kind, mode = excluded.mode, status = excluded.status,
             error = excluded.error, fetched_at = excluded.fetched_at, text = excluded.text
           RETURNING id"#,
        crawl_id, url, page.kind, steps, page.method, status, error, page.fetched_at, text
    ).fetch_one(pool).await
}

/// Uses the unique index and never reads the extracted text or loads a full visited-URL set.
pub async fn visited_page(
    pool: &SqlitePool,
    crawl_id: i64,
    url: &PageUrl,
    steps: &[PageStep],
) -> Result<Option<PageVisit>, sqlx::Error> {
    let url = url.as_str();
    let steps = Json(steps);
    sqlx::query_as!(
        PageVisit,
        r#"SELECT id, kind AS "kind: PageKind", mode AS "method: PageMethod",
             status AS "status: PageStatus", error, fetched_at
           FROM crawl_page WHERE crawl_id = ? AND url = ? AND steps = ?"#,
        crawl_id,
        url,
        steps
    )
    .fetch_optional(pool)
    .await
}

/// Large text is fetched separately, one page at a time.
pub async fn page_text(pool: &SqlitePool, page_id: i64) -> Result<Option<String>, sqlx::Error> {
    Ok(
        sqlx::query_scalar!("SELECT text FROM crawl_page WHERE id = ?", page_id)
            .fetch_optional(pool)
            .await?
            .flatten(),
    )
}
