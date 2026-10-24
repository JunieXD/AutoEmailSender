//! Emails the app writes and sends (docs/data-model.md#联系).

use std::collections::HashMap;

use serde::Serialize;
use sqlx::types::Json;
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

/// A first email being written on the mentor's page, outside any task.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FirstDraft {
    pub subject: Option<String>,
    pub body_md: Option<String>,
    /// The materials attached, by id.
    pub attachments: Vec<i64>,
}

pub async fn first_draft(
    db: &SqlitePool,
    mentor_id: i64,
) -> Result<Option<FirstDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(row) = sqlx::query!(
        "SELECT id, subject, body_md FROM email
         WHERE mentor_id = ? AND kind = 'first' AND task_id IS NULL AND state = 'review'",
        mentor_id
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(None);
    };
    Ok(Some(FirstDraft {
        subject: row.subject,
        body_md: row.body_md,
        attachments: attached(&mut tx, row.id).await?,
    }))
}

/// Saves the mentor's first draft and returns it as saved: none when both parts are blank, or when
/// the mentor has been moved to the trash, which canceled the draft. Attachments alone make no
/// draft.
pub async fn save_first_draft(
    db: &SqlitePool,
    mentor_id: i64,
    subject: &str,
    body_md: &str,
    attachments: &[i64],
    now: i64,
) -> Result<Option<FirstDraft>, sqlx::Error> {
    let subject = Some(subject.trim()).filter(|s| !s.is_empty());
    let body_md = Some(body_md).filter(|b| !b.trim().is_empty());
    if subject.is_none() && body_md.is_none() {
        sqlx::query!(
            "DELETE FROM email
             WHERE mentor_id = ? AND kind = 'first' AND task_id IS NULL AND state = 'review'",
            mentor_id
        )
        .execute(db)
        .await?;
        return Ok(None);
    }
    let mut tx = db.begin().await?;
    let Some(row) = sqlx::query!(
        "INSERT INTO email (mentor_id, kind, state, created_at, updated_at, subject, body_md)
         SELECT id, 'first', 'review', ?2, ?2, ?3, ?4 FROM mentor WHERE id = ?1 AND trashed_at IS NULL
         ON CONFLICT (mentor_id) WHERE kind = 'first' AND task_id IS NULL AND state = 'review'
         DO UPDATE SET subject = ?3, body_md = ?4, updated_at = ?2
         RETURNING id AS \"id!\", subject, body_md",
        mentor_id,
        now,
        subject,
        body_md,
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(None);
    };
    attach(&mut tx, row.id, attachments).await?;
    let attachments = attached(&mut tx, row.id).await?;
    tx.commit().await?;
    Ok(Some(FirstDraft {
        subject: row.subject,
        body_md: row.body_md,
        attachments,
    }))
}

/// Attaches these materials, named as their files are now, in place of what the email had. A
/// material deleted meanwhile is left out, and of two files with one name, the first.
async fn attach(
    conn: &mut SqliteConnection,
    email_id: i64,
    materials: &[i64],
) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM email_attachment WHERE email_id = ?", email_id)
        .execute(&mut *conn)
        .await?;
    let materials = Json(materials);
    // "WHERE true" tells SQLite that ON CONFLICT is not part of the join.
    sqlx::query!(
        "INSERT INTO email_attachment (email_id, material_id, file_name, size)
         SELECT ?1, m.id, m.file_name, m.size FROM json_each(?2) j JOIN material m ON m.id = j.value
         WHERE true ORDER BY j.key
         ON CONFLICT DO NOTHING",
        email_id,
        materials,
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// The materials attached to an email, leaving out any deleted since.
async fn attached(conn: &mut SqliteConnection, email_id: i64) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT material_id AS "material_id!" FROM email_attachment
           WHERE email_id = ? AND material_id IS NOT NULL ORDER BY file_name"#,
        email_id
    )
    .fetch_all(conn)
    .await
}

/// A letter the user approved, with the HTML and plain text the interface rendered from it.
pub struct Approval<'a> {
    pub subject: &'a str,
    pub body_md: &'a str,
    pub html: &'a str,
    pub text: &'a str,
    /// Materials, by id.
    pub attachments: &'a [i64],
}

