//! The latest correspondence with each mentor, with a filter for new replies.

use std::num::NonZeroU32;

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

use crate::{PAGE_SIZE, offset};

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ConversationQuery {
    pub unread: bool,
    pub page: NonZeroU32,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Conversation {
    pub mentor_id: i64,
    pub name: String,
    pub subject: Option<String>,
    pub at: i64,
    pub unread: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ConversationPage {
    pub conversations: Vec<Conversation>,
    pub total: i64,
}

pub async fn list(
    db: &SqlitePool,
    query: &ConversationQuery,
) -> Result<ConversationPage, sqlx::Error> {
    let mut tx = db.begin().await?;
    let conversations = sqlx::query_as!(
        Conversation,
        r#"SELECT c.mentor_id AS "mentor_id!", c.name AS "name!", c.at AS "at!: i64", c.unread AS "unread!",
             (SELECT subject FROM (
                SELECT subject, sent_at FROM email
                WHERE mentor_id = c.mentor_id AND state IN ('maybe_sent', 'sent', 'bounced')
                UNION ALL
                SELECT m.subject, m.sent_at FROM message m
                JOIN message_mentor mm ON mm.message_id = m.id
                WHERE mm.mentor_id = c.mentor_id AND m.kind <> 'auto_reply'
              ) ORDER BY sent_at DESC LIMIT 1) AS "subject?: String"
           FROM (
             SELECT mc.mentor_id, m.name, mc.unread,
               max(coalesce(mc.last_out_at, 0), coalesce(mc.last_reply_at, 0)) AS at
             FROM mentor_contact mc JOIN mentor m ON m.id = mc.mentor_id
             WHERE m.trashed_at IS NULL AND (NOT ?1 OR mc.unread > 0)
             ORDER BY at DESC, mc.mentor_id DESC LIMIT ?2 OFFSET ?3
           ) c ORDER BY c.at DESC, c.mentor_id DESC"#,
        query.unread,
        PAGE_SIZE,
        offset(query.page),
    )
    .fetch_all(&mut *tx)
    .await?;
    let total = sqlx::query_scalar!(
        r#"SELECT count(*) AS "n!: i64"
           FROM mentor_contact c JOIN mentor m ON m.id = c.mentor_id
           WHERE m.trashed_at IS NULL AND (NOT ?1 OR c.unread > 0)"#,
        query.unread,
    )
    .fetch_one(&mut *tx)
    .await?;
    Ok(ConversationPage {
        conversations,
        total,
    })
}

pub async fn unread_count(db: &SqlitePool) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT coalesce(sum(c.unread), 0) AS "n!: i64"
           FROM mentor_contact c JOIN mentor m ON m.id = c.mentor_id
           WHERE m.trashed_at IS NULL"#,
    )
    .fetch_one(db)
    .await
}
