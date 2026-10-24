// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use std::num::NonZeroU32;

use mentor::{Affiliation, MentorFields, MentorForm, MentorQuery, SaveError};
use sqlx::SqlitePool;

fn form(name: &str, email: &str, homepage: &str) -> MentorForm {
    MentorForm {
        name: name.into(),
        aliases: Vec::new(),
        emails: vec![email.into()],
        affiliations: vec![Affiliation {
            university: Some("清华大学".into()),
            school: Some(String::new()),
            title: Some(" ".into()),
            ..Affiliation::default()
        }],
        homepages: vec![homepage.into()],
        research: String::new(),
    }
}

fn place(university: &str) -> Affiliation {
    Affiliation {
        university: Some(university.into()),
        ..Affiliation::default()
    }
}

#[test]
fn form_is_trimmed_and_checked() {
    let fields = form(" 张伟 ", " zhang@tsinghua.edu.cn ", "https://x.cn")
        .check()
        .unwrap();
    assert_eq!(fields.name, "张伟");
    assert_eq!(fields.emails, ["zhang@tsinghua.edu.cn"]);
    assert_eq!(fields.affiliations, [place("清华大学")]);
    assert!(form("张伟", "", "").check().unwrap().emails.is_empty());

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
            format!("邮箱地址“{email}”格式不正确"),
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

#[test]
fn lists_keep_their_order_without_blanks_or_repeats() {
    let blank = Affiliation {
        school: Some(" ".into()),
        ..Affiliation::default()
    };
    let fields = MentorForm {
        name: "张伟".into(),
        aliases: vec![
            "Wei Zhang".into(),
            " 张伟 ".into(),
            "Wei Zhang".into(),
            " ".into(),
        ],
        emails: vec![
            "zhang@gmail.com".into(),
            " ".into(),
            "zhangwei@pku.edu.cn".into(),
            "Zhang@Gmail.com".into(),
        ],
        affiliations: vec![
            place("北京大学"),
            blank,
            place(" 北京大学 "),
            place("清华大学"),
        ],
        homepages: vec!["https://x.cn".into(), "https://x.cn".into()],
        research: String::new(),
    }
    .check()
    .unwrap();
    assert_eq!(fields.aliases, ["Wei Zhang"]);
    assert_eq!(fields.emails, ["zhang@gmail.com", "zhangwei@pku.edu.cn"]);
    assert_eq!(fields.affiliations, [place("北京大学"), place("清华大学")]);
    assert_eq!(fields.homepages, ["https://x.cn"]);
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
    fields.affiliations = vec![place("北京大学")];
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
        emails: vec!["Zhang@X.cn".into()],
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &zhang, 0).await.unwrap();
    let li = MentorFields {
        name: "李静".into(),
        emails: vec!["li@x.cn".into(), "zhang@x.cn".into()],
        ..MentorFields::default()
    };
    let Err(SaveError::EmailTaken {
        address,
        name,
        trashed,
    }) = mentor::create(&db, &li, 0).await
    else {
        panic!("expected the email to be taken");
    };
    assert_eq!(
        (address.as_str(), name.as_str(), trashed),
        ("zhang@x.cn", "张伟", false)
    );
    // Nothing of the failed save stays.
    let mentors: i64 = sqlx::query_scalar("SELECT count(*) FROM mentor")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(mentors, 1);
    // Saving a mentor with its own email is fine.
    assert!(mentor::update(&db, id, &zhang, 1).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn addresses_kept_are_not_looked_up_again(db: SqlitePool) {
    sqlx::query(
        "INSERT INTO mailbox VALUES (1, 'me@qq.com', '林晓', 'qq', 'smtp.qq.com', 465, 'tls',
           'imap.qq.com', 993, 'tls', 'password', 'secret', 50, 60, 1, NULL, NULL, NULL, NULL, 0)",
    )
    .execute(&db)
    .await
    .unwrap();
    let mut fields = MentorFields {
        name: "张伟".into(),
        emails: vec!["zhang@gmail.com".into(), "zhangwei@pku.edu.cn".into()],
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    let queued = || async {
        sqlx::query_scalar::<_, String>("SELECT address FROM mentor_scan ORDER BY address")
            .fetch_all(&db)
            .await
            .unwrap()
    };
    assert_eq!(queued().await, ["zhang@gmail.com", "zhangwei@pku.edu.cn"]);
    sqlx::query("DELETE FROM mentor_scan")
        .execute(&db)
        .await
        .unwrap();

    fields.emails = vec!["ZhangWei@pku.edu.cn".into(), "zw@lab.pku.edu.cn".into()];
    assert!(mentor::update(&db, id, &fields, 1).await.unwrap());
    assert_eq!(queued().await, ["zw@lab.pku.edu.cn"]);
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.emails, ["ZhangWei@pku.edu.cn", "zw@lab.pku.edu.cn"]);
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
                    emails: vec![format!("m{i}@x.cn")],
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
