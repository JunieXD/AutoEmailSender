//! Local calendar windows become fixed UTC reservations at approval time.
use chrono::{Local, NaiveDate, NaiveTime, TimeZone};
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Schedule {
    Now,
    Window(Window),
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Window {
    pub dates: Vec<String>,
    pub start: String,
    pub end: String,
    pub per_window: u32,
}

impl Window {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.dates.is_empty()
            || self.dates.iter().any(|d| {
                !NaiveDate::parse_from_str(d, "%Y-%m-%d").is_ok_and(|date| date.to_string() == *d)
            })
            || !NaiveTime::parse_from_str(&self.start, "%H:%M")
                .is_ok_and(|t| t.format("%H:%M").to_string() == self.start)
            || !NaiveTime::parse_from_str(&self.end, "%H:%M")
                .is_ok_and(|t| t.format("%H:%M").to_string() == self.end)
            || self.start >= self.end
            || self.per_window == 0
        {
            return Err("请选择日期、同一天内的有效时间窗口和每个窗口的数量");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Slot {
    pub planned_at: i64,
    pub latest_at: i64,
}

/// A repeated wall time uses its first occurrence; a window with a missing DST boundary is skipped.
/// Reservations include sent letters, so a running task never exceeds its window capacity.
pub fn plan<T: TimeZone>(
    window: &Window,
    reserved: &[i64],
    now: i64,
    zone: &T,
    random: &mut impl FnMut(i64) -> i64,
) -> Option<Slot> {
    let start = NaiveTime::parse_from_str(&window.start, "%H:%M").ok()?;
    let end = NaiveTime::parse_from_str(&window.end, "%H:%M").ok()?;
    let mut dates = window.dates.iter().collect::<Vec<_>>();
    dates.sort();
    dates.dedup();
    for date in dates {
        let date = NaiveDate::parse_from_str(date, "%Y-%m-%d").ok()?;
        let Some(start) = zone.from_local_datetime(&date.and_time(start)).earliest() else {
            continue;
        };
        let Some(end) = zone.from_local_datetime(&date.and_time(end)).earliest() else {
            continue;
        };
        let (start, end) = (start.timestamp_millis(), end.timestamp_millis());
        if now >= end {
            continue;
        }
        let times = reserved
            .iter()
            .copied()
            .filter(|t| *t >= start && *t < end)
            .collect::<Vec<_>>();
        let remaining = i64::from(window.per_window) - times.len() as i64;
        if remaining <= 0 {
            continue;
        }
        let after = times.into_iter().max().map_or(start, |t| t + 1).max(now);
        let width = (end - after) / remaining;
        if width <= 0 {
            continue;
        }
        return Some(Slot {
            planned_at: after + random(width),
            latest_at: end,
        });
    }
    None
}

pub async fn get(conn: &mut SqliteConnection, task: i64) -> Result<Schedule, sqlx::Error> {
    let r = sqlx::query!(r#"SELECT schedule,window_dates AS "dates: Json<Vec<String>>",window_start,window_end,per_window FROM task WHERE id=?"#,task).fetch_one(conn).await?;
    match (
        r.schedule.as_str(),
        r.dates,
        r.window_start,
        r.window_end,
        r.per_window,
    ) {
        ("now", ..) => Ok(Schedule::Now),
        ("window", Some(dates), Some(start), Some(end), Some(per_window)) => {
            Ok(Schedule::Window(Window {
                dates: dates.0,
                start,
                end,
                per_window: u32::try_from(per_window).map_err(|e| sqlx::Error::Decode(e.into()))?,
            }))
        }
        _ => Err(sqlx::Error::Decode("invalid task schedule".into())),
    }
}

pub async fn save(
    conn: &mut SqliteConnection,
    task: i64,
    schedule: &Schedule,
) -> Result<(), sqlx::Error> {
    let (kind, dates, start, end, count) = match schedule {
        Schedule::Now => ("now", None, None, None, None),
        Schedule::Window(w) => (
            "window",
            Some(Json(&w.dates)),
            Some(&w.start),
            Some(&w.end),
            Some(i64::from(w.per_window)),
        ),
    };
    sqlx::query!("UPDATE task SET schedule=?,window_dates=?,window_start=?,window_end=?,per_window=? WHERE id=?",kind,dates,start,end,count,task).execute(conn).await?;
    Ok(())
}

/// Caller holds the write lock; reservation and approval commit together.
pub async fn assign<T: TimeZone>(
    conn: &mut SqliteConnection,
    id: i64,
    task: i64,
    now: i64,
    zone: &T,
    random: &mut impl FnMut(i64) -> i64,
) -> Result<(), sqlx::Error> {
    let slot = match get(conn, task).await? {
        Schedule::Now => Some(Slot {
            planned_at: now,
            latest_at: now,
        }),
        Schedule::Window(w) => {
            let reserved = sqlx::query_scalar!(r#"SELECT planned_at AS "planned_at!" FROM email WHERE task_id=?1 AND id<>?2 AND planned_at IS NOT NULL AND state NOT IN ('review','generating','generation_failed','canceled')"#,task,id).fetch_all(&mut *conn).await?;
            plan(&w, &reserved, now, zone, random)
        }
    };
    let planned = slot.map(|s| s.planned_at);
    let latest = slot
        .filter(|s| s.latest_at > s.planned_at)
        .map(|s| s.latest_at);
    sqlx::query!("UPDATE email SET planned_at=?,latest_at=?,manual_schedule=0 WHERE id=? AND state='approved'",planned,latest,id).execute(conn).await?;
    Ok(())
}

/// Keeps an expired reservation when there is no later slot, to explain the missed deadline.
pub async fn rollover<T: TimeZone>(
    db: &SqlitePool,
    now: i64,
    zone: &T,
    random: &mut impl FnMut(i64) -> i64,
) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    let expired = sqlx::query!(r#"UPDATE email SET updated_at=updated_at WHERE state='approved' AND manual_schedule=0 AND (latest_at<=? OR planned_at IS NULL) AND task_id IS NOT NULL RETURNING id,task_id AS "task!",planned_at,latest_at"#,now).fetch_all(&mut *tx).await?;
    let mut expired = expired;
    expired.sort_by_key(|r| (r.latest_at, r.id));
    for e in expired {
        assign(&mut tx, e.id, e.task, now, zone, random).await?;
        sqlx::query!("UPDATE email SET planned_at=coalesce(planned_at,?1),latest_at=CASE WHEN planned_at IS NULL THEN ?2 ELSE latest_at END WHERE id=?3",e.planned_at,e.latest_at,e.id).execute(&mut *tx).await?;
    }
    tx.commit().await
}

pub async fn retry(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(task) = sqlx::query!("UPDATE email SET state='approved',planned_at=?1,latest_at=NULL,manual_schedule=0,error=NULL,updated_at=?1 WHERE id=?2 AND state IN ('failed','maybe_sent') AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state<>'stopped')) AND mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL) RETURNING task_id",now,id).fetch_optional(&mut *tx).await? else { return Ok(false); };
    if let Some(task) = task.task_id {
        assign(&mut tx, id, task, now, &Local, &mut |n| fastrand::i64(0..n)).await?;
    }
    tx.commit().await?;
    Ok(true)
}

pub async fn reschedule(
    db: &SqlitePool,
    id: i64,
    planned: i64,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let latest = planned + 3_600_000;
    Ok(sqlx::query!("UPDATE email SET planned_at=?,latest_at=?,manual_schedule=1,updated_at=? WHERE id=? AND state='approved' AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state<>'stopped'))",planned,latest,now,id).execute(db).await?.rows_affected()==1)
}

pub async fn send_now(db: &SqlitePool, id: i64, now: i64) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query!("UPDATE email SET planned_at=?1,latest_at=NULL,manual_schedule=0,updated_at=?1 WHERE id=?2 AND state='approved' AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state<>'stopped'))",now,id).execute(db).await?.rows_affected()==1)
}
