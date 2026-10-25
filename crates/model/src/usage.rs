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
#[derive(Debug, Clone, Copy, serde::Serialize, sqlx::Type, ts_rs::TS)]
#[sqlx(rename_all = "snake_case")]
#[serde(rename_all = "snake_case")]
#[ts(rename = "UsagePurpose")]
pub enum Purpose {
    Crawl,
    Match,
    Draft,
    Rewrite,
    FactCheck,
    ReplyOutcome,
    ReplyTodos,
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

/// Usage is retained even when a claim is invalidated after the provider has answered.
pub async fn record_crawl(
    db: &SqlitePool,
    config_id: i64,
    model: &str,
    crawl_id: i64,
    usage: Usage,
    now: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!("INSERT INTO usage (at, model_config_id, model, purpose, crawl_id, input_tokens, cached_tokens, output_tokens) VALUES (?, (SELECT id FROM model_config WHERE id = ?), ?, 'crawl', (SELECT id FROM crawl WHERE id = ?), ?, ?, ?)", now, config_id, model, crawl_id, usage.input, usage.cached, usage.output).execute(db).await?;
    Ok(())
}
