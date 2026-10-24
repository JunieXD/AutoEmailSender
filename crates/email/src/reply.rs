use chrono::{DateTime, Local, TimeZone};
use serde::Serialize;
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

use super::{Approval, Approved, attach, attached, snapshot};

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyTarget {
    pub id: i64,
    pub message_id: Option<String>,
    pub from_address: String,
    pub from_name: Option<String>,
    pub subject: Option<String>,
    pub sent_at: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyDraft {
    // Approval consumes this draft, so repeating it cannot send another email.
    pub id: i64,
    pub reply_to: String,
    pub body_md: Option<String>,
    pub attachments: Vec<i64>,
    pub target: Option<ReplyTarget>,
}

async fn target(
    conn: &mut SqliteConnection,
    mentor_id: i64,
    id: Option<i64>,
    message_id: Option<&str>,
) -> Result<Option<ReplyTarget>, sqlx::Error> {
    sqlx::query_as!(
        ReplyTarget,
        r#"SELECT m.id, m.message_id, m.from_address, m.from_name, m.subject, m.sent_at
           FROM message_mentor mm JOIN message m ON m.id = mm.message_id
           WHERE mm.mentor_id = ?1 AND m.kind = 'reply'
             AND (?2 IS NULL OR m.id = ?2) AND (?3 IS NULL OR m.message_id = ?3)
             AND (?2 IS NOT NULL OR m.message_id IS NOT NULL)
           ORDER BY m.sent_at DESC, m.id DESC LIMIT 1"#,
        mentor_id,
        id,
        message_id,
    )
    .fetch_optional(conn)
    .await
}

/// A selected reply from this mentor, or their latest non-automatic reply with a Message-ID.
pub async fn reply_target(
    db: &SqlitePool,
    mentor_id: i64,
    id: Option<i64>,
) -> Result<Option<ReplyTarget>, sqlx::Error> {
    target(&mut *db.acquire().await?, mentor_id, id, None).await
}

pub async fn reply_draft(
    db: &SqlitePool,
    mentor_id: i64,
) -> Result<Option<ReplyDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(row) = sqlx::query!(
        r#"SELECT id, reply_to AS "reply_to!", body_md FROM email
           WHERE mentor_id = ? AND kind = 'reply' AND state = 'review'"#,
        mentor_id
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(None);
    };
    Ok(Some(ReplyDraft {
        id: row.id,
        target: target(&mut tx, mentor_id, None, Some(&row.reply_to)).await?,
        reply_to: row.reply_to,
        body_md: row.body_md,
        attachments: attached(&mut tx, row.id).await?,
    }))
}

/// An empty reply with no files removes the draft. A missing target may still be saved for a
/// draft already pointing to it, so that deleting a mailbox never loses the user's writing.
pub async fn save_reply_draft(
    db: &SqlitePool,
    mentor_id: i64,
    reply_to: &str,
    body_md: &str,
    attachments: &[i64],
    now: i64,
) -> Result<Option<ReplyDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    if body_md.trim().is_empty() && attachments.is_empty() {
        sqlx::query!(
            "DELETE FROM email WHERE mentor_id = ? AND kind = 'reply' AND state = 'review'",
            mentor_id
        )
        .execute(&mut *tx)
        .await?;
    } else {
        let body = Some(body_md).filter(|b| !b.trim().is_empty());
        let row = sqlx::query!(
            "INSERT INTO email (mentor_id, kind, reply_to, state, body_md, created_at, updated_at)
             SELECT t.id, 'reply', ?2, 'review', ?3, ?4, ?4 FROM mentor t
             WHERE t.id = ?1 AND t.trashed_at IS NULL AND (
               EXISTS (SELECT 1 FROM message_mentor mm JOIN message m ON m.id = mm.message_id
                 WHERE mm.mentor_id = t.id AND m.kind = 'reply' AND m.message_id = ?2)
               OR EXISTS (SELECT 1 FROM email e WHERE e.mentor_id = t.id AND e.kind = 'reply'
                 AND e.state = 'review' AND e.reply_to = ?2))
             ON CONFLICT (mentor_id) WHERE kind = 'reply' AND state = 'review'
             DO UPDATE SET reply_to = ?2, body_md = ?3, updated_at = ?4
             RETURNING id AS \"id!\"",
            mentor_id,
            reply_to,
            body,
            now,
        )
        .fetch_optional(&mut *tx)
        .await?;
        let Some(row) = row else {
            return Ok(None);
        };
        attach(&mut tx, row.id, attachments).await?;
    }
    tx.commit().await?;
    reply_draft(db, mentor_id).await
}

