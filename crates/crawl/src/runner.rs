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
use tracing::Instrument;
use ts_rs::TS;
const READ_FAILED: &str = "抓取数据暂时无法读取，请继续或重启应用。";
const SAVE_FAILED: &str = "网页资料暂时无法保存，请继续或重启应用。";
const MODEL_READ_FAILED: &str = "模型配置暂时无法读取，请重试。";
const CANDIDATE_READ_FAILED: &str = "候选资料暂时无法读取，请继续或重启应用。";
#[derive(Clone, Default)]
pub struct Crawler {
    wake: Arc<Notify>,
    writes: db::Writes,
}
impl Crawler {
    pub fn new(writes: db::Writes) -> Self {
        Self {
            wake: Arc::default(),
            writes,
        }
    }
    pub fn wake(&self) {
        self.wake.notify_one();
    }
}
#[derive(Debug, Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CrawlFinished {
    pub id: i64,
    pub message: String,
}
struct Pages {
    writes: db::Writes,
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
    .map_err(database("save fetched page", SAVE_FAILED))?;
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
        if let Some(reason) = &m.needs_browser {
            tracing::info!(
                url = c.url.as_str(),
                ?reason,
                available = self.browser.is_some(),
                "browser takeover requested"
            );
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
        let started = Instant::now();
        let result =
            Self::render(browser.as_ref(), db, c, target, fetched.relaxed_certificate).await;
        let status = match &result {
            Ok(_) => "success",
            Err(ReadError::Gone) => "canceled",
            Err(_) => "failed",
        };
        tracing::info!(
            url = c.url.as_str(),
            method = "browser",
            result = status,
            elapsed_ms = started.elapsed().as_millis(),
            "page fetched"
        );
        result
    }

