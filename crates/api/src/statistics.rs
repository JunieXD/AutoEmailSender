use super::*;

#[derive(Debug, Deserialize, TS)]
pub struct TodayWorkQuery {
    pub start: i64,
    pub end: i64,
}

pub(super) async fn today_work(
    ctx: &Context,
    input: TodayWorkQuery,
) -> Result<stats::TodayWork, CallError> {
    Ok(stats::today_work(&ctx.db, input.start, input.end).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct StatsPeriod {
    pub range: stats::Range,
    pub timezone: String,
    pub now: i64,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct StatsQuery {
    #[serde(flatten)]
    #[ts(flatten)]
    pub period: StatsPeriod,
    pub page: std::num::NonZeroU32,
}

pub(super) async fn contact_stats(
    ctx: &Context,
    input: StatsQuery,
) -> Result<stats::ContactProgress, CallError> {
    let mut tx = ctx.db.begin().await?;
    let first = if matches!(input.period.range, stats::Range::All) {
        stats::first_event(&mut *tx).await?
    } else {
        None
    };
    let window = stats::Window::new(
        input.period.range,
        &input.period.timezone,
        input.period.now,
        first,
    )
    .map_err(CallError::invalid)?;
    let data = stats::contact(&mut tx, &window, input.page).await?;
    tx.commit().await?;
    Ok(data)
}

pub(super) async fn mentor_stats(
    ctx: &Context,
    _: Nothing,
) -> Result<stats::MentorOverview, CallError> {
    Ok(stats::mentors(&ctx.db).await?)
}

pub(super) async fn token_stats(
    ctx: &Context,
    input: StatsPeriod,
) -> Result<stats::TokenUsage, CallError> {
    let window = stats::Window::new(input.range, &input.timezone, input.now, None)
        .map_err(CallError::invalid)?;
    Ok(stats::tokens(&ctx.db, &window).await?)
}
