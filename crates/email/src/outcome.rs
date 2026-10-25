//! Reply results and the persisted work that has not produced a result yet.

use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool};
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum ReplyOutcome {
    Interested,
    NoSlot,
    Declined,
    Unclear,
}

#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum OutcomeMarking {
    Marking,
    Failed { reason: String },
}

#[derive(Debug, Serialize, TS)]
pub struct ReplyProcessing {
    pub remaining: i64,
    pub failed: i64,
    pub pause: Option<ReplyProcessingPause>,
}

#[derive(Debug, Serialize, TS)]
pub struct ReplyProcessingPause {
    pub at: i64,
    pub reason: String,
}

pub async fn enabled(db: &SqlitePool) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT ai_reply_outcome AS "enabled!: bool" FROM profile"#)
        .fetch_one(db)
        .await
}

/// Called in the receiving transaction, including when an older message gains a mentor link.
pub async fn enqueue(db: &mut SqliteConnection, id: i64, now: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "INSERT INTO reply_processing (message_id, action, queued_at, state)
        SELECT id, 'outcome', ?2, 'queued' FROM message
        WHERE id = ?1 AND kind = 'reply' AND outcome IS NULL
          AND (SELECT ai_reply_outcome FROM profile)
        ON CONFLICT DO NOTHING",
        id,
        now
    )
    .execute(db)
    .await?;
    Ok(())
}

pub async fn set_enabled(db: &SqlitePool, enabled: bool, now: i64) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE profile SET ai_reply_outcome = ?", enabled)
        .execute(&mut *tx)
        .await?;
    if enabled {
        sqlx::query!(
            "INSERT INTO reply_processing (message_id, action, queued_at, state)
            SELECT id, 'outcome', ?, 'queued' FROM message WHERE kind = 'reply' AND outcome IS NULL
            ON CONFLICT DO NOTHING",
            now
        )
        .execute(&mut *tx)
        .await?;
    } else {
        sqlx::query!("DELETE FROM reply_processing WHERE action = 'outcome'")
            .execute(&mut *tx)
            .await?;
        sqlx::query!("UPDATE profile SET reply_processing_paused_at = NULL, reply_processing_pause_reason = NULL")
            .execute(&mut *tx).await?;
    }
    tx.commit().await
}

/// False for anything other than an existing mentor reply.
pub async fn set(db: &SqlitePool, id: i64, outcome: ReplyOutcome) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let updated = sqlx::query!(
        "UPDATE message SET outcome = ? WHERE id = ? AND kind = 'reply'",
        outcome,
        id
    )
    .execute(&mut *tx)
    .await?;
    if updated.rows_affected() == 0 {
        return Ok(false);
    }
    sqlx::query!(
        "DELETE FROM reply_processing WHERE message_id = ? AND action = 'outcome'",
        id
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(true)
}

pub async fn retry(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let updated = sqlx::query!(
        "UPDATE reply_processing SET state = 'queued', error = NULL, queued_at = ?
        WHERE message_id = ? AND action = 'outcome' AND state = 'failed'",
        now,
        id
    )
    .execute(db)
    .await?;
    Ok(updated.rows_affected() > 0)
}

pub async fn progress(db: &SqlitePool) -> Result<ReplyProcessing, sqlx::Error> {
    let mut tx = db.begin().await?;
    let counts = sqlx::query!(
        r#"SELECT
        coalesce(sum(state IN ('queued', 'running')), 0) AS "remaining!: i64",
        coalesce(sum(state = 'failed'), 0) AS "failed!: i64"
        FROM reply_processing q WHERE action = 'outcome' AND EXISTS (
            SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
            WHERE mm.message_id = q.message_id AND m.trashed_at IS NULL)"#
    )
    .fetch_one(&mut *tx)
    .await?;
    let pause = sqlx::query_as!(
        ReplyProcessingPause,
        r#"SELECT reply_processing_paused_at AS "at!", reply_processing_pause_reason AS "reason!"
           FROM profile WHERE reply_processing_paused_at IS NOT NULL"#
    )
    .fetch_optional(&mut *tx)
    .await?;
    Ok(ReplyProcessing {
        remaining: counts.remaining,
        failed: counts.failed,
        pause,
    })
}

pub async fn resume(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE profile SET reply_processing_paused_at = NULL, reply_processing_pause_reason = NULL"
    )
    .execute(db)
    .await?;
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Claim {
    pub id: i64,
    pub claim: i64,
}

