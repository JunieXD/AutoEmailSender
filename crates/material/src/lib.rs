//! Materials: the user's files, such as a résumé, kept with the text taken out of them
//! (docs/data-model.md#实体).

use std::io::{self, Cursor};
use std::path::{Path, PathBuf};

use office_oxide::format::DocumentFormat;
use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use tokio::io::AsyncWriteExt;
use ts_rs::TS;

/// The most Unicode characters AI reads from each material.
pub const AI_TEXT_LIMIT: usize = 10_000;

/// Unicode characters, without cutting a character in the middle.
pub fn for_ai(text: &str) -> &str {
    &text[..text
        .char_indices()
        .nth(AI_TEXT_LIMIT)
        .map_or(text.len(), |(i, _)| i)]
}

/// The largest file a material may be, in bytes.
pub const MAX_SIZE: usize = 20 * 1024 * 1024;

/// A material in the list, without its text.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Material {
    pub id: i64,
    pub file_name: String,
    pub size: i64,
    /// False for a scan, which can only be attached.
    pub has_text: bool,
    pub exceeds_ai_limit: bool,
    pub for_matching: bool,
    pub for_writing: bool,
    pub attach_by_default: bool,
}

#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum Purpose {
    Matching,
    Writing,
    AttachByDefault,
}

#[derive(Debug)]
pub enum Error {
    /// Not a PDF, DOCX or DOC file by its name.
    Type,
    TooLarge,
    /// Not readable as what its name says, such as when damaged or locked by a password; the
    /// detail is for the log.
    Unreadable(String),
    /// Attached to an email that may still go out.
    InUse,
    /// A task still needs the reference for queued or running generation.
    TaskInUse {
        name: String,
    },
    Db(sqlx::Error),
    File(io::Error),
}

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Error::Db(e)
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::File(e)
    }
}

/// Where a material's file is kept in `dir`.
pub fn file(dir: &Path, id: i64) -> PathBuf {
    dir.join(id.to_string())
}

/// Every material, oldest first.
pub async fn list(db: &SqlitePool) -> Result<Vec<Material>, sqlx::Error> {
    sqlx::query_as!(
        Material,
        r#"SELECT id, file_name, size, extracted_text IS NOT NULL AS "has_text: bool",
             coalesce(length(extracted_text) > ?, 0) AS "exceeds_ai_limit!: bool",
             for_matching AS "for_matching: bool", for_writing AS "for_writing: bool",
             attach_by_default AS "attach_by_default: bool"
           FROM material ORDER BY id"#,
        AI_TEXT_LIMIT as i64
    )
    .fetch_all(db)
    .await
}

/// A material's text, as writing draws on it.
#[derive(Debug)]
pub struct Reference {
    pub file_name: String,
    pub text: String,
}

/// The materials the user writes letters from, oldest first.
pub async fn for_writing(db: &SqlitePool) -> Result<Vec<Reference>, sqlx::Error> {
    // The table lets only a material with text be used for writing.
    sqlx::query_as!(
        Reference,
        r#"SELECT file_name, substr(extracted_text, 1, ?) AS "text!: String"
        FROM material
        WHERE for_writing = 1
        ORDER BY id"#,
        AI_TEXT_LIMIT as i64
    )
    .fetch_all(db)
    .await
}

/// The materials used for matching, oldest first.
pub async fn for_matching(db: &SqlitePool) -> Result<Vec<Reference>, sqlx::Error> {
    sqlx::query_as!(
        Reference,
        r#"SELECT file_name, substr(extracted_text, 1, ?) AS "text!: String"
        FROM material
        WHERE for_matching = 1
        ORDER BY id"#,
        AI_TEXT_LIMIT as i64
    )
    .fetch_all(db)
    .await
}

/// Whether matching has any material to draw on, without fetching its text.
pub async fn has_matching(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT EXISTS(SELECT 1 FROM material WHERE for_matching = 1) AS "exists!: bool""#
    )
    .fetch_one(db)
    .await
}

