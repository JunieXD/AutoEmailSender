//! Models: the provider table, the user's model configurations, and calling OpenAI-compatible
//! APIs (docs/design.md#桌面端).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

mod client;
mod error;
pub mod providers;
pub mod usage;

pub use client::{Access, Answer, ask, list_models, test};
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
    /// The one AI features use: the user's choice, or else the earliest added.
    pub is_default: bool,
    /// The user agreed to what AI features send to its provider.
    pub agreed: bool,
    pub input_price: Option<f64>,
    pub output_price: Option<f64>,
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
    #[serde(default)]
    pub input_price: Option<f64>,
    #[serde(default)]
    pub output_price: Option<f64>,
}

/// A checked `ModelForm`.
#[derive(Debug)]
pub struct ModelFields {
    pub name: Option<String>,
    pub source: Source,
    pub model: String,
    pub input_price: Option<f64>,
    pub output_price: Option<f64>,
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
        if [self.input_price, self.output_price]
            .into_iter()
            .flatten()
            .any(|p| !p.is_finite() || p < 0.0)
        {
            return Err("单价不能是负数，请按每百万 Token 的人民币价格填写。".into());
        }
        Ok(ModelFields {
            name: Some(self.name.trim())
                .filter(|n| !n.is_empty())
                .map(str::to_owned),
            source,
            model: model.to_owned(),
            input_price: self.input_price,
            output_price: self.output_price,
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
        r#"SELECT id, name, provider, base_url, model, input_price, output_price,
             id = coalesce((SELECT default_model_config_id FROM profile),
                           (SELECT min(id) FROM model_config)) AS "is_default!: bool",
             agreed AS "agreed: bool"
           FROM model_config ORDER BY id"#
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
        "INSERT INTO model_config (name, provider, base_url, model, api_key, agreed, created_at, input_price, output_price)
         VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
        fields.name,
        provider,
        base_url,
        fields.model,
        api_key,
        now,
        fields.input_price,
        fields.output_price,
    )
    .execute(db)
    .await?;
    Ok(row.last_insert_rowid())
}

/// False when there is no such configuration. What the user agreed to stays agreed only while the
/// provider and address stay the same.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &ModelFields,
    api_key: &str,
) -> Result<bool, sqlx::Error> {
    let (provider, base_url) = (fields.source.id(), fields.source.custom_url());
    let row = sqlx::query!(
        "UPDATE model_config SET agreed = agreed AND provider = ? AND base_url IS ?,
           name = ?, provider = ?, base_url = ?, model = ?, api_key = ?, input_price = ?, output_price = ?
         WHERE id = ?",
        provider,
        base_url,
        fields.name,
        provider,
        base_url,
        fields.model,
        api_key,
        fields.input_price,
        fields.output_price,
        id,
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// Records that the user agreed to what AI features send to this provider and address, as they
/// were told; false when the configuration is gone or now calls somewhere else.
pub async fn agree(
    db: &SqlitePool,
    id: i64,
    provider: &str,
    base_url: Option<&str>,
) -> Result<bool, sqlx::Error> {
    let row = sqlx::query!(
        "UPDATE model_config SET agreed = 1 WHERE id = ? AND provider = ? AND base_url IS ?",
        id,
        provider,
        base_url,
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// The configuration AI features call, with its API key.
#[derive(Debug)]
pub struct Chosen {
    pub id: i64,
    pub model: String,
    pub agreed: bool,
    pub access: Access,
}

/// The default configuration, as `list` marks it; None when there is none.
pub async fn chosen(db: &SqlitePool) -> Result<Option<Chosen>, sqlx::Error> {
    let Some(row) = sqlx::query!(
        r#"SELECT id, provider, base_url, model, api_key, agreed AS "agreed: bool"
           FROM model_config
           WHERE id = coalesce((SELECT default_model_config_id FROM profile),
                               (SELECT min(id) FROM model_config))"#
    )
    .fetch_optional(db)
    .await?
    else {
        return Ok(None);
    };
    // Only a provider this version no longer knows fails.
    let source = Source::check(&row.provider, row.base_url.as_deref().unwrap_or_default())
        .map_err(|e| sqlx::Error::Decode(e.into()))?;
    Ok(Some(Chosen {
        id: row.id,
        model: row.model,
        agreed: row.agreed,
        access: source.access(row.api_key),
    }))
}

/// Deletes a configuration; what used it keeps the model's name (docs/data-model.md#删除).
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM model_config WHERE id = ?", id)
        .execute(db)
        .await?;
    Ok(())
}

/// The saved API key of a configuration, for an edit that leaves it blank; None when the
/// configuration was saved for another provider or address, which must not get this key.
pub async fn api_key(
    db: &SqlitePool,
    id: i64,
    source: &Source,
) -> Result<Option<String>, sqlx::Error> {
    let (provider, base_url) = (source.id(), source.custom_url());
    sqlx::query_scalar!(
        "SELECT api_key FROM model_config WHERE id = ? AND provider = ? AND base_url IS ?",
        id,
        provider,
        base_url,
    )
    .fetch_optional(db)
    .await
}
