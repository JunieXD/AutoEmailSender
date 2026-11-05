//! Measures the queries in docs/data-model.md#性能 at the scale given there, and fails when one is
//! over
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
    let dates = chrono::DateTime::from_timestamp_millis(NOW - 86_400_000)
        .ok_or("invalid benchmark time")?
        .format("%Y-%m-%d")
        .to_string();
    let task = sqlx::query(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
             window_end, per_window, state, created_at)
           VALUES ('无剩余空位的基准任务', 'first', 'ai', 'window', ?, '09:00', '11:00',
             10, 'active', ?)"#,
    )
    .bind(sqlx::types::Json([dates]))
    .bind(NOW)
    .execute(&db)
    .await?
    .last_insert_rowid();
    let without_slot = sqlx::query(
        r#"UPDATE email SET task_id = ?, planned_at = NULL
           WHERE state = 'approved'
             AND id % 3 = 0
             AND id IN (SELECT min(id) FROM email WHERE state = 'approved' GROUP BY mentor_id)"#,
    )
    .bind(task)
    .execute(&db)
    .await?
    .rows_affected();
    let expired = sqlx::query(
        r#"UPDATE email SET planned_at = ?1, latest_at = ?2
           WHERE state = 'approved'
             AND id % 3 = 1"#,
    )
    .bind(NOW - 7_200_000)
    .bind(NOW - 3_600_000)
    .execute(&db)
    .await?
    .rows_affected();
    println!("发送计划样本：{without_slot} 封已批准但无空位，{expired} 封已过截止");
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
        filter: mentor::filters::MentorFilter {
            status,
            keyword: keyword.map(Into::into),
            ..Default::default()
        },
        page: NonZeroU32::MIN.saturating_add(n - 1),
    };
    let mut unanswered = page(Some(ContactStatus::Contacted), None, 1);
    unanswered.filter.unanswered_days = std::num::NonZeroU32::new(7);
    for (label, query) in [
        ("首页，发出超过 7 天没回复", unanswered),
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
            median(async || mentor::list(&db, &query, NOW).await.map(drop)).await?,
            home,
        );
    }

    let library_rows = sqlx::query!(
        r#"SELECT m.id, m.name, a.university, a.school, a.department, a.title
        FROM mentor m LEFT JOIN mentor_affiliation a ON a.mentor_id = m.id AND a.position = 0
        WHERE m.trashed_at IS NULL ORDER BY m.id LIMIT 7406"#
    )
    .fetch_all(&db)
    .await?;
    let summaries: Vec<_> = library_rows
        .iter()
        .map(|r| cloud_api::library::Summary {
            id: format!("mentor_bench{}", r.id),
            name: r.name.clone(),
            aliases: vec![],
            unit: cloud_api::library::Unit {
                university: r.university.clone(),
                school: r.school.clone(),
                department: r.department.clone(),
                title: r.title.clone(),
            },
            research: None,
            email_count: 0,
            fetched: false,
        })
        .collect();
    let records: Vec<_> = library_rows
        .iter()
        .zip(&summaries)
        .map(|(r, summary)| {
            let record = cloud_api::library::Record {
                preview: cloud_api::library::Preview {
                    summary: summary.clone(),
                    units: vec![summary.unit.clone()],
                    papers: vec![],
                    homepages: vec![],
                    source_url: None,
                    verified_at: None,
                },
                emails: vec![],
                revision: 1,
            };
            (r.id, record)
        })
        .collect();
    for (mentor, record) in records.iter().take(10) {
        let summary = &record.preview.summary;
        sqlx::query(
            r#"INSERT INTO mentor_library (library_id, mentor_id, status, record)
            VALUES (?, ?, 'active', ?)"#,
        )
        .bind(&summary.id)
        .bind(mentor)
        .bind(sqlx::types::Json(record))
        .execute(&db)
        .await?;
        sqlx::query(
            r#"INSERT INTO library_update (library_id, field, original, incoming)
            VALUES (?, 'research', ?, ?)"#,
        )
        .bind(&summary.id)
        .bind(sqlx::types::Json("旧研究方向"))
        .bind(sqlx::types::Json("新研究方向"))
        .execute(&db)
        .await?;
    }
    report(
        "导师库，本页 50 位的本机标记",
        median(async || library::local(&db, &summaries[..50]).await.map(drop)).await?,
        home,
    );
    report(
        "导师库，有待处理更新的导师数",
        median(async || library::updates::count(&db).await.map(drop)).await?,
        home,
    );

    let remaining = sqlx::types::Json(
        records
            .iter()
            .skip(10)
            .map(|(mentor, record)| (&record.preview.summary.id, mentor, record))
            .collect::<Vec<_>>(),
    );
    sqlx::query(
        r#"INSERT INTO mentor_library (library_id, mentor_id, status, record)
        SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), 'active',
            json_extract(value, '$[2]') FROM json_each(?)"#,
    )
    .bind(remaining)
    .execute(&db)
    .await?;
    sqlx::query(
        r#"INSERT INTO library_update (library_id, field, original, incoming)
        SELECT library_id, 'research', ?, ? FROM mentor_library
        WHERE library_id LIKE 'mentor_bench%'
        ON CONFLICT (library_id, field, university, school, department) DO NOTHING"#,
    )
    .bind(sqlx::types::Json("旧研究方向"))
    .bind(sqlx::types::Json("新研究方向"))
    .execute(&db)
    .await?;
    report(
        "导师库，7406 位待处理（10 万导师）",
        median(async || library::updates::count(&db).await.map(drop)).await?,
        Duration::from_millis(100),
    );

    let mut university = page(None, None, 1);
    university.filter.universities = vec![Some("清华大学".into())];
    let mut school = page(None, None, 1);
    school.filter.schools = vec![Some("计算机科学与技术学院".into())];
    let mut department = page(None, None, 1);
    department.filter.departments = vec![Some("计算机系".into())];
    let mut title = page(None, None, 1);
    title.filter.titles = vec![Some("教授".into())];
    let mut tag = page(None, None, 1);
    tag.filter.tags = vec![Some(1)];
    let mut missing = page(None, None, 1);
    missing.filter.departments = vec![None];
    let mut score = page(None, None, 1);
    score.filter.matching = Some(mentor::filters::MatchFilter::AtLeast { score: 80 });
    let mut combined = page(Some(ContactStatus::None), Some("学习"), 1);
    combined.filter = mentor::filters::MentorFilter {
        universities: vec![Some("清华大学".into()), Some("北京大学".into())],
        schools: vec![Some("计算机科学与技术学院".into())],
        departments: vec![Some("计算机系".into()), None],
        titles: vec![Some("教授".into()), None],
        tags: vec![Some(1), None],
        matching: Some(mentor::filters::MatchFilter::AtLeast { score: 60 }),
        ..combined.filter
    };
    for (label, query) in [
        ("首页，按学校筛选", university),
        ("首页，按学院筛选", school),
        ("首页，按系所筛选", department),
        ("首页，按职称筛选", title),
        ("首页，按标签筛选", tag),
        ("首页，系所未填写", missing),
        ("首页，匹配度至少 80 分", score),
        ("首页，组合筛选", combined),
    ] {
        report(
            label,
            median(async || mentor::list(&db, &query, NOW).await.map(drop)).await?,
            home,
        );
    }
    report(
        "导师筛选可选值及人数",
        median(async || mentor::filters::options(&db).await.map(drop)).await?,
        Duration::from_millis(150),
    );

    sqlx::query(
        r#"UPDATE message
        SET outcome = CASE id % 5 WHEN 0 THEN 'interested'
        WHEN 1 THEN 'no_slot' WHEN 2 THEN 'declined' WHEN 3 THEN 'unclear'
        ELSE NULL END
        WHERE kind = 'reply'"#,
    )
    .execute(&db)
    .await?;
    for filter in [
        mentor::ReplyOutcomeFilter::Interested,
        mentor::ReplyOutcomeFilter::NoSlot,
        mentor::ReplyOutcomeFilter::Declined,
        mentor::ReplyOutcomeFilter::Unclear,
        mentor::ReplyOutcomeFilter::Unmarked,
    ] {
        let mut query = page(Some(ContactStatus::Replied), None, 1);
        query.filter.reply_outcome = Some(filter);
        report(
            &format!("首页，回信结果 {filter:?}"),
            median(async || mentor::list(&db, &query, NOW).await.map(drop)).await?,
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

    sqlx::query(
        r#"INSERT INTO todo (mentor_id, message_id, text, due_date, due_time, created_at,
            due_changed_at)
        SELECT mm.mentor_id, m.id, '发材料',
            CASE WHEN m.id % 5 <> 0 THEN '2026-10-28' END,
            CASE WHEN m.id % 5 = 1 THEN '17:00' END, 0, 0
        FROM message m
        JOIN message_mentor mm ON mm.message_id = m.id
        WHERE m.kind = 'reply'"#,
    )
    .execute(&db)
    .await?;
    for n in [1, 100] {
        let query = email::todos::TodoQuery {
            page: NonZeroU32::new(n).ok_or("invalid todo page")?,
        };
        report(
            &format!("待办，第 {n} 页"),
            median(async || email::todos::list(&db, &query).await.map(drop)).await?,
            home,
        );
    }
    report(
        "待办，提醒排期",
        median(async || email::todos::local_reminders(&db, NOW).await.map(drop)).await?,
        home,
    );

    for (label, column) in [
        ("发送计划：待处理", send::plan::PlanColumn::Pending),
        ("发送计划：即将发送", send::plan::PlanColumn::Upcoming),
        ("发送计划：历史", send::plan::PlanColumn::History),
    ] {
        let query = send::plan::PlanQuery {
            column,
            page: NonZeroU32::MIN,
        };
        report(
            label,
            median(async || send::plan::list(&db, &query, NOW).await.map(drop)).await?,
            home,
        );
    }

    let stats_budget = Duration::from_millis(50);
    let mut stats_conn = db.acquire().await?;
    for (label, range) in [
        ("7 天", stats::Range::Week),
        ("30 天", stats::Range::Month),
        ("90 天", stats::Range::Quarter),
        ("全部", stats::Range::All),
    ] {
        let first = stats::first_event(&db).await?;
        let window = stats::Window::new(range, "Asia/Shanghai", NOW, first)
            .map_err(sqlx::Error::Protocol)?;
        report(
            &format!("统计，联系数字与回复用时，{label}"),
            median(async || {
                stats::contact_numbers(&mut stats_conn, &window)
                    .await
                    .map(drop)
            })
            .await?,
            stats_budget,
        );
        for page in [1, 3] {
            report(
                &format!("统计，院校对比，{label}，第 {page} 页"),
                median(async || {
                    stats::contact_schools(
                        &mut stats_conn,
                        &window,
                        NonZeroU32::MIN.saturating_add(page - 1),
                    )
                    .await
                    .map(drop)
                })
                .await?,
                stats_budget,
            );
        }
        report(
            &format!("统计，往来趋势，{label}"),
            median(async || {
                stats::contact_trend(&mut stats_conn, &window)
                    .await
                    .map(drop)
            })
            .await?,
            stats_budget,
        );
        report(
            &format!("统计，Token 用量，{label}"),
            median(async || stats::tokens(&db, &window).await.map(drop)).await?,
            stats_budget,
        );
    }
    report(
        "统计，全部趋势的起点",
        median(async || stats::first_event(&db).await.map(drop)).await?,
        stats_budget,
    );
    report(
        "统计，导师匹配分布",
        median(async || stats::mentor_matching(&mut stats_conn).await.map(drop)).await?,
        stats_budget,
    );
    report(
        "统计，导师资料完整度",
        median(async || stats::mentor_completeness(&mut stats_conn).await.map(drop)).await?,
        stats_budget,
    );
    report(
        "统计，导师学校分布",
        median(async || stats::mentor_schools(&mut stats_conn).await.map(drop)).await?,
        stats_budget,
    );
    drop(stats_conn);

    // Each write runs the contact-summary triggers for the mentor.
    let email: i64 = sqlx::query_scalar("SELECT id FROM email WHERE state = 'sent' LIMIT 1")
        .fetch_one(&db)
        .await?;
    let write = median(async || {
        sqlx::query(
            r#"UPDATE email
            SET state = CASE state WHEN 'sent' THEN 'bounced'
            ELSE 'sent' END, error = '退信'
            WHERE id = ?"#,
        )
        .bind(email)
        .execute(&db)
        .await
        .map(drop)
    })
    .await?;
    report("写一封邮件的状态", write, Duration::from_millis(1));

    sqlx::query(
        r#"UPDATE mentor
        SET match_state = NULL, match_error = NULL, match_queued_at = NULL,
            match_priority = NULL
        WHERE match_state IS NOT NULL"#,
    )
    .execute(&db)
    .await?;
    sqlx::query(
        r#"UPDATE mentor
        SET match_state = 'queued', match_queued_at = ?, match_priority = 0
        WHERE id IN (SELECT id FROM mentor WHERE trashed_at IS NULL LIMIT 10000)"#,
    )
    .bind(NOW)
    .execute(&db)
    .await?;
    report(
        "取下一位匹配分析（10 万位导师、1 万位排队）",
        median(async || mentor::matching::next(&db).await.map(drop)).await?,
        Duration::from_millis(1),
    );

    sqlx::query(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://127.0.0.1:1', 'fixture', '', 0, 1)"#,
    )
    .execute(&db)
    .await?;
    let crawl: i64 = sqlx::query_scalar(
        "INSERT INTO crawl (kind, state, created_at) VALUES ('agent', 'running', ?) RETURNING id",
    )
    .bind(NOW)
    .fetch_one(&db)
    .await?;
    sqlx::query(
        "WITH RECURSIVE pages(n) AS (VALUES(1) UNION ALL SELECT n + 1 FROM pages WHERE n < 100000)
         INSERT INTO crawl_page (crawl_id, url, kind, steps, mode, status, fetched_at, text)
         SELECT ?, 'https://school.edu.cn/people/' || n, 'profile', '[]', 'http', 'success', ?,
           '导师的页面文字' FROM pages",
    )
    .bind(crawl)
    .bind(NOW)
    .execute(&db)
    .await?;
    sqlx::query(
        r#"WITH RECURSIVE people(n) AS (
        VALUES (1)
        UNION ALL
        SELECT n + 1
        FROM people
        WHERE n < 10000) INSERT INTO candidate (crawl_id, review, enrich, name, emails,
            affiliations, source_url, match_names, match_schools, profile_url)
        SELECT ?, 'pending', 'none',
            CASE WHEN n % 4 IN (0, 3) THEN '新候选' || n
        ELSE m.name END, CASE WHEN n % 4 = 0 THEN json_array('candidate' || n ||
            '@example.edu.cn') WHEN n % 4 = 1 THEN(
            SELECT json_group_array(address)
            FROM mentor_email
            WHERE mentor_id = m.id
        ) ELSE '[]' END, (
            SELECT json_group_array(json_object('university', university, 'school',
                school, 'department', department, 'title', title))
            FROM mentor_affiliation
            WHERE mentor_id = m.id
        ), 'https://school.edu.cn/faculty', CASE WHEN n % 4 IN (0, 3) THEN json_array(
            '新候选' || n) ELSE(
            SELECT json_group_array(name)
            FROM mentor_name
            WHERE mentor_id = m.id
        ) END, (
            SELECT json_group_array(university_match)
            FROM mentor_affiliation
            WHERE mentor_id = m.id AND university_match IS NOT NULL
        ), 'https://school.edu.cn/people/' || n
        FROM people
        JOIN mentor m ON m.id = n"#,
    )
    .bind(crawl)
    .execute(&db)
    .await?;
    let candidates = crawl::review::CandidateQuery {
        crawl_id: None,
        processed: false,
        filter: crawl::review::CandidateFilter::All,
        page: 1,
    };
    report(
        "候选列表一页（1 万候选、10 万导师）",
        median(async || crawl::review::list(&db, &candidates).await.map(drop)).await?,
        home,
    );
    for (label, filter) in [
        ("新导师", crawl::review::CandidateFilter::New),
        ("可补充", crawl::review::CandidateFilter::Supplement),
        ("可能相同", crawl::review::CandidateFilter::Maybe),
    ] {
        for page in [1, 20] {
            let query = crawl::review::CandidateQuery {
                filter,
                page,
                ..candidates
            };
            report(
                &format!("候选分类，{label}，第 {page} 页（1 万候选、10 万导师）"),
                median(async || crawl::review::list(&db, &query).await.map(drop)).await?,
                home,
            );
        }
    }
    let enrich_ids: Vec<_> = sqlx::query_scalar("SELECT id FROM candidate ORDER BY id LIMIT 50")
        .fetch_all(&db)
        .await?;
    report(
        "预览补全 50 位候选（1 万候选）",
        median(async || crawl::enrich::preview(&db, &enrich_ids).await.map(drop)).await?,
        home,
    );
    crawl::enrich::start(&db, &enrich_ids, NOW).await?;
    report(
        "后台整理与补全进度（10 万页、1 万候选）",
        median(async || crawl::queue::work(&db, NOW, NOW + 1).await.map(drop)).await?,
        home,
    );
    // Every task/mentor pair is unique even when a mentor has several historical letters.
    sqlx::query(
        r#"UPDATE email SET planned_at = coalesce(planned_at, ?1), latest_at = NULL, task_id =
             NULL"#,
    )
    .bind(NOW)
    .execute(&db)
    .await?;
    sqlx::query("DELETE FROM task").execute(&db).await?;
    for id in 1..=20 {
        sqlx::query(
            r#"INSERT INTO task (id, name, kind, writer, schedule, state, created_at)
            VALUES (?, '基准任务', 'first', 'ai', 'now', 'active', ?)"#,
        )
        .bind(id)
        .bind(NOW)
        .execute(&db)
        .await?;
    }
    sqlx::query(
        r#"WITH ranked AS MATERIALIZED(
            SELECT id, row_number() OVER(PARTITION BY mentor_id
            ORDER BY id) AS n
            FROM email
        ) UPDATE email
        SET task_id = ranked.n + 1
        FROM ranked
        WHERE ranked.id = email.id"#,
    )
    .execute(&db)
    .await?;
    sqlx::query(
        r#"UPDATE email
        SET task_id = 1
        WHERE id IN (SELECT min(id) FROM email GROUP BY mentor_id LIMIT 500)"#,
    )
    .execute(&db)
    .await?;
    let query = task::TaskQuery {
        page: NonZeroU32::MIN,
    };
    report(
        "任务列表（20 个任务、6 万封邮件）",
        median(async || task::list(&db, &query).await.map(drop)).await?,
        home,
    );
    report(
        "后台草稿任务摘要（20 个任务、6 万封邮件）",
        median(async || task::work(&db).await.map(drop)).await?,
        home,
    );
    sqlx::query(
        r#"UPDATE email
        SET generated_at = coalesce(sent_at, ?1) - (id % 3) * 86_400_000
        WHERE task_id IS NOT NULL"#,
    )
    .bind(NOW)
    .execute(&db)
    .await?;
    report(
        "后台今天完成（20 个任务、10 万导师、6 万封邮件）",
        median(async || {
            stats::today_work(&db, NOW - 86_400_000, NOW)
                .await
                .map(drop)
        })
        .await?,
        home,
    );
    // Review text includes all rules, aliases, attachment counts and other task mentor names.
    sqlx::query(
        r#"UPDATE email SET state = 'review', error = NULL, cancel_reason = NULL
           WHERE task_id = 1"#,
    )
    .execute(&db)
    .await?;
    let fact_check = serde_json::json!({"state":"completed","findings":[
        {"quote":"北京航空航天大学","reason":"材料中没有就读学校记录"},
        {"quote":"已经改掉的成果","reason":"材料中没有成果记录"},
    ]});
    sqlx::query("UPDATE email SET fact_check = ? WHERE task_id = 1")
        .bind(sqlx::types::Json(fact_check))
        .execute(&db)
        .await?;
    let query = task::DraftQuery {
        task_id: 1,
        filter: task::DraftFilter::All,
        page: NonZeroU32::MIN,
    };
    report(
        "审核页草稿列表一页（500 封的任务，含规则及 AI 核对问题数）",
        median(async || task::drafts(&db, &query, NOW, |_| None).await.map(drop)).await?,
        home,
    );

    let mut table = vec![vec!["姓名".into(), "邮箱".into(), "研究方向".into()]];
    let existing: Vec<(String, String)> = sqlx::query_as(
        r#"SELECT m.name, e.address
        FROM mentor m
        JOIN mentor_email e ON e.mentor_id = m.id
        WHERE m.trashed_at IS NULL AND e.position = 0
        ORDER BY m.id
        LIMIT 500"#,
    )
    .fetch_all(&db)
    .await?;
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
            .intake
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
            if preview.intake.created != 5000 || !preview.intake.maybe_same.is_empty() {
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
