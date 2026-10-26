//! Stored AI checks belong to the email, independently of its current edits.

use serde::{Deserialize, Serialize};
use sqlx::{SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS, schemars::JsonSchema)]
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

/// Only the same draft generation still under review accepts a late answer.
pub async fn save(
    db: &SqlitePool,
    id: i64,
    generation_claim: i64,
    result: &FactCheck,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!(
        r#"UPDATE email
        SET fact_check = ?, updated_at = ?
        WHERE id = ? AND generation_claim = ? AND state = 'review'"#,
        Json(result),
        now,
        id,
        generation_claim,
    )
    .execute(db)
    .await?
    .rows_affected()
        == 1)
}
