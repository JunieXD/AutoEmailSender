//! Tags: the user's own labels for mentors, each with a colour and a place in the user's order.

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

#[derive(Debug, Clone, Serialize, TS)]
pub struct Tag {
    pub id: i64,
    pub name: String,
    /// `#rrggbb`.
    pub color: String,
}

/// A tag's name and colour as the user entered them; `check` validates them.
#[derive(Debug, Deserialize, TS)]
pub struct TagForm {
    pub name: String,
    pub color: String,
}

/// A checked `TagForm`.
#[derive(Debug)]
pub struct TagFields {
    pub name: String,
    pub color: String,
}

impl TagForm {
    pub fn check(self) -> Result<TagFields, String> {
        let name = self.name.trim();
        if name.is_empty() {
            return Err("请填写标签名称".into());
        }
        let hex = self.color.strip_prefix('#').unwrap_or_default();
        if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err("标签颜色不正确".into());
        }
        Ok(TagFields {
            name: name.to_owned(),
            color: self.color.to_ascii_lowercase(),
        })
    }
}

#[derive(Debug)]
pub enum TagError {
    NameTaken,
    Db(sqlx::Error),
}

impl From<sqlx::Error> for TagError {
    fn from(e: sqlx::Error) -> Self {
        // `name` is the only unique column a tag can collide on.
        if e.as_database_error()
            .is_some_and(|d| d.is_unique_violation())
        {
            TagError::NameTaken
        } else {
            TagError::Db(e)
        }
    }
}

/// Every tag, in the user's order.
pub async fn list(db: &SqlitePool) -> Result<Vec<Tag>, sqlx::Error> {
    sqlx::query_as!(Tag, "SELECT id, name, color FROM tag ORDER BY position")
        .fetch_all(db)
        .await
}

/// Adds a tag at the end of the order.
pub async fn create(db: &SqlitePool, tag: &TagFields) -> Result<Tag, TagError> {
    let id = sqlx::query_scalar!(
        "INSERT INTO tag (name, color, position)
         VALUES (?, ?, (SELECT coalesce(max(position) + 1, 0) FROM tag)) RETURNING id",
        tag.name,
        tag.color,
    )
    .fetch_one(db)
    .await?;
    Ok(Tag {
        id,
        name: tag.name.clone(),
        color: tag.color.clone(),
    })
}

/// Renames and recolours a tag. False when there is no such tag.
pub async fn update(db: &SqlitePool, id: i64, tag: &TagFields) -> Result<bool, TagError> {
    let done = sqlx::query!(
        "UPDATE tag SET name = ?, color = ? WHERE id = ?",
        tag.name,
        tag.color,
        id
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// Deletes a tag, taking it off every mentor.
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM tag WHERE id = ?", id)
        .execute(db)
        .await?;
    Ok(())
}

/// Puts the tags in the order of `ids`.
pub async fn reorder(db: &SqlitePool, ids: &[i64]) -> Result<(), sqlx::Error> {
    let ids = crate::json_ids(ids.iter().copied());
    sqlx::query!(
        "UPDATE tag SET position = (SELECT key FROM json_each(?1) WHERE value = tag.id)
         WHERE id IN (SELECT value FROM json_each(?1))",
        ids
    )
    .execute(db)
    .await?;
    Ok(())
}

/// Gives the mentor exactly these tags.
pub async fn set_for_mentor(
    db: &SqlitePool,
    mentor_id: i64,
    tag_ids: &[i64],
) -> Result<(), sqlx::Error> {
    let tag_ids = crate::json_ids(tag_ids.iter().copied());
    let mut tx = db.begin().await?;
    sqlx::query!("DELETE FROM mentor_tag WHERE mentor_id = ?", mentor_id)
        .execute(&mut *tx)
        .await?;
    sqlx::query!(
        "INSERT INTO mentor_tag (mentor_id, tag_id) SELECT ?, value FROM json_each(?)",
        mentor_id,
        tag_ids
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(())
}
