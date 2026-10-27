#![cfg(test)]

use crawl::{
    queue, review,
    submit::{self, SubmitError, Submitted},
};
use mentor::{MentorFields, import::Decisions};
use serde_json::{Value, json};
use sqlx::SqlitePool;

fn candidate(name: &str, email: &str) -> Value {
    json!({"name": name, "emails": [email], "sourceUrl": "https://example.edu/list"})
}

async fn submit(
    db: &SqlitePool,
    crawl: Option<i64>,
    candidates: Vec<Value>,
    at: i64,
) -> Result<Submitted, SubmitError> {
    submit::submit(
        db,
        serde_json::from_value(json!({
            "crawlId": crawl, "candidates": candidates,
        }))
        .unwrap(),
        at,
    )
    .await
}

async fn list(db: &SqlitePool, crawl: i64) -> review::CandidatePage {
    review::list(
        db,
        &review::CandidateQuery {
            crawl_id: Some(crawl),
            processed: false,
            filter: review::CandidateFilter::All,
            page: 1,
        },
    )
    .await
    .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn submitted_candidates_share_review_and_keep_agent_notes_out_of_mentors(db: SqlitePool) {
    for (name, emails) in [
        ("已有导师", vec!["old@example.edu".into()]),
        ("同名导师", vec![]),
    ] {
        mentor::create(
            &db,
            &MentorFields {
                name: name.into(),
                emails,
                ..Default::default()
            },
            1,
        )
        .await
        .unwrap();
    }
    let mut new = candidate("新导师", "new@example.edu");
    new["affiliations"] = json!([{"university":"示例大学", "school":"示例学院"}]);
    new["note"] = json!("学院公共邮箱未收录");
    new["sourceUrl"] = json!("HTTPS://EXAMPLE.EDU:443/people/new#research");
    new["profileUrl"] = new["sourceUrl"].clone();
    let batch = vec![
        new.clone(),
        candidate("已有导师", "old@example.edu"),
        json!({"name":"同名导师", "sourceUrl":"https://example.edu/list",
            "profileUrl":"https://example.edu/same"}),
    ];
    let submitted = submit(&db, None, batch.clone(), 10).await.unwrap();
    assert_eq!(
        (submitted.added, submitted.merged, submitted.total),
        (3, 0, 3)
    );
    let page = list(&db, submitted.crawl_id).await;
    assert_eq!(
        page.candidates
            .iter()
            .map(|c| c.category)
            .collect::<Vec<_>>(),
        vec![
            review::CandidateFilter::New,
            review::CandidateFilter::Supplement,
            review::CandidateFilter::Maybe
        ]
    );
    let id = page.candidates[0].id;
    let detail = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(detail.candidate.note.as_deref(), Some("学院公共邮箱未收录"));
    assert_eq!(
        detail.candidate.source_url.as_deref(),
        Some("https://example.edu/people/new")
    );
    assert_eq!(detail.evidence[0].url, "https://example.edu/people/new");
    assert_eq!(detail.evidence[0].emails, vec!["new@example.edu"]);
    let summary = queue::list(&db, 1).await.unwrap().crawls.remove(0);
    assert_eq!(summary.university.as_deref(), Some("示例大学"));
    assert_eq!(summary.school.as_deref(), Some("示例学院"));
    let option = queue::options(&db).await.unwrap().remove(0);
    assert_eq!(option.university, summary.university);
    assert_eq!(option.school, summary.school);
    assert_eq!(summary.pages, 0);
    let repeated = submit(&db, Some(submitted.crawl_id), batch, 20)
        .await
        .unwrap();
    assert_eq!((repeated.added, repeated.merged, repeated.total), (0, 3, 3));
    review::accept(
        &db,
        &review::ReviewInput {
            ids: vec![id],
            decisions: Decisions::default(),
        },
        21,
    )
    .await
    .unwrap();
    let accepted = review::detail(&db, id).await.unwrap().unwrap().candidate;
    let mentor = mentor::get(&db, accepted.mentor_id.unwrap())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(mentor.note, None);
    submit(&db, Some(submitted.crawl_id), vec![new.clone()], 22)
        .await
        .unwrap();
    assert_eq!(
        review::detail(&db, id)
            .await
            .unwrap()
            .unwrap()
            .candidate
            .review,
        "accepted"
    );
    new["papers"] = json!(["代表论文"]);
    new["note"] = json!("新说明");
    submit(&db, Some(submitted.crawl_id), vec![new], 23)
        .await
        .unwrap();
    let detail = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(detail.candidate.review, "pending");
    assert_eq!(detail.candidate.mentor_id, accepted.mentor_id);
    assert_eq!(detail.candidate.note.as_deref(), Some("新说明"));
    assert_eq!(
        list(&db, submitted.crawl_id).await.candidates[0].category,
        review::CandidateFilter::Supplement
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn duplicate_profiles_and_name_email_overlap_merge_and_keep_first_values(db: SqlitePool) {
    let mut first = candidate("张三", "a@example.edu");
    first["profileUrl"] = json!("HTTPS://EXAMPLE.EDU:443/p#one");
    first["research"] = json!("原方向");
    let mut same_profile = candidate("Zhang San", "b@example.edu");
    same_profile["profileUrl"] = json!("https://example.edu/p#two");
    same_profile["research"] = json!("新方向");
    let overlap = candidate("张三", "b@example.edu");
    let batch = vec![first, same_profile, overlap];
    let result = submit(&db, None, batch.clone(), 1).await.unwrap();
    assert_eq!((result.added, result.merged, result.total), (1, 2, 1));
    let again = submit(&db, Some(result.crawl_id), batch, 2).await.unwrap();
    assert_eq!((again.added, again.merged, again.total), (0, 3, 1));
    let id = list(&db, result.crawl_id).await.candidates[0].id;
    let c = review::detail(&db, id).await.unwrap().unwrap().candidate;
    assert_eq!(c.fields.emails, vec!["a@example.edu", "b@example.edu"]);
    assert_eq!(c.fields.research.as_deref(), Some("原方向"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invalid_batch_reports_positions_and_leaves_database_unchanged(db: SqlitePool) {
    let good = candidate("有效导师", "valid@example.edu");
    let crawl = submit(&db, None, vec![good.clone()], 1)
        .await
        .unwrap()
        .crawl_id;
    let invalid = json!({"name":"错误导师", "emails":["invalid"],
        "sourceUrl":"http://127.0.0.1/", "profileUrl":"file:///tmp/p",
        "papers": vec!["paper";21], "note":"字".repeat(501)});
    for id in [None, Some(crawl)] {
        let error = submit(&db, id, vec![good.clone(), invalid.clone()], 2)
            .await
            .unwrap_err();
        let SubmitError::Invalid(message) = error else {
            panic!("unexpected error")
        };
        assert!(message.contains("第 2 位（错误导师）"));
        for field in ["邮箱", "sourceUrl", "profileUrl", "papers", "note"] {
            assert!(message.contains(field), "{message}");
        }
    }
    let SubmitError::Invalid(message) = submit(&db, None, vec![invalid; 3], 2).await.unwrap_err()
    else {
        panic!("unexpected error")
    };
    assert_eq!(message.lines().count(), 11);
    assert!(message.contains("共 15 条"));
    for batch in [vec![], vec![good; 101]] {
        assert!(matches!(
            submit(&db, Some(crawl), batch, 3).await,
            Err(SubmitError::Invalid(_))
        ));
    }
    let SubmitError::Invalid(message) = submit(
        &db,
        Some(crawl),
        vec![
            json!({"name":"缺来源"}),
            json!({"sourceUrl":"https://example.edu/p"}),
        ],
        3,
    )
    .await
    .unwrap_err() else {
        panic!("unexpected error")
    };
    assert!(message.contains("第 1 位（缺来源）：来源页"));
    assert!(message.contains("第 2 位（）"));
    assert_eq!(queue::list(&db, 1).await.unwrap().total, 1);
    assert_eq!(list(&db, crawl).await.total, 1);
    assert_eq!(
        queue::list(&db, 1).await.unwrap().crawls[0].finished_at,
        Some(1)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn capacity_uses_final_candidate_count_and_overflow_rolls_back_merges(db: SqlitePool) {
    let first = candidate("原导师", "first@example.edu");
    let crawl = submit(&db, None, vec![first.clone()], 1)
        .await
        .unwrap()
        .crawl_id;
    sqlx::query(
        r#"WITH RECURSIVE n(x) AS (
        VALUES(1) UNION ALL SELECT x + 1 FROM n WHERE x < 998
    )
    INSERT INTO candidate (crawl_id, name, review, enrich)
    SELECT ?, '导师' || x, 'pending', 'none' FROM n"#,
    )
    .bind(crawl)
    .execute(&db)
    .await
    .unwrap();
    let second = candidate("第二位", "second@example.edu");
    let accepted = submit(&db, Some(crawl), vec![first.clone(), second.clone()], 2)
        .await
        .unwrap();
    assert_eq!(
        (accepted.added, accepted.merged, accepted.total),
        (1, 1, 1000)
    );
    let mut changed = first;
    changed["note"] = json!("必须回滚");
    let error = submit(
        &db,
        Some(crawl),
        vec![changed, candidate("超限", "extra@example.edu")],
        3,
    )
    .await
    .unwrap_err();
    let SubmitError::Invalid(message) = error else {
        panic!("unexpected error")
    };
    assert!(message.contains("crawlId"));
    let page = list(&db, crawl).await;
    assert_eq!(page.total, 1000);
    assert_eq!(
        review::detail(&db, page.candidates[0].id)
            .await
            .unwrap()
            .unwrap()
            .candidate
            .note,
        None
    );
    assert_eq!(
        queue::list(&db, 1).await.unwrap().crawls[0].finished_at,
        Some(2)
    );
    let repeated = submit(&db, Some(crawl), vec![second], 4).await.unwrap();
    assert_eq!(
        (repeated.added, repeated.merged, repeated.total),
        (0, 1, 1000)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn append_checks_kind_and_serializes_concurrent_submissions(db: SqlitePool) {
    let batch = vec![candidate("导师", "same@example.edu")];
    let crawl = submit(&db, None, batch.clone(), 1).await.unwrap().crawl_id;
    let (a, b) = tokio::join!(
        submit(
            &db,
            Some(crawl),
            vec![candidate("第二位", "second@example.edu")],
            2
        ),
        submit(
            &db,
            Some(crawl),
            vec![candidate("第二位", "second@example.edu")],
            2
        )
    );
    let (a, b) = (a.unwrap(), b.unwrap());
    assert_eq!((a.added + b.added, a.merged + b.merged), (1, 1));
    assert_eq!(list(&db, crawl).await.total, 2);
    for (state, finished) in [
        ("running", None),
        ("paused", None),
        ("canceled", Some(5)),
        ("failed", Some(5)),
        ("done", Some(5)),
    ] {
        sqlx::query("UPDATE crawl SET state = ?, finished_at = ?, error = 'fixture' WHERE id = ?")
            .bind(state)
            .bind(finished)
            .bind(crawl)
            .execute(&db)
            .await
            .unwrap();
        submit(&db, Some(crawl), batch.clone(), 10).await.unwrap();
        let summary = queue::list(&db, 1).await.unwrap().crawls.remove(0);
        assert_eq!(serde_json::to_value(summary.state).unwrap(), state);
        assert_eq!(
            summary.finished_at,
            if state == "done" { Some(10) } else { finished }
        );
    }
    assert_eq!(queue::work(&db, 10, 11).await.unwrap().len(), 1);
    let site = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO crawl(kind, start_urls, state, created_at, finished_at)
        VALUES ('site', '[]', 'done', 1, 1) RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    assert!(matches!(
        submit(&db, Some(site), batch.clone(), 11).await,
        Err(SubmitError::Invalid(_))
    ));
    queue::delete(&db, crawl).await.unwrap();
    assert!(matches!(
        submit(&db, Some(crawl), batch, 11).await,
        Err(SubmitError::Deleted)
    ));
}
