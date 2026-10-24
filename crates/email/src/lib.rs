//! Emails the app writes and sends (docs/data-model.md#联系).

pub mod tasks;

use std::collections::HashMap;

mod reply;
pub use reply::*;

use serde::{Deserialize, Serialize};
use sqlx::types::Json;
use sqlx::{Connection, SqliteConnection, SqlitePool};
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
pub async fn attach(
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
pub async fn attached(conn: &mut SqliteConnection, email_id: i64) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT material_id AS "material_id!" FROM email_attachment
           WHERE email_id = ? AND material_id IS NOT NULL ORDER BY file_name"#,
        email_id
    )
    .fetch_all(conn)
    .await
}

/// The standalone draft an attachment picker belongs to.
#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum DraftKind {
    First,
    Reply,
}

/// Adds only this file to the current draft, replacing a same-named attachment. False when the
/// draft or material no longer exists.
pub async fn attach_draft_material(
    db: &SqlitePool,
    mentor_id: i64,
    kind: DraftKind,
    material_id: i64,
) -> Result<bool, sqlx::Error> {
    let kind = match kind {
        DraftKind::First => "first",
        DraftKind::Reply => "reply",
    };
    let done = sqlx::query!(
        "INSERT INTO email_attachment (email_id, material_id, file_name, size)
         SELECT e.id, m.id, m.file_name, m.size FROM email e CROSS JOIN material m
         WHERE e.mentor_id = ? AND e.kind = ? AND e.task_id IS NULL AND e.state = 'review'
           AND m.id = ?
         ON CONFLICT (email_id, file_name) DO UPDATE SET material_id = excluded.material_id,
           size = excluded.size",
        mentor_id,
        kind,
        material_id,
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
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
    ReplyGone,
    ReplyChanged,
    NoAddress,
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
        "UPDATE email SET updated_at = ?1
         WHERE mentor_id = ?2 AND kind = 'first' AND task_id IS NULL AND state = 'review'
         RETURNING id AS \"id!\"",
        now,
        mentor_id,
    )
    .fetch_optional(&mut *tx)
    .await?;
    let id = match drafted {
        Some(id) => id,
        None => match sqlx::query_scalar!(
            "INSERT INTO email (mentor_id, kind, state, created_at, updated_at)
             SELECT m.id, 'first', 'review', ?2, ?2 FROM mentor m
             WHERE m.id = ?1 AND m.trashed_at IS NULL AND NOT EXISTS (
               SELECT 1 FROM email e WHERE e.mentor_id = m.id AND e.kind = 'first'
                 AND e.task_id IS NULL AND e.state <> 'canceled')
             RETURNING id AS \"id!\"",
            mentor_id,
            now,
        )
        .fetch_optional(&mut *tx)
        .await?
        {
            Some(id) => id,
            None => return Ok(Approved::Refused),
        },
    };
    if !snapshot(&mut tx, id, approval, now).await? {
        return Ok(Approved::MaterialGone);
    }
    tx.commit().await?;
    Ok(Approved::Yes)
}

/// Freezes exactly the approved content and files for both first letters and replies.
async fn snapshot(
    conn: &mut SqliteConnection,
    id: i64,
    approval: &Approval<'_>,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let materials = Json(approval.attachments);
    let gone = sqlx::query_scalar!(
        r#"SELECT count(*) AS "gone!: i64" FROM json_each(?) j
           WHERE NOT EXISTS (SELECT 1 FROM material m WHERE m.id = j.value)"#,
        materials
    )
    .fetch_one(&mut *conn)
    .await?;
    if gone > 0 {
        return Ok(false);
    }
    sqlx::query!(
        "UPDATE email SET state = 'approved', subject = ?, body_md = ?, html = ?, text = ?,
           planned_at = ?, updated_at = ? WHERE id = ?",
        approval.subject,
        approval.body_md,
        approval.html,
        approval.text,
        now,
        now,
        id,
    )
    .execute(&mut *conn)
    .await?;
    attach(conn, id, approval.attachments).await?;
    Ok(true)
}

