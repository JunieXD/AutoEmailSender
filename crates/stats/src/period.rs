use chrono::{DateTime, Datelike, Days, NaiveDate, TimeZone};
use chrono_tz::Tz;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum Range {
    Week,
    Month,
    Quarter,
    All,
}

#[derive(Debug, Serialize)]
pub(crate) struct Bucket {
    date: String,
    start: i64,
    end: i64,
}

pub struct Window {
    pub(crate) start: Option<i64>,
    pub(crate) end: i64,
    pub(crate) buckets: Vec<Bucket>,
}

impl Window {
    /// `first` is the earliest contact event, needed only for the monthly all-time series.
    pub fn new(range: Range, timezone: &str, now: i64, first: Option<i64>) -> Result<Self, String> {
        let tz: Tz = timezone
            .parse()
            .map_err(|_| "时区不正确，请重新打开统计页")?;
        let today = DateTime::from_timestamp_millis(now)
            .ok_or("时间不正确")?
            .with_timezone(&tz)
            .date_naive();
        let tomorrow = today.checked_add_days(Days::new(1)).ok_or("时间超出范围")?;
        let days = match range {
            Range::Week => Some(7),
            Range::Month => Some(30),
            Range::Quarter => Some(90),
            Range::All => None,
        };
        let mut date = if let Some(days) = days {
            today
                .checked_sub_days(Days::new(days - 1))
                .ok_or("时间超出范围")?
        } else {
            let first = first
                .and_then(DateTime::from_timestamp_millis)
                .map(|d| d.with_timezone(&tz).date_naive())
                .unwrap_or(today)
                .min(today);
            first.with_day(1).ok_or("时间超出范围")?
        };
        let start = days.map(|_| midnight(tz, date)).transpose()?;
        let end = midnight(tz, tomorrow)?;
        let mut buckets = Vec::new();
        while date < tomorrow {
            let next = if days.is_some() {
                date.checked_add_days(Days::new(1))
            } else {
                date.checked_add_months(chrono::Months::new(1))
            }
            .ok_or("时间超出范围")?;
            buckets.push(Bucket {
                date: date
                    .format(if days.is_some() { "%Y-%m-%d" } else { "%Y-%m" })
                    .to_string(),
                start: midnight(tz, date)?,
                end: midnight(tz, next.min(tomorrow))?,
            });
            date = next;
        }
        Ok(Self {
            start,
            end,
            buckets,
        })
    }
}

fn midnight(tz: Tz, date: NaiveDate) -> Result<i64, String> {
    let mut local = date.and_hms_opt(0, 0, 0).ok_or("时间超出范围")?;
    // Some zones move the clock at midnight, or skip a civil date entirely.
    loop {
        if let Some(at) = tz.from_local_datetime(&local).earliest() {
            return Ok(at.timestamp_millis());
        }
        local = local
            .checked_add_signed(chrono::Duration::minutes(1))
            .ok_or("时间超出范围")?;
    }
}

pub async fn first_event<'e>(
    db: impl sqlx::Executor<'e, Database = sqlx::Sqlite>,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar!("SELECT min(at) FROM (SELECT min(e.sent_at) AS at FROM contact_email e JOIN mentor m ON m.id=e.mentor_id WHERE m.trashed_at IS NULL UNION ALL SELECT min(msg.sent_at) FROM contact_message msg JOIN message_mentor mm ON mm.message_id=msg.id JOIN mentor m ON m.id=mm.mentor_id WHERE m.trashed_at IS NULL)")
        .fetch_one(db).await
}