    async fn render(
        browser: &dyn Browser,
        db: &SqlitePool,
        c: &Claim,
        target: &PageTarget,
        relaxed_certificate: bool,
    ) -> Result<Material, ReadError> {
        let render = browser.render(target, &c.steps);
        let invalidated = async {
            loop {
                let mut conn = db
                    .acquire()
                    .await
                    .map_err(database("acquire browser claim connection", READ_FAILED))?;
                if !queue::valid(&mut conn, c)
                    .await
                    .map_err(database("check browser claim", READ_FAILED))?
                {
                    return Err(ReadError::Gone);
                }
                drop(conn);
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        };
        let rendered = tokio::select! {
            result = render => result.map_err(|e| {
                let category = match &e {
                    BrowserError::TimedOut if relaxed_certificate => "certificate_timeout",
                    BrowserError::TimedOut => "timeout",
                    BrowserError::Failed(_) => "browser_error",
                };
                tracing::info!(url = c.url.as_str(), category, "browser read failed");
                ReadError::Failed(match e {
                    BrowserError::TimedOut if relaxed_certificate => {
                        "网站的证书有问题，浏览器打不开".into()
                    }
                    e => e.message(),
                })
            })?,
            result = invalidated => return result,
        };
        let url = target.redirect(target.url(), &rendered.url).map_err(|e| {
            tracing::info!(
                error = %e,
                "browser result URL rejected"
            );
            ReadError::Failed(e.message())
        })?;
        if rendered.html.len() > crate::fetch::MAX_PAGE_BYTES {
            tracing::info!(reason = "too_large", "browser content rejected");
            return Err(ReadError::Failed(crate::CrawlError::TooLarge.message()));
        }
        let span = tracing::Span::current();
        let mut material = tokio::task::spawn_blocking(move || {
            span.in_scope(|| crate::parse_rendered(&url, &rendered.html))
        })
        .await
        .map_err(|e| {
            tracing::info!(reason = "parse_worker_failed", "browser content rejected");
            ReadError::Failed(crate::CrawlError::Worker(e).message())
        })?;
        // Interactive pagination remains present after rendering, and is now replayable.
        if material.needs_browser == Some(crate::BrowserReason::Pagination)
            && !material.pagination.buttons.is_empty()
        {
            material.needs_browser = None;
        }
        if let Some(reason) = &material.needs_browser {
            tracing::info!(?reason, "browser content still unavailable");
            return Err(ReadError::Failed(format!(
                "浏览器读取后仍未取得页面内容：{}",
                self::browser(reason)
            )));
        }
        if material.units.is_empty() {
            tracing::info!(reason = "empty_page", "browser content still unavailable");
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

#[derive(Debug)]
enum ReadError {
    Failed(String),
    Pause(String),
    Database {
        source: sqlx::Error,
        operation: &'static str,
        reason: &'static str,
    },
    RateLimit,
    Gone,
}

fn database(
    operation: &'static str,
    reason: &'static str,
) -> impl FnOnce(sqlx::Error) -> ReadError {
    move |source| ReadError::Database {
        source,
        operation,
        reason,
    }
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
    writes: &'a db::Writes,
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
        step: &'static str,
        system: &str,
        data: serde_json::Value,
    ) -> Result<T, ReadError> {
        let mut rules = system.to_owned();
        for attempt in 0..2 {
            let _permit = loop {
                let permit = self.cooldown.acquire(self.limit).await;
                let mut conn = self
                    .db
                    .acquire()
                    .await
                    .map_err(database("acquire model claim connection", READ_FAILED))?;
                if !queue::valid(&mut conn, self.claim)
                    .await
                    .map_err(database("check model claim", READ_FAILED))?
                {
                    return Err(ReadError::Gone);
                }
                let current = model::selected(&mut *conn, self.claim.model_id)
                    .await
                    .map_err(database("read model configuration", MODEL_READ_FAILED))?;
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
            let started = Instant::now();
            let answer = model::ask(
                self.net,
                &self.chosen.access,
                &self.chosen.model,
                &rules,
                &data.to_string(),
            )
            .await
            .map_err(|e| {
                let category = match &e {
                    model::ModelError::MissingKey => "missing_key",
                    model::ModelError::Unreachable { .. } => "unreachable",
                    model::ModelError::TimedOut { .. } => "timeout",
                    model::ModelError::Refused { .. } => "refused",
                    model::ModelError::Failed(_) => "stream_failed",
                    model::ModelError::NoAnswer { .. } => "no_answer",
                    model::ModelError::Broken(_) => "invalid_response",
                };
                let status = match &e {
                    model::ModelError::Refused { status, .. } => Some(*status),
                    _ => None,
                };
                let failure = model::background::classify(e);
                let action = match &failure {
                    Failure::Pause(_) => "pause",
                    Failure::RateLimit(_) => "cooldown",
                    Failure::Failed(_) => "fail_page",
                };
                tracing::info!(
                    url = self.claim.url.as_str(),
                    step,
                    attempt,
                    category,
                    status,
                    action,
                    elapsed_ms = started.elapsed().as_millis(),
                    "model call failed"
                );
                match failure {
                    Failure::Pause(r) => ReadError::Pause(r),
                    Failure::RateLimit(w) => {
                        tracing::info!(wait_ms = w.as_millis(), "model rate limited");
                        self.cooldown.defer(w);
                        ReadError::RateLimit
                    }
                    Failure::Failed(r) => ReadError::Failed(r),
                }
            })?;
            tracing::info!(
                url = self.claim.url.as_str(),
                step,
                attempt,
                usage_known = answer.usage.is_some(),
                input_tokens = answer.usage.map(|u| u.input),
                cached_tokens = answer.usage.map(|u| u.cached),
                output_tokens = answer.usage.map(|u| u.output),
                elapsed_ms = started.elapsed().as_millis(),
                "model call completed"
            );
            if let Some(usage) = answer.usage {
                self.writes
                    .retry("record crawl usage", || {
                        model::usage::record_crawl(
                            self.db,
                            self.chosen.id,
                            &self.chosen.model,
                            self.claim.crawl,
                            usage,
                            now(),
                        )
                    })
                    .await;
            }
            if answer.finish_reason.as_deref() == Some("length") {
                tracing::info!(step, reason = "output_truncated", "model output rejected");
                return Err(ReadError::Failed(
                    "模型的回答被截断，请换用支持更长输出的模型后重试。".into(),
                ));
            }
            match extract::parse(&answer.text) {
                Ok(v) => return Ok(v),
                Err(reason) if attempt == 0 => {
                    tracing::info!(step, reason = "invalid_fields", "retry model output");
                    rules =
                        format!("{system}\n上次输出未通过字段校验：{reason}。请重新输出完整 JSON。")
                }
                Err(reason) => {
                    tracing::info!(step, reason = "invalid_fields", "model output rejected");
                    return Err(ReadError::Failed(format!(
                        "模型返回的资料不合格：{reason}。请重试或换一个模型。"
                    )));
                }
            }
        }
        unreachable!("both attempts return")
    }
}
#[tracing::instrument(name = "crawl", parent = None, skip_all, fields(id = c.crawl))]
async fn process(
    db: &SqlitePool,
    net: &net::Net,
    fetcher: &Pages,
    limit: &Limit,
    c: &Claim,
    cooldown: &Cooldown,
) -> Result<Outcome, sqlx::Error> {
    tracing::info!(
        url = c.url.as_str(),
        kind = ?c.kind,
        "page started"
    );
    let Some(chosen) = model::selected(db, c.model_id).await? else {
        tracing::info!(reason = "missing_model", "crawl cannot continue");
        return Ok(Outcome::Pause(
            "尚未添加模型，请在“设置 › AI › 模型”中添加后继续。".into(),
            None,
        ));
    };
    if !chosen.agreed {
        tracing::info!(reason = "model_consent_required", "crawl cannot continue");
        return Ok(Outcome::Pause(
            "模型配置需要确认，请确认网页内容将发往的服务商后继续。".into(),
            Some(chosen),
        ));
    }
    Ok(
        match read(db, net, fetcher, limit, c, &chosen, cooldown).await {
            Ok(page) => Outcome::Done(page),
            Err(ReadError::Pause(reason)) => Outcome::Pause(reason, Some(chosen)),
            Err(ReadError::Database {
                source,
                operation,
                reason,
            }) => {
                tracing::error!(
                    operation,
                    error = %source,
                    "crawl database"
                );
                Outcome::Pause(reason.into(), Some(chosen))
            }
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
        writes: &fetcher.writes,
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
            .map_err(database("read enrichment candidate", CANDIDATE_READ_FAILED))?;
        let candidate = review::read(&mut conn, &[id])
            .await
            .map_err(database("read enrichment candidate", CANDIDATE_READ_FAILED))?
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
            .ask::<extract::Enriched>("enrich", extract::ENRICH, data)
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
        let mut person = extract::profile(&m, result.profile).map_err(|reason| {
            tracing::info!(reason = "invalid_profile", "enrichment extraction failed");
            ReadError::Failed(reason)
        })?;
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
            .ask::<extract::Route>("route", extract::ROUTE, extract::routing(&m, &scope))
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
                        .ask::<extract::People>("list", extract::LIST, extract::data(&m, range))
                        .await?
                        .people,
                );
            }
            extract::listed(&m, all, &scope).map_err(|reason| {
                tracing::info!(reason = "invalid_people", "page extraction failed");
                ReadError::Failed(reason)
            })?
        }
        Kind::Profile => {
            let p = reader
                .ask::<extract::Profile>(
                    "profile",
                    extract::PROFILE,
                    extract::data(&m, 0..m.units.len()),
                )
                .await?;
            let mut p = extract::profile(&m, p).map_err(|reason| {
                tracing::info!(reason = "invalid_profile", "page extraction failed");
                ReadError::Failed(reason)
            })?;
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
#[tracing::instrument(name = "crawl", parent = None, skip_all, fields(id = c.crawl))]
async fn save(db: &SqlitePool, c: &Claim, outcome: &Outcome) -> Result<bool, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    if !queue::valid(&mut tx, c).await? {
        tracing::info!(url = c.url.as_str(), "page claim invalidated");
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
            tracing::info!(
                url = c.url.as_str(),
                reason = "enrichment_identity_mismatch",
                "page rejected"
            );
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
            tracing::info!(
                url = c.url.as_str(),
                reason = "profile_identity_mismatch",
                "page rejected"
            );
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
    match outcome {
        Outcome::Done(p) => tracing::info!(
            url = c.url.as_str(),
            kind = ?p.kind,
            people = p.people.len(),
            next_pages = p.next.len(),
            needs_browser = p.material.needs_browser.is_some(),
            "page completed"
        ),
        Outcome::Failed(_) => tracing::info!(
            url = c.url.as_str(),
            kind = ?c.kind,
            "page failed"
        ),
        Outcome::Pause(_, _) => tracing::info!("crawl paused"),
        Outcome::RateLimit => {}
        Outcome::Gone => tracing::info!(url = c.url.as_str(), "page claim invalidated"),
    }
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
    for &id in &ids {
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
    let summaries = if ids.is_empty() {
        Vec::new()
    } else {
        queue::summaries(&mut tx, &ids).await?
    };
    tx.commit().await?;
    for summary in summaries {
        crate::span(summary.id).in_scope(|| {
            tracing::info!(
                state = ?summary.state,
                candidates = summary.candidates,
                pages = summary.pages,
                failed = summary.failed,
                usage_known = summary.tokens.is_some(),
                tokens = summary.tokens,
                "crawl finished"
            )
        });
    }
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
            Ok(f) => {
                break Arc::new(Pages {
                    writes: crawler.writes.clone(),
                    http: f,
                    browser,
                });
            }
            Err(e) => {
                tracing::error!("create crawl fetcher: {e}");
                tokio::time::sleep(Duration::from_secs(5)).await;
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
    let writes = &fetcher.writes;
    writes.retry("recover crawl", || queue::recover(&db)).await;
    let mut workers = JoinSet::new();
    let mut claims = HashMap::new();
    let cooldown = Cooldown::default();
    loop {
        for result in writes.retry("finish crawl", || finish(&db, now())).await {
            changed();
            finished(result);
        }
        let retry_at = cooldown.until();
        let cooling = retry_at.is_some();
        if !cooling
            && workers.len() < CONCURRENCY
            && let Some(c) = writes.retry("claim crawl", || queue::next(&db)).await
        {
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
        tokio::select! {
            Some(joined) = workers.join_next_with_id(), if !workers.is_empty() => {
                let (c, outcome) = match joined {
                    Ok((id, (c, result))) => {
                        claims.remove(&id);
                        let outcome = match result {
                            Ok(o) => o,
                            Err(e) => {
                                crate::span(c.crawl).in_scope(|| tracing::error!("crawl database: {e}"));
                                Outcome::Pause(
                                    "抓取数据暂时无法保存，请继续或重启应用。".into(),
                                    None,
                                )
                            }
                        };
                        (c, outcome)
                    }
                    Err(e) => {
                        let Some(c) = claims.remove(&e.id()) else {
                            continue;
                        };
                        crate::span(c.crawl).in_scope(|| tracing::error!("crawl worker: {e}"));
                        (
                            c,
                            Outcome::Pause("抓取意外中断，请继续或重启应用。".into(), None),
                        )
                    }
                };
                writes
                    .retry("save crawl", || save(&db, &c, &outcome))
                    .instrument(crate::span(c.crawl))
                    .await;
                changed();
            },
            () = crawler.wake.notified() => {},
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
