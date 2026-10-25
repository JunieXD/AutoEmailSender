use super::*;
use crate::enrich::{self, EnrichSkipReason, EnrichState};

pub(super) async fn candidate(db: &SqlitePool, site: &Site, path: &str) -> (i64, i64) {
    let crawl = sqlx::query_scalar::<_,i64>("INSERT INTO crawl(kind,start_urls,state,created_at,finished_at) VALUES('site','[]','done',1,1) RETURNING id").fetch_one(db).await.unwrap();
    let id = sqlx::query_scalar::<_,i64>("INSERT INTO candidate(crawl_id,profile_url,review,enrich,name,aliases,emails,affiliations,homepages,papers,match_names) VALUES(?,?,'pending','none','张三','[\"Zhang San\"]','[]','[]','[]','[]','[\"张三\",\"Zhang San\"]') RETURNING id").bind(crawl).bind(site.url(path).as_str()).fetch_one(db).await.unwrap();
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
    let fetcher = Fetcher::local(site.port);
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
async fn enrichment_personal_page_fills_only_empty_fields(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三 a@example.edu 机器学习</p>",
    );
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/p").await;
    sqlx::query("UPDATE candidate SET papers='[\"原论文\"]',affiliations='[{\"university\":\"原大学\",\"school\":null,\"department\":null,\"title\":\"教授\"}]' WHERE id=?").bind(id).execute(&db).await.unwrap();
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
            .any(|c| c.field == "邮箱" && c.value == json!(["a@example.edu"]))
    );
    finish(&db).await.unwrap();
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
    site.page("/p","<title>张三</title><p>张三</p><a href='https://outside.example.org/mail'>站外</a><a href='/contact'>联系方式</a><a href='/research'>研究</a><a href='/papers'>论文</a><a href='/fourth'>更多</a>");
    site.page(
        "/contact",
        "<title>学院联系方式</title><div><p>张三 a@example.edu</p><p>李四 b@example.edu</p></div>",
    );
    site.page("/research", "<p>张三 机器学习</p>");
    site.page("/papers", "<p>张三 论文</p>");
    let mut provider = Provider::start().await;
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
    let mut provider = Provider::start().await;
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
    let mut provider = Provider::start().await;
    config(&db, &provider.address).await;
    let (crawl, id) = candidate(&db, &site, "/missing").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let c = queue::next(&db).await.unwrap().unwrap();
    let result = process(
        &db,
        &net::Net::new(vec![]).unwrap(),
        &Fetcher::local(site.port),
        &Limit::default(),
        &c,
        &Cooldown::default(),
    )
    .await
    .unwrap();
    save(&db, &c, &result).await.unwrap();
    finish(&db).await.unwrap();
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
                &Fetcher::local(site.port),
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
        let mut old = Provider::start().await;
        let mut new = Provider::start().await;
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
        let reader = Reader {
            db: &db,
            net: &net,
            limit: &limit,
            claim: &c,
            chosen: &chosen,
            cooldown: &cooldown,
        };
        let ask =
            reader.ask::<extract::Enriched>(extract::ENRICH, json!({"target":{"name":"张三"}}));
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
        tokio::select! {r=&mut ask=>assert!(matches!(r,Err(ReadError::Gone))),_=old.requests.recv()=>panic!("stale model request started")}
        save(&db, &c, &Outcome::Gone).await.unwrap();
        let c = queue::next(&db).await.unwrap().unwrap();
        let outcome = process(&db, &net, &Fetcher::local(site.port), &limit, &c, &cooldown)
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
        finish(&db).await.unwrap();
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
    let mut p = Provider::start().await;
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
                &Fetcher::local(port),
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
    let (a_limited, (claim, result)) =
        tokio::select! {r=&mut a=>(true,r.unwrap()),r=&mut b=>(false,r.unwrap())};
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
    let mut p = Provider::start().await;
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
    let keep=sqlx::query_scalar::<_,i64>("INSERT INTO candidate(crawl_id,profile_url,review,enrich,name,emails,mentor_id) VALUES(?,?,'accepted','none','张三','[\"a@example.edu\"]',?) RETURNING id").bind(crawl).bind(site.url("/keep").as_str()).bind(mentor).fetch_one(&db).await.unwrap();
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
    let page=sqlx::query_scalar::<_,i64>("INSERT INTO crawl_page(crawl_id,url,kind,mode,status,text,fetched_at) VALUES(?,?,'profile','http','success','张三',1) RETURNING id").bind(crawl).bind(site.url("/keep").as_str()).fetch_one(&mut *tx).await.unwrap();
    review::save(
        &mut tx,
        crawl,
        page,
        PageKind::Profile,
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
    let mut provider = Provider::start().await;
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
    let duplicate=sqlx::query_scalar::<_,i64>("INSERT INTO candidate(crawl_id,profile_url,review,enrich,name,emails) VALUES(?,?,'pending','none','张三','[\"a@example.edu\"]') RETURNING id").bind(crawl).bind(site.url("/duplicate").as_str()).fetch_one(&db).await.unwrap();
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
    let page=sqlx::query_scalar::<_,i64>("INSERT INTO crawl_page(crawl_id,url,kind,mode,status,text,fetched_at) VALUES(?,?,'profile','http','success','张三',1) RETURNING id").bind(crawl).bind(site.url("/active").as_str()).fetch_one(&mut *tx).await.unwrap();
    review::save(
        &mut tx,
        crawl,
        page,
        PageKind::Profile,
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