/// What came of approving a draft.
#[derive(Debug, PartialEq, Eq)]
pub enum Approved {
    Yes,
    /// The mentor has a first email already, as when this one was approved a moment ago, or is in
    /// the trash.
    Refused,
    /// A material attached has been deleted since, so the email would not go as approved.
    MaterialGone,
}

/// Approves the mentor's first email with this content, to be sent now: the draft, or else a new
/// email, as when the letter is the default template unchanged.
pub async fn approve_first(
    db: &SqlitePool,
    mentor_id: i64,
    approval: &Approval<'_>,
    now: i64,
) -> Result<Approved, sqlx::Error> {
    let mut tx = db.begin().await?;
    let drafted = sqlx::query_scalar!(
        "UPDATE email SET state = 'approved', subject = ?1, body_md = ?2, html = ?3, text = ?4,
           planned_at = ?5, updated_at = ?5
         WHERE mentor_id = ?6 AND kind = 'first' AND task_id IS NULL AND state = 'review'
         RETURNING id AS \"id!\"",
        approval.subject,
        approval.body_md,
        approval.html,
        approval.text,
        now,
        mentor_id,
    )
    .fetch_optional(&mut *tx)
    .await?;
    let id = match drafted {
        Some(id) => id,
        // Only while the mentor has no first email: the one approved by a click repeated stops a
        // second.
        None => match sqlx::query_scalar!(
            "INSERT INTO email (mentor_id, kind, state, planned_at, created_at, updated_at,
               subject, body_md, html, text)
             SELECT m.id, 'first', 'approved', ?2, ?2, ?2, ?3, ?4, ?5, ?6 FROM mentor m
             WHERE m.id = ?1 AND m.trashed_at IS NULL AND NOT EXISTS (
               SELECT 1 FROM email e WHERE e.mentor_id = m.id AND e.kind = 'first'
                 AND e.task_id IS NULL AND e.state <> 'canceled')
             RETURNING id AS \"id!\"",
            mentor_id,
            now,
            approval.subject,
            approval.body_md,
            approval.html,
            approval.text,
        )
        .fetch_optional(&mut *tx)
        .await?
        {
            Some(id) => id,
            None => return Ok(Approved::Refused),
        },
    };
    let materials = Json(approval.attachments);
    let gone = sqlx::query_scalar!(
        r#"SELECT count(*) AS "gone!: i64" FROM json_each(?) j
           WHERE NOT EXISTS (SELECT 1 FROM material m WHERE m.id = j.value)"#,
        materials
    )
    .fetch_one(&mut *tx)
    .await?;
    if gone > 0 {
        return Ok(Approved::MaterialGone);
    }
    attach(&mut tx, id, approval.attachments).await?;
    tx.commit().await?;
    Ok(Approved::Yes)
}

/// An email in the correspondence with a mentor, from approval on.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Email {
    pub id: i64,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<Attachment>,
    #[serde(flatten)]
    pub delivery: Delivery,
}

/// A file an email went out with, as it was named then.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Attachment {
    pub file_name: String,
    pub size: i64,
}

/// Where an email is on its way out (docs/data-model.md#邮件状态). Times are UTC milliseconds.
#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(
    tag = "state",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum Delivery {
    Approved,
    Sending,
    MaybeSent {
        from_address: String,
        sent_at: i64,
        error: String,
    },
    Sent {
        from_address: String,
        sent_at: i64,
    },
    Bounced {
        from_address: String,
        sent_at: i64,
        error: String,
    },
    Failed {
        error: String,
    },
}

#[derive(Debug, Clone, Copy, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
enum DeliveryColumn {
    Approved,
    Sending,
    MaybeSent,
    Sent,
    Bounced,
    Failed,
}

