use super::*;
use crawl::{queue, review};
#[derive(Debug, Deserialize, Serialize, TS)]
pub struct CrawlId {
    pub id: i64,
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlStart {
    pub urls: String,
    pub model_id: i64,
}
#[derive(Debug, Deserialize, TS)]
pub struct CrawlQuery {
    pub page: u32,
}
#[derive(Debug, Deserialize, TS)]
pub struct CrawlDetailQuery {
    pub id: i64,
    pub page: u32,
}
#[derive(Debug, Deserialize, TS)]
pub struct CrawlRetry {
    pub id: i64,
    pub page: Option<i64>,
}
#[derive(Debug, Deserialize, TS)]
pub struct CandidateIds {
    pub ids: Vec<i64>,
}
async fn crawl_ready(ctx: &Context, id: Option<i64>) -> Result<model::Chosen, CallError> {
    let m = model::selected(&ctx.db, id).await?.ok_or_else(|| {
        CallError::invalid("尚未添加模型，请在“设置 › AI › 模型”中添加后再开始。".into())
    })?;
    if !m.agreed {
        return Err(CallError{code:ErrorCode::NeedsAgreement,message:"网页的标题、文字、链接文字及公开的导师资料将发给这个模型的服务商，用于寻找名单和整理资料。".into()});
    }
    Ok(m)
}
#[derive(Debug, Deserialize, TS)]
pub struct CrawlUrls {
    pub urls: String,
}
#[derive(Debug, Deserialize, TS)]
pub struct CrawlWorkQuery {
    pub start: i64,
    pub end: i64,
}
pub(super) async fn check_crawl_urls(
    _: &Context,
    input: CrawlUrls,
) -> Result<Vec<String>, CallError> {
    Ok(queue::starts(&input.urls)
        .map_err(CallError::invalid)?
        .iter()
        .map(|u| u.as_str().to_owned())
        .collect())
}
pub(super) async fn crawl_work(
    ctx: &Context,
    input: CrawlWorkQuery,
) -> Result<Vec<queue::CrawlSummary>, CallError> {
    Ok(queue::work(&ctx.db, input.start, input.end).await?)
}
pub(super) async fn start_crawl(ctx: &Context, input: CrawlStart) -> Result<CrawlId, CallError> {
    let urls = queue::starts(&input.urls).map_err(CallError::invalid)?;
    let m = crawl_ready(ctx, Some(input.model_id)).await?;
    let id = queue::start(&ctx.db, &urls, m.id, now()).await?;
    ctx.crawler.wake();
    Ok(CrawlId { id })
}
pub(super) async fn list_crawls(
    ctx: &Context,
    input: CrawlQuery,
) -> Result<Vec<queue::CrawlSummary>, CallError> {
    Ok(queue::list(&ctx.db, input.page).await?)
}
pub(super) async fn get_crawl(
    ctx: &Context,
    input: CrawlDetailQuery,
) -> Result<queue::CrawlDetail, CallError> {
    queue::detail(&ctx.db, input.id, input.page)
        .await?
        .ok_or_else(|| CallError::not_found("这次抓取已被删除"))
}
pub(super) async fn pause_crawl(ctx: &Context, input: CrawlId) -> Result<(), CallError> {
    queue::control(&ctx.db, input.id, "pause", now()).await?;
    ctx.crawler.wake();
    Ok(())
}
pub(super) async fn resume_crawl(ctx: &Context, input: CrawlId) -> Result<(), CallError> {
    let id = sqlx::query_scalar!("SELECT model_config_id FROM crawl WHERE id = ?", input.id)
        .fetch_optional(&ctx.db)
        .await?
        .flatten();
    crawl_ready(ctx, id).await?;
    queue::control(&ctx.db, input.id, "resume", now()).await?;
    ctx.crawler.wake();
    Ok(())
}
pub(super) async fn cancel_crawl(ctx: &Context, input: CrawlId) -> Result<(), CallError> {
    queue::control(&ctx.db, input.id, "cancel", now()).await?;
    ctx.crawler.wake();
    Ok(())
}
pub(super) async fn retry_crawl(ctx: &Context, input: CrawlRetry) -> Result<u64, CallError> {
    let id = sqlx::query_scalar!("SELECT model_config_id FROM crawl WHERE id = ?", input.id)
        .fetch_optional(&ctx.db)
        .await?
        .flatten();
    crawl_ready(ctx, id).await?;
    let n = queue::retry(&ctx.db, input.id, input.page, now()).await?;
    ctx.crawler.wake();
    Ok(n)
}
pub(super) async fn delete_crawl(ctx: &Context, input: CrawlId) -> Result<(), CallError> {
    queue::delete(&ctx.db, input.id).await?;
    ctx.crawler.wake();
    Ok(())
}
pub(super) async fn list_candidates(
    ctx: &Context,
    input: review::CandidateQuery,
) -> Result<review::CandidatePage, CallError> {
    Ok(review::list(&ctx.db, &input).await?)
}
pub(super) async fn get_candidate(
    ctx: &Context,
    input: CrawlId,
) -> Result<review::CandidateDetail, CallError> {
    review::detail(&ctx.db, input.id)
        .await?
        .ok_or_else(|| CallError::not_found("这位候选已被删除"))
}
pub(super) async fn preview_candidates(
    ctx: &Context,
    input: review::ReviewInput,
) -> Result<mentor::import::Preview, CallError> {
    Ok(review::preview(&ctx.db, &input).await?)
}
pub(super) async fn accept_candidates(
    ctx: &Context,
    input: review::ReviewInput,
) -> Result<review::Reviewed, CallError> {
    Ok(review::accept(&ctx.db, &input, now()).await?)
}
pub(super) async fn reject_candidates(
    ctx: &Context,
    input: CandidateIds,
) -> Result<u64, CallError> {
    Ok(review::reject(&ctx.db, &input.ids).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn starting_uses_the_selected_model(db: SqlitePool) {
        let mut ids = Vec::new();
        for name in ["first", "second"] {
            let id=sqlx::query_scalar!("INSERT INTO model_config (provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://127.0.0.1:1',?,'fixture',1,1) RETURNING id",name).fetch_one(&db).await.unwrap();
            ids.push(id);
        }
        let ctx = Context {
            db: db.clone(),
            net: net::Net::new(vec![]).unwrap(),
            sender: send::Sender::default(),
            receiver: receive::Receiver::default(),
            matcher: matching::Matcher::default(),
            generator: task::generation::Generator::default(),
            crawler: crawl::runner::Crawler::default(),
            model_limit: Default::default(),
            materials: Default::default(),
        };
        sqlx::query!("UPDATE profile SET default_model_config_id = ?", ids[1])
            .execute(&db)
            .await
            .unwrap();
        let started = start_crawl(
            &ctx,
            CrawlStart {
                urls: "https://school.example.edu/".into(),
                model_id: ids[0],
            },
        )
        .await
        .unwrap();
        assert_eq!(
            sqlx::query_scalar!("SELECT model_config_id FROM crawl WHERE id = ?", started.id)
                .fetch_one(&db)
                .await
                .unwrap(),
            Some(ids[0])
        );
    }
}