/// Only the existing draft can be approved; another click with the same id finds it consumed.
pub async fn approve_reply(
    db: &SqlitePool,
    mentor_id: i64,
    draft_id: i64,
    reply_to: &str,
    approval: &Approval<'_>,
    now: i64,
) -> Result<Approved, sqlx::Error> {
    let mut tx = db.begin().await?;
    let updated = sqlx::query!(
        "UPDATE email SET updated_at = ?1
           WHERE id = ?2 AND mentor_id = ?3 AND kind = 'reply' AND state = 'review'
             AND reply_to = ?4
             AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)",
        now,
        draft_id,
        mentor_id,
        reply_to,
    )
    .execute(&mut *tx)
    .await?;
    if updated.rows_affected() == 0 {
        let changed = sqlx::query_scalar!(
            r#"SELECT EXISTS (SELECT 1 FROM email
               WHERE id = ?1 AND mentor_id = ?2 AND kind = 'reply' AND state = 'review'
                 AND reply_to <> ?3
                 AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)) AS "changed!: bool""#,
            draft_id,
            mentor_id,
            reply_to,
        )
        .fetch_one(&mut *tx)
        .await?;
        return Ok(if changed {
            Approved::ReplyChanged
        } else {
            Approved::Refused
        });
    }
    let row = sqlx::query!(
        r#"SELECT m.from_name, m.from_address, m.subject, m.sent_at, m.body_text, m.refs,
             (SELECT address FROM mentor_email WHERE mentor_id = t.id ORDER BY position LIMIT 1) AS address
           FROM mentor t JOIN message_mentor mm ON mm.mentor_id = t.id
           JOIN message m ON m.id = mm.message_id
           WHERE t.id = ?1 AND m.message_id = ?2 AND m.kind = 'reply'"#,
        mentor_id,
        reply_to,
    )
    .fetch_optional(&mut *tx)
    .await?;
    let Some(row) = row else {
        return Ok(Approved::ReplyGone);
    };
    if row.address.is_none() {
        return Ok(Approved::NoAddress);
    }
    let subject = reply_subject(row.subject.as_deref().unwrap_or_default());
    let at = DateTime::from_timestamp_millis(row.sent_at)
        .ok_or_else(|| sqlx::Error::Decode("invalid message timestamp".into()))?;
    let (html, text) = quote(
        approval.html,
        approval.text,
        &at.with_timezone(&Local),
        row.from_name.as_deref(),
        &row.from_address,
        row.body_text.as_deref().unwrap_or_default(),
    );
    let frozen = Approval {
        subject: &subject,
        html: &html,
        text: &text,
        ..*approval
    };
    if !snapshot(&mut tx, draft_id, &frozen, now).await? {
        return Ok(Approved::MaterialGone);
    }
    let refs = references(row.refs.as_deref(), reply_to);
    sqlx::query!("UPDATE email SET refs = ? WHERE id = ?", refs, draft_id,)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(Approved::Yes)
}

fn reply_subject(subject: &str) -> String {
    let lower = subject.to_lowercase();
    if ["re:", "re：", "回复:", "回复：", "答复:", "答复："]
        .iter()
        .any(|p| lower.starts_with(p))
    {
        subject.to_owned()
    } else {
        format!("Re: {subject}")
    }
}

fn quote<T: TimeZone>(
    html: &str,
    text: &str,
    at: &DateTime<T>,
    name: Option<&str>,
    address: &str,
    body: &str,
) -> (String, String)
where
    T::Offset: std::fmt::Display,
{
    let sender = name.map_or_else(|| address.to_owned(), |n| format!("{n} <{address}>"));
    let attribution = format!("在 {}，{sender} 写道：", at.format("%Y年%-m月%-d日 %H:%M"));
    let quoted = body
        .replace("\r\n", "\n")
        .split('\n')
        .map(|l| format!("> {l}"))
        .collect::<Vec<_>>()
        .join("\n");
    (
        format!(
            "{html}<p>{}</p><blockquote style=\"border-left:2px solid #ccc;margin:0;padding-left:1em;white-space:pre-wrap\">{}</blockquote>",
            escape(&attribution),
            escape(body)
        ),
        format!("{text}\n\n{attribution}\n{quoted}"),
    )
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

/// RFC 5322 §3.6.4: the parent's References followed by its Message-ID. Keep the root and the
/// nearest ancestors within a conservative header size; the mail builder folds the lines.
fn references(parent: Option<&str>, id: &str) -> String {
    let mut ids: Vec<_> = parent.unwrap_or_default().split_whitespace().collect();
    ids.push(id);
    while ids.len() > 2 && ids.iter().map(|id| id.len() + 1).sum::<usize>() > 900 {
        ids.remove(1);
    }
    ids.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reply_subject_keeps_existing_prefixes() {
        for subject in [
            "Re: 申请",
            "RE：申请",
            "回复: 申请",
            "回复：申请",
            "答复：申请",
            "rE: 申请",
        ] {
            assert_eq!(reply_subject(subject), subject);
        }
        assert_eq!(reply_subject("申请"), "Re: 申请");
    }

    #[test]
    fn quote_uses_local_time_and_escapes_the_untrusted_message() {
        let at = DateTime::parse_from_rfc3339("2026-10-24T14:00:00+08:00").unwrap();
        let (html, text) = quote(
            "<p>谢谢。</p>",
            "谢谢。",
            &at,
            Some("王伟"),
            "wang@pku.edu.cn",
            "收到。\n<script>x</script>",
        );
        assert_eq!(
            text,
            "谢谢。\n\n在 2026年10月24日 14:00，王伟 <wang@pku.edu.cn> 写道：\n> 收到。\n> <script>x</script>"
        );
        assert!(html.contains("王伟 &lt;wang@pku.edu.cn&gt; 写道："));
        assert!(html.contains("<blockquote style=\"border-left:"));
        assert!(html.contains("&lt;script&gt;x&lt;/script&gt;"));
        let (_, text) = quote("", "", &at, None, "wang@pku.edu.cn", "");
        assert!(text.contains("，wang@pku.edu.cn 写道："));
    }

    #[test]
    fn long_references_keep_the_root_and_nearest_ancestors() {
        let chain = (0..100)
            .map(|i| format!("<{i}@example.edu>"))
            .collect::<Vec<_>>()
            .join(" ");
        let refs = references(Some(&chain), "<parent@example.edu>");
        assert!(refs.starts_with("<0@example.edu> "));
        assert!(refs.ends_with("<99@example.edu> <parent@example.edu>"));
        assert!(refs.len() < 900);
    }
}