/// The mentor's emails that have been approved, oldest first.
pub async fn list(db: &SqlitePool, mentor_id: i64) -> Result<Vec<Email>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let mut attachments: HashMap<i64, Vec<Attachment>> = HashMap::new();
    for a in sqlx::query!(
        r#"SELECT a.email_id, a.file_name AS "file_name!", a.size FROM email_attachment a
           JOIN email e ON e.id = a.email_id WHERE e.mentor_id = ? ORDER BY a.file_name"#,
        mentor_id
    )
    .fetch_all(&mut *tx)
    .await?
    {
        attachments.entry(a.email_id).or_default().push(Attachment {
            file_name: a.file_name,
            size: a.size,
        });
    }
    sqlx::query!(
        r#"SELECT id, state AS "state: DeliveryColumn", subject AS "subject!", body_md AS "body_md!",
             from_address, sent_at, error
           FROM email
           WHERE mentor_id = ? AND state IN ('approved', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed')
           ORDER BY id"#,
        mentor_id
    )
    .fetch_all(&mut *tx)
    .await?
    .into_iter()
    .map(|r| {
        use DeliveryColumn as C;
        let delivery = match (r.state, r.from_address, r.sent_at, r.error) {
            (C::Approved, ..) => Delivery::Approved,
            (C::Sending, ..) => Delivery::Sending,
            (C::MaybeSent, Some(from_address), Some(sent_at), Some(error)) => Delivery::MaybeSent {
                from_address,
                sent_at,
                error,
            },
            (C::Sent, Some(from_address), Some(sent_at), _) => Delivery::Sent {
                from_address,
                sent_at,
            },
            (C::Bounced, Some(from_address), Some(sent_at), Some(error)) => Delivery::Bounced {
                from_address,
                sent_at,
                error,
            },
            (C::Failed, _, _, Some(error)) => Delivery::Failed { error },
            _ => {
                return Err(sqlx::Error::Decode(
                    format!("email {} has inconsistent delivery columns", r.id).into(),
                ));
            }
        };
        Ok(Email {
            id: r.id,
            subject: r.subject,
            body_md: r.body_md,
            attachments: attachments.remove(&r.id).unwrap_or_default(),
            delivery,
        })
    })
    .collect()
}

