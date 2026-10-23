//! Emails the app writes and sends (docs/data-model.md#联系).

use serde::Serialize;
use sqlx::SqlitePool;
use ts_rs::TS;

/// A first email being written on the mentor's page, outside any task.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FirstDraft {
    pub subject: Option<String>,
    pub body_md: Option<String>,
}

pub async fn first_draft(
    db: &SqlitePool,
    mentor_id: i64,
) -> Result<Option<FirstDraft>, sqlx::Error> {
    sqlx::query_as!(
        FirstDraft,
        "SELECT subject, body_md FROM email
         WHERE mentor_id = ? AND kind = 'first' AND task_id IS NULL AND state = 'review'",
        mentor_id
    )
    .fetch_optional(db)
    .await
}

/// Saves the mentor's first draft; with both parts blank there is none.
pub async fn save_first_draft(
    db: &SqlitePool,
    mentor_id: i64,
    subject: &str,
    body_md: &str,
    now: i64,
) -> Result<(), sqlx::Error> {
    let subject = Some(subject.trim()).filter(|s| !s.is_empty());
    let body_md = Some(body_md).filter(|b| !b.trim().is_empty());
    if subject.is_none() && body_md.is_none() {
        sqlx::query!(
            "DELETE FROM email
             WHERE mentor_id = ? AND kind = 'first' AND task_id IS NULL AND state = 'review'",
            mentor_id
        )
        .execute(db)
        .await?;
    } else {
        sqlx::query!(
            "INSERT INTO email (mentor_id, kind, state, created_at, updated_at, subject, body_md)
             VALUES (?1, 'first', 'review', ?2, ?2, ?3, ?4)
             ON CONFLICT (mentor_id) WHERE kind = 'first' AND task_id IS NULL AND state = 'review'
             DO UPDATE SET subject = ?3, body_md = ?4, updated_at = ?2",
            mentor_id,
            now,
            subject,
            body_md,
        )
        .execute(db)
        .await?;
    }
    Ok(())
}
