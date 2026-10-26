//! The shared, limited material for automatic reply processing.

use serde::Serialize;
use sqlx::{SqlitePool, types::Json};

#[derive(Serialize)]
struct Letter {
    subject: String,
    text: String,
}

#[derive(Serialize)]
struct Input {
    reply: Letter,
    student: Option<Letter>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reply_date: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reply_weekday: Option<String>,
}

fn take(text: &str, remaining: &mut usize) -> String {
    let result: String = text.chars().take(*remaining).collect();
    *remaining -= result.chars().count();
    result
}

pub async fn input(
    db: &SqlitePool,
    id: i64,
    todos: bool,
) -> Result<Option<(String, Option<i64>, Option<String>)>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(reply) = sqlx::query!(
        "SELECT subject, body_text, in_reply_to, refs, sent_at FROM message WHERE id = ? AND kind = 'reply'",
        id
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(None);
    };
    let mut ids: Vec<&str> = reply.in_reply_to.iter().map(String::as_str).collect();
    ids.extend(
        reply
            .refs
            .as_deref()
            .unwrap_or_default()
            .split_whitespace()
            .rev(),
    );
    let parent = sqlx::query!(r#"SELECT email_id AS "email_id?: i64", subject AS "subject?: String", body AS "body?: String" FROM (
        SELECT e.id AS email_id, e.subject, e.body_md AS body, r.key AS priority, 1 AS source
        FROM json_each(?1) r JOIN email e ON e.message_id = r.value OR e.copy_message_id = r.value
        WHERE e.sent_at IS NOT NULL
        UNION ALL
        SELECT NULL, m.subject, m.body_text, r.key, 0
        FROM json_each(?1) r JOIN message m ON m.message_id = r.value WHERE m.kind = 'sent'
        ) ORDER BY priority, source DESC LIMIT 1"#, Json(ids))
        .fetch_optional(&mut *tx).await?;
    let (reply_date, reply_weekday) = if todos {
        let (date, weekday) = email::todos::reply_date(reply.sent_at)?;
        (Some(date), Some(weekday))
    } else {
        (None, None)
    };
    let mut remaining = 6000;
    let reply = Letter {
        subject: take(reply.subject.as_deref().unwrap_or_default(), &mut remaining),
        text: take(
            &super::quote::for_ai(reply.body_text.as_deref().unwrap_or_default()),
            &mut remaining,
        ),
    };
    let email_id = parent.as_ref().and_then(|p| p.email_id);
    let student = parent.map(|p| {
        let body = p.body.unwrap_or_default();
        // Application drafts already exclude the quote appended when sending.
        let text = if p.email_id.is_some() {
            body
        } else {
            super::quote::for_ai(&body)
        };
        Letter {
            subject: take(p.subject.as_deref().unwrap_or_default(), &mut remaining),
            text: take(&text, &mut remaining),
        }
    });
    // All fields are strings; serializing this closed shape is infallible.
    let message = serde_json::to_string(&Input {
        reply,
        student,
        reply_date: reply_date.clone(),
        reply_weekday,
    })
    .unwrap_or_else(|_| unreachable!("string fields"));
    Ok(Some((message, email_id, reply_date)))
}
