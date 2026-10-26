//! Background page/model work. Page results and newly discovered work commit together.
use crate::{
    Material, PageTarget, PageUrl, Scope,
    browser::{Browser, BrowserError},
    enrich,
    extract::{self, Kind, Person},
    fetch::Fetcher,
    now,
    queue::{self, Claim},
    review,
    store::{PageKind, PageMethod, PageStatus, PageStep},
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
    pub message: String,
}
struct Pages {
    http: Fetcher,
    browser: Option<Arc<dyn Browser>>,
}

async fn save_material(
    db: &SqlitePool,
    c: &Claim,
    m: &Material,
    method: PageMethod,
) -> Result<(), ReadError> {
    let text = m.text();
    let reason = m.needs_browser.as_ref().map(browser);
    let status = if reason.is_some() {
        PageStatus::NeedsBrowser
    } else {
        PageStatus::Success
    };
    let saved = sqlx::query!(
        r#"UPDATE crawl_page
        SET text = ?, mode = ?, status = ?, error = ?, fetched_at = ?
        WHERE id = ? AND claim = ? AND work_state = 'running'
        AND EXISTS (
            SELECT 1
            FROM crawl
            WHERE id = crawl_page.crawl_id AND state = 'running'
        )"#,
        text,
        method,
        status,
        reason,
        now(),
        c.id,
        c.generation
    )
    .execute(db)
    .await
    .map_err(|e| {
        tracing::error!("save fetched page: {e}");
        ReadError::Pause("网页资料暂时无法保存，请继续或重启应用。".into())
    })?;
    if saved.rows_affected() == 0 {
        return Err(ReadError::Gone);
    }
    Ok(())
}

