#![cfg(test)]
use sqlx::SqlitePool;
use stats::{Range, Window};
use std::num::NonZeroU32;
async fn contact(
    db: &SqlitePool,
    window: &Window,
    page: NonZeroU32,
) -> Result<stats::ContactProgress, sqlx::Error> {
    let mut tx = db.begin().await?;
    let result = stats::contact(&mut tx, window, page).await?;
    tx.commit().await?;
    Ok(result)
}
const DAY: i64 = 86_400_000;
fn at(s: &str) -> i64 {
    chrono::DateTime::parse_from_rfc3339(s)
        .unwrap()
        .timestamp_millis()
}
async fn mentor(db: &SqlitePool, n: i64) {
    sqlx::query("INSERT INTO mentor(id,name,created_at,updated_at) VALUES(?,'导师',0,0)")
        .bind(n)
        .execute(db)
        .await
        .unwrap();
    sqlx::query("INSERT INTO mentor_detail(mentor_id) VALUES(?)")
        .bind(n)
        .execute(db)
        .await
        .unwrap();
}
async fn sent(db: &SqlitePool, n: i64, time: i64, state: &str) {
    let id = test_fixtures::mail::first_email(db, n, "sent", time)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE email SET state=?,error=CASE WHEN ?='sent' THEN NULL ELSE '示例' END WHERE id=?",
    )
    .bind(state)
    .bind(state)
    .bind(id)
    .execute(db)
    .await
    .unwrap();
}
async fn message(db: &SqlitePool, mentors: &[i64], time: i64, kind: &str, bounced: bool) {
    let id=sqlx::query("INSERT INTO message(mailbox_id,folder,uidvalidity,uid,kind,sent_at,from_address,truncated) VALUES(1,'INBOX',1,(SELECT coalesce(max(uid),0)+1 FROM message),?,?,'mentor@example.cn',0)").bind(kind).bind(time).execute(db).await.unwrap().last_insert_rowid();
    for n in mentors {
        sqlx::query("INSERT INTO message_mentor(mentor_id,message_id,bounce_reason) VALUES(?,?,?)")
            .bind(n)
            .bind(id)
            .bind(bounced.then_some("退信"))
            .execute(db)
            .await
            .unwrap();
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn contact_cohort_shares_contact_rules_and_counts_real_mail(db: SqlitePool) {
    test_fixtures::mail::mailbox(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
    for n in 1..=10 {
        mentor(&db, n).await;
    }
    let start = at("2026-09-25T00:00:00+08:00");
    let now = at("2026-10-24T23:30:00+08:00");
    for (n, time, state) in [
        (1, start, "sent"),
        (2, start + DAY, "maybe_sent"),
        (3, start + DAY, "bounced"),
        (4, start - 1, "sent"),
        (5, start, "sent"),
        (6, start, "sent"),
        (7, start, "failed"),
    ] {
        sent(&db, n, time, state).await;
    }
    // A reply before our first outgoing mail still means replied; its negative wait is excluded.
    message(&db, &[1], start + DAY, "reply", false).await;
    message(&db, &[2], start + DAY, "auto_reply", false).await;
    message(&db, &[3], start - 1, "reply", false).await;
    message(&db, &[4], now, "reply", false).await;
    message(&db, &[5], start + 2 * DAY, "reply", false).await;
    message(&db, &[6], start + 3 * DAY, "reply", false).await;
    message(&db, &[8], start + DAY, "sent", true).await;
    message(&db, &[8], start + 21 * DAY, "reply", false).await;
    // A single reply linked to several mentors is one received message in the trend.
    message(&db, &[5, 6], now, "reply", false).await;
    message(&db, &[9], now, "auto_reply", false).await;
    message(&db, &[10], now, "reply", false).await;
    sqlx::query("UPDATE mentor SET trashed_at=1 WHERE id=6")
        .execute(&db)
        .await
        .unwrap();
    for (n, pos, school) in [
        (1, 0, "甲大学"),
        (1, 1, "甲大学"),
        (1, 2, "乙大学"),
        (2, 0, "甲大学"),
        (3, 0, "乙大学"),
        (6, 0, "丙大学"),
    ] {
        sqlx::query("INSERT INTO mentor_affiliation(mentor_id,position,university) VALUES(?,?,?)")
            .bind(n)
            .bind(pos)
            .bind(school)
            .execute(&db)
            .await
            .unwrap();
    }
    let w = Window::new(Range::Month, "Asia/Shanghai", now, None).unwrap();
    let s = contact(&db, &w, NonZeroU32::MIN).await.unwrap();
    assert_eq!(
        (s.numbers.contacted, s.numbers.replied, s.numbers.bounced),
        (5, 4, 2)
    );
    assert_eq!(s.numbers.median_days, Some(2.0));
    assert_eq!(s.numbers.waiting, [1, 1, 0, 0, 1]);
    assert_eq!(s.trend.len(), 30);
    assert_eq!(s.trend[0].date, "2026-09-25");
    assert_eq!((s.trend[0].sent, s.trend[0].replies), (2, 0));
    assert_eq!(s.trend.last().unwrap().replies, 3);
    assert_eq!(
        s.school_page
            .schools
            .iter()
            .map(|s| (s.university.as_str(), s.contacted, s.replied))
            .collect::<Vec<_>>(),
        [("乙大学", 2, 2), ("甲大学", 2, 1)]
    );
    assert_eq!(s.school_page.school_count, 2);
    assert!(
        contact(&db, &w, NonZeroU32::new(2).unwrap())
            .await
            .unwrap()
            .school_page
            .schools
            .is_empty()
    );
    let conn = &mut db.acquire().await.unwrap();
    test_fixtures::contact::reconciled(conn).await.unwrap();
    for replied in [false, true] {
        let first_ids: Vec<i64> = sqlx::query_scalar("SELECT f.mentor_id FROM mentor_contact_first f JOIN mentor m ON m.id=f.mentor_id WHERE m.trashed_at IS NULL AND CASE WHEN ? THEN f.reply_at IS NOT NULL ELSE f.first_at IS NOT NULL END ORDER BY f.mentor_id").bind(replied).fetch_all(&db).await.unwrap();
        let summary_ids: Vec<i64> = sqlx::query_scalar("SELECT c.mentor_id FROM mentor_contact c JOIN mentor m ON m.id=c.mentor_id WHERE m.trashed_at IS NULL AND CASE WHEN ? THEN c.status='replied' ELSE c.last_out_at IS NOT NULL END ORDER BY c.mentor_id").bind(replied).fetch_all(&db).await.unwrap();
        assert_eq!(first_ids, summary_ids);
    }

    for (status, expected) in [
        (mentor::ContactStatus::Contacted, "contacted"),
        (mentor::ContactStatus::Replied, "replied"),
    ] {
        let query = mentor::MentorQuery {
            status: Some(status),
            reply_outcome: None,
            keyword: None,
            unanswered_days: None,
            page: NonZeroU32::MIN,
        };
        let listed = mentor::list(&db, &query, now).await.unwrap();
        let ids:Vec<i64>=sqlx::query_scalar("SELECT c.mentor_id FROM mentor_contact c JOIN mentor m ON m.id=c.mentor_id WHERE c.status=? AND m.trashed_at IS NULL ORDER BY c.mentor_id").bind(expected).fetch_all(&db).await.unwrap();
        let mut actual: Vec<_> = listed.mentors.iter().map(|m| m.id).collect();
        actual.sort();
        assert_eq!(actual, ids);
    }
    let all = Window::new(
        Range::All,
        "Asia/Shanghai",
        now,
        stats::first_event(&db).await.unwrap(),
    )
    .unwrap();
    let all = contact(&db, &all, NonZeroU32::MIN).await.unwrap();
    assert_eq!(
        all.trend
            .iter()
            .map(|t| t.date.as_str())
            .collect::<Vec<_>>(),
        ["2026-09", "2026-10"]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn overview_deduplicates_schools_and_keeps_unanalyzed_and_missing_fields(db: SqlitePool) {
    for n in 1..=14 {
        mentor(&db, n).await;
    }
    for (n, score) in [
        (1, 0),
        (2, 19),
        (3, 20),
        (4, 39),
        (5, 40),
        (6, 59),
        (7, 60),
        (8, 79),
        (9, 80),
        (10, 100),
    ] {
        sqlx::query(
            "UPDATE mentor SET match_score=?,matched_at=1,match_model='fixture' WHERE id=?",
        )
        .bind(score)
        .bind(n)
        .execute(&db)
        .await
        .unwrap();
    }
    sqlx::query("UPDATE mentor SET trashed_at=1 WHERE id=14")
        .execute(&db)
        .await
        .unwrap();
    for n in 1..=14 {
        sqlx::query("INSERT INTO mentor_affiliation(mentor_id,position,university) VALUES(?,0,?)")
            .bind(n)
            .bind(format!("学校{n:02}"))
            .execute(&db)
            .await
            .unwrap();
    }
    sqlx::query(
        "INSERT INTO mentor_affiliation(mentor_id,position,university) VALUES(1,1,'学校01')",
    )
    .execute(&db)
    .await
    .unwrap();
    sqlx::query("INSERT INTO mentor_email VALUES('a@example.cn',1,0),('b@example.cn',1,1)")
        .execute(&db)
        .await
        .unwrap();
    sqlx::query(
        r#"UPDATE mentor_detail SET research='方向',papers='["论文"]' WHERE mentor_id IN (1,14)"#,
    )
    .execute(&db)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO mentor_homepage VALUES(1,0,'https://example.cn'),(1,1,'https://example.org')",
    )
    .execute(&db)
    .await
    .unwrap();
    let s = stats::mentors(&db).await.unwrap();
    assert_eq!(s.distribution.total, 13);
    assert_eq!(s.distribution.matching, [2, 2, 2, 2, 2, 3]);
    assert_eq!(
        (
            s.completeness.email,
            s.completeness.research,
            s.completeness.papers,
            s.completeness.homepage
        ),
        (1, 1, 1, 1)
    );
    assert_eq!(s.school_distribution.schools.len(), 10);
    assert_eq!(s.school_distribution.other_schools, 3);
    assert_eq!(s.school_distribution.schools[0].count, 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tokens_use_local_days_and_keep_unpriced_usage(db: SqlitePool) {
    let now = at("2026-03-01T00:10:00+08:00");
    let start = at("2026-02-23T00:00:00+08:00");
    sqlx::query("INSERT INTO model_config(id,provider,model,api_key,agreed,input_price,output_price,created_at) VALUES(1,'deepseek','fixture','',0,2,8,0),(2,'deepseek','fixture','',0,NULL,NULL,0),(3,'deepseek','fixture','',0,1,1,0)").execute(&db).await.unwrap();
    for (t, config, purpose, input, cached, output) in [
        (start - 1, Some(1), "crawl", 9999, 0, 0),
        (start, Some(1), "match", 1000, 200, 500),
        (now, Some(1), "draft", 2000, 1000, 250),
        (now, Some(2), "draft", 3000, 0, 100),
        (now, Some(3), "rewrite", 4000, 1000, 200),
        (
            at("2026-03-02T00:00:00+08:00"),
            Some(1),
            "crawl",
            9999,
            0,
            0,
        ),
    ] {
        sqlx::query("INSERT INTO usage(at,model_config_id,model,purpose,input_tokens,cached_tokens,output_tokens) VALUES(?,?,'fixture',?,?,?,?)").bind(t).bind(config).bind(purpose).bind(input).bind(cached).bind(output).execute(&db).await.unwrap();
    }
    sqlx::query("DELETE FROM model_config WHERE id=3")
        .execute(&db)
        .await
        .unwrap();
    let w = Window::new(Range::Week, "Asia/Shanghai", now, None).unwrap();
    let s = stats::tokens(&db, &w).await.unwrap();
    assert_eq!(
        (
            s.total.input,
            s.total.cached,
            s.total.output,
            s.total.unpriced_calls
        ),
        (10000, 2200, 1050, 2)
    );
    assert!((s.total.estimated_cost.unwrap() - 0.012).abs() < 1e-10);
    assert_eq!(s.purposes.len(), 3);
    assert!(
        s.purposes
            .iter()
            .find(|p| matches!(p.purpose, model::usage::Purpose::Rewrite))
            .unwrap()
            .tokens
            .estimated_cost
            .is_none()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn empty_sections_and_daylight_saving_boundaries(db: SqlitePool) {
    let now = at("2026-03-08T23:30:00-04:00");
    test_fixtures::mail::mailbox(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
    mentor(&db, 1).await;
    for t in [
        "2026-03-02T00:00:00-05:00",
        "2026-03-08T23:59:59-04:00",
        "2026-03-09T00:00:00-04:00",
    ] {
        message(&db, &[1], at(t), "sent", false).await;
    }
    let w = Window::new(Range::Week, "America/New_York", now, None).unwrap();
    let s = contact(&db, &w, NonZeroU32::MIN).await.unwrap();
    assert_eq!(
        s.trend.iter().map(|t| t.sent).collect::<Vec<_>>(),
        [1, 0, 0, 0, 0, 0, 1]
    );
    assert_eq!(s.numbers.median_days, None);
    assert_eq!(s.numbers.waiting, [0; 5]);
    assert!(stats::tokens(&db, &w).await.unwrap().purposes.is_empty());
    assert!(Window::new(Range::Week, "invalid", now, None).is_err());
    let fall = at("2026-11-01T23:30:00-05:00");
    for t in ["2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"] {
        message(&db, &[1], at(t), "sent", false).await;
    }
    let window = Window::new(Range::Week, "America/New_York", fall, None).unwrap();
    let s = contact(&db, &window, NonZeroU32::MIN).await.unwrap();
    assert_eq!(
        s.trend.iter().map(|t| t.sent).collect::<Vec<_>>(),
        [0, 0, 0, 0, 0, 0, 2]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn waiting_buckets_and_even_median_use_elapsed_time(db: SqlitePool) {
    test_fixtures::mail::mailbox(&mut db.acquire().await.unwrap())
        .await
        .unwrap();
    let first = at("2026-10-01T00:00:00+08:00");
    for (i, hours) in [12, 24, 72, 168, 336, 360].into_iter().enumerate() {
        let n = i as i64 + 1;
        mentor(&db, n).await;
        sent(&db, n, first, "sent").await;
        let kind = match i % 3 {
            0 => "first",
            1 => "follow_up",
            _ => "reply",
        };
        sqlx::query("UPDATE email SET kind=?,reply_to=? WHERE mentor_id=?")
            .bind(kind)
            .bind((kind != "first").then_some("<parent@example.cn>"))
            .bind(n)
            .execute(&db)
            .await
            .unwrap();

        message(&db, &[n], first + hours * 3_600_000, "reply", false).await;
    }
    let window = Window::new(
        Range::Month,
        "Asia/Shanghai",
        at("2026-10-25T12:00:00+08:00"),
        None,
    )
    .unwrap();
    let s = contact(&db, &window, NonZeroU32::MIN).await.unwrap();
    assert_eq!(s.numbers.waiting, [2, 1, 1, 1, 1]);
    assert_eq!(s.numbers.median_days, Some(5.0));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn contact_schools_are_sorted_and_paginated(db: SqlitePool) {
    mentor(&db, 1).await;
    let first = at("2026-10-01T00:00:00+08:00");
    sent(&db, 1, first, "sent").await;
    for n in 0..13 {
        sqlx::query("INSERT INTO mentor_affiliation(mentor_id,position,university) VALUES(1,?,?)")
            .bind(n)
            .bind(format!("学校{n:02}"))
            .execute(&db)
            .await
            .unwrap();
    }
    let w = Window::new(Range::All, "Asia/Shanghai", first, Some(first)).unwrap();
    let first_page = contact(&db, &w, NonZeroU32::MIN).await.unwrap().school_page;
    let second_page = contact(&db, &w, NonZeroU32::new(2).unwrap())
        .await
        .unwrap()
        .school_page;
    assert_eq!(first_page.school_count, 13);
    assert_eq!(first_page.schools.len(), 10);
    assert_eq!(first_page.schools[0].university, "学校00");
    assert_eq!(second_page.schools.len(), 3);
    assert_eq!(second_page.schools[0].university, "学校10");
}