/// An email in the correspondence with a mentor, from approval on.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Email {
    pub id: i64,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<Attachment>,
    /// The bounced email this one sends again.
    pub resend_of: Option<i64>,
    /// Whether this bounced email has been sent again, as an email in any state.
    pub resent: bool,
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
        to_address: String,
        sent_at: i64,
        error: String,
    },
    Sent {
        from_address: String,
        to_address: String,
        sent_at: i64,
    },
    Bounced {
        from_address: String,
        to_address: String,
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
             resend_of, EXISTS (SELECT 1 FROM email r WHERE r.resend_of = email.id) AS "resent!: bool",
             from_address, to_address, sent_at, error
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
        let delivery = match (r.state, r.from_address, r.to_address, r.sent_at, r.error) {
            (C::Approved, ..) => Delivery::Approved,
            (C::Sending, ..) => Delivery::Sending,
            (C::MaybeSent, Some(from_address), Some(to_address), Some(sent_at), Some(error)) => {
                Delivery::MaybeSent {
                    from_address,
                    to_address,
                    sent_at,
                    error,
                }
            }
            (C::Sent, Some(from_address), Some(to_address), Some(sent_at), _) => Delivery::Sent {
                from_address,
                to_address,
                sent_at,
            },
            (C::Bounced, Some(from_address), Some(to_address), Some(sent_at), Some(error)) => {
                Delivery::Bounced {
                    from_address,
                    to_address,
                    sent_at,
                    error,
                }
            }
            (C::Failed, .., Some(error)) => Delivery::Failed { error },
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
            resend_of: r.resend_of,
            resent: r.resent,
            delivery,
        })
    })
    .collect()
}

/// Takes an email that has not gone out back to a draft. False when it is on its way out by now,
/// or sends a bounced one again: that is the letter as it went, not a draft.
/// Approved again, it is another letter, with a Message-ID of its own: mailboxes drop a second
/// email under the same one (docs/design.md#桌面端), and a copy of an earlier try is no proof the
/// new text went out.
pub async fn withdraw(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE email SET state = 'review', planned_at = NULL, mailbox_id = NULL, from_address = NULL,
           to_address = NULL, message_id = NULL, copy_message_id = NULL, sent_at = NULL, error = NULL,
           html = NULL, text = NULL, refs = NULL, updated_at = ?
         WHERE id = ? AND state IN ('approved', 'failed') AND resend_of IS NULL
           AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state <> 'stopped'))",
        now,
        id,
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// Sends a failed or maybe-sent email again, now, unless its mentor is in the trash. It keeps its
/// Message-ID, so that mailboxes that drop duplicates drop a second copy (docs/design.md#桌面端),
/// and where and when it went before, so that a copy of that turning up still settles it.
pub async fn retry(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE email SET state = 'approved', planned_at = ?1, error = NULL, updated_at = ?1
         WHERE id = ?2 AND state IN ('failed', 'maybe_sent')
           AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state <> 'stopped'))
           AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)",
        now,
        id,
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// Sends a bounced email again, now, as another email with the same content and files, to the
/// mentor's sending address by then; the bounced one keeps its bounce. Refused when it was sent
/// again already, or the mentor is in the trash.
pub async fn resend(db: &SqlitePool, id: i64, now: i64) -> Result<Approved, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(again) = sqlx::query_scalar!(
        "INSERT INTO email (mentor_id, task_id, kind, reply_to, resend_of, state, planned_at,
           created_at, updated_at, subject, body_md, html, text, refs)
         SELECT e.mentor_id, NULL, e.kind, e.reply_to, e.id, 'approved', ?2, ?2, ?2,
           e.subject, e.body_md, e.html, e.text, e.refs
         FROM email e JOIN mentor m ON m.id = e.mentor_id
         WHERE e.id = ?1 AND e.state = 'bounced' AND m.trashed_at IS NULL
         ON CONFLICT (resend_of) DO NOTHING
         RETURNING id AS \"id!\"",
        id,
        now,
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(Approved::Refused);
    };
    let materials = sqlx::query_scalar!(
        r#"SELECT material_id AS "material_id?" FROM email_attachment WHERE email_id = ?"#,
        id
    )
    .fetch_all(&mut *tx)
    .await?;
    let Some(materials) = materials.into_iter().collect::<Option<Vec<i64>>>() else {
        return Ok(Approved::MaterialGone);
    };
    attach(&mut tx, again, &materials).await?;
    tx.commit().await?;
    Ok(Approved::Yes)
}

