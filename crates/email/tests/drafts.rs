// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use mentor::MentorForm;
use sqlx::SqlitePool;

async fn add_mentor(db: &SqlitePool) -> i64 {
    let form = MentorForm {
        name: "王伟".into(),
        title: String::new(),
        email: String::new(),
        university: String::new(),
        school: String::new(),
        department: String::new(),
        homepage_url: String::new(),
        research: String::new(),
    };
    mentor::create(db, &form.check().unwrap(), 0).await.unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mentor_has_one_first_draft_until_it_is_cleared(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    email::save_first_draft(&db, mentor, "申请", "", 1)
        .await
        .unwrap();
    email::save_first_draft(&db, mentor, " 申请硕士 ", "　　王老师：", 2)
        .await
        .unwrap();
    let draft = email::first_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(draft.subject.as_deref(), Some("申请硕士"));
    assert_eq!(draft.body_md.as_deref(), Some("　　王老师："));

    let saved = email::save_first_draft(&db, mentor, " ", "\n", 3)
        .await
        .unwrap();
    assert!(saved.is_none());
    assert!(email::first_draft(&db, mentor).await.unwrap().is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_draft_saved_after_the_mentor_is_trashed_is_dropped(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    email::save_first_draft(&db, mentor, "申请", "", 1)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[mentor], 2).await.unwrap();

    let saved = email::save_first_draft(&db, mentor, "申请硕士", "", 3)
        .await
        .unwrap();
    assert!(saved.is_none());
    mentor::trash::restore(&db, &[mentor], 4).await.unwrap();
    assert!(email::first_draft(&db, mentor).await.unwrap().is_none());
}
