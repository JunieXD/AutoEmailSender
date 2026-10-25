//! Stored AI checks belong to the email, independently of its current edits.

use serde::{Deserialize, Serialize};
use sqlx::{SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS)]
pub struct Finding {
    pub quote: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum FactCheck {
    Completed { findings: Vec<Finding> },
    Failed { reason: String },
}

/// Approval, cancellation and deletion fence off an answer that arrives late.
pub async fn save(
    db: &SqlitePool,
    id: i64,
    result: &FactCheck,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!(
        "UPDATE email SET fact_check = ?, updated_at = ? WHERE id = ? AND state = 'review'",
        Json(result),
        now,
        id,
    )
    .execute(db)
    .await?
    .rows_affected()
        == 1)
}