/// The text of a material: None when there is no such material, Some(None) when it has none.
pub async fn text(db: &SqlitePool, id: i64) -> Result<Option<Option<String>>, sqlx::Error> {
    sqlx::query_scalar!("SELECT extracted_text FROM material WHERE id = ?", id)
        .fetch_optional(db)
        .await
}

/// A checked and extracted file, not yet available as a material.
pub struct Extracted {
    file_name: String,
    data: Vec<u8>,
    text: Option<String>,
}

pub async fn extract(file_name: &str, data: Vec<u8>) -> Result<Extracted, Error> {
    let pdf = match file_name
        .rsplit_once('.')
        .map(|(_, e)| e.to_ascii_lowercase())
    {
        Some(e) if e == "pdf" => true,
        Some(e) if e == "docx" || e == "doc" => false,
        _ => return Err(Error::Type),
    };
    if data.len() > MAX_SIZE {
        return Err(Error::TooLarge);
    }
    // Parsing is slow work, and a library may panic on a damaged file.
    let (text, data) = tokio::task::spawn_blocking(move || (extract_text(pdf, &data), data))
        .await
        .map_err(|e| Error::Unreadable(e.to_string()))?;
    Ok(Extracted {
        file_name: file_name.into(),
        data,
        text: text.map_err(Error::Unreadable)?,
    })
}

/// Stores the file before committing its row. Take `now` after extraction so the basis time
/// reflects when the material becomes available, rather than when parsing started.
pub async fn add(
    db: &SqlitePool,
    dir: &Path,
    extracted: Extracted,
    now: i64,
) -> Result<i64, Error> {
    let Extracted {
        file_name,
        data,
        text,
    } = extracted;
    let size = data.len() as i64;
    let has_text = text.is_some();
    let mut tx = db.begin().await?;
    let id = sqlx::query_scalar!(
        "INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default,
           created_at, extracted_text)
         VALUES (?, ?, ?, ?, 0, ?, ?) RETURNING id",
        file_name,
        size,
        has_text,
        has_text,
        now,
        text,
    )
    .fetch_one(&mut *tx)
    .await?;
    // Renamed into place before the row is committed, so that no row is ever without its file.
    // Stopped in between, the id is not taken and the next material writes over the file.
    tokio::fs::create_dir_all(dir).await?;
    let temp = dir.join(format!("{id}.tmp"));
    let mut out = tokio::fs::File::create(&temp).await?;
    out.write_all(&data).await?;
    out.sync_all().await?;
    tokio::fs::rename(&temp, file(dir, id)).await?;
    if has_text {
        sqlx::query!(
            r#"UPDATE profile
            SET match_basis_changed_at = max(coalesce(match_basis_changed_at, ?1), ?1)"#,
            now
        )
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await?;
    Ok(id)
}

/// The text of a PDF or Word file, or None when it has none, such as a scan.
fn extract_text(pdf: bool, data: &[u8]) -> Result<Option<String>, String> {
    let mut text = if pdf {
        pdf_oxide::PdfDocument::from_bytes(data.to_vec())
            .and_then(|d| d.extract_all_text())
            .map_err(|e| e.to_string())?
    } else {
        // By what the file is rather than its name, as a DOCX may be named .doc.
        let format = if data.starts_with(b"PK") {
            DocumentFormat::Docx
        } else {
            DocumentFormat::Doc
        };
        office_oxide::Document::from_reader(Cursor::new(data.to_vec()), format)
            .map_err(|e| e.to_string())?
            .plain_text()
    };
    // Extractors may emit NUL for symbol fonts; SQLite text functions stop at it.
    text.retain(|c| c != '\0');
    Ok(tidy(&text))
}

/// Without spaces ending lines or runs of blank lines, which only take up a model's tokens.
fn tidy(text: &str) -> Option<String> {
    let mut out = String::with_capacity(text.len());
    let mut blank = false;
    for line in text.lines().map(str::trim_end) {
        if line.trim_start().is_empty() {
            blank = !out.is_empty();
            continue;
        }
        if blank {
            out.push('\n');
            blank = false;
        }
        out.push_str(line);
        out.push('\n');
    }
    out.truncate(out.trim_end().len());
    (!out.is_empty()).then_some(out)
}

