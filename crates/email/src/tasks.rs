//! Task email transitions. Claims fence off late model responses after cancellation or regeneration.
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum DraftState {
    Generating,
    GenerationFailed,
    Review,
    Approved,
    Cloud,
    Sending,
    MaybeSent,
    Sent,
    Bounced,
    Failed,
    Canceled,
}

pub async fn create(
    conn: &mut SqliteConnection,
    task: i64,
    mentor: i64,
    content: Option<(&str, &str)>,
    attachments: &[i64],
    now: i64,
) -> Result<i64, sqlx::Error> {
    let (state, subject, body) = match content {
        Some((s, b)) => ("review", Some(s), Some(b)),
        None => ("generating", None, None),
    };
    let queued = content.is_none().then_some(now);
    let id = sqlx::query_scalar!("INSERT INTO email (task_id, mentor_id, kind, state, subject, body_md, generation_queued_at, created_at, updated_at) VALUES (?, ?, 'first', ?, ?, ?, ?, ?, ?) RETURNING id AS \"id!\"", task, mentor, state, subject, body, queued, now, now).fetch_one(&mut *conn).await?;
    super::attach(conn, id, attachments).await?;
    Ok(id)
}

pub async fn save(
    conn: &mut SqliteConnection,
    task: i64,
    id: i64,
    subject: &str,
    body: &str,
    attachments: &[i64],
    now: i64,
) -> Result<bool, sqlx::Error> {
    let n = sqlx::query!("UPDATE email SET subject = ?, body_md = ?, updated_at = ? WHERE id = ? AND task_id = ? AND state = 'review' AND task_id IN (SELECT id FROM task WHERE state <> 'stopped')", subject, body, now, id, task).execute(&mut *conn).await?.rows_affected();
    if n > 0 {
        super::attach(conn, id, attachments).await?;
    }
    Ok(n > 0)
}

pub async fn approve(
    conn: &mut SqliteConnection,
    task: i64,
    id: i64,
    html: &str,
    text: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET state = 'approved', html = ?, text = ?, planned_at = ?, updated_at = ? WHERE id = ? AND task_id = ? AND state = 'review' AND trim(coalesce(subject, '')) <> '' AND trim(coalesce(body_md, '')) <> '' AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL AND id IN (SELECT mentor_id FROM mentor_email)) AND task_id IN (SELECT id FROM task WHERE state <> 'stopped')", html, text, now, now, id, task).execute(conn).await?.rows_affected() > 0)
}

pub async fn cancel(
    conn: &mut SqliteConnection,
    task: i64,
    id: Option<i64>,
    reason: &str,
    now: i64,
) -> Result<u64, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET state = 'canceled', error = ?, generation_claim = generation_claim + 1, generation_queued_at = NULL, updated_at = ? WHERE task_id = ? AND (?4 IS NULL OR id = ?4) AND state IN ('generating', 'generation_failed', 'review', 'approved', 'failed')", reason, now, task, id).execute(conn).await?.rows_affected())
}

pub async fn delete(conn: &mut SqliteConnection, task: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM email WHERE task_id = ? AND state NOT IN ('sending', 'cloud', 'maybe_sent', 'sent', 'bounced')", task).execute(conn).await?;
    Ok(())
}

pub async fn regenerate(
    conn: &mut SqliteConnection,
    task: i64,
    id: Option<i64>,
    failed_only: bool,
    now: i64,
) -> Result<u64, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET state = 'generating', generation_claim = generation_claim + 1, generation_queued_at = ?, error = NULL, updated_at = ? WHERE task_id = ? AND (?4 IS NULL OR id = ?4) AND (state = 'generation_failed' OR (?5 = 0 AND state = 'review')) AND task_id IN (SELECT id FROM task WHERE writer = 'ai' AND state <> 'stopped')", now, now, task, id, failed_only).execute(conn).await?.rows_affected())
}

pub async fn reapply(
    conn: &mut SqliteConnection,
    task: i64,
    id: i64,
    subject: &str,
    body: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET subject = ?, body_md = ?, updated_at = ? WHERE id = ? AND task_id = ? AND state = 'review' AND task_id IN (SELECT id FROM task WHERE writer = 'template' AND state <> 'stopped')", subject, body, now, id, task).execute(conn).await?.rows_affected() > 0)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Claim {
    pub id: i64,
    pub task: i64,
    pub mentor: i64,
    pub version: i64,
}

pub async fn recover(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE email SET generation_claim = generation_claim + 1, generation_queued_at = coalesce(generation_queued_at, created_at) WHERE state = 'generating'").execute(db).await?;
    Ok(())
}

pub async fn next(db: &SqlitePool) -> Result<Option<Claim>, sqlx::Error> {
    sqlx::query_as!(Claim, r#"UPDATE email SET generation_claim = generation_claim + 1, generation_queued_at = NULL
        WHERE id = (SELECT id FROM email WHERE state = 'generating' AND generation_queued_at IS NOT NULL
          AND task_id IN (SELECT id FROM task WHERE state = 'active' AND generation_paused_at IS NULL)
          AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)
          ORDER BY generation_queued_at, id LIMIT 1)
        RETURNING id, task_id AS "task!", mentor_id AS mentor, generation_claim AS version"#).fetch_optional(db).await
}

pub async fn valid(db: &SqlitePool, c: Claim) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar!("SELECT id FROM email WHERE id = ? AND generation_claim = ? AND state = 'generating' AND task_id IN (SELECT id FROM task WHERE state = 'active' AND generation_paused_at IS NULL)", c.id, c.version).fetch_optional(db).await?.is_some())
}

pub async fn complete(
    conn: &mut SqliteConnection,
    c: Claim,
    subject: &str,
    body: &str,
    model: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET state = 'review', subject = ?, body_md = ?, model = ?, error = NULL, generation_queued_at = NULL, updated_at = ? WHERE id = ? AND generation_claim = ? AND state = 'generating'", subject, body, model, now, c.id, c.version).execute(conn).await?.rows_affected() > 0)
}

pub async fn fail(
    conn: &mut SqliteConnection,
    c: Claim,
    reason: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET state = 'generation_failed', error = ?, generation_queued_at = NULL, updated_at = ? WHERE id = ? AND generation_claim = ? AND state = 'generating'", reason, now, c.id, c.version).execute(conn).await?.rows_affected() > 0)
}

pub async fn requeue(conn: &mut SqliteConnection, c: Claim) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET generation_queued_at = created_at WHERE id = ? AND generation_claim = ? AND state = 'generating'", c.id, c.version).execute(conn).await?.rows_affected() > 0)
}

/// An upload attaches to this task draft only, without restoring the text from upload start.
pub async fn attach_material(
    db: &SqlitePool,
    task: i64,
    id: i64,
    material: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("INSERT INTO email_attachment(email_id,material_id,file_name,size)
        SELECT e.id,m.id,m.file_name,m.size FROM email e CROSS JOIN material m
        WHERE e.id=?1 AND e.task_id=?2 AND e.state='review' AND m.id=?3
          AND e.task_id IN (SELECT id FROM task WHERE state <> 'stopped')
        ON CONFLICT(email_id,file_name) DO UPDATE SET material_id=excluded.material_id,size=excluded.size",id,task,material).execute(db).await?.rows_affected()==1)
}
