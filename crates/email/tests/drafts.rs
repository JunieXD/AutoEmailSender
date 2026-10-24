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

/// A material, without the file that only sending reads.
async fn add_material(db: &SqlitePool, file_name: &str) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
         VALUES (?, 1000, 0, 0, 0, 0) RETURNING id",
    )
    .bind(file_name)
    .fetch_one(db)
    .await
    .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mentor_has_one_first_draft_until_it_is_cleared(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    email::save_first_draft(&db, mentor, "申请", "", &[], 1)
        .await
        .unwrap();
    email::save_first_draft(&db, mentor, " 申请硕士 ", "　　王老师：", &[], 2)
        .await
        .unwrap();
    let draft = email::first_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(draft.subject.as_deref(), Some("申请硕士"));
    assert_eq!(draft.body_md.as_deref(), Some("　　王老师："));

    let saved = email::save_first_draft(&db, mentor, " ", "\n", &[], 3)
        .await
        .unwrap();
    assert!(saved.is_none());
    assert!(email::first_draft(&db, mentor).await.unwrap().is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_draft_saved_after_the_mentor_is_trashed_is_dropped(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    email::save_first_draft(&db, mentor, "申请", "", &[], 1)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[mentor], 2).await.unwrap();

    let saved = email::save_first_draft(&db, mentor, "申请硕士", "", &[], 3)
        .await
        .unwrap();
    assert!(saved.is_none());
    mentor::trash::restore(&db, &[mentor], 4).await.unwrap();
    assert!(email::first_draft(&db, mentor).await.unwrap().is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_draft_is_approved_once_and_can_be_taken_back(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    email::save_first_draft(&db, mentor, "申请", "王老师：", &[], 1)
        .await
        .unwrap();
    let approval = email::Approval {
        subject: "申请",
        body_md: "王老师：",
        html: "<p>王老师：</p>",
        text: "王老师：",
        attachments: &[],
    };
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 2)
            .await
            .unwrap(),
        email::Approved::Yes
    );
    // Clicking again finds the email approved, so it is not sent twice.
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 3)
            .await
            .unwrap(),
        email::Approved::Refused
    );
    assert!(email::first_draft(&db, mentor).await.unwrap().is_none());
    let emails = email::list(&db, mentor).await.unwrap();
    assert_eq!(emails.len(), 1);
    assert_eq!(emails[0].delivery, email::Delivery::Approved);

    assert!(email::withdraw(&db, emails[0].id, 4).await.unwrap());
    assert!(email::list(&db, mentor).await.unwrap().is_empty());
    let draft = email::first_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(draft.body_md.as_deref(), Some("王老师："));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn attachments_are_kept_with_the_draft_and_named_on_approval(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    let resume = add_material(&db, "简历.pdf").await;
    let transcript = add_material(&db, "成绩单.pdf").await;
    let other_resume = add_material(&db, "简历.pdf").await;
    // Two files of one name would reach the mentor as one, so the first is kept.
    let saved = email::save_first_draft(
        &db,
        mentor,
        "申请",
        "",
        &[resume, transcript, other_resume],
        1,
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(saved.attachments, [transcript, resume]);
    let draft = email::first_draft(&db, mentor).await.unwrap().unwrap();
    assert_eq!(draft.attachments, [transcript, resume]);

    // Deleted after the user picked it: the email does not go without it.
    sqlx::query("DELETE FROM material WHERE id = ?")
        .bind(transcript)
        .execute(&db)
        .await
        .unwrap();
    let picked = [transcript, other_resume];
    let mut approval = email::Approval {
        subject: "申请",
        body_md: "王老师：",
        html: "<p>王老师：</p>",
        text: "王老师：",
        attachments: &picked,
    };
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 2)
            .await
            .unwrap(),
        email::Approved::MaterialGone
    );
    assert!(email::first_draft(&db, mentor).await.unwrap().is_some());

    approval.attachments = &picked[1..];
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 2)
            .await
            .unwrap(),
        email::Approved::Yes
    );
    let emails = email::list(&db, mentor).await.unwrap();
    let attached: Vec<_> = emails[0]
        .attachments
        .iter()
        .map(|a| (a.file_name.as_str(), a.size))
        .collect();
    assert_eq!(attached, [("简历.pdf", 1000)]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_letter_never_saved_is_approved_once(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    let approval = email::Approval {
        subject: "申请",
        body_md: "王老师：",
        html: "<p>王老师：</p>",
        text: "王老师：",
        attachments: &[],
    };
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 1)
            .await
            .unwrap(),
        email::Approved::Yes
    );
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 2)
            .await
            .unwrap(),
        email::Approved::Refused
    );
    assert_eq!(email::list(&db, mentor).await.unwrap().len(), 1);

    let trashed = add_mentor(&db).await;
    mentor::trash::trash(&db, &[trashed], 3).await.unwrap();
    assert_eq!(
        email::approve_first(&db, trashed, &approval, 4)
            .await
            .unwrap(),
        email::Approved::Refused
    );
}