/// Why an email the last run left being sent is only maybe sent.
pub const INTERRUPTED: &str = "发送途中应用退出，无法确定是否已发出。";

/// Why an email to a mentor with no address failed.
pub const NO_ADDRESS: &str = "导师尚未填写邮箱。在导师资料里填写后重试。";

/// An email that has been handed to SMTP, or may have been.
pub struct Handed {
    pub mentor_id: i64,
    /// None once the mailbox is deleted.
    pub mailbox_id: Option<i64>,
}

/// Marks the emails the last run left being sent as maybe sent, but those to a mentor with no
/// address, which cannot have gone, as failed.
pub async fn interrupted(
    conn: &mut SqliteConnection,
    now: i64,
) -> Result<Vec<Handed>, sqlx::Error> {
    sqlx::query!(
        "UPDATE email SET state = 'failed', error = ?, updated_at = ?
         WHERE state = 'sending' AND to_address IS NULL",
        NO_ADDRESS,
        now,
    )
    .execute(&mut *conn)
    .await?;
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
    /// The mentor's first address, which it goes to; None when they have none.
    pub to_address: Option<String>,
    pub message_id: String,
    pub reply_to: Option<String>,
    pub refs: Option<String>,
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
             to_address = (SELECT address FROM mentor_email WHERE mentor_id = email.mentor_id
               ORDER BY position LIMIT 1),
             message_id = coalesce(message_id, ?3), sent_at = ?4, updated_at = ?4
           WHERE id = ?5 AND state = 'approved'
             AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state = 'active' AND generation_paused_at IS NULL))
           RETURNING mentor_id, to_address, message_id AS "message_id!", reply_to, refs, subject AS "subject!",
             html AS "html!", text AS "text!""#,
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
        to_address: row.to_address,
        message_id: row.message_id,
        reply_to: row.reply_to,
        refs: row.refs,
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
    /// An email not yet known to have gone out, which the copy proves did.
    Confirmed,
}

/// Looks up the email the app sent with this Message-ID, or whose copy had it, and marks it sent if
/// it was not known to be. `recipients` are the copy's, in lowercase.
pub async fn sent_copy(
    conn: &mut SqliteConnection,
    message_id: &str,
    recipients: &[String],
    now: i64,
) -> Result<SentCopy, sqlx::Error> {
    let found = sqlx::query_scalar!(
        r#"SELECT id AS "id!" FROM email WHERE message_id = ?1 OR copy_message_id = ?1"#,
        message_id
    )
    .fetch_optional(&mut *conn)
    .await?;
    match found {
        Some(id) => copied(conn, id, message_id, recipients, now).await,
        None => Ok(SentCopy::Foreign),
    }
}

/// A copy, in a mailbox's sent folder, under a Message-ID the app did not give.
pub struct Copy<'a> {
    pub mailbox_id: i64,
    /// Its one recipient, in lowercase: the app sends each email to one mentor.
    pub recipient: &'a str,
    pub subject: &'a str,
    /// When the server got it, in UTC milliseconds.
    pub at: i64,
}

/// An email the copy may be of.
pub struct Candidate {
    pub id: i64,
    pub text: String,
    /// The names and sizes of the files it was approved with.
    pub attachments: Vec<(String, i64)>,
}

