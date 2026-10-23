// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use std::num::NonZeroU32;

use mentor::{Contact, ContactStatus, MatchState, MentorFields, MentorQuery, PAGE_SIZE};
use sqlx::SqlitePool;

fn query(status: Option<ContactStatus>, keyword: Option<&str>, page: u32) -> MentorQuery {
    MentorQuery {
        status,
        keyword: keyword.map(Into::into),
        page: NonZeroU32::new(page).unwrap(),
    }
}

async fn names(
    db: &SqlitePool,
    status: Option<ContactStatus>,
    keyword: Option<&str>,
) -> Vec<String> {
    let page = mentor::list(db, &query(status, keyword, 1)).await.unwrap();
    assert_eq!(page.total, page.mentors.len() as i64);
    page.mentors.into_iter().map(|m| m.name).collect()
}

async fn add(db: &SqlitePool, name: &str, university: &str, research: &str) -> i64 {
    let mentor = MentorFields {
        name: name.into(),
        university: Some(university.into()),
        research: Some(research.into()),
        ..MentorFields::default()
    };
    mentor::create(db, &mentor, 0).await.unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn keyword_finds_text_containing_it(db: SqlitePool) {
    add(&db, "张伟", "清华大学", "三维重建与神经渲染").await;
    add(&db, "李静", "北京大学", "自然语言处理、大语言模型对齐").await;
    add(&db, "王磊", "浙江大学", "Machine Learning on Graphs").await;

    assert_eq!(names(&db, None, Some("张伟")).await, ["张伟"]);
    assert_eq!(names(&db, None, Some("重建")).await, ["张伟"]);
    assert_eq!(names(&db, None, Some("语言模型")).await, ["李静"]);
    assert_eq!(names(&db, None, Some("LEARNING")).await, ["王磊"]);
    // Every word must appear, in any field.
    assert_eq!(names(&db, None, Some("清华 神经")).await, ["张伟"]);
    assert!(names(&db, None, Some("清华 语言")).await.is_empty());
    // One character only matches names, otherwise it would hit nearly everyone.
    assert_eq!(names(&db, None, Some("静")).await, ["李静"]);
    assert!(names(&db, None, Some("学")).await.is_empty());
    assert_eq!(names(&db, None, Some("李 语言")).await, ["李静"]);
    assert!(names(&db, None, Some("张 语言")).await.is_empty());
    assert_eq!(names(&db, None, Some("静 李")).await, ["李静"]);
    assert_eq!(names(&db, None, Some("、！")).await.len(), 3);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn filters_sorts_and_pages(db: SqlitePool) {
    let contacted = add(&db, "甲", "清华大学", "视觉").await;
    let trashed = add(&db, "乙", "清华大学", "视觉").await;
    add(&db, "丙", "清华大学", "视觉").await;
    sqlx::query("UPDATE mentor SET trashed_at = 1 WHERE id = ?")
        .bind(trashed)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query(
        "INSERT INTO email (mentor_id, kind, state, planned_at, from_address, message_id, sent_at,
           created_at, updated_at, subject, body_md, html, text)
         VALUES (?, 'first', 'sent', 5, 'me@qq.com', '<1@aes>', 5, 0, 0, '申请', '信', '<p>信</p>', '信')",
    )
    .bind(contacted)
    .execute(&db)
    .await
    .unwrap();
    let tag = sqlx::query("INSERT INTO tag (name, color, position) VALUES ('梦导', '#991b1b', 1)")
        .execute(&db)
        .await
        .unwrap()
        .last_insert_rowid();
    sqlx::query("INSERT INTO mentor_tag (mentor_id, tag_id) VALUES (?, ?)")
        .bind(contacted)
        .bind(tag)
        .execute(&db)
        .await
        .unwrap();

    assert_eq!(
        names(&db, Some(ContactStatus::Contacted), None).await,
        ["甲"]
    );
    assert_eq!(names(&db, Some(ContactStatus::None), None).await, ["丙"]);
    let page = mentor::list(&db, &query(Some(ContactStatus::Contacted), None, 1))
        .await
        .unwrap();
    let row = &page.mentors[0];
    assert_eq!(
        row.contact,
        Contact::Contacted {
            count: 1,
            last_out_at: 5
        }
    );
    assert_eq!(row.tags[0].name, "梦导");

    // Best matches first; mentors not yet analysed come last.
    for i in 0..PAGE_SIZE + 5 {
        let id = add(&db, &format!("导师{i}"), "北京大学", "语言").await;
        sqlx::query(
            "UPDATE mentor SET match_state = 'done', match_score = ?, matched_at = 0, match_model = 'm'
             WHERE id = ?",
        )
        .bind(45 + i)
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    }
    let first = mentor::list(&db, &query(None, None, 1)).await.unwrap();
    let second = mentor::list(&db, &query(None, None, 2)).await.unwrap();
    assert_eq!(first.total, i64::from(PAGE_SIZE) + 7);
    assert_eq!(first.mentors[0].match_state, MatchState::Done { score: 99 });
    assert_eq!(second.mentors.len(), 7);
    assert_eq!(
        second.mentors.last().unwrap().match_state,
        MatchState::NotAnalyzed
    );
    let scores: Vec<_> = first
        .mentors
        .iter()
        .chain(&second.mentors)
        .map(|m| &m.match_state)
        .collect();
    assert!(scores.windows(2).all(|w| match (w[0], w[1]) {
        (MatchState::Done { score: a }, MatchState::Done { score: b }) => a >= b,
        (_, MatchState::NotAnalyzed) => true,
        _ => false,
    }));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn keyword_does_not_span_fields(db: SqlitePool) {
    add(&db, "李华", "华东师范大学", "视觉").await;
    add(&db, "李华东", "清华大学", "视觉").await;
    assert_eq!(names(&db, None, Some("李华东")).await, ["李华东"]);
}
