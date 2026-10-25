//! Durable page queue. Claims are invalidated before pause/cancel returns.
use crate::{
    PageUrl, Scope, now,
    store::{PageKind, PageMethod, PageStatus},
};
use serde::Serialize;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

pub const LIST_LIMIT: i64 = 60;
pub const PERSON_LIMIT: i64 = 1000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum CrawlState {
    Running,
    Paused,
    Done,
    Failed,
    Canceled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum CrawlStage {
    Lists,
    Profiles,
    Enrich,
    Paused,
    Done,
    Failed,
    Canceled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum PageState {
    Queued,
    Running,
    Done,
}

pub fn starts(input: &str) -> Result<Vec<PageUrl>, String> {
    let mut urls = Vec::new();
    for line in input.lines().map(str::trim).filter(|s| !s.is_empty()) {
        let url = PageUrl::parse(line).map_err(|e| e.message())?;
        Scope::new(&url)
            .page(url.clone())
            .map_err(|e| e.message())?;
        if !urls.contains(&url) {
            urls.push(url);
        }
    }
    if urls.is_empty() {
        return Err("请填写至少一个学院官网或教师名单网址".into());
    }
    if urls.len() > LIST_LIMIT as usize {
        return Err("起点网址不能超过 60 个".into());
    }
    Ok(urls)
}
pub async fn start(
    db: &SqlitePool,
    urls: &[PageUrl],
    model_id: i64,
    at: i64,
) -> Result<i64, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let strings: Vec<_> = urls.iter().map(PageUrl::as_str).collect();
    let id = sqlx::query_scalar!("INSERT INTO crawl (kind,start_urls,model_config_id,state,created_at) VALUES ('site',?,?, 'running',?) RETURNING id", Json(strings),model_id,at).fetch_one(&mut *tx).await?;
    for url in urls {
        enqueue(&mut tx, id, url, url, 0, PageKind::Entry).await?;
    }
    open_run(&mut tx, id, at).await?;
    tx.commit().await?;
    Ok(id)
}
pub(crate) async fn open_run(
    conn: &mut SqliteConnection,
    id: i64,
    at: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "INSERT INTO crawl_run (crawl_id,app_version,started_at) VALUES (?,?,?)",
        id,
        env!("CARGO_PKG_VERSION"),
        at
    )
    .execute(conn)
    .await?;
    Ok(())
}
pub async fn enqueue(
    conn: &mut SqliteConnection,
    crawl: i64,
    url: &PageUrl,
    scope: &PageUrl,
    depth: i64,
    kind: PageKind,
) -> Result<(), sqlx::Error> {
    if sqlx::query_scalar!(
        "SELECT id FROM crawl_page WHERE crawl_id = ? AND url = ? AND steps = '[]' AND candidate_id IS NULL",
        crawl,
        url
    )
    .fetch_optional(&mut *conn)
    .await?
    .is_some()
    {
        return Ok(());
    }
    if kind != PageKind::Profile
        && sqlx::query_scalar!(
            "SELECT count(*) FROM crawl_page WHERE crawl_id = ? AND kind IN ('entry','list')",
            crawl
        )
        .fetch_one(&mut *conn)
        .await?
            >= LIST_LIMIT
    {
        limit(conn, crawl, "找名单已达到 60 页上限").await?;
        return Ok(());
    }
    sqlx::query!("INSERT INTO crawl_page (crawl_id,url,scope_url,depth,kind,mode,status,error,fetched_at,work_state) VALUES (?,?,?,?,?,?,?,'尚未读取',NULL,?)",crawl,url,scope,depth,kind,PageMethod::Http,PageStatus::Failed,PageState::Queued).execute(conn).await?;
    Ok(())
}
pub(crate) async fn limit(
    conn: &mut SqliteConnection,
    id: i64,
    reason: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE crawl SET limits = json_insert(limits,'$[#]',?2) WHERE id = ?1 AND NOT EXISTS (SELECT 1 FROM json_each(limits) WHERE value = ?2)",id,reason).execute(conn).await?;
    Ok(())
}
#[derive(Debug, Clone)]
pub struct Claim {
    pub id: i64,
    pub crawl: i64,
    pub generation: i64,
    pub url: PageUrl,
    pub scope: PageUrl,
    pub depth: i64,
    pub kind: PageKind,
    pub model_id: Option<i64>,
    pub candidate_id: Option<i64>,
}
pub async fn next(db: &SqlitePool) -> Result<Option<Claim>, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let row=sqlx::query!(r#"UPDATE crawl_page SET work_state = 'running',claim = claim + 1 WHERE id = (SELECT p.id FROM crawl_page p JOIN crawl c ON c.id = p.crawl_id WHERE p.work_state = 'queued' AND c.state = 'running' ORDER BY p.kind = 'profile', p.id LIMIT 1) RETURNING id,crawl_id,claim,url AS "url: PageUrl",scope_url AS "scope!: PageUrl",depth,kind AS "kind: PageKind",candidate_id"#,).fetch_optional(&mut *tx).await?;
    let result = if let Some(p) = row {
        let model_id =
            sqlx::query_scalar!("SELECT model_config_id FROM crawl WHERE id = ?", p.crawl_id)
                .fetch_one(&mut *tx)
                .await?;
        Some(Claim {
            id: p.id,
            crawl: p.crawl_id,
            generation: p.claim,
            url: p.url,
            scope: p.scope,
            depth: p.depth,
            kind: p.kind,
            model_id: if p.candidate_id.is_some() {
                None
            } else {
                model_id
            },
            candidate_id: p.candidate_id,
        })
    } else {
        None
    };
    if let Some(c) = &result
        && let Some(id) = c.candidate_id
    {
        sqlx::query!("UPDATE candidate SET enrich='running' WHERE id=? AND enrich='queued' AND review='pending'",id).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    Ok(result)
}
pub async fn valid(conn: &mut SqliteConnection, c: &Claim) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!("SELECT EXISTS(SELECT 1 FROM crawl_page p JOIN crawl c ON c.id = p.crawl_id WHERE p.id = ? AND p.claim = ? AND p.work_state = 'running' AND c.state = 'running' AND (p.candidate_id IS NULL OR EXISTS(SELECT 1 FROM candidate WHERE id=p.candidate_id AND review='pending' AND enrich IN ('queued','running')))) AS \"valid!: bool\"",c.id,c.generation).fetch_one(conn).await
}
pub async fn control(db: &SqlitePool, id: i64, action: &str, at: i64) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let done=match action {
        "pause"=>sqlx::query!("UPDATE crawl SET state = 'paused',error = NULL WHERE id = ? AND state = 'running'",id).execute(&mut *tx).await?.rows_affected(),
        "resume"=>sqlx::query!("UPDATE crawl SET state = 'running',error = NULL,finished_at = NULL WHERE id = ? AND state = 'paused'",id).execute(&mut *tx).await?.rows_affected(),
        "cancel"=>sqlx::query!("UPDATE crawl SET state = 'canceled',error = NULL,finished_at = ? WHERE id = ? AND state IN ('running','paused')",at,id).execute(&mut *tx).await?.rows_affected(),
        _=>return Ok(false),
    };
    if done == 0 {
        return Ok(false);
    }
    sqlx::query!("UPDATE crawl_page SET work_state = 'queued',claim = claim + 1 WHERE crawl_id = ? AND work_state = 'running'",id).execute(&mut *tx).await?;
    if action == "cancel" {
        sqlx::query!("UPDATE candidate SET enrich='none' WHERE crawl_id=? AND enrich IN ('queued','running')",id).execute(&mut *tx).await?;
        sqlx::query!(
            "DELETE FROM crawl_page WHERE crawl_id = ? AND fetched_at IS NULL",
            id
        )
        .execute(&mut *tx)
        .await?;
        sqlx::query!("UPDATE crawl_page SET work_state = 'done',status = CASE WHEN status = 'success' THEN 'failed' ELSE status END,error = CASE WHEN status = 'success' THEN '已取消，页面资料尚未整理完成' ELSE error END WHERE crawl_id = ? AND work_state != 'done'",id).execute(&mut *tx).await?;
    }
    sqlx::query!(
        "UPDATE crawl_run SET ended_at = ? WHERE crawl_id = ? AND ended_at IS NULL",
        at,
        id
    )
    .execute(&mut *tx)
    .await?;
    if action == "resume" {
        open_run(&mut tx, id, at).await?;
    }
    tx.commit().await?;
    Ok(true)
}
pub async fn retry(
    db: &SqlitePool,
    id: i64,
    page: Option<i64>,
    at: i64,
) -> Result<u64, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let n=sqlx::query!("UPDATE crawl_page SET work_state = 'queued',claim = claim + 1 WHERE crawl_id = ?1 AND (?2 IS NULL OR id = ?2) AND work_state = 'done' AND status != 'success' AND (candidate_id IS NULL OR candidate_id IN (SELECT id FROM candidate WHERE review='pending'))",id,page).execute(&mut *tx).await?.rows_affected();
    if n > 0 {
        sqlx::query!("UPDATE candidate SET enrich='queued',enrich_error=NULL WHERE review='pending' AND id IN (SELECT candidate_id FROM crawl_page WHERE crawl_id=? AND work_state='queued')",id).execute(&mut *tx).await?;
        let resumed=sqlx::query!("UPDATE crawl SET state = 'running',finished_at = NULL,error = NULL WHERE id = ? AND state != 'running'",id).execute(&mut *tx).await?.rows_affected();
        if resumed > 0 {
            open_run(&mut tx, id, at).await?;
        }
    }
    tx.commit().await?;
    Ok(n)
}
pub async fn recover(db: &SqlitePool) -> Result<(), sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    sqlx::query!(
        "UPDATE crawl_page SET work_state = 'queued',claim = claim + 1 WHERE work_state = 'running'"
    )
    .execute(&mut *tx)
    .await?;
    let at = now();
    sqlx::query!(
        "UPDATE crawl_run SET ended_at = ? WHERE ended_at IS NULL",
        at
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!("INSERT INTO crawl_run (crawl_id,app_version,started_at) SELECT id,?,? FROM crawl WHERE state = 'running'",env!("CARGO_PKG_VERSION"),at).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(())
}
#[derive(Debug, Serialize, TS, sqlx::FromRow)]
#[serde(rename_all = "camelCase")]
pub struct CrawlSummary {
    pub id: i64,
    pub url: Option<String>,
    pub model_config_id: Option<i64>,
    pub state: CrawlState,
    pub error: Option<String>,
    pub created_at: i64,
    pub finished_at: Option<i64>,
    pub pages: i64,
    pub remaining: i64,
    pub enrich_remaining: i64,
    #[ts(as = "Vec<i64>")]
    pub model_ids: Json<Vec<i64>>,
    pub profiles: i64,
    pub candidates: i64,
    pub failed: i64,
    pub tokens: Option<i64>,
    pub stage: CrawlStage,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlOption {
    pub id: i64,
    pub url: Option<String>,
    pub created_at: i64,
}
pub async fn options(db: &SqlitePool) -> Result<Vec<CrawlOption>, sqlx::Error> {
    sqlx::query_as!(CrawlOption, r#"SELECT id,json_extract(start_urls,'$[0]') AS "url: String",created_at FROM crawl ORDER BY created_at DESC,id DESC"#).fetch_all(db).await
}
pub async fn list(db: &SqlitePool, page: u32) -> Result<Vec<CrawlSummary>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let offset = i64::from(page.saturating_sub(1)) * 50;
    let ids = sqlx::query_scalar!(
        "SELECT id FROM crawl ORDER BY id DESC LIMIT 50 OFFSET ?",
        offset
    )
    .fetch_all(&mut *tx)
    .await?;
    summaries(&mut tx, &ids).await
}
pub async fn work(db: &SqlitePool, start: i64, end: i64) -> Result<Vec<CrawlSummary>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let ids = sqlx::query_scalar!("SELECT id FROM crawl WHERE state IN ('running','paused','failed') OR (state = 'done' AND finished_at >= ? AND finished_at < ?) OR id IN (SELECT crawl_id FROM crawl_page WHERE work_state = 'done' AND status != 'success') ORDER BY id DESC",start,end).fetch_all(&mut *tx).await?;
    summaries(&mut tx, &ids).await
}
async fn summaries(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<Vec<CrawlSummary>, sqlx::Error> {
    sqlx::query_as!(CrawlSummary,r#"SELECT c.id,json_extract(c.start_urls,'$[0]') AS "url: String",c.model_config_id,c.state AS "state: CrawlState",c.error,c.created_at,c.finished_at,
      (SELECT count(*) FROM crawl_page WHERE crawl_id = c.id AND work_state = 'done') AS "pages!: i64",
      (SELECT count(*) FROM crawl_page WHERE crawl_id = c.id AND work_state IN ('queued','running')) AS "remaining!: i64",
      (SELECT count(*) FROM candidate WHERE crawl_id=c.id AND review='pending' AND enrich IN ('queued','running')) AS "enrich_remaining!: i64",
      (SELECT json_group_array(id) FROM model_config WHERE id IN (
        SELECT CASE WHEN candidate_id IS NULL THEN coalesce(c.model_config_id,(SELECT default_model_config_id FROM profile),(SELECT min(id) FROM model_config)) ELSE coalesce((SELECT default_model_config_id FROM profile),(SELECT min(id) FROM model_config)) END
        FROM crawl_page WHERE crawl_id=c.id AND (work_state!='done' OR status!='success') AND (candidate_id IS NULL OR candidate_id IN (SELECT id FROM candidate WHERE review='pending'))
      )) AS "model_ids!: Json<Vec<i64>>",
      (SELECT count(*) FROM crawl_page WHERE crawl_id = c.id AND kind = 'profile' AND work_state = 'done' AND status = 'success') AS "profiles!: i64",
      (SELECT count(*) FROM candidate WHERE crawl_id = c.id) AS "candidates!: i64",
      (SELECT count(*) FROM crawl_page WHERE crawl_id = c.id AND work_state = 'done' AND status IN ('failed','needs_browser')) AS "failed!: i64",
      (SELECT sum(input_tokens+output_tokens) FROM usage WHERE crawl_id = c.id) AS "tokens: i64",
      CASE WHEN c.state != 'running' THEN c.state WHEN EXISTS(SELECT 1 FROM crawl_page WHERE crawl_id = c.id AND kind IN ('entry','list') AND work_state IN ('queued','running')) THEN 'lists' WHEN EXISTS(SELECT 1 FROM crawl_page WHERE crawl_id=c.id AND kind='profile' AND work_state IN ('queued','running')) THEN 'profiles' ELSE 'enrich' END AS "stage!: CrawlStage"
      FROM crawl c WHERE c.id IN (SELECT value FROM json_each(?)) ORDER BY c.id DESC"#,Json(ids)).fetch_all(conn).await
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlDetail {
    pub summary: CrawlSummary,
    pub urls: Vec<String>,
    pub limits: Vec<String>,
    pub pages: Vec<CrawlPage>,
    pub runs: Vec<CrawlRun>,
    pub page_total: i64,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlPage {
    pub id: i64,
    pub url: String,
    pub kind: PageKind,
    pub state: PageState,
    pub status: PageStatus,
    pub error: Option<String>,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlRun {
    pub app_version: String,
    pub started_at: i64,
    pub ended_at: Option<i64>,
}
pub async fn detail(
    db: &SqlitePool,
    id: i64,
    page: u32,
) -> Result<Option<CrawlDetail>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(row)=sqlx::query!(r#"SELECT start_urls AS "urls: Json<Vec<String>>", limits AS "limits: Json<Vec<String>>" FROM crawl WHERE id = ?"#,id).fetch_optional(&mut *tx).await? else {return Ok(None)};
    let offset = i64::from(page.saturating_sub(1)) * 50;
    let pages=sqlx::query_as!(CrawlPage,r#"SELECT id,url,kind AS "kind: PageKind",work_state AS "state: PageState",status AS "status: PageStatus",error FROM crawl_page WHERE crawl_id = ? ORDER BY status = 'success', id LIMIT 50 OFFSET ?"#,id,offset).fetch_all(&mut *tx).await?;
    let page_total = sqlx::query_scalar!("SELECT count(*) FROM crawl_page WHERE crawl_id = ?", id)
        .fetch_one(&mut *tx)
        .await?;
    let runs=sqlx::query_as!(CrawlRun,"SELECT app_version,started_at,ended_at FROM crawl_run WHERE crawl_id = ? ORDER BY id DESC LIMIT 50",id).fetch_all(&mut *tx).await?;
    let summary = summaries(&mut tx, &[id])
        .await?
        .pop()
        .ok_or(sqlx::Error::RowNotFound)?;
    Ok(Some(CrawlDetail {
        summary,
        urls: row.urls.map(|Json(v)| v).unwrap_or_default(),
        limits: row.limits.0,
        pages,
        runs,
        page_total,
    }))
}
pub async fn delete(db: &SqlitePool, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("DELETE FROM crawl WHERE id = ?", id)
        .execute(db)
        .await?;
    Ok(())
}
