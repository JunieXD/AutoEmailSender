//! The trash: mentors set aside, which can be restored or deleted for good (docs/data-model.md#删除).

use std::num::NonZeroU32;

use serde::Deserialize;
use sqlx::SqlitePool;
use ts_rs::TS;

use crate::{MentorPage, PAGE_SIZE, json_ids, offset, rows};

/// Why the emails of a trashed mentor were canceled, as the user sees it.
pub const CANCELED: &str = "导师已移入回收站";

#[derive(Debug, Deserialize, TS)]
pub struct TrashQuery {
    /// Starts at 1.
    pub page: NonZeroU32,
}

/// One page of the trash, most recently trashed first.
pub async fn list(db: &SqlitePool, query: &TrashQuery) -> Result<MentorPage, sqlx::Error> {
    let ids = sqlx::query_scalar!(
        "SELECT id FROM mentor WHERE trashed_at IS NOT NULL
         ORDER BY trashed_at DESC, id LIMIT ? OFFSET ?",
        PAGE_SIZE,
        offset(query.page),
    )
    .fetch_all(db)
    .await?;
    let total = sqlx::query_scalar!(
        r#"SELECT count(*) AS "n!: i64" FROM mentor WHERE trashed_at IS NOT NULL"#
    )
    .fetch_one(db)
    .await?;
    Ok(MentorPage {
        mentors: rows(db, &ids).await?,
        total,
    })
}

/// Moves mentors to the trash and cancels their emails that have not gone out; restoring them does
/// not bring those back. Emails already handed to SMTP are left to finish.
pub async fn trash(db: &SqlitePool, ids: &[i64], now: i64) -> Result<(), sqlx::Error> {
    let ids = json_ids(ids.iter().copied());
    let mut tx = db.begin().await?;
    sqlx::query!(
        "UPDATE mentor SET trashed_at = ?1, updated_at = ?1
         WHERE id IN (SELECT value FROM json_each(?2)) AND trashed_at IS NULL",
        now,
        ids,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE email SET state = 'canceled', error = ?1, updated_at = ?2
         WHERE mentor_id IN (SELECT value FROM json_each(?3))
           AND state IN ('generating', 'generation_failed', 'review', 'approved', 'failed')",
        CANCELED,
        now,
        ids,
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await
}

/// Takes mentors out of the trash.
pub async fn restore(db: &SqlitePool, ids: &[i64], now: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE mentor SET trashed_at = NULL, updated_at = ?
         WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL",
        now,
        json_ids(ids.iter().copied()),
    )
    .execute(db)
    .await?;
    Ok(())
}

/// Deletes mentors in the trash for good. Their emails, todos and tags go with them through the
/// foreign keys; mail seen in the mailbox goes too unless it also concerns a mentor who stays. The
/// copies in the mailbox itself are untouched.
pub async fn delete(db: &SqlitePool, ids: &[i64]) -> Result<(), sqlx::Error> {
    let ids = json_ids(ids.iter().copied());
    let mut tx = db.begin().await?;
    sqlx::query!(
        "WITH doomed AS (SELECT id FROM mentor
                         WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL)
         DELETE FROM message
         WHERE id IN (SELECT message_id FROM message_mentor WHERE mentor_id IN doomed)
           AND NOT EXISTS (SELECT 1 FROM message_mentor mm
                           WHERE mm.message_id = message.id AND mm.mentor_id NOT IN doomed)",
        ids,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "DELETE FROM mentor_search WHERE rowid IN
           (SELECT id FROM mentor WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL)",
        ids,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "DELETE FROM mentor WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL",
        ids,
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await
}
