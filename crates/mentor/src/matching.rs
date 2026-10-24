//! The persisted match queue and results (docs/data-model.md#导师).

use serde::Serialize;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Default, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MatchPlan {
    pub analyze: i64,
    pub existing: i64,
    pub changed: i64,
    pub insufficient: i64,
    pub queued: i64,
    #[serde(skip)]
    #[ts(skip)]
    pub ids: Vec<i64>,
}

/// Disjoint skipped groups; changed results are included in the analysis by default.
pub async fn plan(db: &SqlitePool, ids: &[i64], redo: bool) -> Result<MatchPlan, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT m.id, m.match_state, m.match_score,
             coalesce(m.matched_at < p.match_basis_changed_at, 0) AS "changed!: bool",
             (d.research IS NULL AND coalesce(json_array_length(d.papers), 0) = 0) AS "insufficient!: bool"
           FROM mentor m JOIN mentor_detail d ON d.mentor_id = m.id CROSS JOIN profile p
           WHERE m.id IN (SELECT value FROM json_each(?)) AND m.trashed_at IS NULL ORDER BY m.id"#,
        Json(ids)
    ).fetch_all(db).await?;
    let mut plan = MatchPlan::default();
    for r in rows {
        if matches!(r.match_state.as_deref(), Some("queued" | "running")) {
            plan.queued += 1;
        } else if r.insufficient {
            plan.insufficient += 1;
        } else {
            if r.match_score.is_some() {
                if r.changed {
                    plan.changed += 1;
                } else {
                    plan.existing += 1;
                }
            }
            if redo || r.match_score.is_none() || r.changed {
                plan.analyze += 1;
                plan.ids.push(r.id);
            }
        }
    }
    Ok(plan)
}

/// A single write avoids a race with another enqueue or the worker claiming a mentor.
pub async fn enqueue(
    db: &SqlitePool,
    ids: &[i64],
    single: bool,
    redo: bool,
    now: i64,
) -> Result<u64, sqlx::Error> {
    let mut tx = db.begin().await?;
    let row = sqlx::query!(
        "UPDATE mentor SET match_state = 'queued', match_error = NULL,
           match_queued_at = ?1, match_priority = ?2, match_claim = match_claim + 1
         WHERE id IN (SELECT value FROM json_each(?3)) AND trashed_at IS NULL
           AND (match_state IS NULL OR match_state = 'failed')
           AND id IN (SELECT mentor_id FROM mentor_detail
                      WHERE research IS NOT NULL OR coalesce(json_array_length(papers), 0) > 0)
           AND (?4 OR match_score IS NULL OR matched_at < (SELECT match_basis_changed_at FROM profile))",
        now, single, Json(ids), redo || single,
    ).execute(&mut *tx).await?;
    sqlx::query!("UPDATE profile SET match_paused_at = NULL, match_pause_reason = NULL")
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(row.rows_affected())
}

#[derive(Debug, Serialize, TS)]
pub struct MatchProgress {
    pub remaining: i64,
    pub failed: i64,
    pub pause: Option<MatchPause>,
}

/// An absent reason means the user paused the analysis.
#[derive(Debug, Serialize, TS)]
pub struct MatchPause {
    pub at: i64,
    pub reason: Option<String>,
}

pub async fn progress(db: &SqlitePool) -> Result<MatchProgress, sqlx::Error> {
    let r = sqlx::query!(
        r#"SELECT match_paused_at, match_pause_reason,
             (SELECT count(*) FROM mentor WHERE match_state IN ('queued', 'running') AND trashed_at IS NULL) AS "remaining!: i64",
             (SELECT count(*) FROM mentor WHERE match_state = 'failed' AND trashed_at IS NULL) AS "failed!: i64"
           FROM profile"#
    ).fetch_one(db).await?;
    Ok(MatchProgress {
        remaining: r.remaining,
        failed: r.failed,
        pause: r.match_paused_at.map(|at| MatchPause {
            at,
            reason: r.match_pause_reason,
        }),
    })
}

pub async fn pause(db: &SqlitePool, now: i64, reason: Option<&str>) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE profile SET match_paused_at = ?, match_pause_reason = ?",
        now,
        reason
    )
    .execute(db)
    .await?;
    Ok(())
}

pub async fn resume(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE profile SET match_paused_at = NULL, match_pause_reason = NULL")
        .execute(db)
        .await?;
    Ok(())
}