/// False when there is no such material.
pub async fn set_purpose(
    db: &SqlitePool,
    id: i64,
    purpose: Purpose,
    on: bool,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let row = match purpose {
        Purpose::Matching => {
            sqlx::query!(
                "UPDATE material SET for_matching = ?1 WHERE id = ?2 AND for_matching <> ?1",
                on,
                id
            )
            .execute(&mut *tx)
            .await?
        }
        Purpose::Writing => {
            sqlx::query!("UPDATE material SET for_writing = ? WHERE id = ?", on, id)
                .execute(&mut *tx)
                .await?
        }
        Purpose::AttachByDefault => {
            sqlx::query!(
                "UPDATE material SET attach_by_default = ? WHERE id = ?",
                on,
                id
            )
            .execute(&mut *tx)
            .await?
        }
    };
    if matches!(purpose, Purpose::Matching) && row.rows_affected() > 0 {
        sqlx::query!(
            r#"UPDATE profile
            SET match_basis_changed_at = max(coalesce(match_basis_changed_at, ?1), ?1)"#,
            now
        )
        .execute(&mut *tx)
        .await?;
    }
    let exists = sqlx::query_scalar!("SELECT id FROM material WHERE id = ?", id)
        .fetch_optional(&mut *tx)
        .await?
        .is_some();
    tx.commit().await?;
    Ok(exists)
}

/// Deletes the material, then its file in `dir`; false when there is no such material. Drafts
/// lose it as an attachment; emails that went out keep its name. Stopped or failing in between,
/// the file is left behind, which only takes up space: a material without its file would fail the
/// emails it is attached to.
pub async fn delete(db: &SqlitePool, dir: &Path, id: i64, now: i64) -> Result<bool, Error> {
    let mut tx = db.begin().await?;
    // Lock before checking references so generation cannot be queued between the check and
    // deletion.
    let exists = sqlx::query!("UPDATE material SET id = id WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    if exists.rows_affected() == 0 {
        return Ok(false);
    }
    let task = sqlx::query!(
        "SELECT t.name FROM task t JOIN task_material m ON m.task_id = t.id
         WHERE m.material_id = ? AND m.role = 'reference'
           AND EXISTS (SELECT 1 FROM email e WHERE e.task_id = t.id AND e.state = 'generating')
         ORDER BY t.id LIMIT 1",
        id,
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(task) = task {
        return Err(Error::TaskInUse { name: task.name });
    }
    sqlx::query!("DELETE FROM task_material WHERE material_id = ?", id)
        .execute(&mut *tx)
        .await?;
    let row = sqlx::query!(
        r#"DELETE
        FROM material
        WHERE id = ?1 AND NOT EXISTS (
            SELECT 1
            FROM email_attachment a
            JOIN email e ON e.id = a.email_id
            WHERE a.material_id = ?1 AND e.state IN ('approved', 'cloud', 'sending',
                'maybe_sent', 'failed')
        )
        RETURNING for_matching"#,
        id
    )
    .fetch_optional(&mut *tx)
    .await?;
    if row.is_none() {
        return Err(Error::InUse);
    }
    if row.is_some_and(|r| r.for_matching != 0) {
        sqlx::query!(
            r#"UPDATE profile
            SET match_basis_changed_at = max(coalesce(match_basis_changed_at, ?1), ?1)"#,
            now
        )
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await?;
    if let Err(e) = tokio::fs::remove_file(file(dir, id)).await {
        tracing::warn!("removing the file of deleted material {id}: {e}");
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tidy_text_keeps_lines_and_one_blank_between_paragraphs() {
        assert_eq!(
            tidy("\n  林晓 个人简历  \n\n\n   \n教育背景\n  南京大学\n\n"),
            Some("  林晓 个人简历\n\n教育背景\n  南京大学".into())
        );
        assert_eq!(tidy(" \n\n\t\n"), None);
    }
}
