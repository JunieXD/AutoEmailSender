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

    let mut table = vec![vec!["姓名".into(), "邮箱".into(), "研究方向".into()]];
    let existing: Vec<(String, String)> = sqlx::query_as("SELECT m.name, e.address FROM mentor m JOIN mentor_email e ON e.mentor_id = m.id WHERE m.trashed_at IS NULL AND e.position = 0 ORDER BY m.id LIMIT 500").fetch_all(&db).await?;
    for (name, email) in existing {
        table.push(vec![name, email, "导入补充的研究方向".into()]);
    }
    for i in 0..500 {
        table.push(vec![
            format!("导入的新导师{i}"),
            format!("new{i}@import.example.cn"),
            "新研究方向".into(),
        ]);
    }
    let sheets = sheet::read(
        "导师.xlsx",
        &sheet::write_xlsx(&[sheet::Sheet {
            name: "导师".into(),
            rows: table,
        }])?,
    )?;
    let setup = mentor::import::Setup::default();
    let decisions = mentor::import::Decisions {
        take: mentor::import::preview(&db, &sheets, &setup)
            .await?
            .differences,
        ..Default::default()
    };
    report(
        "导入 1000 行，预览（一半新增）",
        median(async || {
            mentor::import::preview(&db, &sheets, &setup)
                .await
                .map(drop)
        })
        .await?,
        Duration::from_secs(1),
    );
    // Each copy starts from the same records, so every run creates 500 mentors.
    let mut times = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        let snapshot = path.with_extension("import.db");
        let snapshot_path = snapshot.to_string_lossy();
        sqlx::query("VACUUM INTO ?")
            .bind(snapshot_path.as_ref())
            .execute(&db)
            .await?;
        let copy = db::open(&snapshot).await?;
        let started = Instant::now();
        let result = mentor::import::import(&copy, &sheets, &setup, &decisions, NOW).await?;
        times.push(started.elapsed());
        if result.created != 500 {
            return Err(format!("导入基准应新增 500 位，实际 {} 位", result.created).into());
        }
        copy.close().await;
        std::fs::remove_file(&snapshot)?;
    }
    times.sort();
    report(
        "导入 1000 行，写入（一半新增）",
        times[RUNS / 2],
        Duration::from_secs(3),
    );

    let names: Vec<String> = sqlx::query_scalar(
        "SELECT name FROM mentor WHERE trashed_at IS NULL ORDER BY id LIMIT 5000",
    )
    .fetch_all(&db)
    .await?;
    let mut rows = vec![vec!["姓名".into(), "学校".into()]];
    rows.extend(
        names
            .into_iter()
            .map(|name| vec![name, "本地不存在的学校".into()]),
    );
    let sheets = vec![sheet::Sheet {
        name: "导师".into(),
        rows,
    }];
    report(
        "导入 5000 行，预览（无邮箱、姓名有候选但学校不符）",
        median(async || {
            let preview = mentor::import::preview(&db, &sheets, &setup).await?;
            if preview.created != 5000 || !preview.maybe_same.is_empty() {
                return Err(sqlx::Error::Protocol("无邮箱基准应新增 5000 位".into()));
            }
            Ok(())
        })
        .await?,
        Duration::from_secs(2),
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