/// Clears waiting work; the few already being analyzed finish normally.
pub async fn cancel(db: &SqlitePool) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE mentor SET match_state = NULL, match_queued_at = NULL, match_priority = NULL WHERE match_state IN ('queued', 'running')")
        .execute(&mut *tx).await?;
    sqlx::query!("UPDATE profile SET match_paused_at = NULL, match_pause_reason = NULL")
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

pub async fn retry(db: &SqlitePool, now: i64) -> Result<u64, sqlx::Error> {
    let r = sqlx::query!("UPDATE mentor SET match_state = 'queued', match_error = NULL,
        match_queued_at = ?, match_priority = 0, match_claim = match_claim + 1 WHERE match_state = 'failed' AND trashed_at IS NULL", now)
        .execute(db).await?;
    Ok(r.rows_affected())
}

/// The mentor and the counter identifying this particular execution.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Claim {
    pub id: i64,
    pub claim: i64,
}

/// Claims in priority then FIFO order, only while the queue is not paused.
pub async fn next(db: &SqlitePool) -> Result<Option<Claim>, sqlx::Error> {
    sqlx::query_as!(
        Claim,
        r#"UPDATE mentor SET match_state = 'running', match_claim = match_claim + 1 WHERE id = (
           SELECT id FROM mentor WHERE match_state = 'queued' AND trashed_at IS NULL
           ORDER BY match_priority DESC, match_queued_at, id LIMIT 1)
         AND (SELECT match_paused_at FROM profile) IS NULL RETURNING id AS "id!", match_claim AS "claim!""#
    )
    .fetch_optional(db)
    .await
}

pub async fn recover(db: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE mentor SET match_state = 'queued' WHERE match_state = 'running'")
        .execute(db)
        .await?;
    Ok(())
}

/// Whether a format retry still belongs to active work.
pub async fn running(db: &SqlitePool, claim: Claim) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(r#"SELECT EXISTS (SELECT 1 FROM mentor
        WHERE id = ? AND match_claim = ? AND match_state = 'running' AND trashed_at IS NULL) AS "running!: bool""#, claim.id, claim.claim)
        .fetch_one(db).await
}

/// Only the current claim can requeue work that is still running.
pub async fn requeue(db: &mut SqliteConnection, claim: Claim) -> Result<bool, sqlx::Error> {
    let row = sqlx::query!("UPDATE mentor SET match_state = 'queued' WHERE id = ? AND match_claim = ? AND match_state = 'running' AND trashed_at IS NULL", claim.id, claim.claim)
        .execute(db).await?;
    Ok(row.rows_affected() > 0)
}

pub async fn pause_if_unpaused(
    db: &mut SqliteConnection,
    now: i64,
    reason: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE profile SET match_paused_at = ?, match_pause_reason = ?
         WHERE match_paused_at IS NULL",
        now,
        reason,
    )
    .execute(db)
    .await?;
    Ok(())
}

/// The validated model output, before it is stored with its time and model.
#[derive(Debug)]
pub struct MatchAnswer {
    pub score: i64,
    pub reason: String,
    pub directions: Vec<String>,
    pub highlights: Vec<String>,
    pub risks: Vec<String>,
}

/// Late results of a deleted or trashed mentor are discarded.
pub async fn complete(
    db: &SqlitePool,
    claim: Claim,
    answer: &MatchAnswer,
    model: &str,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let r = sqlx::query!("UPDATE mentor SET match_state = NULL, match_error = NULL,
        match_queued_at = NULL, match_priority = NULL, match_score = ?, matched_at = ?, match_model = ?
        WHERE id = ? AND match_claim = ? AND (match_state = 'running' OR match_state IS NULL) AND trashed_at IS NULL", answer.score, now, model, claim.id, claim.claim)
        .execute(&mut *tx).await?;
    if r.rows_affected() == 0 {
        return Ok(false);
    }
    sqlx::query!("UPDATE mentor_detail SET match_reason = ?, match_directions = ?, match_highlights = ?, match_risks = ? WHERE mentor_id = ?",
        answer.reason, Json(&answer.directions), Json(&answer.highlights), Json(&answer.risks), claim.id)
        .execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(true)
}

pub async fn fail(db: &SqlitePool, claim: Claim, reason: &str) -> Result<bool, sqlx::Error> {
    let r = sqlx::query!("UPDATE mentor SET match_state = 'failed', match_error = ?,
        match_queued_at = NULL, match_priority = NULL WHERE id = ? AND match_claim = ? AND match_state = 'running' AND trashed_at IS NULL", reason, claim.id, claim.claim)
        .execute(db).await?;
    Ok(r.rows_affected() > 0)
}
