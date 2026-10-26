//! Local statistics, derived from contact events and the current mentor and model data.

mod period;
pub use period::{Range, Window, first_event};
mod today;
pub use today::{TodayDrafts, TodayWork, today_work};

use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Trend {
    pub date: String,
    pub sent: i64,
    pub replies: i64,
}

#[derive(Debug, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SchoolProgress {
    pub university: String,
    pub contacted: i64,
    pub replied: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ContactNumbers {
    pub contacted: i64,
    pub replied: i64,
    pub median_days: Option<f64>,
    pub bounced: i64,
    pub waiting: [i64; 5],
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SchoolProgressPage {
    pub schools: Vec<SchoolProgress>,
    pub school_count: i64,
}

#[derive(Debug, Serialize, TS)]
pub struct ContactProgress {
    #[serde(flatten)]
    #[ts(flatten)]
    pub numbers: ContactNumbers,
    #[serde(flatten)]
    #[ts(flatten)]
    pub school_page: SchoolProgressPage,
    pub trend: Vec<Trend>,
}

pub const SCHOOL_PAGE_SIZE: i64 = 10;

/// The caller holds a read transaction, including the all-time series' first event.
pub async fn contact(
    conn: &mut SqliteConnection,
    window: &Window,
    page: std::num::NonZeroU32,
) -> Result<ContactProgress, sqlx::Error> {
    let numbers = contact_numbers(conn, window).await?;
    let school_page = contact_schools(conn, window, page).await?;
    let trend = contact_trend(conn, window).await?;
    Ok(ContactProgress {
        numbers,
        school_page,
        trend,
    })
}

pub async fn contact_numbers(
    conn: &mut SqliteConnection,
    window: &Window,
) -> Result<ContactNumbers, sqlx::Error> {
    let start = window.start;
    let end = window.end;
    let row = sqlx::query_file!("src/contact.sql", start, end)
        .fetch_one(conn)
        .await?;
    Ok(ContactNumbers {
        contacted: row.contacted,
        replied: row.replied,
        median_days: row.median_days,
        bounced: row.bounced,
        waiting: decode(&row.waiting)?,
    })
}

pub async fn contact_schools(
    conn: &mut SqliteConnection,
    window: &Window,
    page: std::num::NonZeroU32,
) -> Result<SchoolProgressPage, sqlx::Error> {
    let start = window.start;
    let end = window.end;
    let offset = i64::from(page.get() - 1) * SCHOOL_PAGE_SIZE;
    let row = sqlx::query_file!(
        "src/contact-schools.sql",
        start,
        end,
        SCHOOL_PAGE_SIZE,
        offset
    )
    .fetch_one(conn)
    .await?;
    Ok(SchoolProgressPage {
        schools: decode(&row.schools)?,
        school_count: row.school_count,
    })
}

pub async fn contact_trend(
    conn: &mut SqliteConnection,
    window: &Window,
) -> Result<Vec<Trend>, sqlx::Error> {
    let buckets = Json(&window.buckets);
    sqlx::query_file_as!(Trend, "src/trend.sql", buckets)
        .fetch_all(conn)
        .await
}

fn decode<T: serde::de::DeserializeOwned>(json: &str) -> Result<T, sqlx::Error> {
    serde_json::from_str(json).map_err(|e| sqlx::Error::Decode(Box::new(e)))
}

#[derive(Debug, Serialize, Deserialize, TS)]
pub struct SchoolCount {
    pub university: String,
    pub count: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MatchingDistribution {
    pub total: i64,
    pub matching: [i64; 6],
}

#[derive(Debug, Serialize, TS)]
pub struct Completeness {
    pub email: i64,
    pub research: i64,
    pub papers: i64,
    pub homepage: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SchoolDistribution {
    pub schools: Vec<SchoolCount>,
    pub other_schools: i64,
}

#[derive(Debug, Serialize, TS)]
pub struct MentorOverview {
    #[serde(flatten)]
    #[ts(flatten)]
    pub distribution: MatchingDistribution,
    #[serde(flatten)]
    #[ts(flatten)]
    pub completeness: Completeness,
    #[serde(flatten)]
    #[ts(flatten)]
    pub school_distribution: SchoolDistribution,
}

pub async fn mentors(db: &SqlitePool) -> Result<MentorOverview, sqlx::Error> {
    let mut tx = db.begin().await?;
    let distribution = mentor_matching(&mut tx).await?;
    let completeness = mentor_completeness(&mut tx).await?;
    let school_distribution = mentor_schools(&mut tx).await?;
    tx.commit().await?;
    Ok(MentorOverview {
        distribution,
        completeness,
        school_distribution,
    })
}

pub async fn mentor_matching(
    conn: &mut SqliteConnection,
) -> Result<MatchingDistribution, sqlx::Error> {
    let row = sqlx::query_file!("src/matching.sql")
        .fetch_one(conn)
        .await?;
    Ok(MatchingDistribution {
        total: row.total,
        matching: decode(&row.matching)?,
    })
}

pub async fn mentor_completeness(conn: &mut SqliteConnection) -> Result<Completeness, sqlx::Error> {
    let row = sqlx::query_file!("src/completeness.sql")
        .fetch_one(conn)
        .await?;
    Ok(Completeness {
        email: row.email,
        research: row.research,
        papers: row.papers,
        homepage: row.homepage,
    })
}

pub async fn mentor_schools(
    conn: &mut SqliteConnection,
) -> Result<SchoolDistribution, sqlx::Error> {
    let row = sqlx::query_file!("src/mentor-schools.sql")
        .fetch_one(conn)
        .await?;
    Ok(SchoolDistribution {
        schools: decode(&row.schools)?,
        other_schools: row.other_schools,
    })
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TokenTotals {
    pub input: i64,
    pub cached: i64,
    pub output: i64,
    pub estimated_cost: Option<f64>,
    pub unpriced_calls: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PurposeUsage {
    pub purpose: model::usage::Purpose,
    #[serde(flatten)]
    #[ts(flatten)]
    pub tokens: TokenTotals,
}

#[derive(Debug, Serialize, TS)]
pub struct TokenUsage {
    pub purposes: Vec<PurposeUsage>,
    pub total: TokenTotals,
}

pub async fn tokens(db: &SqlitePool, window: &Window) -> Result<TokenUsage, sqlx::Error> {
    let start = window.start;
    let end = window.end;
    let rows = sqlx::query_file!("src/tokens.sql", start, end)
        .fetch_all(db)
        .await?;
    let mut total = TokenTotals {
        input: 0,
        cached: 0,
        output: 0,
        estimated_cost: None,
        unpriced_calls: 0,
    };
    let purposes = rows
        .into_iter()
        .map(|r| {
            total.input += r.input;
            total.cached += r.cached;
            total.output += r.output;
            total.unpriced_calls += r.unpriced_calls;
            if let Some(cost) = r.estimated_cost {
                *total.estimated_cost.get_or_insert(0.0) += cost;
            }
            PurposeUsage {
                purpose: r.purpose,
                tokens: TokenTotals {
                    input: r.input,
                    cached: r.cached,
                    output: r.output,
                    estimated_cost: r.estimated_cost,
                    unpriced_calls: r.unpriced_calls,
                },
            }
        })
        .collect();
    Ok(TokenUsage { purposes, total })
}
