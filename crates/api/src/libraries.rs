use serde::Deserialize;
use ts_rs::TS;

use crate::{CallError, Context, ErrorCode, Nothing};

#[derive(Deserialize, TS)]
pub struct LibraryId {
    pub id: String,
}

pub async fn library_orgs(
    ctx: &Context,
    _: Nothing,
) -> Result<cloud_api::library::Orgs, CallError> {
    Ok(library::orgs(&ctx.account, &ctx.db).await?)
}
pub async fn library_mentors(
    ctx: &Context,
    input: cloud_api::library::ListRequest,
) -> Result<library::Mentors, CallError> {
    Ok(library::list(&ctx.account, &ctx.db, &input).await?)
}
pub async fn library_preview(
    ctx: &Context,
    input: LibraryId,
) -> Result<library::Preview, CallError> {
    Ok(library::preview(&ctx.account, &ctx.db, &input.id).await?)
}
pub async fn library_quota(
    ctx: &Context,
    _: Nothing,
) -> Result<cloud_api::library::Quota, CallError> {
    Ok(library::quota(&ctx.account, &ctx.db).await?)
}
pub async fn library_add_preview(
    ctx: &Context,
    input: library::add::Selection,
) -> Result<library::add::Preview, CallError> {
    Ok(library::add::preview(&ctx.account, &ctx.db, &input).await?)
}
pub async fn library_add(
    ctx: &Context,
    input: library::add::Selection,
) -> Result<library::add::Added, CallError> {
    Ok(library::add::add(&ctx.account, &ctx.db, &input, crate::now()).await?)
}
pub async fn library_updates(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<library::updates::Group>, CallError> {
    Ok(library::updates::list(&ctx.db).await?)
}
pub async fn library_apply(
    ctx: &Context,
    input: library::updates::Choices,
) -> Result<u32, CallError> {
    Ok(library::updates::apply(&ctx.db, &input, crate::now()).await?)
}
pub async fn library_update_count(ctx: &Context, _: Nothing) -> Result<i64, CallError> {
    Ok(library::updates::count(&ctx.db).await?)
}
pub async fn library_sync(ctx: &Context, _: Nothing) -> Result<u32, CallError> {
    Ok(ctx
        .library
        .check(&ctx.account, &ctx.db, crate::now(), true)
        .await?)
}

pub async fn run(ctx: std::sync::Arc<Context>) {
    let mut ticks = tokio::time::interval(std::time::Duration::from_secs(60 * 60));
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        ticks.tick().await;
        let result = ctx
            .writes
            .retry("mentor library synchronization", async || {
                match ctx
                    .library
                    .check(&ctx.account, &ctx.db, crate::now(), false)
                    .await
                {
                    Err(library::Error::Database(error)) => Err(error),
                    result => Ok(result),
                }
            })
            .await;
        if let Err(error) = result {
            tracing::warn!(
                category = error.category(),
                "mentor library synchronization failed"
            );
        }
    }
}

impl From<library::Error> for CallError {
    fn from(error: library::Error) -> Self {
        tracing::warn!(
            category = error.category(),
            "mentor library operation failed"
        );
        let code = match error {
            library::Error::Invalid(_) => ErrorCode::Invalid,
            library::Error::Account(account::Error::Server(cloud_api::ApiError {
                code: cloud_api::ErrorCode::NotFound,
                ..
            })) => ErrorCode::NotFound,
            library::Error::Account(_) => ErrorCode::External,
            _ => ErrorCode::Internal,
        };
        Self {
            code,
            message: error.message(),
        }
    }
}
