//! Reply context, with the draft's fixed target ahead of the rest of the correspondence.

use sqlx::SqlitePool;

use crate::Letter;

#[derive(Debug)]
pub enum Error {
    NoTarget,
    Db(sqlx::Error),
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        Self::Db(error)
    }
}

pub struct Message {
    pub letter: Letter,
    pub from_mentor: bool,
}

/// A draft fixes the target even if the caller has selected a different message meanwhile.
pub async fn history(db: &SqlitePool, mentor: i64, reply_to: &str) -> Result<Vec<Message>, Error> {
    let mut tx = db.begin().await?;
    let target = sqlx::query_scalar!(
        r#"SELECT m.id FROM message m JOIN message_mentor mm ON mm.message_id = m.id
           WHERE mm.mentor_id = ?1 AND m.kind = 'reply' AND m.message_id IS NOT NULL
             AND m.message_id = coalesce(
               (SELECT reply_to FROM email
                WHERE mentor_id = ?1 AND kind = 'reply' AND state = 'review'), ?2)"#,
        mentor,
        reply_to,
    )
    .fetch_optional(&mut *tx)
    .await?
    .ok_or(Error::NoTarget)?;
    let rows = sqlx::query!(
        r#"SELECT coalesce(subject, '') AS "subject!: String",
             coalesce(body, '') AS "body!: String", from_mentor AS "from_mentor!: bool"
           FROM (
             SELECT subject, substr(body_md, 1, 10000) AS body, sent_at, id,
               1 AS source, 0 AS from_mentor, 0 AS target
             FROM email WHERE mentor_id = ?1 AND state IN ('sent', 'bounced')
             UNION ALL
             SELECT m.subject, substr(m.body_text, 1, 10000), m.sent_at, m.id,
               0, m.kind <> 'sent', m.id = ?2
             FROM message m JOIN message_mentor mm ON mm.message_id = m.id
             WHERE mm.mentor_id = ?1
           ) ORDER BY target DESC, sent_at DESC, source DESC, id DESC LIMIT 20"#,
        mentor,
        target,
    )
    .fetch_all(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(rows
        .into_iter()
        .map(|row| Message {
            letter: Letter {
                subject: row.subject,
                body_md: if row.from_mentor {
                    receive::quote::split(&row.body).0.to_owned()
                } else {
                    row.body
                },
            },
            from_mentor: row.from_mentor,
        })
        .collect())
}
