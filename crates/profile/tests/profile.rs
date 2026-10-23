// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use profile::{LetterFont, LetterStyle, ProfileForm};
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_letter_style_is_saved(db: SqlitePool) {
    let style = LetterStyle {
        font: Some(LetterFont::Song),
        font_size_pt: 12.0,
        line_height: 1.8,
        paragraph_spacing_em: 0.0,
        first_line_indent: true,
    };
    profile::update_letter_style(&db, &style).await.unwrap();
    let saved = profile::letter_style(&db).await.unwrap();
    assert_eq!(saved.font, Some(LetterFont::Song));
    assert_eq!(saved.font_size_pt, 12.0);
    assert!(saved.first_line_indent);
}
