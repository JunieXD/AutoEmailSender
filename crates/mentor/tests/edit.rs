// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use std::num::NonZeroU32;

use mentor::{MentorFields, MentorForm, MentorQuery, SaveError};
use sqlx::SqlitePool;

fn form(name: &str, email: &str, homepage: &str) -> MentorForm {
    MentorForm {
        name: name.into(),
        title: " ".into(),
        email: email.into(),
        university: "清华大学".into(),
        school: String::new(),
        department: String::new(),
        homepage_url: homepage.into(),
        research: String::new(),
    }
}

#[test]
fn form_is_trimmed_and_checked() {
    let fields = form(" 张伟 ", " zhang@tsinghua.edu.cn ", "https://x.cn")
        .check()
        .unwrap();
    assert_eq!(fields.name, "张伟");
    assert_eq!(fields.email.as_deref(), Some("zhang@tsinghua.edu.cn"));
    assert_eq!(fields.title, None);
    assert_eq!(fields.school, None);

    assert_eq!(form("  ", "", "").check().unwrap_err(), "请填写姓名");
    for email in [
        "zhang",
        "zhang@tsinghua",
        "zhang@@x.cn",
        "zhang wei@x.cn",
        "@x.cn",
        "a@x..cn",
        "zhang..wei@x.cn",
        "zhang@tsinghua.edu.cn;",
        "zhang@tsinghua.edu.cn，",
        "zhang@x.cn/people",
    ] {
        assert_eq!(
            form("张伟", email, "").check().unwrap_err(),
            "邮箱地址格式不正确",
            "{email}"
        );
    }
    for email in ["o'brien+phd@x.edu.cn", "Zhang.Wei@mail.x-y.cn"] {
        assert!(form("张伟", email, "").check().is_ok(), "{email}");
    }
    for homepage in [
        "www.x.cn",
        "https://",
        "https://bad host/zhang",
        "ftp://x.cn",
    ] {
        assert!(form("张伟", "", homepage).check().is_err(), "{homepage}");
    }
    assert!(
        form("张伟", "", "HTTPS://faculty.x.cn/Zhang")
            .check()
            .is_ok()
    );
}

async fn names(db: &SqlitePool, keyword: &str) -> Vec<String> {
    let query = MentorQuery {
        status: None,
        keyword: Some(keyword.into()),
        page: NonZeroU32::MIN,
    };
    let page = mentor::list(db, &query).await.unwrap();
    page.mentors.into_iter().map(|m| m.name).collect()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn editing_updates_the_keyword_index(db: SqlitePool) {
    let mut fields = form("张伟", "", "").check().unwrap();
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    fields.university = Some("北京大学".into());
    fields.research = Some("神经渲染".into());
    assert!(mentor::update(&db, id, &fields, 1).await.unwrap());

    assert!(names(&db, "清华").await.is_empty());
    assert_eq!(names(&db, "北京 渲染").await, ["张伟"]);
    assert!(!mentor::update(&db, id + 1, &fields, 1).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_email_belongs_to_one_mentor(db: SqlitePool) {
    let zhang = MentorFields {
        name: "张伟".into(),
        email: Some("Zhang@X.cn".into()),
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &zhang, 0).await.unwrap();
    let li = MentorFields {
        name: "李静".into(),
        email: Some("zhang@x.cn".into()),
        ..MentorFields::default()
    };
    let Err(SaveError::EmailTaken { name, trashed }) = mentor::create(&db, &li, 0).await else {
        panic!("expected the email to be taken");
    };
    assert_eq!((name.as_str(), trashed), ("张伟", false));
    // Saving a mentor with its own email is fine.
    assert!(mentor::update(&db, id, &zhang, 1).await.unwrap());
}

#[tokio::test]
async fn concurrent_saves_wait_their_turn() {
    let dir = std::path::Path::new(env!("CARGO_TARGET_TMPDIR")).join("concurrent-saves");
    if dir.exists() {
        std::fs::remove_dir_all(&dir).unwrap();
    }
    std::fs::create_dir_all(&dir).unwrap();
    let db = db::open(&dir.join("app.db")).await.unwrap();
    let saves: Vec<_> = (0..8)
        .map(|i| {
            let db = db.clone();
            tokio::spawn(async move {
                let fields = MentorFields {
                    name: format!("导师{i}"),
                    email: Some(format!("m{i}@x.cn")),
                    ..MentorFields::default()
                };
                mentor::create(&db, &fields, 0).await
            })
        })
        .collect();
    for save in saves {
        save.await.unwrap().unwrap();
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mailbox_deleted_while_it_sent_is_not_assigned(db: SqlitePool) {
    let id = mentor::create(&db, &form("张伟", "", "").check().unwrap(), 0)
        .await
        .unwrap();
    // No mailbox 1 any more; recording the email it sent must still succeed.
    mentor::assign_mailbox(&mut db.acquire().await.unwrap(), id, 1)
        .await
        .unwrap();
    let own: Option<i64> = sqlx::query_scalar("SELECT mailbox_id FROM mentor WHERE id = ?")
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(own, None);
}
