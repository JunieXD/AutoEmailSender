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
    pub onboarded: bool,
}

/// The fields to change, as typed; the others stay, and blank ones are cleared.
#[derive(Debug, Default, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ProfileForm {
    #[ts(optional)]
    pub name: Option<String>,
    #[ts(optional)]
    pub research_interests: Option<String>,
    #[ts(optional)]
    pub rewrite_preference: Option<String>,
}

fn filled(text: &str) -> Option<&str> {
    Some(text.trim()).filter(|t| !t.is_empty())
}

pub async fn get(db: &SqlitePool) -> Result<Profile, sqlx::Error> {
    sqlx::query_as!(
        Profile,
        r#"SELECT name, research_interests, rewrite_preference, onboarded AS "onboarded: bool"
           FROM profile"#
    )
    .fetch_one(db)
    .await
}

pub async fn finish_onboarding(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET onboarded = 1")
        .execute(db)
        .await?;
    Ok(())
}

pub async fn update(db: &SqlitePool, form: &ProfileForm, now: i64) -> Result<Profile, sqlx::Error> {
    sqlx::query_as!(
        Profile,
        r#"UPDATE profile
        SET name = iif(?1, ?2, name), research_interests = iif(?3, ?4, research_interests),
            rewrite_preference = iif(?5, ?6, rewrite_preference),
            match_basis_changed_at = CASE WHEN ?3 AND research_interests IS NOT ?4 THEN max(
            coalesce(match_basis_changed_at, ?7), ?7) ELSE match_basis_changed_at END
        RETURNING name, research_interests, rewrite_preference, onboarded AS "onboarded: bool""#,
        form.name.is_some(),
        form.name.as_deref().and_then(filled),
        form.research_interests.is_some(),
        form.research_interests.as_deref().and_then(filled),
        form.rewrite_preference.is_some(),
        form.rewrite_preference.as_deref().and_then(filled),
        now,
    )
    .fetch_one(db)
    .await
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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, sqlx::Type)]
#[serde(rename_all = "camelCase")]
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

pub async fn set_rotation(db: &SqlitePool, rotation: Rotation) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET rotation = ?", rotation)
        .execute(db)
        .await?;
    Ok(())
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

pub async fn background_notice_shown(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT background_notice_shown AS "shown: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

/// Whether AI-generated drafts receive a separate check against the student's materials.
pub async fn ai_fact_check(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT ai_fact_check AS "on: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

pub async fn mark_background_notice_shown(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET background_notice_shown = 1")
        .execute(db)
        .await?;
    Ok(())
}

pub async fn set_ai_fact_check(db: &SqlitePool, on: bool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET ai_fact_check = ?", on)
        .execute(db)
        .await?;
    Ok(())
}

pub async fn todo_reminders(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT todo_reminders AS "on: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

pub async fn set_todo_reminders(db: &SqlitePool, on: bool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET todo_reminders = ?", on)
        .execute(db)
        .await?;
    Ok(())
}

/// Local Agent access is enabled only by the desktop user.
pub async fn agent_access(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT agent_access AS "enabled!: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

pub async fn set_agent_access(db: &SqlitePool, enabled: bool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET agent_access = ?", enabled)
        .execute(db)
        .await?;
    Ok(())
}
