//! Background HTTP/model work. Page results and newly discovered work commit together.
use crate::{
    Material, PageTarget, PageUrl, Scope,
    extract::{self, Kind, Person},
    fetch::Fetcher,
    now,
    queue::{self, Claim},
    review,
    store::{PageKind, PageStatus},
};
use model::{
    Chosen,
    background::{CONCURRENCY, Failure, Limit},
};
use serde::Serialize;
use sqlx::{SqlitePool, types::Json};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{sync::Notify, task::JoinSet, time::Instant};
use ts_rs::TS;
const RETRY: Duration = Duration::from_secs(5);
#[derive(Clone, Default)]
pub struct Crawler(Arc<Notify>);
impl Crawler {
    pub fn wake(&self) {
        self.0.notify_one();
    }
}
#[derive(Debug, Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlFinished {
    pub id: i64,
    pub candidates: i64,
    pub failed: i64,
}
struct Page {
    material: Material,
    kind: PageKind,
    people: Vec<Person>,
    next: Vec<PageUrl>,
}
enum Outcome {
    Done(Page),
    Failed(String),
    Pause(String, Option<Chosen>),
    RateLimit,
    Gone,
}

enum ReadError {
    Failed(String),
    Pause(String),
    RateLimit,
    Gone,
}

#[derive(Clone, Default)]
struct Cooldown(Arc<Mutex<Option<Instant>>>);
impl Cooldown {
    fn until(&self) -> Option<Instant> {
        self.0
            .lock()
            .unwrap_or_else(|_| {
                unreachable!("cooldown updates cannot panic while holding the lock")
            })
            .filter(|at| *at > Instant::now())
    }
    fn defer(&self, wait: Duration) {
        let at = Instant::now() + wait;
        let mut current = self.0.lock().unwrap_or_else(|_| {
            unreachable!("cooldown updates cannot panic while holding the lock")
        });
        *current = Some(current.map_or(at, |old| old.max(at)));
    }
    async fn acquire(&self, limit: &Limit) -> tokio::sync::OwnedSemaphorePermit {
        loop {
            if let Some(at) = self.until() {
                tokio::time::sleep_until(at).await;
            }
            let permit = limit.acquire().await;
            if self.until().is_none() {
                return permit;
            }
        }
    }
}

