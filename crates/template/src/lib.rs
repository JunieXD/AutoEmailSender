//! Letter templates: a subject and a Markdown body with placeholders the app fills in for each
//! mentor (docs/design.md#邮件格式).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

/// What may stand in braces in a subject or body, such as `{导师姓名}`.
pub const PLACEHOLDERS: [&str; 7] = [
    "导师姓名",
    "导师职称",
    "学校",
    "学院",
    "系所",
    "导师研究方向",
    "我的姓名",
];

/// What fills the placeholders for one mentor; a missing value leaves its placeholder as written.
#[derive(Debug)]
pub struct Fill<'a> {
    pub mentor_name: &'a str,
    pub mentor_title: Option<&'a str>,
    pub university: Option<&'a str>,
    pub school: Option<&'a str>,
    pub department: Option<&'a str>,
    pub research: Option<&'a str>,
    pub my_name: Option<&'a str>,
}

impl Fill<'_> {
    /// Each placeholder's value, in the order of `PLACEHOLDERS`.
    fn values(&self) -> [Option<&str>; PLACEHOLDERS.len()] {
        [
            Some(self.mentor_name),
            self.mentor_title,
            self.university,
            self.school,
            self.department,
            self.research,
            self.my_name,
        ]
    }

    pub fn subject(&self, subject: &str) -> String {
        self.fill(subject, str::to_owned)
    }

    /// A Markdown body with the values as plain text: links or formatting written on a mentor's
    /// page never take effect.
    pub fn body(&self, markdown: &str) -> String {
        self.fill(markdown, markdown_text)
    }

    /// Fills in every placeholder in one pass, so a value is never searched for placeholders.
    fn fill(&self, text: &str, write: impl Fn(&str) -> String) -> String {
        let values = self.values();
        let mut out = String::with_capacity(text.len());
        let mut rest = text;
        while let Some(open) = rest.find('{') {
            out.push_str(&rest[..open]);
            let after = &rest[open + 1..];
            let filled = after.find('}').and_then(|close| {
                let i = PLACEHOLDERS.iter().position(|p| *p == &after[..close])?;
                Some((values[i]?, close))
            });
            match filled {
                Some((value, close)) => {
                    out.push_str(&write(&one_line(value)));
                    rest = &after[close + 1..];
                }
                None => {
                    out.push('{');
                    rest = after;
                }
            }
        }
        out.push_str(rest);
        out
    }
}

fn one_line(text: &str) -> String {
    text.split(['\r', '\n'])
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Text as Markdown that reads back as the same text, wherever it goes on a line.
pub fn markdown_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for (i, c) in text.char_indices() {
        // A list marker would start a list when the text starts a line.
        let marker = (i == 0 && matches!(c, '-' | '+'))
            || (i > 0 && matches!(c, '.' | ')') && text[..i].bytes().all(|b| b.is_ascii_digit()));
        if marker || r"\*_[]<>&`~!".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

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
