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
async fn a_blank_secret_keeps_the_saved_one(db: SqlitePool) {
    let fields = form("me@qq.com").check().unwrap();
    let id = mailbox::create(&db, &fields, "first", 0).await.unwrap();
    let secret = async || mailbox::account(&db, id).await.unwrap().unwrap().secret;

    assert!(mailbox::update(&db, id, &fields, None).await.unwrap());
    assert_eq!(secret().await, "first");
    assert!(
        mailbox::update(&db, id, &fields, Some("second"))
            .await
            .unwrap()
    );
    assert_eq!(secret().await, "second");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_address_is_added_once_in_any_letter_case(db: SqlitePool) {
    let first = form("Me@qq.com").check().unwrap();
    mailbox::create(&db, &first, "s", 0).await.unwrap();
    let again = form("me@QQ.com").check().unwrap();
    let result = mailbox::create(&db, &again, "s", 0).await;
    assert!(matches!(result, Err(SaveError::AddressTaken)), "{result:?}");
}
