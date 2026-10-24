//! Token usage: a row for each call to a model (docs/data-model.md#个人资料和配置).

use sqlx::SqlitePool;

/// The tokens one call took.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Usage {
    /// With the cached ones.
    pub input: i64,
    pub cached: i64,
    pub output: i64,
}

/// What a call was for.
#[derive(Debug, Clone, Copy, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
pub enum Purpose {
    Draft,
    Rewrite,
}

pub async fn record(
    db: &SqlitePool,
    config_id: i64,
    model: &str,
    purpose: Purpose,
    usage: Usage,
    now: i64,
) -> Result<(), sqlx::Error> {
    // The configuration may have been deleted while the model was answering.
    sqlx::query!(
        "INSERT INTO usage (at, model_config_id, model, purpose, input_tokens, cached_tokens,
           output_tokens)
         VALUES (?, (SELECT id FROM model_config WHERE id = ?), ?, ?, ?, ?, ?)",
        now,
        config_id,
        model,
        purpose,
        usage.input,
        usage.cached,
        usage.output,
    )
    .execute(db)
    .await?;
    Ok(())
}
