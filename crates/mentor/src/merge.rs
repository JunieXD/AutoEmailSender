//! Merging a mentor listed twice into the other (docs/data-model.md#合并导师).

use sqlx::SqlitePool;
use sqlx::types::Json;

use crate::{MatchResult, MentorFields, SaveError, detail, index, place, save_lists};

#[derive(Debug)]
pub enum MergeError {
    /// One of them is gone, in the trash, or both are the same.
    Gone,
    /// The mentor has this many emails that may still go out, which would go out under the other.
    Busy {
        name: String,
        count: i64,
    },
    Db(sqlx::Error),
}

impl From<sqlx::Error> for MergeError {
    fn from(e: sqlx::Error) -> Self {
        MergeError::Db(e)
    }
}

impl std::fmt::Display for MergeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MergeError::Gone => write!(f, "mentor gone"),
            MergeError::Busy { .. } => write!(f, "mentor has emails still to go out"),
            MergeError::Db(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for MergeError {}

/// Merges `other` into `keep`, which takes its profile, mail and the rest, and deletes it. `now` is
/// UTC milliseconds.
pub async fn merge(db: &SqlitePool, keep: i64, other: i64, now: i64) -> Result<(), MergeError> {
    let mut tx = db.begin().await?;
    let both = sqlx::query!(
        "UPDATE mentor SET updated_at = ? WHERE id IN (?, ?) AND trashed_at IS NULL",
        now,
        keep,
        other,
    )
    .execute(&mut *tx)
    .await?;
    if both.rows_affected() != 2 {
        return Err(MergeError::Gone);
    }
    let busy = sqlx::query!(
        r#"SELECT m.name, count(*) AS "count!: i64" FROM email e JOIN mentor m ON m.id = e.mentor_id
           WHERE e.mentor_id IN (?1, ?2)
             AND (e.state IN ('approved', 'cloud', 'sending', 'failed')
                  OR e.task_id IS NOT NULL AND e.state IN ('generating', 'generation_failed', 'review'))
           GROUP BY m.id ORDER BY m.id = ?1 DESC LIMIT 1"#,
        keep,
        other,
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(b) = busy {
        return Err(MergeError::Busy {
            name: b.name,
            count: b.count,
        });
    }
    let (Some(a), Some(b)) = (detail(&mut tx, keep).await?, detail(&mut tx, other).await?) else {
        return Err(MergeError::Gone);
    };

    let mut fields = MentorFields {
        name: a.name,
        aliases: a.aliases,
        emails: a.emails,
        affiliations: a.affiliations,
        homepages: a.homepages,
        research: a.research.or(b.research),
    };
    for alias in std::iter::once(b.name).chain(b.aliases) {
        if alias != fields.name && !fields.aliases.contains(&alias) {
            fields.aliases.push(alias);
        }
    }
    for address in b.emails {
        let at = place(&fields.emails, &address);
        fields.emails.insert(at, address);
    }
    for affiliation in b.affiliations {
        if !fields.affiliations.contains(&affiliation) {
            fields.affiliations.push(affiliation);
        }
    }
    for url in b.homepages {
        if !fields.homepages.contains(&url) {
            fields.homepages.push(url);
        }
    }
    let mut papers = a.papers;
    for paper in b.papers {
        if !papers.contains(&paper) {
            papers.push(paper);
        }
    }
    let papers = Json(papers);
    let note = match (a.note, b.note) {
        (Some(a), Some(b)) => Some(format!("{a}\n\n{b}")),
        (a, b) => a.or(b),
    };

    sqlx::query!(
        "UPDATE mentor_email SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    save_lists(&mut tx, keep, &fields)
        .await
        .map_err(|e| match e {
            SaveError::Db(e) => MergeError::Db(e),
            SaveError::EmailTaken { .. } => unreachable!("every address is the kept one's by now"),
        })?;
    index(&mut tx, keep, &fields).await?;
    sqlx::query!(
        "UPDATE mentor_detail SET research = ?, papers = ?, note = ? WHERE mentor_id = ?",
        fields.research,
        papers,
        note,
        keep,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE mentor SET source_url = coalesce(mentor.source_url, o.source_url),
           mailbox_id = coalesce(mentor.mailbox_id, o.mailbox_id)
         FROM mentor o WHERE mentor.id = ? AND o.id = ?",
        keep,
        other,
    )
    .execute(&mut *tx)
    .await?;
    let analyzed = |m: &MatchResult| matches!(m, MatchResult::Done { .. });
    if !analyzed(&a.analysis) && analyzed(&b.analysis) {
        sqlx::query!(
            "UPDATE mentor SET (match_state, match_score, matched_at, match_model, match_error) =
               (SELECT match_state, match_score, matched_at, match_model, match_error FROM mentor
                WHERE id = ?2)
             WHERE id = ?1",
            keep,
            other,
        )
        .execute(&mut *tx)
        .await?;
        sqlx::query!(
            "UPDATE mentor_detail SET (match_reason, match_directions, match_highlights, match_risks) =
               (SELECT match_reason, match_directions, match_highlights, match_risks
                FROM mentor_detail WHERE mentor_id = ?2)
             WHERE mentor_id = ?1",
            keep,
            other,
        )
        .execute(&mut *tx)
        .await?;
    }

    // A draft each can have only one: the kept mentor's stays.
    sqlx::query!(
        "DELETE FROM email
         WHERE mentor_id = ?2 AND state = 'review' AND task_id IS NULL AND kind IN ('first', 'reply')
           AND EXISTS (SELECT 1 FROM email k WHERE k.mentor_id = ?1 AND k.state = 'review'
                         AND k.task_id IS NULL AND k.kind = email.kind)",
        keep,
        other,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE email SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    // Mail both had stays once, with the kept mentor; the other's link goes with it.
    sqlx::query!(
        "UPDATE OR IGNORE message_mentor SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "INSERT OR IGNORE INTO mentor_tag (mentor_id, tag_id)
         SELECT ?, tag_id FROM mentor_tag WHERE mentor_id = ?",
        keep,
        other,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE mentor_library SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE library_update SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE todo SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "UPDATE candidate SET mentor_id = ? WHERE mentor_id = ?",
        keep,
        other
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!("DELETE FROM mentor_search WHERE rowid = ?", other)
        .execute(&mut *tx)
        .await?;
    sqlx::query!("DELETE FROM mentor WHERE id = ?", other)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
