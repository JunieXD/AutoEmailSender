//! Standalone follow-ups use the same admission rules as tasks and freeze the selected thread.
use super::{Approval, Approved, attach, attached, snapshot, thread};
use serde::Serialize;
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

pub struct Eligibility {
    pub reason: Option<String>,
    pub previous: Option<thread::ThreadMessage>,
    pub pending: bool,
}

pub(super) async fn replied(conn: &mut SqliteConnection, mentor: i64) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT EXISTS (SELECT 1 FROM mentor_contact
           WHERE mentor_id = ? AND last_reply_at IS NOT NULL) AS "replied!: bool""#,
        mentor
    )
    .fetch_one(conn)
    .await
}

/// Call after linking incoming mail, in the same write transaction.
pub async fn cancel_replied(
    conn: &mut SqliteConnection,
    mentor: i64,
    now: i64,
) -> Result<(), sqlx::Error> {
    if replied(conn, mentor).await? {
        super::cancel(
            conn,
            super::CancelScope::FollowUps {
                mentor,
                parent: None,
            },
            "导师已回信",
            now,
        )
        .await?;
    }
    Ok(())
}

pub async fn eligibility(
    conn: &mut SqliteConnection,
    mentor: i64,
    source_task: Option<i64>,
) -> Result<Eligibility, sqlx::Error> {
    let row = sqlx::query!(
        r#"SELECT m.trashed_at,
             EXISTS (SELECT 1 FROM mentor_email WHERE mentor_id = m.id) AS "address!: bool",
             EXISTS (SELECT 1 FROM email WHERE mentor_id = m.id
               AND kind IN ('first', 'follow_up')
               AND state IN ('generating', 'generation_failed', 'review', 'approved',
                 'cloud', 'sending', 'maybe_sent', 'failed')
               AND NOT (task_id IS ?2 AND ?2 IS NOT NULL
                 AND state IN ('failed', 'generation_failed'))) AS "pending!: bool"
           FROM mentor m WHERE m.id = ?1"#,
        mentor,
        source_task,
    )
    .fetch_one(&mut *conn)
    .await?;
    let previous = thread::previous(conn, mentor, None, false).await?;
    let reason = if replied(conn, mentor).await? {
        Some("导师已回信，请到往来里回复")
    } else if previous.as_ref().is_some_and(|p| p.bounced) {
        Some("上一封被退回，请先核对邮箱")
    } else if previous.as_ref().is_some_and(|p| p.uncertain) {
        Some("上一封说不清发没发出，请先在往来里确认")
    } else if row.trashed_at.is_some() {
        Some("导师在回收站里")
    } else if !row.address {
        Some("导师尚未填写邮箱")
    } else if row.pending {
        Some("还有一封没发出的首封或追问")
    } else if previous.as_ref().is_none_or(|p| p.message_id.is_none()) {
        Some("上一封缺少邮件编号，无法在原线程追问")
    } else {
        None
    }
    .map(str::to_owned);
    Ok(Eligibility {
        reason,
        previous,
        pending: row.pending,
    })
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FollowUpTarget {
    pub message_id: Option<String>,
    pub subject: String,
    pub sent_at: i64,
    pub from_address: String,
    pub from_name: Option<String>,
}
impl From<thread::ThreadMessage> for FollowUpTarget {
    fn from(p: thread::ThreadMessage) -> Self {
        Self {
            message_id: p.message_id,
            subject: super::reply_subject(p.subject.as_deref().unwrap_or_default()),
            sent_at: p.sent_at,
            from_address: p.from_address,
            from_name: p.from_name,
        }
    }
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FollowUpDraft {
    pub id: i64,
    pub reply_to: String,
    pub body_md: Option<String>,
    pub attachments: Vec<i64>,
    pub target: Option<FollowUpTarget>,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FollowUpEntry {
    pub reason: Option<String>,
    pub pending: bool,
    pub previous: Option<FollowUpTarget>,
}
pub async fn entry(db: &SqlitePool, mentor: i64) -> Result<FollowUpEntry, sqlx::Error> {
    let mut tx = db.begin().await?;
    let e = eligibility(&mut tx, mentor, None).await?;
    Ok(FollowUpEntry {
        reason: e.reason,
        pending: e.pending,
        previous: e.previous.map(Into::into),
    })
}
pub async fn draft(db: &SqlitePool, mentor: i64) -> Result<Option<FollowUpDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    draft_on(&mut tx, mentor).await
}
async fn draft_on(
    conn: &mut SqliteConnection,
    mentor: i64,
) -> Result<Option<FollowUpDraft>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT id, reply_to AS "reply_to!", body_md FROM email
           WHERE mentor_id = ? AND kind = 'follow_up' AND task_id IS NULL AND state = 'review'"#,
        mentor
    )
    .fetch_optional(&mut *conn)
    .await?
    else {
        return Ok(None);
    };
    Ok(Some(FollowUpDraft {
        id: r.id,
        target: thread::previous(conn, mentor, Some(&r.reply_to), false)
            .await?
            .map(Into::into),
        reply_to: r.reply_to,
        body_md: r.body_md,
        attachments: attached(conn, r.id).await?,
    }))
}

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Db(sqlx::Error),
}
impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Self::Db(e)
    }
}

