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
        return Err(CallError {
            code: ErrorCode::NeedsAgreement,
            message: "网页的标题、文字、链接文字及公开的导师资料将发给这个模型的服务商，\
                用于寻找名单和整理资料。"
                .into(),
        });
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
) -> Result<queue::CrawlList, CallError> {
    Ok(queue::list(&ctx.db, input.page).await?)
}
pub(super) async fn crawl_options(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<queue::CrawlOption>, CallError> {
    Ok(queue::options(&ctx.db).await?)
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
    let enrichment = sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE crawl_id = ? AND candidate_id IS NOT NULL
            AND work_state != 'done'
        ) AS "present!: bool""#,
        input.id
    )
    .fetch_one(&ctx.db)
    .await?;
    let ordinary = sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE crawl_id = ? AND candidate_id IS NULL AND work_state != 'done'
        ) AS "present!: bool""#,
        input.id
    )
    .fetch_one(&ctx.db)
    .await?;
    if ordinary {
        crawl_ready(ctx, id).await?;
    }
    if enrichment {
        crawl_ready(ctx, None).await?;
    }
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
    let enrichment = sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE crawl_id = ?1 AND (?2 IS NULL OR id = ?2)
            AND candidate_id IN (SELECT id FROM candidate WHERE review = 'pending')
            AND status != 'success'
        ) AS "present!: bool""#,
        input.id,
        input.page
    )
    .fetch_one(&ctx.db)
    .await?;
    let ordinary = sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE crawl_id = ?1 AND (?2 IS NULL OR id = ?2)
            AND candidate_id IS NULL AND status != 'success'
        ) AS "present!: bool""#,
        input.id,
        input.page
    )
    .fetch_one(&ctx.db)
    .await?;
    if ordinary {
        crawl_ready(ctx, id).await?;
    }
    if enrichment {
        crawl_ready(ctx, None).await?;
    }
    let n = queue::retry(&ctx.db, input.id, input.page, now()).await?;
    ctx.crawler.wake();
    Ok(n)
}
pub(super) async fn delete_crawl(ctx: &Context, input: CrawlId) -> Result<(), CallError> {
    queue::delete(&ctx.db, input.id).await?;
    ctx.crawler.wake();
    Ok(())
}
pub(super) async fn preview_enrich(
    ctx: &Context,
    input: CandidateIds,
) -> Result<crawl::enrich::EnrichPlan, CallError> {
    Ok(crawl::enrich::preview(&ctx.db, &input.ids).await?)
}
pub(super) async fn enrich_candidates(
    ctx: &Context,
    input: CandidateIds,
) -> Result<crawl::enrich::EnrichPlan, CallError> {
    crawl_ready(ctx, None).await?;
    let result = crawl::enrich::start(&ctx.db, &input.ids, now()).await?;
    ctx.crawler.wake();
    Ok(result)
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
            let id = sqlx::query_scalar!(
                r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
                    created_at)
                VALUES ('custom', 'http://127.0.0.1:1', ?, 'fixture', 1, 1)
                RETURNING id"#,
                name
            )
            .fetch_one(&db)
            .await
            .unwrap();
            ids.push(id);
        }
        let ctx = Context {
            writes: db::Writes::default(),
            db: db.clone(),
            net: net::Net::new(vec![]).unwrap(),
            sender: send::Sender::default(),
            receiver: receive::Receiver::default(),
            matcher: matching::Matcher::default(),
            reply_processor: Default::default(),
            todo_reminders: Default::default(),
            generator: task::generation::Generator::default(),
            crawler: crawl::runner::Crawler::default(),
            model_limit: Default::default(),
            mail_domains: Default::default(),
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
    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn enrichment_requires_current_default_consent_instead_of_original_crawl_model(
        db: SqlitePool,
    ) {
        let first = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
                created_at)
            VALUES ('custom', 'http://127.0.0.1:1', 'original', 'fixture', 1, 1)
            RETURNING id"#,
        )
        .fetch_one(&db)
        .await
        .unwrap();
        let default = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
                created_at)
            VALUES ('custom', 'http://127.0.0.1:2', 'default', 'fixture', 0, 1)
            RETURNING id"#,
        )
        .fetch_one(&db)
        .await
        .unwrap();
        sqlx::query("UPDATE profile SET default_model_config_id=?")
            .bind(default)
            .execute(&db)
            .await
            .unwrap();
        let crawl = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO crawl (kind, start_urls, model_config_id, state, created_at,
                finished_at)
            VALUES ('site', '[]', ?, 'done', 1, 1)
            RETURNING id"#,
        )
        .bind(first)
        .fetch_one(&db)
        .await
        .unwrap();
        let id = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name)
            VALUES (?, 'https://school.example.edu/p', 'pending', 'none', '张三')
            RETURNING id"#,
        )
        .bind(crawl)
        .fetch_one(&db)
        .await
        .unwrap();
        let ctx = Context {
            writes: db::Writes::default(),
            db: db.clone(),
            net: net::Net::new(vec![]).unwrap(),
            sender: send::Sender::default(),
            receiver: receive::Receiver::default(),
            matcher: matching::Matcher::default(),
            reply_processor: Default::default(),
            todo_reminders: Default::default(),
            generator: task::generation::Generator::default(),
            crawler: crawl::runner::Crawler::default(),
            model_limit: Default::default(),
            mail_domains: Default::default(),
            materials: Default::default(),
        };
        let error = enrich_candidates(&ctx, CandidateIds { ids: vec![id] })
            .await
            .unwrap_err();
        assert!(matches!(error.code, ErrorCode::NeedsAgreement));
        assert_eq!(
            preview_enrich(&ctx, CandidateIds { ids: vec![id] })
                .await
                .unwrap()
                .ids,
            vec![id]
        );
        model::agree(&db, default, "custom", Some("http://127.0.0.1:2"))
            .await
            .unwrap();
        assert_eq!(
            enrich_candidates(&ctx, CandidateIds { ids: vec![id] })
                .await
                .unwrap()
                .ids,
            vec![id]
        );
        let claim = queue::next(&db).await.unwrap().unwrap();
        assert_eq!(claim.model_id, None);
        assert_eq!(
            model::selected(&db, claim.model_id)
                .await
                .unwrap()
                .unwrap()
                .id,
            default
        );
        let summary = queue::work(&db, 0, i64::MAX).await.unwrap();
        assert_eq!(summary[0].enrich_remaining, 1);
        assert_eq!(summary[0].stage, queue::CrawlStage::Enrich);
        assert_eq!(summary[0].model_ids.0, vec![default]);
    }
}
