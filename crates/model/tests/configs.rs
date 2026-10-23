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
async fn a_blank_key_keeps_the_saved_one(db: SqlitePool) {
    let fields = form("deepseek", "").check().unwrap();
    let id = model::create(&db, &fields, "first", 0).await.unwrap();
    let key = async || model::api_key(&db, id).await.unwrap().unwrap();

    assert!(model::update(&db, id, &fields, None).await.unwrap());
    assert_eq!(key().await, "first");
    assert!(
        model::update(&db, id, &fields, Some("second"))
            .await
            .unwrap()
    );
    assert_eq!(key().await, "second");
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