struct Reader<'a> {
    db: &'a SqlitePool,
    net: &'a net::Net,
    limit: &'a Limit,
    claim: &'a Claim,
    chosen: &'a Chosen,
    cooldown: &'a Cooldown,
}
impl Reader<'_> {
    async fn ask<T: extract::Output>(
        &self,
        system: &str,
        data: serde_json::Value,
    ) -> Result<T, ReadError> {
        let mut rules = system.to_owned();
        for attempt in 0..2 {
            let _permit = loop {
                let permit = self.cooldown.acquire(self.limit).await;
                let mut conn = self.db.acquire().await.map_err(|e| {
                    tracing::error!("crawl connection: {e}");
                    ReadError::Pause("抓取数据暂时无法读取，请继续或重启应用。".into())
                })?;
                if !queue::valid(&mut conn, self.claim).await.map_err(|_| {
                    ReadError::Pause("抓取数据暂时无法读取，请继续或重启应用。".into())
                })? {
                    return Err(ReadError::Gone);
                }
                let current = model::selected(&mut *conn, Some(self.chosen.id))
                    .await
                    .map_err(|_| ReadError::Pause("模型配置暂时无法读取，请重试。".into()))?;
                if !current.as_ref().is_some_and(|m| {
                    m.agreed
                        && m.provider == self.chosen.provider
                        && model::background::same_config(m, self.chosen)
                }) {
                    return Err(ReadError::Gone);
                }
                if self.cooldown.until().is_none() {
                    break permit;
                }
            };
            let answer = model::ask(
                self.net,
                &self.chosen.access,
                &self.chosen.model,
                &rules,
                &data.to_string(),
            )
            .await
            .map_err(|e| match model::background::classify(e) {
                Failure::Pause(r) => ReadError::Pause(r),
                Failure::RateLimit(w) => {
                    self.cooldown.defer(w);
                    ReadError::RateLimit
                }
                Failure::Failed(r) => ReadError::Failed(r),
            })?;
            if let Some(usage) = answer.usage {
                while let Err(e) = model::usage::record_crawl(
                    self.db,
                    self.chosen.id,
                    &self.chosen.model,
                    self.claim.crawl,
                    usage,
                    now(),
                )
                .await
                {
                    tracing::error!("record crawl usage: {e}");
                    tokio::time::sleep(RETRY).await;
                }
            }
            if answer.finish_reason.as_deref() == Some("length") {
                return Err(ReadError::Failed(
                    "模型的回答被截断，请换用支持更长输出的模型后重试。".into(),
                ));
            }
            match extract::parse(&answer.text) {
                Ok(v) => return Ok(v),
                Err(reason) if attempt == 0 => {
                    rules =
                        format!("{system}\n上次输出未通过字段校验：{reason}。请重新输出完整 JSON。")
                }
                Err(reason) => {
                    return Err(ReadError::Failed(format!(
                        "模型返回的资料不合格：{reason}。请重试或换一个模型。"
                    )));
                }
            }
        }
        unreachable!("both attempts return")
    }
}
async fn process(
    db: &SqlitePool,
    net: &net::Net,
    fetcher: &Fetcher,
    limit: &Limit,
    c: &Claim,
    cooldown: &Cooldown,
) -> Result<Outcome, sqlx::Error> {
    let Some(chosen) = model::selected(db, c.model_id).await? else {
        return Ok(Outcome::Pause(
            "尚未添加模型，请在“设置 › AI › 模型”中添加后继续。".into(),
            None,
        ));
    };
    if !chosen.agreed {
        return Ok(Outcome::Pause(
            "模型配置需要确认，请确认网页内容将发往的服务商后继续。".into(),
            Some(chosen),
        ));
    }
    Ok(
        match read(db, net, fetcher, limit, c, &chosen, cooldown).await {
            Ok(page) => Outcome::Done(page),
            Err(ReadError::Pause(reason)) => Outcome::Pause(reason, Some(chosen)),
            Err(ReadError::Failed(reason)) => Outcome::Failed(reason),
            Err(ReadError::RateLimit) => Outcome::RateLimit,
            Err(ReadError::Gone) => Outcome::Gone,
        },
    )
}

