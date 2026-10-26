//! The parent of a reply or follow-up, read again when approval freezes its thread.
use sqlx::SqliteConnection;

#[derive(Debug)]
pub struct ThreadMessage {
    pub message_id: Option<String>,
    pub subject: Option<String>,
    pub sent_at: i64,
    pub from_address: String,
    pub from_name: Option<String>,
    pub body_text: Option<String>,
    pub refs: Option<String>,
    pub bounced: bool,
    pub uncertain: bool,
}

/// The latest outgoing contact; missing Message-ID must not silently select an older letter.
pub async fn previous(
    conn: &mut SqliteConnection,
    mentor: i64,
    reply_to: Option<&str>,
    include_body: bool,
) -> Result<Option<ThreadMessage>, sqlx::Error> {
    sqlx::query_as!(
        ThreadMessage,
        r#"SELECT message_id, subject, sent_at AS "sent_at!",
            from_address AS "from_address!", from_name, body_text,
            refs, bounced AS "bounced!: bool", uncertain AS "uncertain!: bool"
        FROM (
            SELECT coalesce(?2, e.copy_message_id, e.message_id) AS message_id,
                e.subject, e.sent_at, e.from_address, b.sender_name AS from_name,
                CASE WHEN ?3 THEN e.text END AS body_text,
                CASE WHEN ?3 THEN e.refs END AS refs, e.state = 'bounced' AS bounced,
                e.state = 'maybe_sent' AS uncertain, e.id AS id, 1 AS source
            FROM email e
            LEFT JOIN mailbox b ON b.id = e.mailbox_id
            WHERE e.mentor_id = ?1 AND e.kind IN ('first', 'follow_up')
            AND e.state IN ('sent', 'bounced', 'maybe_sent')
            AND (?2 IS NULL OR e.message_id = ?2 OR e.copy_message_id = ?2)
            UNION ALL
            SELECT m.message_id, m.subject, m.sent_at, m.from_address, m.from_name,
                CASE WHEN ?3 THEN m.body_text END, CASE WHEN ?3 THEN m.refs END,
                mm.bounce_reason IS NOT NULL, 0, m.id, 0
            FROM message m
            JOIN message_mentor mm ON mm.message_id = m.id
            WHERE mm.mentor_id = ?1 AND m.kind = 'sent' AND (?2 IS NULL
            OR m.message_id = ?2)
        )
        ORDER BY sent_at DESC, source DESC, id DESC
        LIMIT 1"#,
        mentor,
        reply_to,
        include_body
    )
    .fetch_optional(conn)
    .await
}

impl ThreadMessage {
    pub fn attribution<T: chrono::TimeZone>(&self, zone: &T) -> Result<String, sqlx::Error>
    where
        T::Offset: std::fmt::Display,
    {
        let at = chrono::DateTime::from_timestamp_millis(self.sent_at)
            .ok_or_else(|| sqlx::Error::Decode("invalid message timestamp".into()))?;
        Ok(super::reply::attribution(
            &at.with_timezone(zone),
            self.from_name.as_deref(),
            &self.from_address,
        ))
    }
}

pub struct Frozen {
    pub subject: String,
    pub html: String,
    pub text: String,
}

pub async fn freeze<T: chrono::TimeZone>(
    conn: &mut SqliteConnection,
    id: i64,
    parent: &ThreadMessage,
    html: &str,
    text: &str,
    zone: &T,
) -> Result<Frozen, sqlx::Error>
where
    T::Offset: std::fmt::Display,
{
    let at = chrono::DateTime::from_timestamp_millis(parent.sent_at)
        .ok_or_else(|| sqlx::Error::Decode("invalid message timestamp".into()))?;
    let (html, text) = super::reply::quote(
        html,
        text,
        &at.with_timezone(zone),
        parent.from_name.as_deref(),
        &parent.from_address,
        parent.body_text.as_deref().unwrap_or_default(),
    );
    let refs = super::reply::references(
        parent.refs.as_deref(),
        parent.message_id.as_deref().unwrap_or_default(),
    );
    let subject = super::reply::reply_subject(parent.subject.as_deref().unwrap_or_default());
    sqlx::query!(
        "UPDATE email SET subject=?, refs=? WHERE id=?",
        subject,
        refs,
        id
    )
    .execute(conn)
    .await?;
    Ok(Frozen {
        subject,
        html,
        text,
    })
}
