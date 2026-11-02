// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use std::borrow::Cow;

use mailbox::{MailboxForm, SaveError, Security, Server};
use sqlx::SqlitePool;

fn form(address: &str) -> MailboxForm {
    let server = |host: &'static str, port| Server {
        host: Cow::Borrowed(host),
        port,
        security: Security::Tls,
    };
    MailboxForm {
        address: address.into(),
        sender_name: "林晓".into(),
        provider: "qq".into(),
        smtp: server("smtp.qq.com", 465),
        imap: server("imap.qq.com", 993),
        daily_limit: 50,
        min_interval_s: 60,
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_saved_secret_is_only_for_its_servers(db: SqlitePool) {
    let fields = form("me@qq.com").check().unwrap();
    let id = mailbox::create(&db, &fields, "secret", 0).await.unwrap();

    let same = form("me@qq.com").check().unwrap();
    assert_eq!(
        mailbox::secret(&db, id, &same).await.unwrap().as_deref(),
        Some("secret")
    );
    let mut moved = form("me@qq.com");
    moved.smtp.host = Cow::Borrowed("smtp.example.com");
    let moved = moved.check().unwrap();
    assert_eq!(mailbox::secret(&db, id, &moved).await.unwrap(), None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_address_is_added_once_in_any_letter_case(db: SqlitePool) {
    let first = form("Me@qq.com").check().unwrap();
    mailbox::create(&db, &first, "s", 0).await.unwrap();
    let again = form("me@QQ.com").check().unwrap();
    let result = mailbox::create(&db, &again, "s", 0).await;
    assert!(matches!(result, Err(SaveError::AddressTaken)), "{result:?}");
    // As Chinese input methods type it.
    let full_width = form("Ｍｅ＠ｑｑ．ｃｏｍ").check().unwrap();
    assert_eq!(full_width.address.to_string(), "Me@qq.com");
    let result = mailbox::create(&db, &full_width, "s", 0).await;
    assert!(matches!(result, Err(SaveError::AddressTaken)), "{result:?}");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pauses_expire_extend_and_clear_on_resume_or_edit(db: SqlitePool) {
    use mailbox::pause::{self, Pause};
    let fields = form("me@qq.com").check().unwrap();
    let id = mailbox::create(&db, &fields, "secret", 0).await.unwrap();
    let apply = |until, reason: &str| Pause {
        until,
        reason: reason.into(),
    };
    let mut tx = db.begin().await.unwrap();
    pause::apply(&mut tx, id, &apply(2000, "限流"))
        .await
        .unwrap();
    pause::apply(&mut tx, id, &apply(1000, "连不上"))
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let active = mailbox::list(&db, 1999)
        .await
        .unwrap()
        .remove(0)
        .pause
        .unwrap();
    assert_eq!((active.until, active.reason.as_str()), (2000, "限流"));
    assert!(mailbox::list(&db, 2000).await.unwrap()[0].pause.is_none());
    assert!(mailbox::resume(&db, id).await.unwrap());
    assert!(mailbox::list(&db, 0).await.unwrap()[0].pause.is_none());
    let mut tx = db.begin().await.unwrap();
    pause::apply(&mut tx, id, &apply(3000, "每日额度"))
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(mailbox::update(&db, id, &fields, "secret").await.unwrap());
    assert_eq!(
        sqlx::query_as::<_, (Option<i64>, Option<String>)>(
            "SELECT paused_until, pause_reason FROM mailbox WHERE id = ?"
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap(),
        (None, None)
    );
    assert!(!mailbox::resume(&db, id + 1).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_request_failure_only_pauses_the_configuration_it_used(db: SqlitePool) {
    use mailbox::pause::{self, Pause};
    let original = form("me@qq.com").check().unwrap();
    let id = mailbox::create(&db, &original, "secret", 0).await.unwrap();
    let account = mailbox::account(&db, id).await.unwrap().unwrap();
    let pause = Pause {
        until: 2000,
        reason: "登录被拒绝".into(),
    };
    for field in 0..8 {
        let mut edited = form("me@qq.com");
        let mut secret = "secret";
        match field {
            0 => edited.address = "other@qq.com".into(),
            1 => edited.smtp.host = Cow::Borrowed("smtp.example.com"),
            2 => edited.smtp.port = 587,
            3 => edited.smtp.security = Security::Starttls,
            4 => edited.imap.host = Cow::Borrowed("imap.example.com"),
            5 => edited.imap.port = 143,
            6 => edited.imap.security = Security::Starttls,
            7 => secret = "new secret",
            _ => unreachable!(),
        }
        mailbox::update(&db, id, &edited.check().unwrap(), secret)
            .await
            .unwrap();
        let mut tx = db.begin().await.unwrap();
        pause::apply_for_account(&mut tx, id, &account, &pause)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        assert!(
            mailbox::list(&db, 0).await.unwrap()[0].pause.is_none(),
            "field {field}"
        );
    }
    mailbox::update(&db, id, &original, "secret").await.unwrap();
    let mut tx = db.begin().await.unwrap();
    pause::apply_for_account(&mut tx, id, &account, &pause)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert_eq!(
        mailbox::list(&db, 0).await.unwrap()[0]
            .pause
            .as_ref()
            .unwrap()
            .until,
        pause.until
    );
}
