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
    MailboxSecret,
    Ready,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PlanEmail {
    pub id: i64,
    pub kind: email::DraftKind,
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
    /// With the official server, which sends it from `from_address`.
    pub cloud: Option<Cloud>,
    /// The official server sent it, or tried to.
    pub by_cloud: bool,
}

/// An email with the official server.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "PlanCloud")]
pub struct Cloud {
    /// When it is to go.
    pub at: i64,
    /// Still going up: withdrawn at the next sync if that was cut short.
    pub uploading: bool,
    /// Its mailbox's sending is paused there, as last synced.
    pub pause: Option<pause::Pause>,
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
                   WHERE (state = 'approved'
                       AND planned_at IS NOT NULL
                       AND (latest_at IS NULL OR latest_at > ?))
                     OR state = 'cloud'"#,
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
                   WHERE (state = 'approved'
                       AND planned_at IS NOT NULL
                       AND (latest_at IS NULL OR latest_at > ?1))
                     OR state = 'cloud'
                   ORDER BY coalesce(cloud_at, planned_at), id
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
        r#"SELECT e.id, e.kind AS "kind: email::DraftKind", e.mentor_id, m.name,
            e.subject, e.task_id, t.name AS task_name,
            e.state AS "state: DraftState", e.planned_at, e.latest_at, e.sent_at,
            e.from_address, e.error, e.cancel_reason, e.resend_of,
            t.state AS task_state, t.generation_paused_at, own.mailbox_id AS own,
            e.kind IN ('reply', 'follow_up') AS "reply!: bool",
            e.cloud_at, e.cloud_uploading AS "cloud_uploading: bool",
            e.by_cloud AS "by_cloud: bool", b.cloud_paused_until, b.cloud_pause_reason
        FROM json_each(?1) j
        JOIN email e ON e.id = j.value
        JOIN mentor m ON m.id = e.mentor_id
        JOIN email_own_mailbox own ON own.email_id = e.id
        LEFT JOIN task t ON t.id = e.task_id
        LEFT JOIN mailbox b ON b.id = e.mailbox_id AND e.state = 'cloud'
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
            let cloud = r.cloud_at.map(|at| Cloud {
                at,
                uploading: r.cloud_uploading,
                pause: r
                    .cloud_paused_until
                    .zip(r.cloud_pause_reason.clone())
                    .filter(|(until, _)| *until > now)
                    .map(|(until, reason)| pause::Pause { until, reason }),
            });
            let waiting = if !matches!(query.column, PlanColumn::Upcoming) || cloud.is_some() {
                None
            } else if r.task_state.as_deref().is_some_and(|s| s != "active")
                || r.generation_paused_at.is_some()
            {
                Some(Waiting::TaskPaused)
            } else if r.planned_at.is_some_and(|t| t > now) {
                Some(Waiting::Time)
            } else {
                Some(match choose(rotation, r.own, r.reply, &mailboxes, now) {
                    None if mailboxes
                        .iter()
                        .any(|m| m.needs_secret && r.own.is_none_or(|id| m.id == id)) =>
                    {
                        Waiting::MailboxSecret
                    }
                    None => Waiting::NoMailbox,
                    Some((id, at)) if at > now => {
                        if mailboxes
                            .iter()
                            .any(|m| m.id == id && m.paused_until.is_some_and(|t| t > now))
                        {
                            Waiting::MailboxPaused
                        } else if mailboxes.iter().any(|m| m.id == id && !m.has_room(now)) {
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
                kind: r.kind,
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
                cloud,
                by_cloud: r.by_cloud,
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
    work_in_zone(db, now, &chrono::Local).await
}

pub async fn work_in_zone<T: chrono::TimeZone>(
    db: &SqlitePool,
    now: i64,
    zone: &T,
) -> Result<ScheduledWork, sqlx::Error> {
    let tomorrow = pause::next_day_start(now, zone);
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
