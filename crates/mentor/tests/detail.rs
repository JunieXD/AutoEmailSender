// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use mentor::{MatchResult, MentorFields};
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn mentor_page_reads_analysis_and_note(db: SqlitePool) {
    let new = MentorFields {
        name: "张伟".into(),
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &new, 0).await.unwrap();
    sqlx::query(
        "UPDATE mentor SET match_state = 'done', match_score = 88, matched_at = 5, match_model = 'm'
         WHERE id = ?",
    )
    .bind(id)
    .execute(&db)
    .await
    .unwrap();
    sqlx::query(
        r#"UPDATE mentor_detail SET match_reason = '方向一致', match_directions = '["三维重建"]',
             match_highlights = '[]', match_risks = '["名额少"]', papers = '["论文甲"]'
           WHERE mentor_id = ?"#,
    )
    .bind(id)
    .execute(&db)
    .await
    .unwrap();
    assert!(mentor::set_note(&db, id, Some("组会周三")).await.unwrap());

    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.note.as_deref(), Some("组会周三"));
    assert_eq!(m.papers, ["论文甲"]);
    let MatchResult::Done {
        score,
        directions,
        risks,
        ..
    } = m.analysis
    else {
        panic!("expected a finished analysis, got {:?}", m.analysis);
    };
    assert_eq!(
        (score, directions, risks),
        (88, vec!["三维重建".into()], vec!["名额少".into()])
    );

    assert!(mentor::set_note(&db, id, None).await.unwrap());
    assert_eq!(mentor::get(&db, id).await.unwrap().unwrap().note, None);
    assert!(mentor::get(&db, id + 1).await.unwrap().is_none());
    assert!(!mentor::set_note(&db, id + 1, None).await.unwrap());
}