/// Takes an email that has not gone out back to a draft. False when it is on its way out by now.
pub async fn withdraw(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE email SET state = 'review', planned_at = NULL, mailbox_id = NULL, from_address = NULL,
           sent_at = NULL, error = NULL, html = NULL, text = NULL, updated_at = ?
         WHERE id = ? AND state IN ('approved', 'failed')",
        now,
        id,
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// Sends a failed or maybe-sent email again, now, unless its mentor is in the trash. It keeps its
/// Message-ID, so that mailboxes that drop duplicates drop a second copy (docs/design.md#桌面端).
pub async fn retry(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE email SET state = 'approved', planned_at = ?1, mailbox_id = NULL, from_address = NULL,
           sent_at = NULL, error = NULL, updated_at = ?1
         WHERE id = ?2 AND state IN ('failed', 'maybe_sent')
           AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)",
        now,
        id,
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// Why an email the last run left being sent is only maybe sent.
pub const INTERRUPTED: &str = "发送途中应用退出，无法确定是否已发出。";

/// An email that has been handed to SMTP, or may have been.
pub struct Handed {
    pub mentor_id: i64,
    /// None once the mailbox is deleted.
    pub mailbox_id: Option<i64>,
}

/// Marks the emails the last run left being sent as maybe sent.
pub async fn interrupted(
    conn: &mut SqliteConnection,
    now: i64,
) -> Result<Vec<Handed>, sqlx::Error> {
    sqlx::query_as!(
        Handed,
        "UPDATE email SET state = 'maybe_sent', error = ?, updated_at = ? WHERE state = 'sending'
         RETURNING mentor_id, mailbox_id",
        INTERRUPTED,
        now,
    )
    .fetch_all(conn)
    .await
}

/// What was approved, as an email is marked as being handed over.
pub struct Handover {
    pub mentor_id: i64,
    pub message_id: String,
    pub subject: String,
    pub html: String,
    pub text: String,
    pub attachments: Vec<Attached>,
}

/// A material to attach, named as the user approved it.
pub struct Attached {
    pub file_name: String,
    /// Never null here: material::delete keeps a material that an email still to go out uses.
    pub material_id: i64,
}

/// Marks an approved email as being handed to SMTP from this mailbox, and returns what was
/// approved: read together, so that what goes out is the latest approval. None when it is no longer
/// approved, as when it was taken back or its mentor moved to the trash meanwhile. An email that
/// already has a Message-ID keeps it.
pub async fn start_sending(
    conn: &mut SqliteConnection,
    id: i64,
    mailbox_id: i64,
    from_address: &str,
    message_id: &str,
    now: i64,
) -> Result<Option<Handover>, sqlx::Error> {
    let Some(row) = sqlx::query!(
        r#"UPDATE email SET state = 'sending', mailbox_id = ?1, from_address = ?2,
             message_id = coalesce(message_id, ?3), sent_at = ?4, updated_at = ?4
           WHERE id = ?5 AND state = 'approved'
           RETURNING mentor_id, message_id AS "message_id!", subject AS "subject!", html AS "html!",
             text AS "text!""#,
        mailbox_id,
        from_address,
        message_id,
        now,
        id,
    )
    .fetch_optional(&mut *conn)
    .await?
    else {
        return Ok(None);
    };
    let attachments = sqlx::query_as!(
        Attached,
        r#"SELECT file_name AS "file_name!", material_id AS "material_id!"
           FROM email_attachment WHERE email_id = ?"#,
        id
    )
    .fetch_all(conn)
    .await?;
    Ok(Some(Handover {
        mentor_id: row.mentor_id,
        message_id: row.message_id,
        subject: row.subject,
        html: row.html,
        text: row.text,
        attachments,
    }))
}

pub async fn sent(conn: &mut SqliteConnection, id: i64, now: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE email SET state = 'sent', sent_at = ?1, updated_at = ?1
         WHERE id = ?2 AND state = 'sending'",
        now,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

pub async fn maybe_sent(
    conn: &mut SqliteConnection,
    id: i64,
    error: &str,
    now: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE email SET state = 'maybe_sent', error = ?, updated_at = ?
         WHERE id = ? AND state = 'sending'",
        error,
        now,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// Records why an email did not go out, whether it was turned down or never handed over.
pub async fn failed(
    conn: &mut SqliteConnection,
    id: i64,
    error: &str,
    now: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE email SET state = 'failed', error = ?, updated_at = ?
         WHERE id = ? AND state IN ('approved', 'sending')",
        error,
        now,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// What a copy of mail in a mailbox's sent folder tells about the app's emails.
#[derive(Debug, PartialEq, Eq)]
pub enum SentCopy {
    /// Not an email the app sent.
    Foreign,
    Own,
    /// An email only maybe sent, which the copy proves went out.
    Confirmed,
}

/// Looks up the email the app sent with this Message-ID, and marks it sent if it was only maybe
/// sent.
pub async fn sent_copy(
    conn: &mut SqliteConnection,
    message_id: &str,
    now: i64,
) -> Result<SentCopy, sqlx::Error> {
    let maybe_sent = sqlx::query_scalar!(
        r#"SELECT state = 'maybe_sent' AS "maybe_sent!: bool" FROM email WHERE message_id = ?"#,
        message_id
    )
    .fetch_optional(&mut *conn)
    .await?;
    match maybe_sent {
        None => Ok(SentCopy::Foreign),
        Some(false) => Ok(SentCopy::Own),
        Some(true) => {
            sqlx::query!(
                "UPDATE email SET state = 'sent', error = NULL, updated_at = ?
                 WHERE message_id = ? AND state = 'maybe_sent'",
                now,
                message_id,
            )
            .execute(conn)
            .await?;
            Ok(SentCopy::Confirmed)
        }
    }
}

/// Marks the email that went out with this Message-ID as bounced, for this reason. False when the
/// app sent no such email.
pub async fn bounced(
    conn: &mut SqliteConnection,
    message_id: &str,
    reason: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE email SET state = 'bounced', error = ?, updated_at = ?
         WHERE message_id = ? AND state IN ('maybe_sent', 'sent')",
        reason,
        now,
        message_id,
    )
    .execute(conn)
    .await?;
    Ok(done.rows_affected() == 1)
}
