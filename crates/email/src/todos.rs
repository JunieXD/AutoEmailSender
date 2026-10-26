//! Correspondence todos and their local civil deadlines.

use chrono::{NaiveDate, NaiveTime, TimeZone};
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

use crate::outcome::{self as queue, Claim};

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TodoForm {
    pub text: String,
    pub due_date: Option<String>,
    pub due_time: Option<String>,
}

pub struct Fields {
    pub text: String,
    pub due_date: Option<String>,
    pub due_time: Option<String>,
}

pub fn date(text: &str) -> Option<NaiveDate> {
    let date = NaiveDate::parse_from_str(text, "%Y-%m-%d").ok()?;
    (date.format("%Y-%m-%d").to_string() == text && text.len() == 10).then_some(date)
}

pub fn time(text: &str) -> Option<NaiveTime> {
    let time = NaiveTime::parse_from_str(text, "%H:%M").ok()?;
    (time.format("%H:%M").to_string() == text).then_some(time)
}

pub fn text(value: &str) -> Result<String, &'static str> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 100 {
        return Err("待办内容须为 1 到 100 个字。");
    }
    Ok(value.to_owned())
}

impl TodoForm {
    pub fn check(self) -> Result<Fields, &'static str> {
        let text = text(&self.text)?;
        if self.due_date.as_deref().is_some_and(|s| date(s).is_none()) {
            return Err("截止日期须为有效的 YYYY-MM-DD 日期。");
        }
        if self.due_time.as_deref().is_some_and(|s| time(s).is_none()) {
            return Err("截止时间须为有效的 HH:MM 时间。");
        }
        if self.due_time.is_some() && self.due_date.is_none() {
            return Err("请先填写截止日期，再填写时间。");
        }
        Ok(Fields {
            text,
            due_date: self.due_date,
            due_time: self.due_time,
        })
    }
}

pub fn reply_date(at: i64) -> Result<(String, String), sqlx::Error> {
    use chrono::Datelike;
    let at = chrono::Local
        .timestamp_millis_opt(at)
        .single()
        .ok_or_else(|| sqlx::Error::Decode("invalid reply timestamp".into()))?;
    Ok((at.format("%Y-%m-%d").to_string(), at.weekday().to_string()))
}

pub fn extracted(form: TodoForm, reply_date: &str) -> Result<Fields, &'static str> {
    let text = text(&form.text)?;
    let reply = date(reply_date).ok_or("回信日期无效")?;
    let first = reply.pred_opt().ok_or("回信日期超出范围")?;
    let last = reply
        .checked_add_months(chrono::Months::new(12))
        .ok_or("回信日期超出范围")?;
    let due_date = form
        .due_date
        .filter(|s| date(s).is_some_and(|at| at >= first && at <= last));
    let due_time = form
        .due_time
        .filter(|s| due_date.is_some() && time(s).is_some());
    Ok(Fields {
        text,
        due_date,
        due_time,
    })
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Todo {
    pub id: i64,
    pub text: String,
    pub due_date: Option<String>,
    pub due_time: Option<String>,
    pub done: bool,
    pub message_id: Option<i64>,
    pub created_at: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpenTodo {
    #[serde(flatten)]
    pub todo: Todo,
    pub mentor_id: i64,
    pub mentor_name: String,
}

#[derive(Debug, Deserialize, TS)]
pub struct TodoQuery {
    pub page: std::num::NonZeroU32,
}

#[derive(Debug, Serialize, TS)]
pub struct TodoPage {
    pub todos: Vec<OpenTodo>,
    pub total: i64,
}

#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum TodoExtraction {
    Extracting,
    Failed { reason: String },
}

pub async fn enabled(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT ai_reply_todos AS "on!: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

pub async fn set_enabled(db: &SqlitePool, enabled: bool) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE profile SET ai_reply_todos = ?", enabled)
        .execute(&mut *tx)
        .await?;
    if !enabled {
        sqlx::query!("DELETE FROM reply_processing WHERE action = 'todos'")
            .execute(&mut *tx)
            .await?;
        sqlx::query!(
            r#"UPDATE profile
            SET reply_processing_paused_at = NULL,
                reply_processing_pause_reason = NULL
            WHERE NOT ai_reply_outcome"#
        )
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await
}

/// Only called for a newly inserted reply from the incremental receive pass.
pub async fn enqueue(db: &mut SqliteConnection, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"INSERT INTO reply_processing(message_id, action, state)
        SELECT id, 'todos', 'queued' FROM message
        WHERE id = ? AND kind = 'reply' AND (SELECT ai_reply_todos FROM profile)
        ON CONFLICT DO NOTHING"#,
        id
    )
    .execute(db)
    .await?;
    Ok(())
}

pub async fn add(
    db: &SqlitePool,
    mentor: i64,
    fields: &Fields,
    now: i64,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"INSERT INTO todo(mentor_id, text, due_date, due_time, created_at, due_changed_at)
        SELECT id, ?2, ?3, ?4, ?5, ?5 FROM mentor WHERE id = ?1 AND trashed_at IS NULL
        RETURNING id AS "id!""#,
        mentor,
        fields.text,
        fields.due_date,
        fields.due_time,
        now
    )
    .fetch_optional(db)
    .await
}

