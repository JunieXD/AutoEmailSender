// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use model::{ModelForm, Source};
use sqlx::SqlitePool;

fn form(provider: &str, base_url: &str) -> ModelForm {
    ModelForm {
        name: String::new(),
        provider: provider.into(),
        base_url: base_url.into(),
        model: " deepseek-chat ".into(),
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_saved_key_is_only_for_where_it_was_saved(db: SqlitePool) {
    let fields = form("deepseek", "").check().unwrap();
    let id = model::create(&db, &fields, "sk-deepseek", 0).await.unwrap();
    let key = async |source| model::api_key(&db, id, &source).await.unwrap();

    assert_eq!(
        key(Source::check("deepseek", "").unwrap()).await.as_deref(),
        Some("sk-deepseek")
    );
    assert_eq!(key(Source::check("openai", "").unwrap()).await, None);
    let elsewhere = Source::check("custom", "https://api.example.com/v1").unwrap();
    assert_eq!(key(elsewhere).await, None);
}

#[test]
fn a_custom_address_must_be_a_web_address() {
    let source = Source::check("custom", " https://api.example.com/v1/ ").unwrap();
    assert_eq!(
        source.access("k".into()).base_url,
        "https://api.example.com/v1"
    );
    assert!(Source::check("custom", "api.example.com/v1").is_err());
}
