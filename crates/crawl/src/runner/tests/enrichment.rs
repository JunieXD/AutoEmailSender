use super::*;
use crate::enrich::{self, EnrichField, EnrichSkipReason, EnrichState};

pub(super) async fn candidate(db: &SqlitePool, site: &Site, path: &str) -> (i64, i64) {
    let crawl = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO crawl (kind, start_urls, state, created_at, finished_at)
        VALUES ('site', '[]', 'done', 1, 1)
        RETURNING id"#,
    )
    .fetch_one(db)
    .await
    .unwrap();
    let id = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name, aliases,
            emails, affiliations, homepages, papers, match_names)
        VALUES (?, ?, 'pending', 'none', '张三', '["Zhang San"]', '[]', '[]', '[]',
            '[]', '["张三","Zhang San"]')
        RETURNING id"#,
    )
    .bind(crawl)
    .bind(site.url(path).as_str())
    .fetch_one(db)
    .await
    .unwrap();
    sqlx::query("UPDATE candidate SET homepages=? WHERE id=?")
        .bind(Json(vec![site.url(path).as_str()]))
        .bind(id)
        .execute(db)
        .await
        .unwrap();
    (crawl, id)
}
fn answer(email: &str, next: Value) -> Value {
    let mut p = person("张三", email);
    p["names"] = json!(["张三", "李四"]);
    p["next"] = next;
    p
}
async fn process_answer(
    db: &SqlitePool,
    site: &Site,
    provider: &mut Provider,
    claim: &Claim,
    answer: Value,
) -> Outcome {
    let net = net::Net::new(vec![]).unwrap();
    let fetcher = Pages::local(site.port);
    let limit = Limit::default();
    let cooldown = Cooldown::default();
    let read = process(db, &net, &fetcher, &limit, claim, &cooldown);
    let respond = async {
        provider
            .requests
            .recv()
            .await
            .unwrap()
            .respond
            .send(Response::answer(&answer.to_string()))
            .ok()
            .unwrap();
    };
    let (result, ()) = tokio::join!(read, respond);
    result.unwrap()
}
async fn detail(db: &SqlitePool, id: i64) -> review::CandidateDetail {
    review::detail(db, id).await.unwrap().unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn agent_submission_reopens_for_profile_and_child_enrichment(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三</p><a href='/contact'>联系</a>");
    site.page("/contact", "<p>张三 a@example.edu 机器学习</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let submitted = crate::submit::submit(
        &db,
        serde_json::from_value(json!({"candidates":[{
            "name":"张三", "profileUrl":site.url("/p").as_str(),
            "sourceUrl":site.url("/").as_str(), "note":"待补邮箱"
        }]}))
        .unwrap(),
        1,
    )
    .await
    .unwrap();
    assert!(finish(&db, 1).await.unwrap().is_empty());
    let id = review::list(
        &db,
        &CandidateQuery {
            crawl_id: Some(submitted.crawl_id),
            processed: false,
            filter: CandidateFilter::All,
            page: 1,
        },
    )
    .await
    .unwrap()
    .candidates[0]
        .id;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut profile = answer("a@example.edu", json!([1]));
    profile["research"] = Value::Null;
    let result = process_answer(&db, &site, &mut provider, &claim, profile).await;
    save(&db, &claim, &result).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.kind, PageKind::EnrichChild);
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &claim,
        answer("a@example.edu", json!([])),
    )
    .await;
    save(&db, &claim, &result).await.unwrap();
    finish(&db, 3).await.unwrap();
    let detail = detail(&db, id).await;
    assert_eq!(detail.candidate.fields.emails, vec!["a@example.edu"]);
    assert_eq!(
        detail.candidate.fields.research.as_deref(),
        Some("机器学习")
    );
    assert_eq!(detail.candidate.note.as_deref(), Some("待补邮箱"));
    assert_eq!(detail.candidate.enrichment.enrich, EnrichState::Done);
    assert!(
        detail
            .evidence
            .iter()
            .any(|e| e.url == site.url("/contact").as_str())
    );
    assert_eq!(
        queue::list(&db, 1).await.unwrap().crawls[0].state,
        queue::CrawlState::Done
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_personal_page_fills_only_empty_fields(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三 a@example.edu 机器学习</p>",
    );
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    sqlx::query(
        r#"UPDATE candidate
        SET papers = '["原论文"]', affiliations =
            '[{"university":"原大学","school":null,"department":null,"title":"教授"}]'
        WHERE id = ?"#,
    )
    .bind(id)
    .execute(&db)
    .await
    .unwrap();
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut a = answer("a@example.edu", json!([]));
    a["affiliations"] =
        json!([{"university":"原大学","school":"计算机学院","department":null,"title":"副教授"}]);
    let result = process_answer(&db, &site, &mut provider, &claim, a.clone()).await;
    save(&db, &claim, &result).await.unwrap();
    let d = detail(&db, id).await;
    assert_eq!(d.candidate.fields.emails, vec!["a@example.edu"]);
    assert_eq!(d.candidate.fields.research.as_deref(), Some("机器学习"));
    assert_eq!(
        d.candidate.fields.affiliations[0].title.as_deref(),
        Some("教授")
    );
    assert_eq!(
        d.candidate.fields.affiliations[0].school.as_deref(),
        Some("计算机学院")
    );
    assert_eq!(d.candidate.papers, vec!["原论文"]);
    assert_eq!(d.candidate.enrichment.enrich, EnrichState::Done);
    assert!(d.candidate.enrichment.enrich_error.is_none());
    assert!(
        d.candidate
            .enrichment
            .enrich_changes
            .iter()
            .any(|c| c.field == EnrichField::Email && c.value == json!(["a@example.edu"]))
    );
    finish(&db, now()).await.unwrap();
    // Re-enrichment is allowed for a missing direction, but cannot replace existing email.
    sqlx::query("UPDATE candidate SET research=NULL WHERE id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    enrich::start(&db, &[id], 3).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    site.page("/p", "<title>张三</title><p>张三 b@example.edu 新方向</p>");
    a["emails"] = json!(["b@example.edu"]);
    a["research"] = json!("新方向");
    let result = process_answer(&db, &site, &mut provider, &claim, a).await;
    save(&db, &claim, &result).await.unwrap();
    let d = detail(&db, id).await;
    assert_eq!(d.candidate.fields.emails, vec!["a@example.edu"]);
    assert_eq!(d.candidate.fields.research.as_deref(), Some("新方向"));
    assert_eq!(
        queue::detail(&db, crawl, 1)
            .await
            .unwrap()
            .unwrap()
            .page_total,
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_children_validate_links_limit_and_email_ownership(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三</p><a href='https://outside.example.org/mail'>站外</a><a \
        href='/contact'>联系方式</a><a href='/research'>研究</a><a href='/papers'>论文</a><a \
        href='/fourth'>更多</a>",
    );
    site.page(
        "/contact",
        "<title>学院联系方式</title><div><p>张三 a@example.edu</p><p>李四 b@example.edu</p></div>",
    );
    site.page("/research", "<p>张三 机器学习</p>");
    site.page("/papers", "<p>张三 论文</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut a = answer("missing@example.edu", json!([999, 0, 1, 2, 2, 3, 4, 5]));
    a["research"] = Value::Null;
    a["papers"] = json!([]);
    let result = process_answer(&db, &site, &mut provider, &claim, a).await;
    save(&db, &claim, &result).await.unwrap();
    let pages = sqlx::query_scalar::<_, String>(
        "SELECT url FROM crawl_page WHERE kind='enrich_child' ORDER BY id",
    )
    .fetch_all(&db)
    .await
    .unwrap();
    assert_eq!(
        pages,
        vec![
            site.url("/contact").as_str(),
            site.url("/research").as_str(),
            site.url("/papers").as_str()
        ]
    );
    assert_eq!(
        detail(&db, id).await.candidate.enrichment.enrich,
        EnrichState::Running
    );
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut a = answer("a@example.edu", json!([1, 2, 3]));
    a["emails"] = json!(["b@example.edu", "a@example.edu"]);
    let result = process_answer(&db, &site, &mut provider, &claim, a).await;
    save(&db, &claim, &result).await.unwrap();
    assert_eq!(
        detail(&db, id).await.candidate.fields.emails,
        vec!["a@example.edu"]
    );
    while let Some(c) = queue::next(&db).await.unwrap() {
        let result = process_answer(
            &db,
            &site,
            &mut provider,
            &c,
            answer("b@example.edu", json!([1, 2, 3])),
        )
        .await;
        save(&db, &c, &result).await.unwrap();
    }
    let d = detail(&db, id).await;
    assert_eq!(d.candidate.enrichment.enrich, EnrichState::Done);
    assert_eq!(d.evidence.len(), 4);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM candidate")
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_skip_groups_are_rechecked_when_starting(db: SqlitePool) {
    let site = Site::new().await;
    let (_, missing) = candidate(&db, &site, "/missing").await;
    let (_, complete) = candidate(&db, &site, "/complete").await;
    let (_, active) = candidate(&db, &site, "/active").await;
    let (_, ready) = candidate(&db, &site, "/ready").await;
    sqlx::query("UPDATE candidate SET profile_url=NULL WHERE id=?")
        .bind(missing)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE candidate SET emails='[\"a@example.edu\"]',research='方向' WHERE id=?")
        .bind(complete)
        .execute(&db)
        .await
        .unwrap();
    enrich::start(&db, &[active], 2).await.unwrap();
    let p = enrich::preview(&db, &[missing, complete, active, ready, ready])
        .await
        .unwrap();
    assert_eq!(p.ids, vec![ready]);
    for (reason, id) in [
        (EnrichSkipReason::MissingProfile, missing),
        (EnrichSkipReason::Complete, complete),
        (EnrichSkipReason::Active, active),
    ] {
        assert_eq!(
            p.skipped.iter().find(|g| g.reason == reason).unwrap().ids,
            vec![id]
        );
    }
    review::reject(&db, &[ready]).await.unwrap();
    let p = enrich::start(&db, &[ready], 3).await.unwrap();
    assert!(p.ids.is_empty());
    assert_eq!(p.skipped[0].reason, EnrichSkipReason::Reviewed);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_pause_restart_cancel_and_late_review_results(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 a@example.edu</p><a href='/child'>联系</a>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        answer("a@example.edu", json!([1])),
    )
    .await;
    queue::control(&db, crawl, "pause", 3).await.unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    assert!(detail(&db, id).await.candidate.fields.emails.is_empty());
    queue::control(&db, crawl, "resume", 4).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    let c = queue::next(&db).await.unwrap().unwrap();
    save(&db, &c, &result).await.unwrap();
    assert_eq!(
        detail(&db, id).await.candidate.enrichment.enrich,
        EnrichState::Running
    );
    let c = queue::next(&db).await.unwrap().unwrap();
    queue::control(&db, crawl, "cancel", 6).await.unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    let d = detail(&db, id).await;
    assert_eq!(d.candidate.enrichment.enrich, EnrichState::None);
    assert_eq!(d.candidate.fields.emails, vec!["a@example.edu"]);
    for accept in [false, true] {
        let (_, id) = candidate(&db, &site, "/p").await;
        enrich::start(&db, &[id], 7).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let result = process_answer(
            &db,
            &site,
            &mut provider,
            &c,
            answer("a@example.edu", json!([])),
        )
        .await;
        if accept {
            review::accept(
                &db,
                &ReviewInput {
                    ids: vec![id],
                    decisions: Decisions::default(),
                },
                8,
            )
            .await
            .unwrap();
        } else {
            review::reject(&db, &[id]).await.unwrap();
        }
        let before = serde_json::to_value(detail(&db, id).await).unwrap();
        assert!(!save(&db, &c, &result).await.unwrap());
        assert_eq!(serde_json::to_value(detail(&db, id).await).unwrap(), before);
    }
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_failure_reasons_and_single_page_retry(db: SqlitePool) {
    let site = Site::new().await;
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/missing").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process(
        &db,
        &net::Net::new(vec![]).unwrap(),
        &Pages::local(site.port),
        &Limit::default(),
        &c,
        &Cooldown::default(),
    )
    .await
    .unwrap();
    save(&db, &c, &result).await.unwrap();
    finish(&db, now()).await.unwrap();
    assert!(
        detail(&db, id)
            .await
            .candidate
            .enrichment
            .enrich_error
            .unwrap()
            .contains("读取失败")
    );
    site.page("/missing", "<p>张三 a@example.edu</p>");
    queue::retry(&db, crawl, Some(c.id), 3).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        answer("a@example.edu", json!([])),
    )
    .await;
    save(&db, &c, &result).await.unwrap();
    assert_eq!(
        detail(&db, id).await.candidate.fields.emails,
        vec!["a@example.edu"]
    );
    for (path, html, expected) in [
        ("/empty", "<p>张三</p>", "页面上没有"),
        (
            "/browser",
            "<div id='app'></div><script src='/main.js'></script><noscript>请启用 JavaScript</noscript>",
            "需要浏览器",
        ),
    ] {
        site.page(path, html);
        let (_, id) = candidate(&db, &site, path).await;
        enrich::start(&db, &[id], 4).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let result = if path == "/empty" {
            let mut a = answer("a@example.edu", json!([]));
            a["research"] = Value::Null;
            a["papers"] = json!([]);
            process_answer(&db, &site, &mut provider, &c, a).await
        } else {
            process(
                &db,
                &net::Net::new(vec![]).unwrap(),
                &Pages::local(site.port),
                &Limit::default(),
                &c,
                &Cooldown::default(),
            )
            .await
            .unwrap()
        };
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert!(
            d.candidate
                .enrichment
                .enrich_error
                .unwrap()
                .contains(expected)
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_waiting_for_quota_rechecks_default_deletion_edits_and_consent(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 a@example.edu</p>");
    for mode in ["delete", "edit", "default", "consent"] {
        let mut old = Provider::start().await.unwrap();
        let mut new = Provider::start().await.unwrap();
        let old_id = config(&db, &old.address).await;
        let new_id = config(&db, &new.address).await;
        sqlx::query("UPDATE profile SET default_model_config_id=?")
            .bind(old_id)
            .execute(&db)
            .await
            .unwrap();
        let (crawl, id) = candidate(&db, &site, "/p").await;
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        assert_eq!(c.model_id, None);
        let chosen = model::selected(&db, None).await.unwrap().unwrap();
        let limit = Limit::default();
        let permits = [
            limit.acquire().await,
            limit.acquire().await,
            limit.acquire().await,
        ];
        let net = net::Net::new(vec![]).unwrap();
        let cooldown = Cooldown::default();
        let writes = db::Writes::default();
        let reader = Reader {
            writes: &writes,
            db: &db,
            net: &net,
            limit: &limit,
            claim: &c,
            chosen: &chosen,
            cooldown: &cooldown,
        };
        let ask = reader.ask::<extract::Enriched>(
            "enrich",
            extract::ENRICH,
            json!({"target":{"name":"张三"}}),
        );
        tokio::pin!(ask);
        assert!(
            std::future::poll_fn(|cx| std::task::Poll::Ready(ask.as_mut().poll(cx).is_pending()))
                .await
        );
        match mode {
            "delete" => {
                model::delete(&db, old_id).await.unwrap();
                sqlx::query("UPDATE profile SET default_model_config_id=?")
                    .bind(new_id)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            "edit" => {
                let f = model::ModelForm {
                    name: "新配置".into(),
                    provider: "custom".into(),
                    base_url: new.address.clone(),
                    model: "fixture".into(),
                    input_price: None,
                    output_price: None,
                }
                .check()
                .unwrap();
                model::update(&db, old_id, &f, "fixture").await.unwrap();
            }
            "default" => {
                sqlx::query("UPDATE profile SET default_model_config_id=?")
                    .bind(new_id)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            _ => {
                sqlx::query("UPDATE model_config SET agreed=0 WHERE id=?")
                    .bind(old_id)
                    .execute(&db)
                    .await
                    .unwrap();
            }
        }
        // The replacement configuration must require the same explicit consent.
        let selected = model::selected(&db, None).await.unwrap().unwrap();
        sqlx::query("UPDATE model_config SET agreed=0 WHERE id=?")
            .bind(selected.id)
            .execute(&db)
            .await
            .unwrap();
        drop(permits);
        tokio::select! {
            r = &mut ask => assert!(matches!(r, Err(ReadError::Gone))),
            _ = old.requests.recv() => panic!("stale model request started")
        }
        save(&db, &c, &Outcome::Gone).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let outcome = process(&db, &net, &Pages::local(site.port), &limit, &c, &cooldown)
            .await
            .unwrap();
        assert!(matches!(outcome, Outcome::Pause(_, _)));
        save(&db, &c, &outcome).await.unwrap();
        assert!(detail(&db, id).await.candidate.fields.emails.is_empty());
        let address = if mode == "consent" {
            &old.address
        } else {
            &new.address
        };
        model::agree(&db, selected.id, "custom", Some(address))
            .await
            .unwrap();
        queue::control(&db, crawl, "resume", 3).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let provider = if mode == "consent" {
            &mut old
        } else {
            &mut new
        };
        let outcome =
            process_answer(&db, &site, provider, &c, answer("a@example.edu", json!([]))).await;
        save(&db, &c, &outcome).await.unwrap();
        finish(&db, now()).await.unwrap();
        assert_eq!(
            detail(&db, id).await.candidate.fields.emails,
            vec!["a@example.edu"]
        );
    }
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_rate_limit_cools_other_pages_format_retry(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/a", "<p>张三 a@example.edu</p>");
    site.page("/b", "<p>张三 a@example.edu</p>");
    let mut p = Provider::start().await.unwrap();
    config(&db, &p.address).await;
    for path in ["/a", "/b"] {
        let (_, id) = candidate(&db, &site, path).await;
        enrich::start(&db, &[id], 1).await.unwrap();
    }
    let cooldown = Cooldown::default();
    let limit = Limit::default();
    let start = |claim: Claim| {
        let (db, cd, limit, port) = (db.clone(), cooldown.clone(), limit.clone(), site.port);
        tokio::spawn(async move {
            let result = process(
                &db,
                &net::Net::new(vec![]).unwrap(),
                &Pages::local(port),
                &limit,
                &claim,
                &cd,
            )
            .await
            .unwrap();
            (claim, result)
        })
    };
    let a = start(queue::next(&db).await.unwrap().unwrap());
    let b = start(queue::next(&db).await.unwrap().unwrap());
    let first = p.requests.recv().await.unwrap();
    let second = p.requests.recv().await.unwrap();
    let guard = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });
    tokio::time::pause();
    first
        .respond
        .send(Response::Refused {
            status: 429,
            message: "slow down".into(),
            retry_after: Some(60),
        })
        .ok();
    tokio::pin!(a, b);
    let (a_limited, (claim, result)) = tokio::select! {
        r = &mut a => (true, r.unwrap()),
        r = &mut b => (false, r.unwrap())
    };
    assert!(matches!(result, Outcome::RateLimit));
    save(&db, &claim, &result).await.unwrap();
    second.respond.send(Response::answer("invalid JSON")).ok();
    loop {
        if sqlx::query_scalar::<_, i64>("SELECT count(*) FROM usage")
            .fetch_one(&db)
            .await
            .unwrap()
            == 1
        {
            break;
        }
        tokio::task::yield_now().await;
    }
    tokio::time::advance(Duration::from_secs(59)).await;
    assert!(p.requests.try_recv().is_err());
    tokio::time::advance(Duration::from_secs(2)).await;
    tokio::time::resume();
    guard.abort();
    p.requests
        .recv()
        .await
        .unwrap()
        .respond
        .send(Response::answer(
            &answer("a@example.edu", json!([])).to_string(),
        ))
        .ok();
    let (claim, result) = if a_limited {
        b.await.unwrap()
    } else {
        a.await.unwrap()
    };
    save(&db, &claim, &result).await.unwrap();
    assert_eq!(
        detail(&db, claim.candidate_id.unwrap())
            .await
            .candidate
            .fields
            .emails,
        vec!["a@example.edu"]
    );
    let fresh = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut p,
        &fresh,
        answer("a@example.edu", json!([])),
    )
    .await;
    save(&db, &fresh, &result).await.unwrap();
    assert_eq!(
        detail(&db, fresh.candidate_id.unwrap())
            .await
            .candidate
            .enrichment
            .enrich,
        EnrichState::Done
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_redirect_checks_identity_and_merged_candidate_discards_results(db: SqlitePool) {
    let site = Site::new().await;
    let mut p = Provider::start().await.unwrap();
    config(&db, &p.address).await;
    for (path, destination, name, aliases, success) in [
        ("/slash", "/slash/", "Zhang San", json!([]), true),
        ("/old", "/new", "Zhang San", json!(["张三"]), true),
        ("/wrong", "/other", "李四", json!([]), false),
        ("/directory", "/contacts", "张三", json!([]), false),
    ] {
        site.redirect(path, destination);
        site.page(
            destination,
            "<p>张三 a@example.edu</p><p>李四 b@example.edu</p>",
        );
        if path == "/directory" {
            site.page(destination, "<p>学院办公室 b@example.edu</p>");
        }
        let (_, id) = candidate(&db, &site, path).await;
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = answer(
            if success {
                "a@example.edu"
            } else {
                "b@example.edu"
            },
            json!([]),
        );
        a["name"] = json!(name);
        a["aliases"] = aliases;
        let result = process_answer(&db, &site, &mut p, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(!d.candidate.fields.emails.is_empty(), success);
        if !success {
            assert!(
                d.candidate
                    .enrichment
                    .enrich_error
                    .unwrap()
                    .contains("对不上")
            );
        }
    }
    site.page("/merged", "<p>张三 a@example.edu</p>");
    let (crawl, id) = candidate(&db, &site, "/merged").await;
    enrich::start(&db, &[id], 3).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(&db, &site, &mut p, &c, answer("a@example.edu", json!([]))).await;
    let mentor = mentor::create(
        &db,
        &MentorFields {
            name: "张三".into(),
            emails: vec!["a@example.edu".into()],
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    let keep = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name, emails,
            mentor_id, match_names)
        VALUES (?, ?, 'accepted', 'none', '张三', '["a@example.edu"]', ?, '["张三"]')
        RETURNING id"#,
    )
    .bind(crawl)
    .bind(site.url("/keep").as_str())
    .bind(mentor)
    .fetch_one(&db)
    .await
    .unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    sqlx::query("INSERT INTO candidate_profile(candidate_id,crawl_id,url) VALUES(?,?,?),(?,?,?)")
        .bind(id)
        .bind(crawl)
        .bind(site.url("/merged").as_str())
        .bind(keep)
        .bind(crawl)
        .bind(site.url("/keep").as_str())
        .execute(&mut *tx)
        .await
        .unwrap();
    let page = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO crawl_page (crawl_id, url, kind, mode, status, text, fetched_at)
        VALUES (?, ?, 'profile', 'http', 'success', '张三', 1)
        RETURNING id"#,
    )
    .bind(crawl)
    .bind(site.url("/keep").as_str())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    review::save(
        &mut tx,
        crawl,
        review::Source::Page {
            id: page,
            kind: PageKind::Profile,
        },
        site.url("/merged").as_str(),
        extract::Person {
            fields: MentorFields {
                name: "张三".into(),
                emails: vec!["a@example.edu".into()],
                ..Default::default()
            },
            papers: vec![],
            profile: Some(site.url("/merged")),
        },
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    assert!(review::detail(&db, id).await.unwrap().is_none());
    let before = serde_json::to_value(detail(&db, keep).await).unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    assert_eq!(
        serde_json::to_value(detail(&db, keep).await).unwrap(),
        before
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_merge_invalidates_results_even_when_active_candidate_is_retained(
    db: SqlitePool,
) {
    let site = Site::new().await;
    site.page("/active", "<p>张三 a@example.edu</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/active").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        answer("a@example.edu", json!([])),
    )
    .await;
    let duplicate = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name, emails,
            match_names)
        VALUES (?, ?, 'pending', 'none', '张三', '["a@example.edu"]', '["张三"]')
        RETURNING id"#,
    )
    .bind(crawl)
    .bind(site.url("/duplicate").as_str())
    .fetch_one(&db)
    .await
    .unwrap();
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    sqlx::query("INSERT INTO candidate_profile(candidate_id,crawl_id,url) VALUES(?,?,?),(?,?,?)")
        .bind(id)
        .bind(crawl)
        .bind(site.url("/active").as_str())
        .bind(duplicate)
        .bind(crawl)
        .bind(site.url("/duplicate").as_str())
        .execute(&mut *tx)
        .await
        .unwrap();
    let page = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO crawl_page (crawl_id, url, kind, mode, status, text, fetched_at)
        VALUES (?, ?, 'profile', 'http', 'success', '张三', 1)
        RETURNING id"#,
    )
    .bind(crawl)
    .bind(site.url("/active").as_str())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    review::save(
        &mut tx,
        crawl,
        review::Source::Page {
            id: page,
            kind: PageKind::Profile,
        },
        site.url("/active").as_str(),
        extract::Person {
            fields: MentorFields {
                name: "张三".into(),
                emails: vec!["a@example.edu".into()],
                ..Default::default()
            },
            papers: vec![],
            profile: Some(site.url("/active")),
        },
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    assert!(review::detail(&db, duplicate).await.unwrap().is_none());
    let before = serde_json::to_value(detail(&db, id).await).unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    assert_eq!(serde_json::to_value(detail(&db, id).await).unwrap(), before);
    assert_eq!(
        detail(&db, id).await.candidate.enrichment.enrich,
        EnrichState::None
    );
}

fn empty_answer(next: Value) -> Value {
    let mut a = answer("missing@example.edu", next);
    a["research"] = Value::Null;
    a["papers"] = json!([]);
    a
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_child_checks_identity_before_filling_any_field(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三</p><a href='/child'>实验室成员</a>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    for (html, name, aliases, success) in [
        (
            "<p>李四 b@example.edu 肿瘤医学</p>",
            "李四",
            json!([]),
            false,
        ),
        ("<p>学院办公室 b@example.edu</p>", "张三", json!([]), false),
        (
            "<p>Zhang San a@example.edu 机器学习</p><p>张三</p>",
            "Zhang San",
            json!(["张三"]),
            true,
        ),
    ] {
        site.page("/child", html);
        let (crawl, id) = candidate(&db, &site, "/p").await;
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([1]))).await;
        save(&db, &c, &result).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = answer(
            if success {
                "a@example.edu"
            } else {
                "b@example.edu"
            },
            json!([]),
        );
        a["name"] = json!(name);
        a["aliases"] = aliases;
        a["research"] = json!(if success {
            "机器学习"
        } else {
            "肿瘤医学"
        });
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(d.candidate.fields.research.is_some(), success);
        assert_eq!(!d.candidate.papers.is_empty(), success);
        assert_eq!(!d.candidate.fields.emails.is_empty(), success);
        let pages = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
        let child = pages
            .pages
            .iter()
            .find(|p| p.kind == PageKind::EnrichChild)
            .unwrap();
        assert_eq!(child.status == PageStatus::Success, success);
        if !success {
            assert!(child.error.as_ref().unwrap().contains("没有补入"));
            assert_eq!(d.evidence.len(), 1);
            assert!(d.candidate.enrichment.enrich_changes.is_empty());
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_child_accepts_identity_in_page_title(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三</p><a href='/papers'>代表论文</a>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    for (title, success) in [("张三的代表论文", true), ("李四的代表论文", false)] {
        site.page(
            "/papers",
            &format!(
                "<title>{title}</title><h1>代表论文</h1><p>Reliable Learning, 2025</p><p>办公室 \
                office@example.edu</p>"
            ),
        );
        let (crawl, id) = candidate(&db, &site, "/p").await;
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([1]))).await;
        save(&db, &c, &result).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = empty_answer(json!([]));
        a["papers"] = json!(["Reliable Learning, 2025"]);
        a["emails"] = json!(["office@example.edu"]);
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(
            d.candidate.papers,
            if success {
                vec!["Reliable Learning, 2025"]
            } else {
                vec![]
            }
        );
        assert!(d.candidate.fields.emails.is_empty());
        assert_eq!(d.evidence.len(), if success { 2 } else { 1 });
        let pages = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
        let child = pages
            .pages
            .iter()
            .find(|p| p.kind == PageKind::EnrichChild)
            .unwrap();
        assert_eq!(child.status == PageStatus::Success, success);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_child_treats_bilingual_names_as_one_person(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三（Zhang San）</p><a href='/contact'>联系</a>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    for html in [
        "<h1>张三（Zhang San）</h1><p>电子邮箱：a@example.edu</p>",
        "<h1>Zhang San（张三）</h1><p>电子邮箱：a@example.edu</p>",
        "<div><h1>张三（Zhang San）</h1><p>a@example.edu</p></div><div><h1>李四</h1><p>b@example.edu</p></div>",
    ] {
        site.page("/contact", html);
        let (_, id) = candidate(&db, &site, "/p").await;
        sqlx::query("UPDATE candidate SET aliases='[]' WHERE id=?")
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = empty_answer(json!([1]));
        a["aliases"] = json!(["Zhang San"]);
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        assert_eq!(
            detail(&db, id).await.candidate.fields.aliases,
            vec!["Zhang San"]
        );
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = empty_answer(json!([]));
        a["names"] = json!(["张三", "Zhang San", "李四"]);
        a["aliases"] = json!(["Zhang San"]);
        a["emails"] = json!(["a@example.edu", "b@example.edu"]);
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(d.candidate.fields.emails, vec!["a@example.edu"]);
        assert_eq!(d.evidence.last().unwrap().emails, vec!["a@example.edu"]);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_child_with_same_names_requires_unique_email_evidence(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三</p><a href='/contacts'>联系方式</a>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    for (school, multiple) in [
        ("计算机学院", true),
        ("医学院", true),
        ("计算机学院", false),
    ] {
        site.page(
            "/contacts",
            if multiple {
                include_str!("../../../tests/integration/pages/enrich-same-name.html")
            } else {
                "<table><tr><td>张三</td><td>计算机学院</td><td>cs@example.edu</td></tr></table>"
            },
        );
        let (_, id) = candidate(&db, &site, "/p").await;
        let affiliations =
            json!([{"university":"甲大学","school":school,"department":"研究系","title":null}]);
        sqlx::query("UPDATE candidate SET affiliations=? WHERE id=?")
            .bind(Json(&affiliations))
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([1]))).await;
        save(&db, &c, &result).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = answer("cs@example.edu", json!([]));
        a["names"] = json!(["张三", "张三"]);
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(
            d.candidate.fields.emails,
            if multiple {
                vec![]
            } else {
                vec!["cs@example.edu"]
            }
        );
        assert_eq!(d.candidate.fields.research.as_deref(), Some("机器学习"));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_affiliations_fill_only_unique_pairs(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 甲大学计算机学院教授；兼任甲大学医学院</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let affiliation = |school: Option<&str>, title: Option<&str>| {
        json!({
            "university": "甲大学",
            "school": school,
            "department": null,
            "title": title
        })
    };
    for (old, new, titles) in [
        (
            vec![
                affiliation(Some("计算机学院"), None),
                affiliation(Some("医学院"), None),
            ],
            vec![affiliation(None, Some("教授"))],
            vec![None, None],
        ),
        (
            vec![affiliation(None, None)],
            vec![affiliation(Some("计算机学院"), Some("教授"))],
            vec![Some("教授")],
        ),
        (
            vec![
                affiliation(Some("计算机学院"), None),
                affiliation(Some("医学院"), None),
            ],
            vec![
                affiliation(Some("医学院"), Some("讲师")),
                affiliation(Some("计算机学院"), Some("教授")),
            ],
            vec![Some("教授"), Some("讲师")],
        ),
        (
            vec![affiliation(None, None)],
            vec![
                affiliation(Some("计算机学院"), Some("教授")),
                affiliation(Some("医学院"), Some("讲师")),
            ],
            vec![None],
        ),
    ] {
        let (_, id) = candidate(&db, &site, "/p").await;
        sqlx::query("UPDATE candidate SET affiliations=? WHERE id=?")
            .bind(Json(old))
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        enrich::start(&db, &[id], 2).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let mut a = empty_answer(json!([]));
        a["affiliations"] = json!(new);
        let result = process_answer(&db, &site, &mut provider, &c, a).await;
        save(&db, &c, &result).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(
            d.candidate
                .fields
                .affiliations
                .iter()
                .map(|a| a.title.as_deref())
                .collect::<Vec<_>>(),
            titles
        );
        if titles.iter().all(Option::is_none) {
            assert!(d.candidate.enrichment.enrich_changes.is_empty());
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_deleting_crawl_clears_queued_and_running_candidates(db: SqlitePool) {
    let site = Site::new().await;
    let mentor = mentor::create(
        &db,
        &MentorFields {
            name: "张三".into(),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap();
    for running in [false, true] {
        let (crawl, id) = candidate(&db, &site, "/p").await;
        sqlx::query("UPDATE candidate SET mentor_id=? WHERE id=?")
            .bind(mentor)
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        enrich::start(&db, &[id], 2).await.unwrap();
        let claim = if running {
            queue::next(&db).await.unwrap()
        } else {
            None
        };
        sqlx::query(
            r#"UPDATE candidate
            SET research = '已补方向',
                enrich_changes = '[{"field":"research","value":"已补方向"}]'
            WHERE id = ?"#,
        )
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
        queue::delete(&db, crawl).await.unwrap();
        queue::recover(&db).await.unwrap();
        let d = detail(&db, id).await;
        assert_eq!(d.candidate.enrichment.enrich, EnrichState::None);
        assert_eq!(d.candidate.crawl_id, None);
        assert_eq!(d.candidate.fields.research.as_deref(), Some("已补方向"));
        assert_eq!(d.candidate.enrichment.enrich_changes.len(), 1);
        if let Some(c) = claim {
            assert!(
                !save(&db, &c, &Outcome::Failed("迟到结果".into()))
                    .await
                    .unwrap()
            );
        }
        let plan = enrich::start(&db, &[id], 3).await.unwrap();
        assert!(plan.ids.is_empty());
        assert_eq!(
            serde_json::to_value(&plan.skipped[0].reason).unwrap(),
            json!("deleted_crawl")
        );
        assert!(queue::work(&db, 0, i64::MAX).await.unwrap().is_empty());
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_review_removes_unfinished_pages_without_failures(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 a@example.edu</p><a href='/child'>联系</a>");
    site.page("/child", "<p>张三 b@example.edu</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    for action in ["accept", "reject", "cancel"] {
        for stage in ["queued", "fetched", "child"] {
            let (crawl, id) = candidate(&db, &site, "/p").await;
            enrich::start(&db, &[id], 2).await.unwrap();
            let mut late = None;
            if stage != "queued" {
                let c = queue::next(&db).await.unwrap().unwrap();
                let result = process_answer(
                    &db,
                    &site,
                    &mut provider,
                    &c,
                    answer("a@example.edu", json!([1])),
                )
                .await;
                if stage == "child" {
                    save(&db, &c, &result).await.unwrap();
                    let c = queue::next(&db).await.unwrap().unwrap();
                    let result = process_answer(
                        &db,
                        &site,
                        &mut provider,
                        &c,
                        answer("b@example.edu", json!([])),
                    )
                    .await;
                    late = Some((c, result));
                } else {
                    late = Some((c, result));
                }
            }
            match action {
                "accept" => {
                    review::accept(
                        &db,
                        &ReviewInput {
                            ids: vec![id],
                            decisions: Decisions::default(),
                        },
                        3,
                    )
                    .await
                    .unwrap();
                }
                "reject" => {
                    review::reject(&db, &[id]).await.unwrap();
                }
                _ => {
                    queue::control(&db, crawl, "cancel", 3).await.unwrap();
                }
            }
            if let Some((c, result)) = late {
                assert!(!save(&db, &c, &result).await.unwrap());
            }
            finish(&db, now()).await.unwrap();
            let d = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
            assert_eq!(d.summary.failed, 0, "{action}/{stage}");
            assert_eq!(d.summary.remaining, 0);
            assert_eq!(d.pages.len(), usize::from(stage == "child"));
            assert_eq!(
                d.summary.state,
                if action == "cancel" {
                    queue::CrawlState::Canceled
                } else {
                    queue::CrawlState::Done
                }
            );
            if stage == "child" {
                let candidate = detail(&db, id).await;
                assert_eq!(candidate.candidate.fields.emails, vec!["a@example.edu"]);
                assert_eq!(candidate.evidence.len(), 1);
            }
            assert!(queue::work(&db, 0, 1).await.unwrap().is_empty());
            assert_eq!(queue::retry(&db, crawl, None, 4).await.unwrap(), 0);
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_profile_retry_keeps_first_children(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<p>张三</p><button>下一页</button><a href='/a'>甲</a><a href='/b'>乙</a><a href='/c'>\
        丙</a><a href='/d'>丁</a><a href='/e'>戊</a><a href='/f'>己</a>",
    );
    for path in ["/a", "/b", "/c", "/d", "/e", "/f"] {
        site.page(path, "<p>张三</p>");
    }
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let profile = c.id;
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        empty_answer(json!([1, 2, 3])),
    )
    .await;
    save(&db, &c, &result).await.unwrap();
    while let Some(c) = queue::next(&db).await.unwrap() {
        let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([]))).await;
        save(&db, &c, &result).await.unwrap();
    }
    finish(&db, now()).await.unwrap();
    assert_eq!(queue::retry(&db, crawl, Some(profile), 3).await.unwrap(), 1);
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        empty_answer(json!([4, 5, 6])),
    )
    .await;
    save(&db, &c, &result).await.unwrap();
    while let Some(c) = queue::next(&db).await.unwrap() {
        let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([]))).await;
        save(&db, &c, &result).await.unwrap();
    }
    let d = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
    assert_eq!(
        d.pages
            .iter()
            .filter(|p| p.kind == PageKind::EnrichChild)
            .count(),
        3
    );
    assert_eq!(
        *site.visits.lock().unwrap(),
        vec!["/p", "/a", "/b", "/c", "/p"]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_cancel_discards_fetched_but_unfinished_page(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 a@example.edu</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(
        &db,
        &site,
        &mut provider,
        &c,
        answer("a@example.edu", json!([])),
    )
    .await;
    queue::control(&db, crawl, "cancel", 3).await.unwrap();
    assert!(!save(&db, &c, &result).await.unwrap());
    let d = queue::detail(&db, crawl, 1).await.unwrap().unwrap();
    assert_eq!(d.summary.failed, 0);
    assert!(d.pages.is_empty());
    assert_eq!(queue::retry(&db, crawl, None, 4).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_completion_counts_unreported_candidates_and_distinguishes_crawling(
    db: SqlitePool,
) {
    let site = Site::new().await;
    for crawling in [false, true] {
        let (crawl, old) = candidate(&db, &site, "/old").await;
        sqlx::query("UPDATE crawl SET state='running',finished_at=NULL,reported_at=100 WHERE id=?")
            .bind(crawl)
            .execute(&db)
            .await
            .unwrap();
        queue::open_run(&mut db.acquire().await.unwrap(), crawl, 100)
            .await
            .unwrap();
        sqlx::query(
            r#"UPDATE candidate
            SET enrich = 'done',
                enrich_changes = '[{"field":"email","value":["old@example.edu"]}]'
            WHERE id = ?"#,
        )
        .bind(old)
        .execute(&db)
        .await
        .unwrap();
        sqlx::query(
            r#"INSERT INTO crawl_page (crawl_id, candidate_id, url, kind, mode, status,
                text, work_state, fetched_at)
            VALUES (?, ?, 'https://example.edu/old', 'enrich_profile', 'http',
                'success', '张三', 'done', 50)"#,
        )
        .bind(crawl)
        .bind(old)
        .execute(&db)
        .await
        .unwrap();
        for (path, changes, status) in [
            (
                "/filled",
                json!([{"field":"research","value":"机器学习"}]),
                "success",
            ),
            ("/empty", json!([]), "failed"),
        ] {
            let id: i64 = sqlx::query_scalar(
                r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name,
                    enrich_changes)
                VALUES (?, ?, 'pending', 'done', '张三', ?)
                RETURNING id"#,
            )
            .bind(crawl)
            .bind(site.url(path).as_str())
            .bind(Json(changes))
            .fetch_one(&db)
            .await
            .unwrap();
            for kind in ["enrich_profile", "enrich_child"] {
                sqlx::query(
                    r#"INSERT INTO crawl_page (crawl_id, candidate_id, url, kind, mode,
                        status, text, error, work_state, fetched_at)
                    VALUES (?, ?, ?, ?, 'http', ?, '张三', ?, 'done', 110)"#,
                )
                .bind(crawl)
                .bind(id)
                .bind(format!("https://example.edu{path}/{kind}"))
                .bind(kind)
                .bind(status)
                .bind(if status == "success" {
                    None
                } else {
                    Some("页面读取失败")
                })
                .execute(&db)
                .await
                .unwrap();
            }
        }
        let page: i64 = sqlx::query_scalar(
            r#"INSERT INTO crawl_page (crawl_id, url, scope_url, kind, mode, status,
                error, work_state, fetched_at)
            VALUES (?, 'https://example.edu/list', 'https://example.edu/list', 'list',
                'http', 'failed', '读取失败', 'done', ?)
            RETURNING id"#,
        )
        .bind(crawl)
        .bind(if crawling { 110 } else { 50 })
        .fetch_one(&db)
        .await
        .unwrap();
        let results = finish(&db, 150).await.unwrap();
        assert_eq!(results[0].message.contains("抓取完成"), crawling);
        assert!(results[0].message.contains("信息补全完成：2 位"));
        assert_eq!(
            sqlx::query_scalar::<_, Option<i64>>("SELECT reported_at FROM crawl WHERE id=?")
                .bind(crawl)
                .fetch_one(&db)
                .await
                .unwrap(),
            Some(150)
        );
        // A later crawl-only run must not report those enrichment results again.
        assert_eq!(queue::retry(&db, crawl, Some(page), 200).await.unwrap(), 1);
        let c = queue::next(&db).await.unwrap().unwrap();
        assert_eq!(c.id, page);
        save(&db, &c, &Outcome::Failed("读取失败".into()))
            .await
            .unwrap();
        sqlx::query("UPDATE crawl_page SET fetched_at=210 WHERE id=?")
            .bind(page)
            .execute(&db)
            .await
            .unwrap();
        let results = finish(&db, 250).await.unwrap();
        assert!(results[0].message.contains("抓取完成"));
        assert!(!results[0].message.contains("信息补全"));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_completion_includes_work_before_pause(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/first", "<p>张三 机器学习</p>");
    site.page("/second", "<p>张三</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, first) = candidate(&db, &site, "/first").await;
    let (other, second) = candidate(&db, &site, "/second").await;
    sqlx::query("UPDATE candidate SET crawl_id=? WHERE id=?")
        .bind(crawl)
        .bind(second)
        .execute(&db)
        .await
        .unwrap();
    queue::delete(&db, other).await.unwrap();
    enrich::start(&db, &[first, second], 10).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let mut a = empty_answer(json!([]));
    a["research"] = json!("机器学习");
    let result = process_answer(&db, &site, &mut provider, &c, a).await;
    save(&db, &c, &result).await.unwrap();
    sqlx::query("UPDATE crawl_page SET fetched_at=20 WHERE id=?")
        .bind(c.id)
        .execute(&db)
        .await
        .unwrap();
    assert!(finish(&db, 25).await.unwrap().is_empty());
    assert!(queue::control(&db, crawl, "pause", 30).await.unwrap());
    assert!(queue::next(&db).await.unwrap().is_none());
    assert!(queue::control(&db, crawl, "resume", 40).await.unwrap());
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([]))).await;
    save(&db, &c, &result).await.unwrap();
    sqlx::query("UPDATE crawl_page SET fetched_at=50 WHERE id=?")
        .bind(c.id)
        .execute(&db)
        .await
        .unwrap();
    let events = finish(&db, 60).await.unwrap();
    assert_eq!(events.len(), 1);
    let event = &events[0];
    assert!(event.message.contains("信息补全完成：2 位"));
    assert!(!event.message.contains("抓取完成"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_completion_survives_restart_after_final_save(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 机器学习</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 10).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let mut a = empty_answer(json!([]));
    a["research"] = json!("机器学习");
    let result = process_answer(&db, &site, &mut provider, &c, a).await;
    save(&db, &c, &result).await.unwrap();
    sqlx::query("UPDATE crawl_page SET fetched_at=20 WHERE id=?")
        .bind(c.id)
        .execute(&db)
        .await
        .unwrap();
    queue::recover(&db).await.unwrap();
    let events = finish(&db, 30).await.unwrap();
    assert_eq!(events.len(), 1);
    assert!(events[0].message.contains("信息补全完成：1 位"));
    assert!(finish(&db, 40).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_completion_retry_does_not_claim_previous_changes(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 机器学习</p><button>下一页</button>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 10).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let mut a = empty_answer(json!([]));
    a["research"] = json!("机器学习");
    let result = process_answer(&db, &site, &mut provider, &c, a).await;
    save(&db, &c, &result).await.unwrap();
    let before = detail(&db, id).await;
    assert_eq!(
        before.candidate.fields.research.as_deref(),
        Some("机器学习")
    );
    assert_eq!(before.candidate.enrichment.enrich_changes.len(), 1);
    sqlx::query("UPDATE crawl_page SET fetched_at=20 WHERE id=?")
        .bind(c.id)
        .execute(&db)
        .await
        .unwrap();
    let first = finish(&db, 30).await.unwrap();
    assert_eq!(first.len(), 1);
    assert!(first[0].message.contains("信息补全完成：1 位"));
    assert_eq!(queue::retry(&db, crawl, Some(c.id), 40).await.unwrap(), 1);
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process_answer(&db, &site, &mut provider, &c, empty_answer(json!([]))).await;
    save(&db, &c, &result).await.unwrap();
    sqlx::query("UPDATE crawl_page SET fetched_at=50 WHERE id=?")
        .bind(c.id)
        .execute(&db)
        .await
        .unwrap();
    let after = detail(&db, id).await;
    assert_eq!(
        serde_json::to_value(&after.candidate.enrichment.enrich_changes).unwrap(),
        serde_json::to_value(&before.candidate.enrichment.enrich_changes).unwrap()
    );
    let events = finish(&db, 60).await.unwrap();
    assert_eq!(events.len(), 1);
    assert!(events[0].message.contains("信息补全完成：1 位"));
    assert!(!events[0].message.contains("补到"));
    assert_eq!(events[0].message, first[0].message);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enrichment_model_receives_existing_affiliations(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<p>张三 a@example.edu</p>");
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = candidate(&db, &site, "/p").await;
    let affiliations =
        json!([{"university":"甲大学","school":"计算机学院","department":"研究系","title":null}]);
    sqlx::query("UPDATE candidate SET affiliations=? WHERE id=?")
        .bind(Json(&affiliations))
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let fetcher = Pages::local(site.port);
    let limit = Limit::default();
    let cooldown = Cooldown::default();
    let read = process(&db, &net, &fetcher, &limit, &c, &cooldown);
    let respond = async {
        let request = provider.requests.recv().await.unwrap();
        let data: Value =
            serde_json::from_str(request.body["messages"][1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(data["target"]["affiliations"], affiliations);
        request
            .respond
            .send(Response::answer(
                &answer("a@example.edu", json!([])).to_string(),
            ))
            .ok()
            .unwrap();
    };
    let (result, ()) = tokio::join!(read, respond);
    save(&db, &c, &result.unwrap()).await.unwrap();
    assert_eq!(
        detail(&db, id).await.candidate.fields.emails,
        vec!["a@example.edu"]
    );
}
