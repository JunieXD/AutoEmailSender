#![cfg(test)]

use api::{Context, call};
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::provider;
use tokio::{
    sync::mpsc,
    time::{Duration, timeout},
};

const T: i64 = 1_000;
const LETTER: &str = "<subject>申请</subject><body>王老师：您好，我希望申请读研。</body>";

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn today_work_counts_generated_drafts_retained_matches_and_confirmed_sends(db: SqlitePool) {
    let ctx = Context {
        writes: db::Writes::default(),
        db,
        net: net::Net::new(vec![]).unwrap(),
        materials: Default::default(),
        sender: Default::default(),
        receiver: Default::default(),
        matcher: Default::default(),
        crawler: Default::default(),
        generator: Default::default(),
        model_limit: Default::default(),
        mail_domains: Default::default(),
        reply_processor: Default::default(),
        todo_reminders: Default::default(),
    };
    let (address, _) = provider::scripted(vec![(LETTER, "stop"); 2]).await.unwrap();
    sqlx::query(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed, created_at)
        VALUES ('custom', ?, 'fixture', '', 1, 0)"#,
    )
    .bind(address)
    .execute(&ctx.db)
    .await
    .unwrap();
    profile::set_ai_fact_check(&ctx.db, false).await.unwrap();
    sqlx::raw_sql(
        r#"INSERT INTO mentor (id, name, match_score, matched_at, match_model, trashed_at,
            created_at, updated_at)
        VALUES (1, '王伟', 80, 1000, 'fixture', NULL, 0, 0),
            (2, '李明', 80, 1000, 'fixture', 1000, 0, 0),
            (3, '吴华', 80, 1001, 'fixture', NULL, 0, 0);
        INSERT INTO mentor_detail (mentor_id, research, match_reason,
            match_directions, match_highlights, match_risks)
        VALUES (1, '机器学习', '方向相近', '[]', '[]', '[]'),
            (2, '机器学习', '方向相近', '[]', '[]', '[]'),
            (3, '机器学习', '方向相近', '[]', '[]', '[]');
        INSERT INTO task (id, name, kind, writer, schedule, state, created_at)
        VALUES (1, '今天的草稿', 'first', 'ai', 'now', 'active', 0);
        INSERT INTO email (id, mentor_id, task_id, kind, state, generation_queued_at,
            created_at, updated_at)
        VALUES (1, 1, 1, 'first', 'generating', 0, 0, 0);"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    let (done, mut finished) = mpsc::unbounded_channel();
    let worker = tokio::spawn(task::generation::run(
        ctx.db.clone(),
        ctx.writes.clone(),
        ctx.net.clone(),
        ctx.generator.clone(),
        ctx.model_limit.clone(),
        || {},
        move |result| {
            done.send(result).unwrap();
        },
    ));
    for regenerate in [false, true] {
        if regenerate {
            sqlx::query("UPDATE email SET generated_at = 1 WHERE id = 1")
                .execute(&ctx.db)
                .await
                .unwrap();
            task::regenerate(&ctx.db, 1, Some(1), false, T)
                .await
                .unwrap();
            ctx.generator.wake();
        }
        assert_eq!(
            timeout(Duration::from_secs(10), finished.recv())
                .await
                .unwrap()
                .unwrap()
                .generated,
            1
        );
        let at: Option<i64> = sqlx::query_scalar("SELECT generated_at FROM email WHERE id = 1")
            .fetch_one(&ctx.db)
            .await
            .unwrap();
        assert!(at.is_some_and(|at| at > 1));
    }
    worker.abort();
    // Pin persisted times because the background runner uses the wall clock.
    sqlx::raw_sql(
        r#"UPDATE email
        SET state = 'approved', html = '<p>信</p>', text = '信', generated_at = 1000
        WHERE id = 1;
        INSERT INTO email (mentor_id, task_id, kind, state, generated_at, created_at, updated_at)
        VALUES (2, 1, 'first', 'review', 999, 0, 0);
        WITH outgoing(id, state, at) AS (
            VALUES (3, 'sent', 1000), (4, 'bounced', 1000),
                (5, 'maybe_sent', 1000), (6, 'sent', 1001)
        )
        INSERT INTO email (id, mentor_id, kind, state, planned_at, from_address, to_address,
            message_id, sent_at, error, created_at, updated_at, subject, body_md, html, text)
        SELECT id, 1, 'first', state, 0, 'me@example.com', 'wang@example.com',
            '<' || id || '@aes>', at, 'fixture', 0, 0, '申请', '信', '<p>信</p>', '信'
        FROM outgoing;"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    for (start, end, matched, sent) in [(T, T + 1, 1, 2), (T - 1, T, 0, 0)] {
        assert_eq!(
            call(&ctx, "todayWork", json!({"start": start, "end": end}))
                .await
                .unwrap(),
            json!({
                "drafts": [{"id": 1, "name": "今天的草稿", "generated": 1}],
                "matched": matched,
                "sent": sent,
            })
        );
    }
}
