//! Models: the provider table, the user's model configurations, and calling OpenAI-compatible
//! APIs (docs/design.md#桌面端).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

mod client;
mod error;
pub mod providers;

pub use client::{Access, list_models, test};
pub use error::ModelError;

/// A model configuration as the settings show it. Its API key never leaves the core.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelConfig {
    pub id: i64,
    /// None: known by the model's name.
    pub name: Option<String>,
    /// A provider's id, or `providers::CUSTOM`.
    pub provider: String,
    /// Only for `providers::CUSTOM`.
    pub base_url: Option<String>,
    pub model: String,
}

/// A model configuration as the user entered it, without its API key; `check` validates it.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelForm {
    /// May be blank.
    pub name: String,
    pub provider: String,
    /// Only read for `providers::CUSTOM`.
    pub base_url: String,
    pub model: String,
}

/// A checked `ModelForm`.
#[derive(Debug)]
pub struct ModelFields {
    pub name: Option<String>,
    pub source: Source,
    pub model: String,
}

/// Where a configuration calls.
#[derive(Debug)]
pub enum Source {
    Known(&'static providers::Provider),
    /// An OpenAI-compatible address the user filled in, without a trailing slash.
    Custom(String),
}

impl Source {
    /// From the provider and address as entered; the error says what to fix.
    pub fn check(provider: &str, base_url: &str) -> Result<Source, String> {
        if provider != providers::CUSTOM {
            return providers::find(provider)
                .map(Source::Known)
                .ok_or_else(|| "请选择服务商".into());
        }
        // Calls append `/chat/completions` and `/models`.
        let base_url = base_url.trim().trim_end_matches('/');
        let web = url::Url::parse(base_url)
            .is_ok_and(|u| matches!(u.scheme(), "http" | "https") && u.host().is_some());
        if !web {
            return Err("接口地址不正确，需以 http:// 或 https:// 开头".into());
        }
        Ok(Source::Custom(base_url.to_owned()))
    }

    fn id(&self) -> &'static str {
        match self {
            Source::Known(p) => p.id,
            Source::Custom(_) => providers::CUSTOM,
        }
    }

    /// The address to store: only a custom one.
    fn custom_url(&self) -> Option<&str> {
        match self {
            Source::Known(_) => None,
            Source::Custom(url) => Some(url),
        }
    }

    /// Where and how to call, with this API key.
    pub fn access(&self, api_key: String) -> Access {
        match self {
            Source::Known(p) => Access {
                base_url: p.base_url.into(),
                api_key,
                no_thinking: p.no_thinking,
            },
            Source::Custom(url) => Access {
                base_url: url.clone(),
                api_key,
                no_thinking: None,
            },
        }
    }
}

impl ModelForm {
    /// Trims the text and checks every field; the error says what to fix.
    pub fn check(self) -> Result<ModelFields, String> {
        let source = Source::check(&self.provider, &self.base_url)?;
        let model = self.model.trim();
        if model.is_empty() {
            return Err("请填写模型名".into());
        }
        Ok(ModelFields {
            name: Some(self.name.trim())
                .filter(|n| !n.is_empty())
                .map(str::to_owned),
            source,
            model: model.to_owned(),
        })
    }
}

/// An API key as the user typed it, trimmed; None when left blank.
pub fn checked_key(api_key: &str) -> Option<String> {
    Some(api_key.trim())
        .filter(|k| !k.is_empty())
        .map(str::to_owned)
}

/// Every model configuration, oldest first.
pub async fn list(db: &SqlitePool) -> Result<Vec<ModelConfig>, sqlx::Error> {
    sqlx::query_as!(
        ModelConfig,
        "SELECT id, name, provider, base_url, model FROM model_config ORDER BY id"
    )
    .fetch_all(db)
    .await
}

pub async fn create(
    db: &SqlitePool,
    fields: &ModelFields,
    api_key: &str,
    now: i64,
) -> Result<i64, sqlx::Error> {
    let (provider, base_url) = (fields.source.id(), fields.source.custom_url());
    let row = sqlx::query!(
        "INSERT INTO model_config (name, provider, base_url, model, api_key, created_at)
         VALUES (?, ?, ?, ?, ?, ?)",
        fields.name,
        provider,
        base_url,
        fields.model,
        api_key,
        now,
    )
    .execute(db)
    .await?;
    Ok(row.last_insert_rowid())
}

/// Saves the fields, and the API key unless it is None. False when there is no such configuration.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &ModelFields,
    api_key: Option<&str>,
) -> Result<bool, sqlx::Error> {
    let (provider, base_url) = (fields.source.id(), fields.source.custom_url());
    let row = sqlx::query!(
        "UPDATE model_config SET name = ?, provider = ?, base_url = ?, model = ?,
           api_key = coalesce(?, api_key)
         WHERE id = ?",
        fields.name,
        provider,
        base_url,
        fields.model,
        api_key,
        id,
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// Deletes a configuration; what used it keeps the model's name (docs/data-model.md#删除).
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM model_config WHERE id = ?", id)
        .execute(db)
        .await?;
    Ok(())
}

/// The saved API key of a configuration, for testing an edit that leaves it blank.
pub async fn api_key(db: &SqlitePool, id: i64) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar!("SELECT api_key FROM model_config WHERE id = ?", id)
        .fetch_optional(db)
        .await
}
