//! The user's own profile: who they are and how they want their letters (docs/data-model.md).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

/// What the user says about themselves, which writing and matching draw on.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub name: Option<String>,
    pub research_interests: Option<String>,
    pub rewrite_preference: Option<String>,
}

/// The profile as typed; blank fields are cleared.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ProfileForm {
    pub name: String,
    pub research_interests: String,
    pub rewrite_preference: String,
}

fn filled(text: &str) -> Option<&str> {
    Some(text.trim()).filter(|t| !t.is_empty())
}

pub async fn get(db: &SqlitePool) -> Result<Profile, sqlx::Error> {
    sqlx::query_as!(
        Profile,
        "SELECT name, research_interests, rewrite_preference FROM profile"
    )
    .fetch_one(db)
    .await
}

pub async fn update(db: &SqlitePool, form: &ProfileForm) -> Result<(), sqlx::Error> {
    let (name, research_interests, rewrite_preference) = (
        filled(&form.name),
        filled(&form.research_interests),
        filled(&form.rewrite_preference),
    );
    sqlx::query!(
        "UPDATE profile SET name = ?, research_interests = ?, rewrite_preference = ?",
        name,
        research_interests,
        rewrite_preference,
    )
    .execute(db)
    .await?;
    Ok(())
}

/// How every letter looks (docs/design.md#邮件格式); the interface applies it when rendering one.
#[derive(Debug, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct LetterStyle {
    /// None leaves the font to the recipient's mail app.
    pub font: Option<LetterFont>,
    pub font_size_pt: f64,
    pub line_height: f64,
    pub paragraph_spacing_em: f64,
    /// Two characters at the start of each paragraph.
    pub first_line_indent: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum LetterFont {
    Song,
    Hei,
}

impl LetterStyle {
    /// Whether every size is one a letter can sensibly have; the error says which is not.
    pub fn check(&self) -> Result<(), String> {
        if !(9.0..=16.0).contains(&self.font_size_pt) {
            return Err("字号要在 9 到 16 磅之间".into());
        }
        if !(1.0..=2.5).contains(&self.line_height) {
            return Err("行距要在 1 到 2.5 倍之间".into());
        }
        if !(0.0..=2.0).contains(&self.paragraph_spacing_em) {
            return Err("段距要在 0 到 2 个字之间".into());
        }
        Ok(())
    }
}

pub async fn letter_style(db: &SqlitePool) -> Result<LetterStyle, sqlx::Error> {
    sqlx::query_as!(
        LetterStyle,
        r#"SELECT font AS "font: LetterFont", font_size_pt, line_height, paragraph_spacing_em,
             first_line_indent AS "first_line_indent: bool"
           FROM profile"#
    )
    .fetch_one(db)
    .await
}

pub async fn update_letter_style(db: &SqlitePool, style: &LetterStyle) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE profile SET font = ?, font_size_pt = ?, line_height = ?, paragraph_spacing_em = ?,
           first_line_indent = ?",
        style.font,
        style.font_size_pt,
        style.line_height,
        style.paragraph_spacing_em,
        style.first_line_indent,
    )
    .execute(db)
    .await?;
    Ok(())
}
