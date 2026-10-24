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

pub async fn update(db: &SqlitePool, form: &ProfileForm, now: i64) -> Result<(), sqlx::Error> {
    let (name, research_interests, rewrite_preference) = (
        filled(&form.name),
        filled(&form.research_interests),
        filled(&form.rewrite_preference),
    );
    sqlx::query!(
        "UPDATE profile SET name = ?1, research_interests = ?2, rewrite_preference = ?3,
           match_basis_changed_at = CASE WHEN research_interests IS NOT ?2 THEN max(coalesce(match_basis_changed_at, ?4), ?4)
                                        ELSE match_basis_changed_at END",
        name,
        research_interests,
        rewrite_preference,
        now,
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

/// How emails are spread over the mailboxes (docs/design.md#多邮箱轮转).
#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
pub enum Rotation {
    /// One mailbox until it is paused or at its limit, then the next.
    Failover,
    /// The mailbox that has gone longest without sending.
    PerEmail,
}

pub async fn rotation(db: &SqlitePool) -> Result<Rotation, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT rotation AS "rotation: Rotation" FROM profile"#)
        .fetch_one(db)
        .await
}

/// Makes the model configuration the default; false when there is no such configuration.
pub async fn set_default_model(db: &SqlitePool, id: i64) -> Result<bool, sqlx::Error> {
    let row = sqlx::query!(
        "UPDATE profile SET default_model_config_id = ?1
         WHERE EXISTS (SELECT 1 FROM model_config WHERE id = ?1)",
        id
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// Makes the template the default, or none; false when there is no such template.
pub async fn set_default_template(db: &SqlitePool, id: Option<i64>) -> Result<bool, sqlx::Error> {
    let row = sqlx::query!(
        "UPDATE profile SET default_template_id = ?1
         WHERE ?1 IS NULL OR EXISTS (SELECT 1 FROM template WHERE id = ?1)",
        id
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

pub async fn reply_notifications(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT reply_notifications AS "reply_notifications: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

pub async fn set_reply_notifications(db: &SqlitePool, on: bool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET reply_notifications = ?", on)
        .execute(db)
        .await?;
    Ok(())
}

/// Background completion notifications are independent of new-reply notifications.
pub async fn background_notifications(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT background_notifications AS "on: bool" FROM profile"#)
        .fetch_one(db)
        .await
}
pub async fn set_background_notifications(db: &SqlitePool, on: bool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET background_notifications = ?", on)
        .execute(db)
        .await?;
    Ok(())
}
