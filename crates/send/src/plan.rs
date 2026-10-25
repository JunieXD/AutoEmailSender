//! The sending plan is a paginated projection, never another persisted queue.
use super::*;
use email::tasks::DraftState;
use serde::{Deserialize, Serialize};
use std::num::NonZeroU32;
use ts_rs::TS;

#[derive(Debug, Clone, Copy, Deserialize, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum PlanColumn {
    Pending,
    Upcoming,
    History,
}

#[derive(Debug, Deserialize, TS)]
pub struct PlanQuery {
    pub column: PlanColumn,
    pub page: NonZeroU32,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum Waiting {
    Time,
    MailboxLimit,
    MailboxPaused,
    TaskPaused,
    MailboxInterval,
    NoMailbox,
    Ready,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PlanEmail {
    pub id: i64,
    pub mentor_id: i64,
    pub name: String,
    pub subject: Option<String>,
    pub task_id: Option<i64>,
    pub task_name: Option<String>,
    pub state: DraftState,
    pub resend_of: Option<i64>,
    pub planned_at: Option<i64>,
    pub latest_at: Option<i64>,
    pub sent_at: Option<i64>,
    pub from_address: Option<String>,
    pub error: Option<String>,
    pub cancel_reason: Option<String>,
    pub waiting: Option<Waiting>,
}

#[derive(Debug, Serialize, TS)]
pub struct PlanPage {
    pub emails: Vec<PlanEmail>,
    pub total: i64,
}

pub async fn list(db: &SqlitePool, query: &PlanQuery, now: i64) -> Result<PlanPage, sqlx::Error> {
    let offset = i64::from(query.page.get() - 1) * 50;
    let limit = if matches!(query.column, PlanColumn::History) {
        (100 - offset).clamp(0, 50)
    } else {
        50
    };
    let mut tx = db.begin().await?;
    let total = match query.column {
        PlanColumn::Pending => {
            sqlx::query_scalar!(
                r#"SELECT count(*)
                   FROM email
                   WHERE state IN ('generation_failed', 'failed', 'maybe_sent')
                     OR (state = 'approved' AND (planned_at IS NULL OR latest_at <= ?))"#,
                now
            )
            .fetch_one(&mut *tx)
            .await?
        }
        PlanColumn::Upcoming => {
            sqlx::query_scalar!(
                r#"SELECT count(*)
                   FROM email
                   WHERE state = 'approved'
                     AND planned_at IS NOT NULL
                     AND (latest_at IS NULL OR latest_at > ?)"#,
                now
            )
            .fetch_one(&mut *tx)
            .await?
        }
        PlanColumn::History => {
            sqlx::query_scalar!(
                r#"SELECT count(*)
                   FROM (SELECT id FROM email WHERE state IN ('sent', 'bounced') LIMIT 100)"#
            )
            .fetch_one(&mut *tx)
            .await?
        }
    };
    let ids = match query.column {
        PlanColumn::Pending => {
            sqlx::query_scalar!(
                r#"SELECT id
                   FROM email
                   WHERE state IN ('generation_failed', 'failed', 'maybe_sent')
                     OR (state = 'approved' AND (planned_at IS NULL OR latest_at <= ?1))
                   ORDER BY coalesce(planned_at, created_at), id
                   LIMIT ?2 OFFSET ?3"#,
                now,
                limit,
                offset
            )
            .fetch_all(&mut *tx)
            .await?
        }
        PlanColumn::Upcoming => {
            sqlx::query_scalar!(
                r#"SELECT id
                   FROM email
                   WHERE state = 'approved'
                     AND planned_at IS NOT NULL
                     AND (latest_at IS NULL OR latest_at > ?1)
                   ORDER BY planned_at, id
                   LIMIT ?2 OFFSET ?3"#,
                now,
                limit,
                offset
            )
            .fetch_all(&mut *tx)
            .await?
        }
        PlanColumn::History => {
            sqlx::query_scalar!(
                r#"SELECT id
                   FROM email
                   WHERE state IN ('sent', 'bounced')
                   ORDER BY sent_at DESC, id DESC
                   LIMIT ? OFFSET ?"#,
                limit,
                offset
            )
            .fetch_all(&mut *tx)
            .await?
        }
    };
    let rows = sqlx::query!(
        r#"SELECT e.id, e.mentor_id, m.name, e.subject, e.task_id, t.name AS task_name, e.state
             AS "state: DraftState", e.planned_at, e.latest_at, e.sent_at, e.from_address,
             e.error, e.cancel_reason, e.resend_of, t.state AS task_state, t.generation_paused_at, CASE WHEN
             e.kind = 'reply' THEN coalesce(m.mailbox_id, (SELECT r.mailbox_id FROM message r
             JOIN message_mentor mm ON mm.message_id = r.id WHERE mm.mentor_id = e.mentor_id AND
             r.message_id = e.reply_to AND r.kind = 'reply')) ELSE m.mailbox_id END AS own
           FROM json_each(?1) j
           JOIN email e ON e.id = j.value
           JOIN mentor m ON m.id = e.mentor_id
           LEFT JOIN task t ON t.id = e.task_id
           ORDER BY j.key"#,
        sqlx::types::Json(ids)
    )
    .fetch_all(&mut *tx)
    .await?;
    let rotation = sqlx::query_scalar!(
        r#"SELECT rotation AS "rotation: profile::Rotation"
           FROM profile"#
    )
    .fetch_one(&mut *tx)
    .await?;
    let mailboxes = usage_on(&mut tx, now).await?;
    let emails = rows
        .into_iter()
        .map(|r| {
            let waiting = if !matches!(query.column, PlanColumn::Upcoming) {
                None
            } else if r.task_state.as_deref().is_some_and(|s| s != "active")
                || r.generation_paused_at.is_some()
            {
                Some(Waiting::TaskPaused)
            } else if r.planned_at.is_some_and(|t| t > now) {
                Some(Waiting::Time)
            } else {
                Some(match choose(rotation, r.own, &mailboxes, now) {
                    None => Waiting::NoMailbox,
                    Some((id, at)) if at > now => {
                        if mailboxes
                            .iter()
                            .any(|m| m.id == id && m.paused_until.is_some_and(|t| t > now))
                        {
                            Waiting::MailboxPaused
                        } else if mailboxes
                            .iter()
                            .any(|m| m.id == id && m.recent >= m.daily_limit)
                        {
                            Waiting::MailboxLimit
                        } else {
                            Waiting::MailboxInterval
                        }
                    }
                    Some(_) => Waiting::Ready,
                })
            };
            PlanEmail {
                id: r.id,
                mentor_id: r.mentor_id,
                name: r.name,
                subject: r.subject,
                task_id: r.task_id,
                task_name: r.task_name,
                state: r.state,
                resend_of: r.resend_of,
                planned_at: r.planned_at,
                latest_at: r.latest_at,
                sent_at: r.sent_at,
                from_address: r.from_address,
                error: r.error,
                cancel_reason: r.cancel_reason,
                waiting,
            }
        })
        .collect();
    Ok(PlanPage {
        emails,
        total: if matches!(query.column, PlanColumn::History) {
            total.min(100)
        } else {
            total
        },
    })
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ScheduledWork {
    pub pending: i64,
    pub next_at: Option<i64>,
    pub today: i64,
}

pub async fn work(db: &SqlitePool, now: i64) -> Result<ScheduledWork, sqlx::Error> {
    use chrono::TimeZone;
    let date = chrono::Local
        .timestamp_millis_opt(now)
        .single()
        .ok_or_else(|| sqlx::Error::Decode("invalid current time".into()))?;
    let tomorrow = date
        .date_naive()
        .succ_opt()
        .and_then(|d| d.and_hms_opt(0, 0, 0))
        .and_then(|d| chrono::Local.from_local_datetime(&d).earliest())
        .ok_or_else(|| sqlx::Error::Decode("invalid next local day".into()))?
        .timestamp_millis();
    sqlx::query_as!(
        ScheduledWork,
        r#"SELECT (SELECT count(*) FROM email WHERE state IN ('generation_failed', 'failed',
             'maybe_sent') OR (state = 'approved' AND (planned_at IS NULL OR latest_at <= ?1)))
             AS "pending!: i64", min(planned_at) AS "next_at: i64", coalesce(sum(planned_at <
             ?2), 0) AS "today!: i64"
           FROM email
           WHERE state = 'approved'
             AND planned_at IS NOT NULL
             AND (latest_at IS NULL OR latest_at > ?1)"#,
        now,
        tomorrow
    )
    .fetch_one(db)
    .await
}
