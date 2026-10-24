// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use material::{Error, Purpose};
use sqlx::SqlitePool;

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(format!(
        "{}/tests/fixtures/{name}",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap()
}

fn files(dir: &std::path::Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .collect();
    names.sort();
    names
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_material_keeps_its_file_and_text_until_deleted(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let pdf = fixture("resume.pdf");
    let id = material::add(&db, dir.path(), "简历.pdf", pdf.clone(), 1)
        .await
        .unwrap();

    let [m] = &material::list(&db).await.unwrap()[..] else {
        panic!("one material");
    };
    assert_eq!(
        (m.file_name.as_str(), m.size, m.has_text),
        ("简历.pdf", pdf.len() as i64, true)
    );
    // Attaching changes what the mentor gets, so only the user turns it on.
    assert!(m.for_matching && m.for_writing && !m.attach_by_default);
    assert_eq!(
        material::text(&db, id).await.unwrap(),
        Some(Some("林晓 个人简历\n\n南京大学 计算机科学与技术".into()))
    );
    assert_eq!(std::fs::read(dir.path().join(id.to_string())).unwrap(), pdf);

    assert!(material::delete(&db, dir.path(), id).await.unwrap());
    assert!(material::list(&db).await.unwrap().is_empty());
    assert!(files(dir.path()).is_empty());
    assert!(!material::delete(&db, dir.path(), id).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn word_files_are_read_by_what_they_are(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    // A DOCX named .doc, as happens when a file is renamed by hand.
    let id = material::add(&db, dir.path(), "简历.DOC", fixture("resume.docx"), 1)
        .await
        .unwrap();
    assert_eq!(
        material::text(&db, id).await.unwrap(),
        Some(Some("林晓 个人简历\n南京大学 计算机科学与技术".into()))
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_scan_without_text_can_only_be_attached(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let id = material::add(&db, dir.path(), "成绩单.pdf", fixture("scan.pdf"), 1)
        .await
        .unwrap();
    let m = &material::list(&db).await.unwrap()[0];
    assert!(!m.has_text && !m.for_matching && !m.for_writing);
    assert!(
        material::set_purpose(&db, id, Purpose::AttachByDefault, true)
            .await
            .unwrap()
    );
    assert!(material::list(&db).await.unwrap()[0].attach_by_default);
    assert!(
        material::set_purpose(&db, id, Purpose::Writing, true)
            .await
            .is_err()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_file_that_cannot_be_read_is_not_kept(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let damaged = b"%PDF-1.7\nnot really a PDF".to_vec();
    let add = |name: &'static str, data: Vec<u8>| material::add(&db, dir.path(), name, data, 1);
    assert!(matches!(
        add("简历.pdf", damaged).await,
        Err(Error::Unreadable(_))
    ));
    assert!(matches!(
        add("简历.docx", b"not a word file".to_vec()).await,
        Err(Error::Unreadable(_))
    ));
    assert!(matches!(
        add("简历.txt", b"plain text".to_vec()).await,
        Err(Error::Type)
    ));
    assert!(matches!(
        add("简历.pdf", vec![b' '; material::MAX_SIZE + 1]).await,
        Err(Error::TooLarge)
    ));
    assert!(material::list(&db).await.unwrap().is_empty());
    assert!(files(dir.path()).is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_material_stays_while_an_email_to_go_out_has_it_attached(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let id = material::add(&db, dir.path(), "简历.pdf", fixture("resume.pdf"), 1)
        .await
        .unwrap();
    let form = mentor::MentorForm {
        name: "王伟".into(),
        title: String::new(),
        email: String::new(),
        university: String::new(),
        school: String::new(),
        department: String::new(),
        homepage_url: String::new(),
        research: String::new(),
    };
    let mentor = mentor::create(&db, &form.check().unwrap(), 1)
        .await
        .unwrap();
    email::save_first_draft(&db, mentor, "申请", "王老师：", &[id], 1)
        .await
        .unwrap();
    let approval = email::Approval {
        subject: "申请",
        body_md: "王老师：",
        html: "<p>王老师：</p>",
        text: "王老师：",
        attachments: &[id],
    };
    assert_eq!(
        email::approve_first(&db, mentor, &approval, 2)
            .await
            .unwrap(),
        email::Approved::Yes
    );

    assert!(matches!(
        material::delete(&db, dir.path(), id).await,
        Err(Error::InUse)
    ));
    let email = email::list(&db, mentor).await.unwrap().remove(0);
    assert!(email::withdraw(&db, email.id, 3).await.unwrap());
    // Back to a draft, which loses the attachment along with the material.
    assert!(material::delete(&db, dir.path(), id).await.unwrap());
    let draft = email::first_draft(&db, mentor).await.unwrap().unwrap();
    assert!(draft.attachments.is_empty());
}