pub async fn create(db: &SqlitePool, mentor: i64, now: i64) -> Result<FollowUpDraft, Error> {
    let mut tx = db.begin().await?;
    sqlx::query!(
        "UPDATE mentor SET updated_at = updated_at WHERE id = ?",
        mentor
    )
    .execute(&mut *tx)
    .await?;
    let own = sqlx::query_scalar!(
        "SELECT id FROM email
         WHERE mentor_id = ? AND kind = 'follow_up' AND task_id IS NULL AND state = 'review'",
        mentor
    )
    .fetch_optional(&mut *tx)
    .await?;
    if own.is_none() {
        let e = eligibility(&mut tx, mentor, None).await?;
        if let Some(reason) = e.reason {
            return Err(Error::Invalid(reason));
        }
        let parent = e.previous.ok_or(sqlx::Error::RowNotFound)?;
        let subject = super::reply_subject(parent.subject.as_deref().unwrap_or_default());
        sqlx::query!(
            "INSERT INTO email (mentor_id, kind, reply_to, state, subject, created_at, updated_at)
             VALUES (?, 'follow_up', ?, 'review', ?, ?, ?)",
            mentor,
            parent.message_id,
            subject,
            now,
            now
        )
        .execute(&mut *tx)
        .await?;
    }
    let saved = draft_on(&mut tx, mentor)
        .await?
        .ok_or(sqlx::Error::RowNotFound)?;
    tx.commit().await?;
    Ok(saved)
}

pub async fn save(
    db: &SqlitePool,
    mentor: i64,
    id: i64,
    reply_to: &str,
    body: &str,
    files: &[i64],
    now: i64,
) -> Result<Option<FollowUpDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let changed = sqlx::query!(
        "UPDATE email SET body_md = ?, updated_at = ?
         WHERE id = ? AND mentor_id = ? AND reply_to = ? AND kind = 'follow_up'
           AND task_id IS NULL AND state = 'review'",
        body,
        now,
        id,
        mentor,
        reply_to
    )
    .execute(&mut *tx)
    .await?;
    if changed.rows_affected() == 0 {
        return Ok(None);
    }
    attach(&mut tx, id, files).await?;
    let saved = draft_on(&mut tx, mentor).await?;
    tx.commit().await?;
    Ok(saved)
}

pub async fn approve(
    db: &SqlitePool,
    mentor: i64,
    id: i64,
    reply_to: &str,
    approval: &Approval<'_>,
    now: i64,
) -> Result<Approved, sqlx::Error> {
    let mut tx = db.begin().await?;
    let changed = sqlx::query!(
        "UPDATE email SET updated_at = ?
         WHERE id = ? AND mentor_id = ? AND reply_to = ? AND kind = 'follow_up'
           AND task_id IS NULL AND state = 'review'
           AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)
           AND mentor_id IN (SELECT mentor_id FROM mentor_email)",
        now,
        id,
        mentor,
        reply_to
    )
    .execute(&mut *tx)
    .await?;
    if changed.rows_affected() == 0 {
        return Ok(Approved::Refused);
    }
    let Some(parent) = thread::previous(&mut tx, mentor, Some(reply_to), true).await? else {
        return Ok(Approved::ReplyGone);
    };
    if parent.bounced {
        return Ok(Approved::FollowUpParentBounced);
    }
    let frozen = thread::freeze(
        &mut tx,
        id,
        &parent,
        approval.html,
        approval.text,
        &chrono::Local,
    )
    .await?;
    let approval = Approval {
        subject: &frozen.subject,
        html: &frozen.html,
        text: &frozen.text,
        ..*approval
    };
    if !snapshot(&mut tx, id, &approval, now).await? {
        return Ok(Approved::MaterialGone);
    }
    tx.commit().await?;
    Ok(Approved::Yes)
}

pub async fn discard(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!(
        "UPDATE email SET state = 'canceled', cancel_reason = '放弃追问', updated_at = ?
         WHERE id = ? AND kind = 'follow_up' AND task_id IS NULL AND state = 'review'",
        now,
        id
    )
    .execute(db)
    .await?
    .rows_affected()
        == 1)
}