pub async fn next(db: &SqlitePool) -> Result<Option<Claim>, sqlx::Error> {
    let mut tx = db.begin().await?;
    // The counter outlives deleted queue rows, so disabling and re-enabling cannot reuse a claim.
    sqlx::query!("UPDATE profile SET reply_processing_claim = reply_processing_claim + 1")
        .execute(&mut *tx)
        .await?;
    let claim = sqlx::query_as!(
        Claim,
        r#"UPDATE reply_processing SET state = 'running',
        claim = (SELECT reply_processing_claim FROM profile)
        WHERE action = 'outcome' AND message_id = (
            SELECT q.message_id FROM reply_processing q JOIN message msg ON msg.id = q.message_id
            WHERE q.action = 'outcome' AND q.state = 'queued' AND EXISTS (
                SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
                WHERE mm.message_id = q.message_id AND m.trashed_at IS NULL)
            ORDER BY msg.sent_at DESC, msg.id DESC LIMIT 1)
          AND (SELECT ai_reply_outcome AND reply_processing_paused_at IS NULL FROM profile)
        RETURNING message_id AS "id!", claim AS "claim!""#
    )
    .fetch_optional(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(claim)
}

pub async fn recover(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE reply_processing SET state = 'queued', claim = 0 WHERE state = 'running'")
        .execute(db)
        .await?;
    Ok(())
}

pub async fn running(db: &SqlitePool, claim: Claim) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT EXISTS (SELECT 1 FROM reply_processing q
        WHERE message_id = ? AND action = 'outcome' AND claim = ? AND state = 'running'
          AND (SELECT ai_reply_outcome AND reply_processing_paused_at IS NULL FROM profile)
          AND EXISTS (SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
            WHERE mm.message_id = q.message_id AND m.trashed_at IS NULL)) AS "running!: bool""#,
        claim.id,
        claim.claim
    )
    .fetch_one(db)
    .await
}

pub async fn requeue(db: &mut SqliteConnection, claim: Claim) -> Result<bool, sqlx::Error> {
    let updated = sqlx::query!(
        "UPDATE reply_processing SET state = 'queued' WHERE message_id = ?
        AND action = 'outcome' AND claim = ? AND state = 'running'",
        claim.id,
        claim.claim
    )
    .execute(db)
    .await?;
    Ok(updated.rows_affected() > 0)
}

pub async fn pause_if_unpaused(
    db: &mut SqliteConnection,
    now: i64,
    reason: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE profile SET reply_processing_paused_at = ?, reply_processing_pause_reason = ?
        WHERE reply_processing_paused_at IS NULL",
        now,
        reason
    )
    .execute(db)
    .await?;
    Ok(())
}

pub async fn complete(
    db: &SqlitePool,
    claim: Claim,
    outcome: ReplyOutcome,
) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let deleted = sqlx::query!(
        "DELETE FROM reply_processing WHERE message_id = ? AND action = 'outcome'
        AND claim = ? AND state = 'running' AND EXISTS (
            SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
            WHERE mm.message_id = reply_processing.message_id AND m.trashed_at IS NULL)",
        claim.id,
        claim.claim
    )
    .execute(&mut *tx)
    .await?;
    if deleted.rows_affected() == 0 {
        // A reply put in the trash while in flight stays queued for restoration.
        requeue(&mut tx, claim).await?;
        tx.commit().await?;
        return Ok(false);
    }
    sqlx::query!(
        "UPDATE message SET outcome = ? WHERE id = ? AND outcome IS NULL",
        outcome,
        claim.id
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(true)
}

pub async fn fail(db: &SqlitePool, claim: Claim, reason: &str) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let updated = sqlx::query!(
        "UPDATE reply_processing SET state = 'failed', error = ?
        WHERE message_id = ? AND action = 'outcome' AND claim = ? AND state = 'running'
          AND EXISTS (SELECT 1 FROM message_mentor mm JOIN mentor m ON m.id = mm.mentor_id
            WHERE mm.message_id = reply_processing.message_id AND m.trashed_at IS NULL)",
        reason,
        claim.id,
        claim.claim
    )
    .execute(&mut *tx)
    .await?;
    if updated.rows_affected() == 0 {
        requeue(&mut tx, claim).await?;
    }
    tx.commit().await?;
    Ok(updated.rows_affected() > 0)
}
