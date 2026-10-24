//! Measures the queries in docs/data-model.md#性能 at the scale given there, and fails when one is over
//! its budget. Add each new high-traffic query here when its module lands.

use std::num::NonZeroU32;
use std::path::Path;
use std::time::{Duration, Instant};

use mentor::{ContactStatus, MentorQuery};

use crate::Result;
use crate::sample::{self, Scale};

/// A fixed "now" keeps runs comparable.
const NOW: i64 = 1_792_800_000_000;
const RUNS: usize = 15;

pub async fn run(path: &Path) -> Result {
    // Start from an empty file; overwriting it leaves nothing to delete.
    std::fs::File::create(path)?;
    let started = Instant::now();
    let db = db::open(path).await?;
    sample::fill(
        &db,
        Scale {
            mentors: 100_000,
            emails: 60_000,
            messages: 25_000,
        },
        NOW,
    )
    .await?;
    sqlx::query("ANALYZE").execute(&db).await?;
    println!("生成数据用了 {:.0} 秒\n", started.elapsed().as_secs_f64());

    let mut over = 0;
    let mut report = |label: &str, took: Duration, budget: Duration| {
        let flag = if took > budget { "  超出预算" } else { "" };
        println!("{:>8.2} ms  {label}{flag}", took.as_secs_f64() * 1000.0);
        over += usize::from(took > budget);
    };

    let home = Duration::from_millis(30);
    let page = |status, keyword: Option<&str>, n: u32| MentorQuery {
        status,
        keyword: keyword.map(Into::into),
        page: NonZeroU32::MIN.saturating_add(n - 1),
    };
    for (label, query) in [
        ("首页，全部，第 1 页", page(None, None, 1)),
        ("首页，全部，第 100 页", page(None, None, 100)),
        ("首页，全部，第 1900 页", page(None, None, 1900)),
        (
            "首页，已联系",
            page(Some(ContactStatus::Contacted), None, 1),
        ),
        ("首页，未联系", page(Some(ContactStatus::None), None, 1)),
        ("首页，关键词，命中少", page(None, Some("量子计算"), 1)),
        ("首页，关键词，命中七成以上", page(None, Some("学习"), 1)),
        ("首页，关键词，一个字", page(None, Some("伟"), 1)),
        ("首页，关键词，姓加方向", page(None, Some("王 学习"), 1)),
    ] {
        report(
            label,
            median(async || mentor::list(&db, &query).await.map(drop)).await?,
            home,
        );
    }

    for (label, unread) in [("往来列表，第 1 页", false), ("往来，新回信", true)] {
        let query = mentor::conversations::ConversationQuery {
            unread,
            page: NonZeroU32::MIN,
        };
        report(
            label,
            median(async || mentor::conversations::list(&db, &query).await.map(drop)).await?,
            home,
        );
    }

    // Each write runs the contact-summary triggers for the mentor.
    let email: i64 = sqlx::query_scalar("SELECT id FROM email WHERE state = 'sent' LIMIT 1")
        .fetch_one(&db)
        .await?;
    let write = median(async || {
        sqlx::query(
            "UPDATE email SET state = CASE state WHEN 'sent' THEN 'bounced' ELSE 'sent' END, error = '退信'
             WHERE id = ?",
        )
        .bind(email)
        .execute(&db)
        .await
        .map(drop)
    })
    .await?;
    report("写一封邮件的状态", write, Duration::from_millis(1));

    sqlx::query("UPDATE mentor SET match_state = NULL, match_error = NULL, match_queued_at = NULL, match_priority = NULL WHERE match_state IS NOT NULL").execute(&db).await?;
    sqlx::query("UPDATE mentor SET match_state = 'queued', match_queued_at = ?, match_priority = 0 WHERE id IN (SELECT id FROM mentor WHERE trashed_at IS NULL LIMIT 10000)").bind(NOW).execute(&db).await?;
    report(
        "取下一位匹配分析（10 万位导师、1 万位排队）",
        median(async || mentor::matching::next(&db).await.map(drop)).await?,
        Duration::from_millis(1),
    );

    // Every task/mentor pair is unique even when a mentor has several historical letters.
    sqlx::query("UPDATE email SET task_id = NULL")
        .execute(&db)
        .await?;
    sqlx::query("DELETE FROM task").execute(&db).await?;
    for id in 1..=20 {
        sqlx::query("INSERT INTO task(id,name,kind,writer,schedule,state,created_at) VALUES (?,'基准任务','first','ai','now','active',?)").bind(id).bind(NOW).execute(&db).await?;
    }
    sqlx::query("WITH ranked AS MATERIALIZED (SELECT id, row_number() OVER (PARTITION BY mentor_id ORDER BY id) AS n FROM email) UPDATE email SET task_id = ranked.n+1 FROM ranked WHERE ranked.id=email.id").execute(&db).await?;
    sqlx::query("UPDATE email SET task_id=1 WHERE id IN (SELECT min(id) FROM email GROUP BY mentor_id LIMIT 500)").execute(&db).await?;
    let query = task::TaskQuery {
        page: NonZeroU32::MIN,
    };
    report(
        "任务列表（20 个任务、6 万封邮件）",
        median(async || task::list(&db, &query).await.map(drop)).await?,
        home,
    );
    // Review text includes all rules, aliases, attachment counts and other task mentor names.
    sqlx::query("UPDATE email SET state='review',error=NULL WHERE task_id=1")
        .execute(&db)
        .await?;
    let query = task::DraftQuery {
        task_id: 1,
        filter: task::DraftFilter::All,
        page: NonZeroU32::MIN,
    };
    report(
        "审核页草稿列表一页（500 封的任务，含问题数）",
        median(async || task::drafts(&db, &query, NOW).await.map(drop)).await?,
        home,
    );

    if over > 0 {
        return Err(format!("{over} 项超出预算").into());
    }
    Ok(())
}

async fn median(
    mut f: impl AsyncFnMut() -> std::result::Result<(), sqlx::Error>,
) -> Result<Duration> {
    f().await?;
    let mut times = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        let started = Instant::now();
        f().await?;
        times.push(started.elapsed());
    }
    times.sort();
    Ok(times[RUNS / 2])
}