pub async fn update(
    db: &SqlitePool,
    id: i64,
    fields: &Fields,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let changed = sqlx::query!(
        r#"UPDATE todo
        SET text = ?2, due_date = ?3, due_time = ?4, reminded_at = CASE
        WHEN due_date IS NOT ?3 OR due_time IS NOT ?4 THEN NULL
        ELSE reminded_at END, due_changed_at = CASE WHEN due_date IS NOT ?3
        OR due_time IS NOT ?4 THEN ?5 ELSE due_changed_at END
        WHERE id = ?1 AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)"#,
        id,
        fields.text,
        fields.due_date,
        fields.due_time,
        now
    )
    .execute(db)
    .await?;
    Ok(changed.rows_affected() > 0)
}

pub async fn set_done(db: &SqlitePool, id: i64, done: bool, now: i64) -> Result<bool, sqlx::Error> {
    let at = done.then_some(now);
    let changed = sqlx::query!(
        r#"UPDATE todo
        SET done_at = ?
        WHERE id = ? AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)"#,
        at,
        id
    )
    .execute(db)
    .await?;
    Ok(changed.rows_affected() > 0)
}

pub async fn delete(db: &SqlitePool, id: i64) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!(
        r#"DELETE
        FROM todo
        WHERE id = ? AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)"#,
        id
    )
    .execute(db)
    .await?
    .rows_affected()
        > 0)
}

pub async fn for_mentor<'a>(
    db: impl sqlx::Executor<'a, Database = sqlx::Sqlite>,
    mentor: i64,
) -> Result<Vec<Todo>, sqlx::Error> {
    sqlx::query_as!(
        Todo,
        r#"SELECT id, text, due_date, due_time, done_at IS NOT NULL AS "done!: bool",
        message_id, created_at FROM todo WHERE mentor_id = ? ORDER BY created_at, id"#,
        mentor
    )
    .fetch_all(db)
    .await
}

pub async fn list(db: &SqlitePool, query: &TodoQuery) -> Result<TodoPage, sqlx::Error> {
    let mut tx = db.begin().await?;
    let total = sqlx::query_scalar!(
        r#"SELECT count(*) AS "total!"
        FROM todo
        WHERE done_at IS NULL AND mentor_id NOT IN (SELECT id FROM mentor WHERE
            trashed_at IS NOT NULL)"#
    )
    .fetch_one(&mut *tx)
    .await?;
    let offset = (i64::from(query.page.get()) - 1) * 50;
    let rows = sqlx::query!(
        r#"SELECT t.id, t.text, t.due_date, t.due_time, t.message_id, t.created_at,
            t.mentor_id, m.name
        FROM todo t
        JOIN mentor m ON m.id = t.mentor_id
        WHERE t.done_at IS NULL AND t.mentor_id NOT IN (SELECT id FROM mentor WHERE
            trashed_at IS NOT NULL)
        ORDER BY t.due_date IS NULL, t.due_date, coalesce(t.due_time, '24:00'), t.id
        LIMIT 50
        OFFSET ?"#,
        offset
    )
    .fetch_all(&mut *tx)
    .await?;
    Ok(TodoPage {
        total,
        todos: rows
            .into_iter()
            .map(|r| OpenTodo {
                mentor_id: r.mentor_id,
                mentor_name: r.name,
                todo: Todo {
                    id: r.id,
                    text: r.text,
                    due_date: r.due_date,
                    due_time: r.due_time,
                    done: false,
                    message_id: r.message_id,
                    created_at: r.created_at,
                },
            })
            .collect(),
    })
}

pub async fn complete(
    db: &SqlitePool,
    claim: Claim,
    todos: &[Fields],
    now: i64,
) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let deleted = sqlx::query!(
        "DELETE FROM reply_processing WHERE message_id = ? AND action = 'todos'
        AND claim = ? AND state = 'running' AND EXISTS (
            SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
            WHERE mm.message_id = reply_processing.message_id AND m.trashed_at IS NULL)",
        claim.id,
        claim.claim
    )
    .execute(&mut *tx)
    .await?;
    if deleted.rows_affected() == 0 {
        queue::requeue(&mut tx, claim).await?;
        tx.commit().await?;
        return Ok(false);
    }
    let mentor = sqlx::query_scalar!(
        r#"SELECT mm.mentor_id FROM message_mentor mm
        JOIN message msg ON msg.id = mm.message_id WHERE mm.message_id = ?
        ORDER BY EXISTS (SELECT 1 FROM mentor_email e WHERE e.mentor_id = mm.mentor_id
            AND e.address = msg.from_address COLLATE NOCASE) DESC, mm.mentor_id LIMIT 1"#,
        claim.id
    )
    .fetch_one(&mut *tx)
    .await?;
    for todo in todos {
        sqlx::query!(
            r#"INSERT INTO todo (mentor_id, message_id, text, due_date, due_time,
                created_at, due_changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)"#,
            mentor,
            claim.id,
            todo.text,
            todo.due_date,
            todo.due_time,
            now,
            now
        )
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await?;
    Ok(true)
}

