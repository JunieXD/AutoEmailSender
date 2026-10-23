//! Letter templates: a subject and a Markdown body with placeholders the app fills in for each
//! mentor (docs/design.md#邮件格式).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

/// What may stand in braces in a subject or body, such as `{导师姓名}`.
pub const PLACEHOLDERS: &[&str] = &[
    "导师姓名",
    "导师职称",
    "学校",
    "学院",
    "系所",
    "导师研究方向",
    "我的姓名",
];

/// A template in the list, without its body.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TemplateSummary {
    pub id: i64,
    pub name: String,
    pub subject: String,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Template {
    pub id: i64,
    pub name: String,
    pub subject: String,
    pub body_md: String,
}

/// A template as the user entered it; `check` validates it.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TemplateForm {
    pub name: String,
    pub subject: String,
    pub body_md: String,
}

/// A checked `TemplateForm`.
#[derive(Debug)]
pub struct TemplateFields {
    pub name: String,
    pub subject: String,
    pub body_md: String,
}

impl TemplateForm {
    /// Trims the name and subject and checks every field is filled in; the error says what to fill
    /// in.
    pub fn check(self) -> Result<TemplateFields, String> {
        let required = |text: &str, missing: &str| match text.trim() {
            "" => Err(missing.to_owned()),
            text => Ok(text.to_owned()),
        };
        let name = required(&self.name, "请填写模板名称")?;
        let subject = required(&self.subject, "请填写主题")?;
        // The body is kept as the editor wrote it: spaces it starts with, such as a Chinese
        // indent, are text.
        if self.body_md.trim().is_empty() {
            return Err("请填写正文".into());
        }
        Ok(TemplateFields {
            name,
            subject,
            body_md: self.body_md,
        })
    }
}

/// Every template, oldest first.
pub async fn list(db: &SqlitePool) -> Result<Vec<TemplateSummary>, sqlx::Error> {
    sqlx::query_as!(
        TemplateSummary,
        "SELECT id, name, subject FROM template ORDER BY id"
    )
    .fetch_all(db)
    .await
}

pub async fn get(db: &SqlitePool, id: i64) -> Result<Option<Template>, sqlx::Error> {
    sqlx::query_as!(
        Template,
        "SELECT id, name, subject, body_md FROM template WHERE id = ?",
        id
    )
    .fetch_optional(db)
    .await
}

pub async fn create(
    db: &SqlitePool,
    fields: &TemplateFields,
    now: i64,
) -> Result<i64, sqlx::Error> {
    let row = sqlx::query!(
        "INSERT INTO template (name, subject, body_md, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        fields.name,
        fields.subject,
        fields.body_md,
        now,
        now,
    )
    .execute(db)
    .await?;
    Ok(row.last_insert_rowid())
}

/// False when there is no such template.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &TemplateFields,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let row = sqlx::query!(
        "UPDATE template SET name = ?, subject = ?, body_md = ?, updated_at = ? WHERE id = ?",
        fields.name,
        fields.subject,
        fields.body_md,
        now,
        id,
    )
    .execute(db)
    .await?;
    Ok(row.rows_affected() > 0)
}

/// Deletes a template; drafts made from it stay as they are (docs/data-model.md#删除).
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM template WHERE id = ?", id)
        .execute(db)
        .await?;
    Ok(())
}
