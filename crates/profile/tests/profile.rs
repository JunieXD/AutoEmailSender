// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use profile::ProfileForm;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn blank_fields_are_cleared(db: SqlitePool) {
    let form = |name: &str| ProfileForm {
        name: name.into(),
        research_interests: "自然语言处理\n".into(),
        rewrite_preference: " ".into(),
    };
    profile::update(&db, &form(" 林晓 ")).await.unwrap();
    let saved = profile::get(&db).await.unwrap();
    assert_eq!(saved.name.as_deref(), Some("林晓"));
    assert_eq!(saved.research_interests.as_deref(), Some("自然语言处理"));
    assert_eq!(saved.rewrite_preference, None);
}
