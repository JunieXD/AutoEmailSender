// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use mentor::MentorFields;
use sqlx::SqlitePool;

fn approval(attachments: &[i64]) -> email::Approval<'_> {
    email::Approval {
        subject: "申请",
        body_md: "王老师：",
        html: "<p>王老师：</p>",
        text: "王老师：",
        attachments,
    }
}

async fn add_mentor(db: &SqlitePool) -> i64 {
    let fields = MentorFields {
        name: "王伟".into(),
        ..MentorFields::default()
    };
    mentor::create(db, &fields, 0).await.unwrap()
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
    let approval = approval(&[]);
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
    let mut approval = approval(&picked);
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
    let approval = approval(&[]);
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn uploading_a_material_only_changes_the_current_drafts_attachments(db: SqlitePool) {
    let mentor = add_mentor(&db).await;
    let old = add_material(&db, "简历.pdf").await;
    let new = add_material(&db, "简历.pdf").await;
    let transcript = add_material(&db, "成绩单.pdf").await;
    for kind in [email::DraftKind::First, email::DraftKind::Reply] {
        let kind_name = match kind {
            email::DraftKind::First => "first",
            email::DraftKind::Reply => "reply",
        };
        let id: i64 = sqlx::query_scalar(
            "INSERT INTO email (mentor_id, kind, state, subject, body_md, reply_to, created_at, updated_at)
             VALUES (?, ?, 'review', '新主题', '上传前的正文', ?, 1, 1) RETURNING id",
        ).bind(mentor).bind(kind_name).bind(match kind { email::DraftKind::First => None, email::DraftKind::Reply => Some("<current@example.edu>") }).fetch_one(&db).await.unwrap();
        sqlx::query(
            "INSERT INTO email_attachment (email_id, material_id, file_name, size)
          SELECT ?, id, file_name, size FROM material WHERE id IN (?, ?)",
        )
        .bind(id)
        .bind(old)
        .bind(transcript)
        .execute(&db)
        .await
        .unwrap();
        sqlx::query("UPDATE email SET body_md = '返回后保存的新正文' WHERE id = ?")
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        assert!(
            email::attach_draft_material(&db, mentor, kind, new)
                .await
                .unwrap()
        );
        // Retrying the completion cannot add the file twice or remove other files.
        assert!(
            email::attach_draft_material(&db, mentor, kind, new)
                .await
                .unwrap()
        );
        let letter: (String, String, Option<String>) =
            sqlx::query_as("SELECT subject, body_md, reply_to FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(
            letter,
            (
                "新主题".into(),
                "返回后保存的新正文".into(),
                match kind {
                    email::DraftKind::First => None,
                    email::DraftKind::Reply => Some("<current@example.edu>".into()),
                }
            )
        );
        let files: Vec<i64> = sqlx::query_scalar(
            "SELECT material_id FROM email_attachment WHERE email_id = ? ORDER BY file_name",
        )
        .bind(id)
        .fetch_all(&db)
        .await
        .unwrap();
        assert_eq!(files, [transcript, new]);
        sqlx::query("UPDATE email SET state = 'approved', html = '<p>已批准</p>', text = '已批准', planned_at = 2 WHERE id = ?")
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        assert!(
            !email::attach_draft_material(&db, mentor, kind, old)
                .await
                .unwrap()
        );
        let files: Vec<i64> = sqlx::query_scalar(
            "SELECT material_id FROM email_attachment WHERE email_id = ? ORDER BY file_name",
        )
        .bind(id)
        .fetch_all(&db)
        .await
        .unwrap();
        assert_eq!(files, [transcript, new]);
    }
}