async fn read(
    db: &SqlitePool,
    net: &net::Net,
    fetcher: &Fetcher,
    limit: &Limit,
    c: &Claim,
    chosen: &Chosen,
    cooldown: &Cooldown,
) -> Result<Page, ReadError> {
    let scope = Scope::new(&c.scope);
    // External targets can only have been persisted from Scope::linked_profile.
    let target = if c.kind == PageKind::Profile {
        PageTarget { url: c.url.clone() }
    } else {
        scope
            .page(c.url.clone())
            .map_err(|e| ReadError::Failed(e.message()))?
    };
    let m = fetcher
        .fetch(&target)
        .await
        .map_err(|e| ReadError::Failed(e.message()))?
        .material;
    let text = m.text();
    let reason = m.needs_browser.as_ref().map(browser);
    let status = if reason.is_some() {
        PageStatus::NeedsBrowser
    } else {
        PageStatus::Success
    };
    let saved=sqlx::query!("UPDATE crawl_page SET text = ?,status = ?,error = ?,fetched_at = ? WHERE id = ? AND claim = ? AND work_state = 'running' AND EXISTS(SELECT 1 FROM crawl WHERE id = crawl_page.crawl_id AND state = 'running')",text,status,reason,now(),c.id,c.generation).execute(db).await.map_err(|e|{tracing::error!("save fetched page: {e}");ReadError::Pause("网页资料暂时无法保存，请继续或重启应用。".into())})?;
    if saved.rows_affected() == 0 {
        return Err(ReadError::Gone);
    }
    let reader = Reader {
        db,
        net,
        limit,
        claim: c,
        chosen,
        cooldown,
    };
    if m.units.is_empty()
        || matches!(
            m.needs_browser,
            Some(crate::BrowserReason::Challenge | crate::BrowserReason::JavaScript)
        )
    {
        return Ok(Page {
            material: m,
            kind: if c.kind == PageKind::Profile {
                PageKind::Profile
            } else {
                PageKind::Entry
            },
            people: vec![],
            next: vec![],
        });
    }
    let route = if c.kind == PageKind::Profile {
        extract::Route {
            kind: Kind::Profile,
            next: vec![],
        }
    } else {
        reader
            .ask::<extract::Route>(extract::ROUTE, extract::routing(&m, &scope))
            .await?
    };
    let mut next: Vec<PageUrl> = Vec::new();
    let kind = match route.kind {
        Kind::Profile => PageKind::Profile,
        Kind::List => PageKind::List,
        Kind::Entry | Kind::Other => PageKind::Entry,
    };
    if kind != PageKind::Profile {
        let links = extract::links(&m);
        for id in route.next {
            if let Some((_, url)) = id.checked_sub(1).and_then(|n| links.get(n))
                && scope.page((*url).clone()).is_ok()
                && !m.pagination.urls.contains(url)
                && !next.contains(*url)
            {
                next.push((*url).clone());
            }
        }
    }
    let people = match route.kind {
        Kind::List => {
            let mut all = Vec::new();
            for range in extract::segments(&m) {
                all.extend(
                    reader
                        .ask::<extract::People>(extract::LIST, extract::data(&m, range))
                        .await?
                        .people,
                );
            }
            extract::listed(&m, all, &scope).map_err(ReadError::Failed)?
        }
        Kind::Profile => {
            let p = reader
                .ask::<extract::Profile>(extract::PROFILE, extract::data(&m, 0..m.units.len()))
                .await?;
            let mut p = extract::profile(&m, p).map_err(ReadError::Failed)?;
            p.profile = Some(c.url.clone());
            vec![p]
        }
        _ => vec![],
    };
    Ok(Page {
        material: m,
        kind,
        people,
        next,
    })
}
fn browser(reason: &crate::BrowserReason) -> &'static str {
    match reason {
        crate::BrowserReason::Challenge => "网站要求浏览器验证",
        crate::BrowserReason::JavaScript => "页面需要 JavaScript 渲染",
        crate::BrowserReason::EncryptedContent => "页面内容需要浏览器解密",
        crate::BrowserReason::Pagination => "名单需要在浏览器中点击翻页",
    }
}
async fn save(db: &SqlitePool, c: &Claim, outcome: &Outcome) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    if !queue::valid(&mut tx, c).await? {
        return Ok(false);
    }
    let redirect_failure;
    let outcome = if let Outcome::Done(p) = outcome
        && c.kind == PageKind::Profile
        && !p.material.url.is_normalization_of(&c.url)
        && let Some(person) = p.people.first()
    {
        let names = Json(
            std::iter::once(&person.fields.name)
                .chain(&person.fields.aliases)
                .map(|name| mentor::intake::comparable(name))
                .collect::<Vec<_>>(),
        );
        let candidates = sqlx::query!(
            r#"SELECT c.name, EXISTS (
                 SELECT 1 FROM json_each(c.match_names) a JOIN json_each(?) b
                 ON a.value = b.value
               ) AS "matched!: bool"
               FROM candidate_profile p JOIN candidate c ON c.id = p.candidate_id
               WHERE p.crawl_id = ? AND p.url = ? ORDER BY c.id"#,
            names,
            c.crawl,
            c.url,
        )
        .fetch_all(&mut *tx)
        .await?;
        if candidates.iter().any(|c| c.matched) {
            outcome
        } else {
            let original_names = candidates
                .into_iter()
                .map(|c| c.name)
                .collect::<Vec<_>>()
                .join("、");
            redirect_failure = Outcome::Failed(format!(
                "个人页跳到了 {}，读到的姓名是「{}」，和名单上的「{}」对不上，没有补入",
                p.material.url.as_str(),
                person.fields.name,
                original_names,
            ));
            &redirect_failure
        }
    } else {
        outcome
    };
    match outcome {
        Outcome::Done(p) => {
            let text = p.material.text();
            let reason = p.material.needs_browser.as_ref().map(browser);
            let status = if reason.is_some() {
                PageStatus::NeedsBrowser
            } else {
                PageStatus::Success
            };
            sqlx::query!("UPDATE crawl_page SET work_state = 'done',kind = ?,status = ?,error = ?,text = ?,fetched_at = ? WHERE id = ?",p.kind,status,reason,text,now(),c.id).execute(&mut *tx).await?;
            for url in &p.next {
                if c.depth < 2 {
                    queue::enqueue(
                        &mut tx,
                        c.crawl,
                        url,
                        &c.scope,
                        c.depth + 1,
                        PageKind::Entry,
                    )
                    .await?;
                } else {
                    let target = url.as_str();
                    if !sqlx::query_scalar!(r#"SELECT EXISTS(SELECT 1 FROM crawl_page WHERE crawl_id = ? AND url = ?) AS "found!: bool""#,c.crawl,target).fetch_one(&mut *tx).await? {
                        queue::limit(&mut tx, c.crawl, "寻找名单已达到两层链接范围").await?;
                    }
                }
            }
            if p.kind == PageKind::List {
                for url in &p.material.pagination.urls {
                    if Scope::new(&c.scope).page(url.clone()).is_ok() {
                        queue::enqueue(&mut tx, c.crawl, url, &c.scope, c.depth, PageKind::List)
                            .await?;
                    }
                }
            }
            // The transaction serializes duplicate detection with all candidate writes.
            for person in &p.people {
                review::save(
                    &mut tx,
                    c.crawl,
                    c.id,
                    p.kind,
                    p.material.url.as_str(),
                    Person {
                        fields: person.fields.clone(),
                        papers: person.papers.clone(),
                        profile: person.profile.clone(),
                    },
                )
                .await?;
            }
        }
        Outcome::Failed(reason) => {
            sqlx::query!("UPDATE crawl_page SET work_state = 'done',status = 'failed',error = ?,fetched_at = ? WHERE id = ?",reason,now(),c.id).execute(&mut *tx).await?;
        }
        Outcome::Pause(reason, expected) => {
            sqlx::query!(
                "UPDATE crawl_page SET work_state = 'queued' WHERE id = ?",
                c.id
            )
            .execute(&mut *tx)
            .await?;
            let current = model::selected(&mut *tx, c.model_id).await?;
            if expected.as_ref().is_none_or(|e| {
                current
                    .as_ref()
                    .is_some_and(|m| model::background::same_config(m, e))
            }) {
                sqlx::query!("UPDATE crawl SET state = 'paused',error = ? WHERE id = ? AND state = 'running'",reason,c.crawl).execute(&mut *tx).await?;
                sqlx::query!("UPDATE crawl_page SET work_state = 'queued',claim = claim + 1 WHERE crawl_id = ? AND work_state = 'running'",c.crawl).execute(&mut *tx).await?;
                sqlx::query!(
                    "UPDATE crawl_run SET ended_at = ? WHERE crawl_id = ? AND ended_at IS NULL",
                    now(),
                    c.crawl
                )
                .execute(&mut *tx)
                .await?;
            }
        }
        Outcome::RateLimit | Outcome::Gone => {
            sqlx::query!(
                "UPDATE crawl_page SET work_state = 'queued',claim = claim + 1 WHERE id = ?",
                c.id
            )
            .execute(&mut *tx)
            .await?;
        }
    }
    tx.commit().await?;
    Ok(true)
}
async fn finish(db: &SqlitePool) -> Result<Vec<CrawlFinished>, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let at = now();
    let ids=sqlx::query_scalar!("UPDATE crawl SET state = 'done',finished_at = ? WHERE state = 'running' AND NOT EXISTS(SELECT 1 FROM crawl_page WHERE crawl_id = crawl.id AND work_state != 'done') RETURNING id",at).fetch_all(&mut *tx).await?;
    let mut result = Vec::new();
    for id in ids {
        sqlx::query!(
            "UPDATE crawl_run SET ended_at = ? WHERE crawl_id = ? AND ended_at IS NULL",
            at,
            id
        )
        .execute(&mut *tx)
        .await?;
        let candidates =
            sqlx::query_scalar!("SELECT count(*) FROM candidate WHERE crawl_id = ?", id)
                .fetch_one(&mut *tx)
                .await?;
        let failed = sqlx::query_scalar!(
            "SELECT count(*) FROM crawl_page WHERE crawl_id = ? AND status != 'success'",
            id
        )
        .fetch_one(&mut *tx)
        .await?;
        if candidates == 0 && failed > 0 {
            sqlx::query!("UPDATE crawl SET state = 'failed',error = '本次抓取未能处理任何页面，请查看页面原因后重试' WHERE id = ? AND NOT EXISTS(SELECT 1 FROM crawl_page WHERE crawl_id = ? AND status = 'success')",id,id).execute(&mut *tx).await?;
        }
        result.push(CrawlFinished {
            id,
            candidates,
            failed,
        });
    }
    tx.commit().await?;
    Ok(result)
}
pub async fn run(
    db: SqlitePool,
    net: net::Net,
    crawler: Crawler,
    limit: Limit,
    changed: impl Fn() + Send + 'static,
    finished: impl Fn(CrawlFinished) + Send + 'static,
) {
    let fetcher = loop {
        match Fetcher::new(&net) {
            Ok(f) => break Arc::new(f),
            Err(e) => {
                tracing::error!("create crawl fetcher: {e}");
                tokio::time::sleep(RETRY).await;
            }
        }
    };
    run_with_fetcher(db, net, fetcher, crawler, limit, changed, finished).await;
}
async fn run_with_fetcher(
    db: SqlitePool,
    net: net::Net,
    fetcher: Arc<Fetcher>,
    crawler: Crawler,
    limit: Limit,
    changed: impl Fn() + Send + 'static,
    finished: impl Fn(CrawlFinished) + Send + 'static,
) {
    while let Err(e) = queue::recover(&db).await {
        tracing::error!("recover crawl: {e}");
        tokio::time::sleep(RETRY).await;
    }
    let mut workers = JoinSet::new();
    let mut claims = HashMap::new();
    let cooldown = Cooldown::default();
    loop {
        match finish(&db).await {
            Ok(results) => {
                for r in results {
                    changed();
                    finished(r)
                }
            }
            Err(e) => {
                tracing::error!("finish crawl: {e}");
                tokio::time::sleep(RETRY).await;
                continue;
            }
        }
        let retry_at = cooldown.until();
        let cooling = retry_at.is_some();
        if !cooling && workers.len() < CONCURRENCY {
            match queue::next(&db).await {
                Ok(Some(c)) => {
                    let (db, net, limit, fetcher) =
                        (db.clone(), net.clone(), limit.clone(), fetcher.clone());
                    let claim = c.clone();
                    let cooldown = cooldown.clone();
                    let task = workers.spawn(async move {
                        let result = process(&db, &net, &fetcher, &limit, &claim, &cooldown).await;
                        (claim, result)
                    });
                    claims.insert(task.id(), c);
                    changed();
                    continue;
                }
                Ok(None) => {}
                Err(e) => {
                    tracing::error!("claim crawl: {e}");
                    tokio::time::sleep(RETRY).await;
                    continue;
                }
            }
        }
        tokio::select! {
            Some(joined)=workers.join_next_with_id(),if !workers.is_empty()=>{
                let (c,outcome)=match joined{
                    Ok((id,(c,result)))=>{claims.remove(&id);let outcome=match result{Ok(o)=>o,Err(e)=>{tracing::error!("crawl database: {e}");Outcome::Pause("抓取数据暂时无法保存，请继续或重启应用。".into(),None)}};(c,outcome)},
                    Err(e)=>{tracing::error!("crawl worker: {e}");let Some(c)=claims.remove(&e.id())else{continue};(c,Outcome::Pause("抓取意外中断，请继续或重启应用。".into(),None))}
                };
                loop{match save(&db,&c,&outcome).await{Ok(_)=>break,Err(e)=>{tracing::error!("save crawl: {e}");tokio::time::sleep(RETRY).await;}}};
                changed();
            },
            ()=crawler.0.notified()=>{},
            ()=async{if let Some(at)=retry_at{tokio::time::sleep_until(at).await;}},if cooling=>{},
        }
    }
}

#[cfg(test)]
mod tests;
