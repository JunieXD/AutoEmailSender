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
