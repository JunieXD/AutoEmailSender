//! Tags: the user's own labels for mentors, each with a colour and a place in the user's order.

use serde::{Deserialize, Serialize};
use sqlx::types::Json;
use sqlx::{SqliteConnection, SqlitePool};
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
        Ok(TagFields {
            name: checked_name(&self.name)?,
            color: checked_color(&self.color)?,
        })
    }
}

/// A tag name as the user typed it, trimmed.
pub fn checked_name(name: &str) -> Result<String, String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("请填写标签名称".into());
    }
    if name.contains([';', '；', '、']) {
        return Err("标签名称不能含分号或顿号，请换一个名称".into());
    }
    Ok(name.to_owned())
}

/// A `#rrggbb` colour, lowercased.
pub fn checked_color(color: &str) -> Result<String, String> {
    let hex = color.strip_prefix('#').unwrap_or_default();
    if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("标签颜色不正确".into());
    }
    Ok(color.to_ascii_lowercase())
}

/// Which way a tag moves in the user's order.
#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum Direction {
    Up,
    Down,
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

/// Adds a tag at the end of the order and puts it on these mentors, all or nothing, so creating it
/// again after a failure does not find the name taken.
pub async fn create(db: &SqlitePool, tag: &TagFields, mentor_ids: &[i64]) -> Result<Tag, TagError> {
    let mut tx = db.begin().await?;
    let id = sqlx::query_scalar!(
        "INSERT INTO tag (name, color, position)
         VALUES (?, ?, (SELECT coalesce(max(position) + 1, 0) FROM tag)) RETURNING id",
        tag.name,
        tag.color,
    )
    .fetch_one(&mut *tx)
    .await?;
    sqlx::query!(
        "INSERT INTO mentor_tag (mentor_id, tag_id) SELECT value, ? FROM json_each(?)",
        id,
        Json(mentor_ids),
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(Tag {
        id,
        name: tag.name.clone(),
        color: tag.color.clone(),
    })
}

// Renaming and recolouring are separate writes, so a quick change to one never carries a stale
// value of the other.

/// Renames a tag. False when there is no such tag.
pub async fn rename(db: &SqlitePool, id: i64, name: &str) -> Result<bool, TagError> {
    let done = sqlx::query!("UPDATE tag SET name = ? WHERE id = ?", name, id)
        .execute(db)
        .await?;
    Ok(done.rows_affected() == 1)
}

/// Recolours a tag. False when there is no such tag.
pub async fn recolor(db: &SqlitePool, id: i64, color: &str) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!("UPDATE tag SET color = ? WHERE id = ?", color, id)
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

/// Swaps a tag with its neighbour in the given direction; nothing happens at either end. One
/// statement against the saved order, so quick repeated moves each take effect.
pub async fn move_tag(db: &SqlitePool, id: i64, direction: Direction) -> Result<(), sqlx::Error> {
    let step: i64 = match direction {
        Direction::Up => -1,
        Direction::Down => 1,
    };
    sqlx::query!(
        "UPDATE tag SET position = CASE tag.id WHEN ?1 THEN other.position ELSE me.position END
         FROM (SELECT position FROM tag WHERE id = ?1) AS me,
              (SELECT id, position FROM tag
               WHERE (position - (SELECT position FROM tag WHERE id = ?1)) * ?2 > 0
               ORDER BY position * ?2 LIMIT 1) AS other
         WHERE tag.id IN (?1, other.id)",
        id,
        step,
    )
    .execute(db)
    .await?;
    Ok(())
}

/// Puts the tag on these mentors, or takes it off them. Adding or removing one tag at a time means a
/// change made from an out-of-date view never undoes another tag.
pub async fn tag_mentors(
    db: &SqlitePool,
    tag_id: i64,
    mentor_ids: &[i64],
    on: bool,
) -> Result<(), sqlx::Error> {
    let mentor_ids = Json(mentor_ids);
    if on {
        sqlx::query!(
            "INSERT OR IGNORE INTO mentor_tag (mentor_id, tag_id) SELECT value, ? FROM json_each(?)",
            tag_id,
            mentor_ids
        )
        .execute(db)
        .await?;
    } else {
        sqlx::query!(
            "DELETE FROM mentor_tag WHERE tag_id = ? AND mentor_id IN (SELECT value FROM json_each(?))",
            tag_id,
            mentor_ids
        )
        .execute(db)
        .await?;
    }
    Ok(())
}

/// Presets used by both the tag picker and imported new tags.
pub const COLORS: [&str; 8] = [
    "#991b1b", "#c2410c", "#985a0c", "#2c7a4f", "#0e7490", "#3f6aa8", "#7e22ce", "#6f6762",
];

pub async fn add_by_name(
    conn: &mut SqliteConnection,
    mentor: i64,
    name: &str,
) -> Result<(), sqlx::Error> {
    let id = match sqlx::query_scalar!("SELECT id FROM tag WHERE name = ?", name)
        .fetch_optional(&mut *conn)
        .await?
    {
        Some(id) => id,
        None => {
            let count = sqlx::query_scalar!(r#"SELECT count(*) AS "n!: i64" FROM tag"#)
                .fetch_one(&mut *conn)
                .await?;
            let color = COLORS[count as usize % COLORS.len()];
            sqlx::query_scalar!("INSERT INTO tag (name, color, position) VALUES (?, ?, (SELECT coalesce(max(position) + 1, 0) FROM tag)) RETURNING id", name, color).fetch_one(&mut *conn).await?
        }
    };
    sqlx::query!(
        "INSERT OR IGNORE INTO mentor_tag (mentor_id, tag_id) VALUES (?, ?)",
        mentor,
        id
    )
    .execute(conn)
    .await?;
    Ok(())
}
