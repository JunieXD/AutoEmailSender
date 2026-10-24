#![cfg(test)]
use mentor::{MatchState, MentorFields, MentorQuery, matching::*};
use sqlx::SqlitePool;
use std::num::NonZeroU32;

async fn add(db: &SqlitePool, research: Option<&str>) -> i64 {
    mentor::create(
        db,
        &MentorFields {
            name: "老师".into(),
            research: research.map(Into::into),
            ..Default::default()
        },
        1,
    )
    .await
    .unwrap()
}
fn answer() -> MatchAnswer {
    MatchAnswer {
        score: 88,
        reason: "方向相关".into(),
        directions: vec!["视觉".into()],
        highlights: vec![],
        risks: vec![],
    }
}
async fn result(db: &SqlitePool, id: i64, at: i64) {
    enqueue(db, &[id], true, true, 1).await.unwrap();
    assert_eq!(next(db).await.unwrap(), Some(id));
    assert!(complete(db, id, &answer(), "m", at).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn queue_skips_ineligible_and_current_results_but_redoes_changed(db: SqlitePool) {
    let fresh = add(&db, Some("视觉")).await;
    let old = add(&db, Some("视觉")).await;
    let empty = add(&db, None).await;
    let trash = add(&db, Some("视觉")).await;
    let papers = add(&db, None).await;
    sqlx::query("UPDATE mentor_detail SET papers = '[\"论文\"]' WHERE mentor_id = ?")
        .bind(papers)
        .execute(&db)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[trash], 2).await.unwrap();
    result(&db, fresh, 30).await;
    result(&db, old, 10).await;
    sqlx::query("UPDATE profile SET match_basis_changed_at = 20")
        .execute(&db)
        .await
        .unwrap();
    let ids = [fresh, old, empty, trash, papers, fresh];
    let p = plan(&db, &ids, false).await.unwrap();
    assert_eq!(
        (p.analyze, p.existing, p.changed, p.insufficient, p.queued),
        (2, 1, 1, 1, 0)
    );
    assert_eq!(enqueue(&db, &ids, false, false, 40).await.unwrap(), 2);
    assert_eq!(enqueue(&db, &ids, false, true, 41).await.unwrap(), 1);
    let p = plan(&db, &ids, true).await.unwrap();
    assert_eq!((p.analyze, p.queued, p.insufficient), (0, 3, 1));
    let single = add(&db, Some("视觉")).await;
    enqueue(&db, &[single], true, true, 50).await.unwrap();
    assert_eq!(next(&db).await.unwrap(), Some(single));
    assert_eq!(next(&db).await.unwrap(), Some(old));
    assert_eq!(next(&db).await.unwrap(), Some(papers));
    assert_eq!(next(&db).await.unwrap(), Some(fresh));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn results_survive_queue_failure_retry_and_cancellation(db: SqlitePool) {
    let id = add(&db, Some("视觉")).await;
    result(&db, id, 10).await;
    profile::update(
        &db,
        &profile::ProfileForm {
            name: "".into(),
            research_interests: "视觉".into(),
            rewrite_preference: "".into(),
        },
        20,
    )
    .await
    .unwrap();
    let query = MentorQuery {
        status: None,
        keyword: None,
        page: NonZeroU32::MIN,
    };
    enqueue(&db, &[id], false, false, 30).await.unwrap();
    for expected in [
        MatchState::Queued,
        MatchState::Running,
        MatchState::Failed {
            error: "回答不完整".into(),
        },
    ] {
        let list = mentor::list(&db, &query).await.unwrap();
        let detail = mentor::get(&db, id).await.unwrap().unwrap();
        let r = list.mentors[0].match_state.result.as_ref().unwrap();
        assert_eq!(r.score, 88);
        assert!(r.basis_changed);
        assert_eq!(list.mentors[0].match_state.state.as_ref(), Some(&expected));
        assert_eq!(detail.analysis.state.as_ref(), Some(&expected));
        assert!(detail.analysis.result.unwrap().basis_changed);
        match expected {
            MatchState::Queued => {
                next(&db).await.unwrap();
            }
            MatchState::Running => {
                fail(&db, id, "回答不完整").await.unwrap();
            }
            _ => {}
        }
    }
    assert_eq!(retry(&db, 40).await.unwrap(), 1);
    assert_eq!(retry(&db, 41).await.unwrap(), 0);
    cancel(&db).await.unwrap();
    assert!(!complete(&db, id, &answer(), "late", 50).await.unwrap());
    assert!(!fail(&db, id, "late").await.unwrap());
    assert_eq!(
        mentor::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .analysis
            .result
            .unwrap()
            .model,
        "m"
    );
    enqueue(&db, &[id], true, true, 60).await.unwrap();
    next(&db).await.unwrap();
    cancel(&db).await.unwrap();
    assert!(
        complete(&db, id, &answer(), "after cancel", 70)
            .await
            .unwrap()
    );
    enqueue(&db, &[id], true, true, 80).await.unwrap();
    next(&db).await.unwrap();
    mentor::trash::trash(&db, &[id], 81).await.unwrap();
    mentor::trash::restore(&db, &[id], 82).await.unwrap();
    assert!(!complete(&db, id, &answer(), "late", 90).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn unchanged_interests_do_not_invalidate_a_result(db: SqlitePool) {
    let form = profile::ProfileForm {
        name: "甲".into(),
        research_interests: " 视觉 ".into(),
        rewrite_preference: "".into(),
    };
    profile::update(&db, &form, 10).await.unwrap();
    let id = add(&db, Some("视觉")).await;
    result(&db, id, 20).await;
    profile::update(
        &db,
        &profile::ProfileForm {
            name: "乙".into(),
            ..form
        },
        30,
    )
    .await
    .unwrap();
    assert!(
        !mentor::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .analysis
            .result
            .unwrap()
            .basis_changed
    );
}
