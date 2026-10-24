#![cfg(test)]

use crawl::{
    PageUrl,
    store::{
        PageKind, PageMethod, PageRecord, PageResult, PageStatus, PageStep, page_text, save_page,
        visited_page,
    },
};
use sqlx::SqlitePool;

async fn site(db: &SqlitePool) -> i64 {
    sqlx::query_scalar!(
        r#"INSERT INTO crawl (kind, start_urls, state, created_at)
        VALUES ('site', '["https://cs.pku.edu.cn/"]', 'running', 1) RETURNING id AS "id!""#
    )
    .fetch_one(db)
    .await
    .unwrap()
}

fn page() -> PageRecord {
    PageRecord {
        url: PageUrl::parse("https://CS.PKU.EDU.CN/list#top").unwrap(),
        kind: PageKind::List,
        steps: vec![],
        method: PageMethod::Http,
        fetched_at: 1,
        result: PageResult::Success {
            text: "第一页".into(),
        },
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn page_identity_includes_steps_and_retries_replace_in_place(db: SqlitePool) {
    let crawl = site(&db).await;
    let mut page = page();
    let first = save_page(&db, crawl, &page).await.unwrap();
    let lookup = PageUrl::parse("https://cs.pku.edu.cn/list#another").unwrap();
    assert_eq!(
        visited_page(&db, crawl, &lookup, &[])
            .await
            .unwrap()
            .unwrap()
            .id,
        first
    );
    page.steps = vec![PageStep {
        page: 2,
        control: "下一页".into(),
    }];
    page.method = PageMethod::Webview;
    page.result = PageResult::Success {
        text: "第二页".into(),
    };
    let second = save_page(&db, crawl, &page).await.unwrap();
    assert_ne!(first, second);
    assert_eq!(
        page_text(&db, first).await.unwrap().as_deref(),
        Some("第一页")
    );
    assert_eq!(
        page_text(&db, second).await.unwrap().as_deref(),
        Some("第二页")
    );
    page.result = PageResult::Failed {
        reason: "连接中断".into(),
    };
    assert_eq!(save_page(&db, crawl, &page).await.unwrap(), second);
    let visit = visited_page(&db, crawl, &lookup, &page.steps)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(visit.status, PageStatus::Failed);
    assert_eq!(visit.error.as_deref(), Some("连接中断"));
    assert_eq!(page_text(&db, second).await.unwrap(), None);
    page.result = PageResult::NeedsBrowser {
        reason: "需要 JavaScript".into(),
        text: Some("已读到部分内容".into()),
    };
    assert_eq!(save_page(&db, crawl, &page).await.unwrap(), second);
    assert_eq!(
        visited_page(&db, crawl, &lookup, &page.steps)
            .await
            .unwrap()
            .unwrap()
            .status,
        PageStatus::NeedsBrowser
    );
    page.result = PageResult::Success {
        text: "重新读取成功".into(),
    };
    assert_eq!(save_page(&db, crawl, &page).await.unwrap(), second);
    assert!(
        visited_page(&db, crawl, &lookup, &page.steps)
            .await
            .unwrap()
            .unwrap()
            .error
            .is_none()
    );
    let other = site(&db).await;
    assert!(
        visited_page(&db, other, &lookup, &[])
            .await
            .unwrap()
            .is_none()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deleting_a_crawl_removes_pages_and_pending_candidates_but_keeps_reviewed_data(
    db: SqlitePool,
) {
    let crawl = site(&db).await;
    let page = save_page(&db, crawl, &page()).await.unwrap();
    let mentor =
        sqlx::query_scalar!("INSERT INTO mentor (name, created_at, updated_at) VALUES ('已接受的导师', 1, 1) RETURNING id")
            .fetch_one(&db)
            .await
            .unwrap();
    sqlx::query!(
        "INSERT INTO crawl_run (crawl_id, app_version, started_at) VALUES (?, '3.0.0-alpha.0', 1)",
        crawl
    )
    .execute(&db)
    .await
    .unwrap();
    sqlx::query!(
        r#"INSERT INTO candidate (crawl_id, list_page_id, review, enrich, name)
        VALUES (?, ?, 'pending', 'none', '待审核')"#,
        crawl,
        page
    )
    .execute(&db)
    .await
    .unwrap();
    let accepted = sqlx::query_scalar!(r#"INSERT INTO candidate
        (crawl_id, list_page_id, review, mentor_id, enrich, name, emails, affiliations, homepages, aliases)
        VALUES (?, ?, 'accepted', ?, 'done', '已接受', '["a@pku.edu.cn","b@pku.edu.cn"]',
        '[{"university":"北京大学","title":"教授"}]', '["https://cs.pku.edu.cn/a"]', '["Alice"]') RETURNING id"#,
        crawl, page, mentor).fetch_one(&db).await.unwrap();
    sqlx::query!("DELETE FROM crawl WHERE id = ?", crawl)
        .execute(&db)
        .await
        .unwrap();
    assert!(
        sqlx::query_scalar!("SELECT id FROM crawl_page WHERE id = ?", page)
            .fetch_optional(&db)
            .await
            .unwrap()
            .is_none()
    );
    let candidate = sqlx::query!(
        "SELECT crawl_id, list_page_id, mentor_id FROM candidate WHERE id = ?",
        accepted
    )
    .fetch_one(&db)
    .await
    .unwrap();
    assert_eq!(candidate.crawl_id, None);
    assert_eq!(candidate.list_page_id, None);
    assert_eq!(candidate.mentor_id, Some(mentor));
    assert_eq!(
        sqlx::query_scalar!("SELECT count(*) FROM candidate")
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        sqlx::query_scalar!("SELECT count(*) FROM mentor WHERE id = ?", mentor)
            .fetch_one(&db)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        sqlx::query_scalar!("SELECT count(*) FROM crawl_run")
            .fetch_one(&db)
            .await
            .unwrap(),
        0
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_same_profile_cannot_produce_two_candidates_in_a_crawl(db: SqlitePool) {
    let crawl = site(&db).await;
    sqlx::query!(
        r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name)
        VALUES (?, 'https://cs.pku.edu.cn/alice', 'pending', 'none', 'Alice')"#,
        crawl
    )
    .execute(&db)
    .await
    .unwrap();
    assert!(
        sqlx::query!(
            r#"INSERT INTO candidate (crawl_id, profile_url, review, enrich, name)
        VALUES (?, 'https://cs.pku.edu.cn/alice', 'pending', 'none', 'Alice')"#,
            crawl
        )
        .execute(&db)
        .await
        .is_err()
    );
}
