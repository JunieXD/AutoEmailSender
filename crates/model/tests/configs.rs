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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_earliest_model_is_the_default_until_another_is_chosen(db: SqlitePool) {
    let fields = form("deepseek", "").check().unwrap();
    let first = model::create(&db, &fields, "k", 0).await.unwrap();
    let second = model::create(&db, &fields, "k", 0).await.unwrap();
    let default = async || {
        let configs = model::list(&db).await.unwrap();
        let ids: Vec<_> = configs
            .iter()
            .filter(|c| c.is_default)
            .map(|c| c.id)
            .collect();
        ids
    };
    assert_eq!(default().await, [first]);

    assert!(profile::set_default_model(&db, second).await.unwrap());
    assert_eq!(default().await, [second]);
    model::delete(&db, second).await.unwrap();
    assert_eq!(default().await, [first]);
    assert!(!profile::set_default_model(&db, second).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn what_the_user_agreed_to_holds_only_for_the_same_provider(db: SqlitePool) {
    let deepseek = form("deepseek", "").check().unwrap();
    let id = model::create(&db, &deepseek, "k", 0).await.unwrap();
    let agreed = async || model::chosen(&db).await.unwrap().unwrap().agreed;
    assert!(!agreed().await);

    // Not to a provider the user was not told about.
    assert!(
        !model::agree(&db, id, "custom", Some("https://api.example.com/v1"))
            .await
            .unwrap()
    );
    assert!(!agreed().await);
    assert!(model::agree(&db, id, "deepseek", None).await.unwrap());
    assert!(agreed().await);
    // Another model of the same provider was agreed to as well.
    let mut renamed = form("deepseek", "");
    renamed.model = "deepseek-reasoner".into();
    model::update(&db, id, &renamed.check().unwrap(), "k")
        .await
        .unwrap();
    assert!(agreed().await);
    let elsewhere = form("custom", "https://api.example.com/v1")
        .check()
        .unwrap();
    model::update(&db, id, &elsewhere, "k").await.unwrap();
    assert!(!agreed().await);
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