/// Repeated local times use the first occurrence; a skipped minute advances to the first existing
/// one.
fn local<T: TimeZone>(zone: &T, at: chrono::NaiveDateTime) -> Option<i64> {
    (0..=24 * 60).find_map(|minute| {
        at.checked_add_signed(chrono::TimeDelta::minutes(minute))
            .and_then(|at| zone.from_local_datetime(&at).earliest())
            .map(|at| at.timestamp_millis())
    })
}

pub fn reminder_at<T: TimeZone>(due: NaiveDate, zone: &T) -> Option<i64> {
    local(zone, due.pred_opt()?.and_hms_opt(9, 0, 0)?)
}

fn deadline<T: TimeZone>(date: NaiveDate, time: Option<&str>, zone: &T) -> Option<i64> {
    match time {
        Some(time) => local(zone, date.and_time(self::time(time)?)),
        None => local(zone, date.succ_opt()?.and_time(NaiveTime::MIN)),
    }
}

pub struct Reminders {
    pub messages: Vec<String>,
    pub next_at: Option<i64>,
}

pub async fn local_reminders(db: &SqlitePool, now: i64) -> Result<Reminders, sqlx::Error> {
    reminders(db, now, &chrono::Local).await
}

pub async fn reminders<T: TimeZone>(
    db: &SqlitePool,
    now: i64,
    zone: &T,
) -> Result<Reminders, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    // Eligibility and reminder claims must see the same todo edits and completion state.
    let enabled = sqlx::query_scalar!(r#"SELECT todo_reminders AS "on!: bool" FROM profile"#)
        .fetch_one(&mut *tx)
        .await?;
    if !enabled {
        return Ok(Reminders {
            messages: Vec::new(),
            next_at: None,
        });
    }
    let today = zone
        .timestamp_millis_opt(now)
        .single()
        .map(|at| at.date_naive())
        .ok_or_else(|| sqlx::Error::Decode("invalid reminder timestamp".into()))?;
    let today_text = today.format("%Y-%m-%d").to_string();
    let dates = sqlx::query!(
        r#"SELECT due_date AS "due_date!", min(due_changed_at) AS "changed!: i64" FROM todo
        WHERE done_at IS NULL AND reminded_at IS NULL AND due_date >= ?
          AND mentor_id NOT IN (SELECT id FROM mentor WHERE trashed_at IS NOT NULL)
        GROUP BY due_date ORDER BY due_date"#,
        today_text
    )
    .fetch_all(&mut *tx)
    .await?;
    let mut result = Reminders {
        messages: Vec::new(),
        next_at: None,
    };
    for row in dates {
        let due = date(&row.due_date)
            .ok_or_else(|| sqlx::Error::Decode("invalid stored todo date".into()))?;
        let at = reminder_at(due, zone)
            .ok_or_else(|| sqlx::Error::Decode("invalid reminder date".into()))?;
        if row.changed >= at {
            continue;
        }
        if now < at {
            result.next_at = Some(at);
            break;
        }
        let todos = sqlx::query!(
            "SELECT id, text, due_time FROM todo
            WHERE done_at IS NULL AND reminded_at IS NULL AND due_date = ? AND due_changed_at < ?
              AND mentor_id NOT IN (SELECT id FROM mentor WHERE trashed_at IS NOT NULL)
            ORDER BY id",
            row.due_date,
            at
        )
        .fetch_all(&mut *tx)
        .await?;
        for row in todos {
            let end = deadline(due, row.due_time.as_deref(), zone)
                .ok_or_else(|| sqlx::Error::Decode("invalid deadline".into()))?;
            if now >= end {
                continue;
            }
            sqlx::query!("UPDATE todo SET reminded_at = ? WHERE id = ?", now, row.id)
                .execute(&mut *tx)
                .await?;
            let day = if today >= due { "今天" } else { "明天" };
            result.messages.push(format!("{day}截止：{}", row.text));
        }
    }
    tx.commit().await?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_conversion_advances_dst_gaps_and_chooses_the_first_repeated_time() {
        let zone = chrono_tz::America::New_York;
        let gap = NaiveDate::from_ymd_opt(2026, 3, 8)
            .unwrap()
            .and_hms_opt(2, 30, 0)
            .unwrap();
        assert_eq!(
            local(&zone, gap),
            Some(
                zone.with_ymd_and_hms(2026, 3, 8, 3, 0, 0)
                    .unwrap()
                    .timestamp_millis()
            )
        );
        let repeated = NaiveDate::from_ymd_opt(2026, 11, 1)
            .unwrap()
            .and_hms_opt(1, 30, 0)
            .unwrap();
        assert_eq!(
            local(&zone, repeated),
            Some(
                zone.from_local_datetime(&repeated)
                    .earliest()
                    .unwrap()
                    .timestamp_millis()
            )
        );
    }
}