/// How far apart an email and a copy with another Message-ID may have gone out.
const COPY_WINDOW: i64 = 60 * 60 * 1000;

/// Some providers (QQ) keep the copy of an email under a Message-ID of their own. The emails it may
/// be a copy of, nearest in time first: sent, or tried, from the same mailbox about the same time
/// to the same address, with the same subject, and no copy turned up yet. Which one it is, if any,
/// the text and attachments tell.
pub async fn copy_candidates(
    conn: &mut SqliteConnection,
    copy: &Copy<'_>,
) -> Result<Vec<Candidate>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT id AS "id!", text AS "text!",
             (SELECT json_group_array(json_array(file_name, size)) FROM email_attachment
              WHERE email_id = email.id) AS "attachments!: Json<Vec<(String, i64)>>"
           FROM email
           WHERE mailbox_id = ?1
             AND state IN ('approved', 'sending', 'maybe_sent', 'sent', 'bounced')
             AND sent_at BETWEEN ?2 - ?3 AND ?2 + ?3 AND copy_message_id IS NULL
             AND subject = ?4 AND to_address = ?5
           ORDER BY abs(sent_at - ?2)"#,
        copy.mailbox_id,
        copy.at,
        COPY_WINDOW,
        copy.subject,
        copy.recipient,
    )
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| Candidate {
            id: r.id,
            text: r.text,
            attachments: r.attachments.0,
        })
        .collect())
}

/// Records the copy found of an email, and that the email went out if that was not known and the
/// copy is of its last try: maybe sent, still being sent, or waiting to be sent again to the same
/// address. SMTP answering later changes nothing then, as it only writes to an email still being
/// sent. A copy of an earlier try, to an address since changed, settles nothing.
pub async fn copied(
    conn: &mut SqliteConnection,
    id: i64,
    copy_message_id: &str,
    recipients: &[String],
    now: i64,
) -> Result<SentCopy, sqlx::Error> {
    let mut tx = conn.begin().await?;
    sqlx::query!(
        "UPDATE email SET copy_message_id = coalesce(copy_message_id, ?) WHERE id = ?",
        copy_message_id,
        id,
    )
    .execute(&mut *tx)
    .await?;
    let confirmed = sqlx::query!(
        "UPDATE email SET state = 'sent', error = NULL, updated_at = ?1
         WHERE id = ?2 AND to_address IN (SELECT value FROM json_each(?3))
           AND (state IN ('sending', 'maybe_sent')
             OR (state = 'approved' AND sent_at IS NOT NULL
               AND to_address = (SELECT address FROM mentor_email WHERE mentor_id = email.mentor_id
                 ORDER BY position LIMIT 1)))",
        now,
        id,
        Json(recipients),
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(if confirmed.rows_affected() == 1 {
        SentCopy::Confirmed
    } else {
        SentCopy::Own
    })
}

/// Marks the email that went out with this Message-ID, or whose copy had it, as bounced for this
/// reason, unless the bounce names `recipients` without the address it last went to: then it is of
/// an earlier try, to an address since changed; one being sent to no address cannot bounce. False
/// when it was marked already, or the app sent no such email.
pub async fn bounced(
    conn: &mut SqliteConnection,
    message_id: &str,
    recipients: &[String],
    reason: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let recipients = Json(recipients);
    // Sending too, as for a copy in the sent folder.
    let done = sqlx::query!(
        "UPDATE email SET state = 'bounced', error = ?1, updated_at = ?2
         WHERE (message_id = ?3 OR copy_message_id = ?3)
           AND state IN ('sending', 'maybe_sent', 'sent')
           AND to_address IS NOT NULL
           AND (json_array_length(?4) = 0 OR to_address IN (SELECT value FROM json_each(?4)))",
        reason,
        now,
        message_id,
        recipients,
    )
    .execute(conn)
    .await?;
    Ok(done.rows_affected() == 1)
}