impl Pages {
    async fn fetch(
        &self,
        db: &SqlitePool,
        c: &Claim,
        target: &PageTarget,
    ) -> Result<Material, ReadError> {
        let fetched = self
            .http
            .fetch(target)
            .await
            .map_err(|e| ReadError::Failed(e.message()))?;
        let mut m = fetched.material;
        if !c.steps.is_empty() && m.needs_browser.is_none() {
            m.needs_browser = Some(crate::BrowserReason::Pagination);
        }
        save_material(db, c, &m, PageMethod::Http).await?;
        let Some(browser) = self
            .browser
            .as_ref()
            .filter(|_| m.needs_browser.is_some() || !c.steps.is_empty())
        else {
            return Ok(m);
        };
        save_material(db, c, &m, PageMethod::Webview).await?;
        let render = browser.render(target, &c.steps);
        let invalidated = async {
            loop {
                let mut conn = db.acquire().await.map_err(|_| {
                    ReadError::Pause("抓取数据暂时无法读取，请继续或重启应用。".into())
                })?;
                if !queue::valid(&mut conn, c).await.map_err(|_| {
                    ReadError::Pause("抓取数据暂时无法读取，请继续或重启应用。".into())
                })? {
                    return Err(ReadError::Gone);
                }
                drop(conn);
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        };
        let rendered = tokio::select! {
            result = render => result.map_err(|e| ReadError::Failed(match e {
                BrowserError::TimedOut if fetched.relaxed_certificate => "网站的证书有问题，浏览器打不开".into(),
                e => e.message(),
            }))?,
            result = invalidated => return result,
        };
        let url = target
            .redirect(target.url(), &rendered.url)
            .map_err(|e| ReadError::Failed(e.message()))?;
        if rendered.html.len() > crate::fetch::MAX_PAGE_BYTES {
            return Err(ReadError::Failed(crate::CrawlError::TooLarge.message()));
        }
        let mut material =
            tokio::task::spawn_blocking(move || crate::parse_rendered(&url, &rendered.html))
                .await
                .map_err(|e| ReadError::Failed(crate::CrawlError::Worker(e).message()))?
                .map_err(|e| ReadError::Failed(e.message()))?;
        // Interactive pagination remains present after rendering, and is now replayable.
        if material.needs_browser == Some(crate::BrowserReason::Pagination)
            && !material.pagination.buttons.is_empty()
        {
            material.needs_browser = None;
        }
        if let Some(reason) = &material.needs_browser {
            return Err(ReadError::Failed(format!(
                "浏览器读取后仍未取得页面内容：{}",
                self::browser(reason)
            )));
        }
        if material.units.is_empty() {
            return Err(ReadError::Failed(
                "浏览器读取后仍未取得页面内容，网站可能尚未通过验证".into(),
            ));
        }
        save_material(db, c, &material, PageMethod::Webview).await?;
        Ok(material)
    }
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
                let current = model::selected(&mut *conn, self.claim.model_id)
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
    fetcher: &Pages,
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
    fetcher: &Pages,
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
    let m = fetcher.fetch(db, c, &target).await?;
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
            kind: if c.candidate_id.is_some() {
                c.kind
            } else if c.kind == PageKind::Profile {
                PageKind::Profile
            } else {
                PageKind::Entry
            },
            people: vec![],
            next: vec![],
        });
    }
    if let Some(id) = c.candidate_id {
        let mut conn = db
            .acquire()
            .await
            .map_err(|_| ReadError::Pause("候选资料暂时无法读取，请继续或重启应用。".into()))?;
        let candidate = review::read(&mut conn, &[id])
            .await
            .map_err(|_| ReadError::Pause("候选资料暂时无法读取，请继续或重启应用。".into()))?
            .pop()
            .ok_or(ReadError::Gone)?;
        if candidate.review != "pending" {
            return Err(ReadError::Gone);
        }
        drop(conn);
        let mut data = extract::data(&m, 0..m.units.len());
        data["target"] = serde_json::json!({
            "name": candidate.fields.name,
            "aliases": candidate.fields.aliases,
            "affiliations": candidate.fields.affiliations
        });
        data["child"] = serde_json::json!(c.kind == PageKind::EnrichChild);
        let result = reader
            .ask::<extract::Enriched>(extract::ENRICH, data)
            .await?;
        let mut next = Vec::new();
        if c.kind == PageKind::EnrichProfile {
            let links = extract::links(&m);
            for id in result.next {
                if let Some((_, url)) = id.checked_sub(1).and_then(|n| links.get(n))
                    && scope.page((*url).clone()).is_ok()
                    && !url.is_normalization_of(&c.url)
                    && !url.is_normalization_of(&m.url)
                    && !next.contains(*url)
                {
                    next.push((*url).clone());
                    if next.len() == 3 {
                        break;
                    }
                }
            }
        }
        let mut person = extract::profile(&m, result.profile).map_err(ReadError::Failed)?;
        if c.kind == PageKind::EnrichChild {
            let target_names: Vec<_> = std::iter::once(candidate.fields.name.as_str())
                .chain(candidate.fields.aliases.iter().map(String::as_str))
                .collect();
            let names: Vec<_> = result
                .names
                .iter()
                .map(String::as_str)
                .chain(target_names.iter().copied())
                .collect();
            let mut owned = Vec::new();
            for email in m.units.iter().flat_map(|unit| &unit.emails) {
                if !owned.contains(&email.address)
                    && crate::owns_email(&m, &names, &target_names, &email.address)
                {
                    owned.push(email.address.clone());
                }
            }
            person
                .fields
                .emails
                .retain(|email| owned.len() == 1 && owned.contains(email));
            person.fields.homepages.retain(|url| url != m.url.as_str());
        }
        return Ok(Page {
            material: m,
            kind: c.kind,
            people: vec![person],
            next,
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
    let enrich_identity_failure;
    let outcome = if let Some(id) = c.candidate_id
        && let Outcome::Done(p) = outcome
        && (c.kind == PageKind::EnrichChild || !p.material.url.is_normalization_of(&c.url))
        && let Some(person) = p.people.first()
    {
        let candidate = review::read(&mut tx, &[id])
            .await?
            .pop()
            .ok_or(sqlx::Error::RowNotFound)?;
        if enrich::matches(&candidate.fields, &person.fields, &p.material) {
            outcome
        } else {
            let source = if c.kind == PageKind::EnrichChild {
                "子页".to_owned()
            } else {
                format!("页面跳到了 {}，", p.material.url.as_str())
            };
            enrich_identity_failure = Outcome::Failed(format!(
                "{source}读到的姓名是「{}」，和候选「{}」对不上，或页面未出现匹配姓名，没有补入",
                person.fields.name, candidate.fields.name
            ));
            &enrich_identity_failure
        }
    } else {
        outcome
    };
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
            sqlx::query!(
                r#"UPDATE crawl_page
                SET work_state = 'done', kind = ?, status = ?, error = ?, text = ?,
                    fetched_at = ?
                WHERE id = ?"#,
                p.kind,
                status,
                reason,
                text,
                now(),
                c.id
            )
            .execute(&mut *tx)
            .await?;
            if let Some(id) = c.candidate_id {
                if c.kind == PageKind::EnrichProfile
                    && !sqlx::query_scalar!(
                        r#"SELECT EXISTS (
                            SELECT 1
                            FROM crawl_page
                            WHERE candidate_id = ? AND kind = 'enrich_child'
                        ) AS "selected!: bool""#,
                        id
                    )
                    .fetch_one(&mut *tx)
                    .await?
                {
                    for url in &p.next {
                        enrich::enqueue(&mut tx, c.crawl, id, url, &c.scope, PageKind::EnrichChild)
                            .await?;
                    }
                }
                for person in &p.people {
                    enrich::apply(&mut tx, id, p.material.url.as_str(), person).await?;
                }
            } else {
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
                        if !sqlx::query_scalar!(
                            r#"SELECT EXISTS (SELECT 1 FROM crawl_page WHERE crawl_id = ?
                                AND url = ?) AS "found!: bool""#,
                            c.crawl,
                            target
                        )
                        .fetch_one(&mut *tx)
                        .await?
                        {
                            queue::limit(&mut tx, c.crawl, "寻找名单已达到两层链接范围").await?;
                        }
                    }
                }
                if p.kind == PageKind::List {
                    for url in &p.material.pagination.urls {
                        if Scope::new(&c.scope).page(url.clone()).is_ok() {
                            queue::enqueue(
                                &mut tx,
                                c.crawl,
                                url,
                                &c.scope,
                                c.depth,
                                PageKind::List,
                            )
                            .await?;
                        }
                    }
                }
                if p.kind == PageKind::List && p.material.needs_browser.is_none() {
                    let current = c.steps.last().map_or(1, |s| s.page);
                    if let Some(button) = p
                        .material
                        .pagination
                        .buttons
                        .iter()
                        .filter(|b| b.page.is_none_or(|page| page > current))
                        .min_by_key(|b| b.page.unwrap_or(current + 1))
                    {
                        let mut steps = c.steps.clone();
                        steps.push(PageStep {
                            page: button.page.unwrap_or(current + 1),
                            control: button.control.clone(),
                        });
                        queue::enqueue_steps(
                            &mut tx,
                            c.crawl,
                            &c.url,
                            &c.scope,
                            c.depth,
                            PageKind::List,
                            &steps,
                        )
                        .await?;
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
        }
        Outcome::Failed(reason) => {
            sqlx::query!(
                r#"UPDATE crawl_page
                SET work_state = 'done', status = 'failed', error = ?, fetched_at = ?
                WHERE id = ?"#,
                reason,
                now(),
                c.id
            )
            .execute(&mut *tx)
            .await?;
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
                sqlx::query!(
                    r#"UPDATE crawl
                    SET state = 'paused', error = ?
                    WHERE id = ? AND state = 'running'"#,
                    reason,
                    c.crawl
                )
                .execute(&mut *tx)
                .await?;
                sqlx::query!(
                    r#"UPDATE crawl_page
                    SET work_state = 'queued', claim = claim + 1
                    WHERE crawl_id = ? AND work_state = 'running'"#,
                    c.crawl
                )
                .execute(&mut *tx)
                .await?;
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
    if let Some(id) = c.candidate_id {
        enrich::complete(&mut tx, id).await?;
    }
    tx.commit().await?;
    Ok(true)
}
async fn finish(db: &SqlitePool, at: i64) -> Result<Vec<CrawlFinished>, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let ids = sqlx::query_scalar!(
        r#"UPDATE crawl
        SET state = 'done', finished_at = ?
        WHERE state = 'running' AND NOT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE crawl_id = crawl.id AND work_state != 'done'
        )
        RETURNING id"#,
        at
    )
    .fetch_all(&mut *tx)
    .await?;
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
            sqlx::query!(
                r#"UPDATE crawl
                SET state = 'failed',
                    error = '本次抓取未能处理任何页面，请查看页面原因后重试'
                WHERE id = ? AND NOT EXISTS (
                    SELECT 1
                    FROM crawl_page
                    WHERE crawl_id = ? AND status = 'success'
                )"#,
                id,
                id
            )
            .execute(&mut *tx)
            .await?;
        }
        let activity = sqlx::query!(
            r#"SELECT p.candidate_id,c.enrich AS "enrich?: enrich::EnrichState"
            FROM crawl_page p LEFT JOIN candidate c ON c.id=p.candidate_id
            JOIN crawl ON crawl.id=p.crawl_id
            WHERE p.crawl_id=? AND p.work_state='done'
            AND (crawl.reported_at IS NULL OR p.fetched_at>crawl.reported_at)
            GROUP BY p.candidate_id"#,
            id
        )
        .fetch_all(&mut *tx)
        .await?;
        let mut messages = Vec::new();
        if activity.iter().any(|p| p.candidate_id.is_none()) {
            messages.push(format!(
                "抓取完成：找到 {candidates} 位候选，{failed} 个页面未打开"
            ));
        }
        let enriched = activity
            .iter()
            .filter(|p| p.enrich == Some(enrich::EnrichState::Done))
            .count();
        if enriched > 0 {
            messages.push(format!("信息补全完成：{enriched} 位，去候选里查看结果"));
        }
        if !messages.is_empty() {
            sqlx::query!("UPDATE crawl SET reported_at=? WHERE id=?", at, id)
                .execute(&mut *tx)
                .await?;
        }
        result.push(CrawlFinished {
            id,
            message: messages.join("；"),
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
    browser: Option<Arc<dyn Browser>>,
    changed: impl Fn() + Send + 'static,
    finished: impl Fn(CrawlFinished) + Send + 'static,
) {
    let fetcher = loop {
        match Fetcher::new(&net) {
            Ok(f) => break Arc::new(Pages { http: f, browser }),
            Err(e) => {
                tracing::error!("create crawl fetcher: {e}");
                tokio::time::sleep(RETRY).await;
            }
        }
    };
    run_with_pages(db, net, fetcher, crawler, limit, changed, finished).await;
}
async fn run_with_pages(
    db: SqlitePool,
    net: net::Net,
    fetcher: Arc<Pages>,
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
        match finish(&db, now()).await {
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
            Some(joined) = workers.join_next_with_id(), if !workers.is_empty() => {
                let (c, outcome) = match joined {
                    Ok((id, (c, result))) => {
                        claims.remove(&id);
                        let outcome = match result {
                            Ok(o) => o,
                            Err(e) => {
                                tracing::error!("crawl database: {e}");
                                Outcome::Pause(
                                    "抓取数据暂时无法保存，请继续或重启应用。".into(),
                                    None,
                                )
                            }
                        };
                        (c, outcome)
                    }
                    Err(e) => {
                        tracing::error!("crawl worker: {e}");
                        let Some(c) = claims.remove(&e.id()) else {
                            continue;
                        };
                        (
                            c,
                            Outcome::Pause("抓取意外中断，请继续或重启应用。".into(), None),
                        )
                    }
                };
                loop {
                    match save(&db, &c, &outcome).await {
                        Ok(_) => break,
                        Err(e) => {
                            tracing::error!("save crawl: {e}");
                            tokio::time::sleep(RETRY).await;
                        }
                    }
                }
                changed();
            },
            () = crawler.0.notified() => {},
            () = async {
                if let Some(at) = retry_at {
                    tokio::time::sleep_until(at).await;
                }
            }, if cooling => {},
        }
    }
}

#[cfg(test)]
mod tests;
