// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Sending approved emails through the fake SMTP server.

use std::borrow::Cow;

use email::{Approval, Delivery};
use fakemail::{ADDRESS, Inbox, SECRET};
use mailbox::{MailboxForm, Security, Server};
use mentor::MentorForm;
use net::Net;
use send::Next;
use sqlx::SqlitePool;

const T: i64 = 1_800_000_000_000;
const MINUTE: i64 = 60_000;
const DAY: i64 = 24 * 60 * MINUTE;

/// A mailbox on a fake server that sends at most two emails a day, a minute apart.
async fn mailbox(db: &SqlitePool, secret: &str) -> (Net, Inbox) {
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security: Security::Tls,
    };
    let form = MailboxForm {
        address: ADDRESS.into(),
        sender_name: "林晓".into(),
        provider: mailbox::providers::CUSTOM.into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 2,
        min_interval_s: 60,
    };
    mailbox::create(db, &form.check().unwrap(), secret, 0)
        .await
        .unwrap();
    (Net::new(vec![pki.root.clone()]).unwrap(), inbox)
}

/// Writes a first email to a new mentor at this address and approves it; returns the mentor.
async fn approve(db: &SqlitePool, address: &str, now: i64) -> i64 {
    let form = MentorForm {
        name: "王伟".into(),
        title: String::new(),
        email: address.into(),
        university: String::new(),
        school: String::new(),
        department: String::new(),
        homepage_url: String::new(),
        research: String::new(),
    };
    let mentor = mentor::create(db, &form.check().unwrap(), now)
        .await
        .unwrap();
    email::save_first_draft(db, mentor, "申请硕士", "王老师：您好！", now)
        .await
        .unwrap();
    let approval = Approval {
        subject: "申请硕士",
        body_md: "王老师：您好！",
        html: "<p>王老师：您好！</p>",
        text: "王老师：您好！",
    };
    assert!(
        email::approve_first(db, mentor, &approval, now)
            .await
            .unwrap()
    );
    mentor
}

async fn first_email(db: &SqlitePool, mentor: i64) -> email::Email {
    email::list(db, mentor).await.unwrap().remove(0)
}

fn header<'a>(mail: &'a str, name: &str) -> &'a str {
    let headers = mail.split("\r\n\r\n").next().unwrap();
    headers
        .lines()
        .find_map(|l| l.strip_prefix(name))
        .unwrap_or_default()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_approved_email_goes_out_once(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", T).await;

    assert_eq!(send::step(&db, &net, T).await.unwrap(), Next::Now);
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::Sent {
            from_address: ADDRESS.into(),
            sent_at: T
        }
    );
    assert_eq!(send::step(&db, &net, T + DAY).await.unwrap(), Next::Idle);
    let mail = inbox.lock().unwrap().clone();
    assert_eq!(mail.len(), 1);
    assert!(header(&mail[0], "To: ").ends_with(" <wang@example.edu.cn>"));
    assert!(header(&mail[0], "Content-Type: ").starts_with("multipart/alternative"));
    // Later emails to this mentor go from the same mailbox.
    let own: Option<i64> = sqlx::query_scalar("SELECT mailbox_id FROM mentor WHERE id = ?")
        .bind(wang)
        .fetch_one(&db)
        .await
        .unwrap();
    assert!(own.is_some());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mailbox_keeps_to_its_interval_and_daily_limit(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    for address in ["a@example.edu.cn", "b@example.edu.cn", "c@example.edu.cn"] {
        approve(&db, address, T).await;
    }

    assert_eq!(send::step(&db, &net, T).await.unwrap(), Next::Now);
    assert_eq!(
        send::step(&db, &net, T + 1000).await.unwrap(),
        Next::At(T + MINUTE)
    );
    assert_eq!(send::step(&db, &net, T + MINUTE).await.unwrap(), Next::Now);
    // Two in the last 24 hours: the third waits until the first is a day old.
    assert_eq!(
        send::step(&db, &net, T + 2 * MINUTE).await.unwrap(),
        Next::At(T + DAY)
    );
    assert_eq!(send::step(&db, &net, T + DAY).await.unwrap(), Next::Now);
    assert_eq!(inbox.lock().unwrap().len(), 3);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_email_fails_and_a_cut_off_one_may_have_gone_out(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let unknown = approve(&db, "unknown@example.edu.cn", T).await;
    send::step(&db, &net, T).await.unwrap();
    let refused = first_email(&db, unknown).await.delivery;
    assert!(
        matches!(refused, Delivery::Failed { ref error } if error.contains("550")),
        "{refused:?}"
    );

    let hangup = approve(&db, "hangup@example.edu.cn", T + MINUTE).await;
    send::step(&db, &net, T + MINUTE).await.unwrap();
    let cut_off = first_email(&db, hangup).await;
    assert!(matches!(cut_off.delivery, Delivery::MaybeSent { .. }));
    // Not sent again unless the user says so.
    assert_eq!(send::step(&db, &net, T + DAY).await.unwrap(), Next::Idle);

    assert!(email::retry(&db, cut_off.id, T + DAY).await.unwrap());
    send::step(&db, &net, T + DAY).await.unwrap();
    let inbox = inbox.lock().unwrap();
    assert_eq!(inbox.len(), 2);
    // The same Message-ID, so that a mailbox that got the first copy can drop the second.
    assert_eq!(
        header(&inbox[0], "Message-ID: "),
        header(&inbox[1], "Message-ID: ")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_login_fails_the_email(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, "wrong").await;
    let wang = approve(&db, "wang@example.edu.cn", T).await;

    assert_eq!(send::step(&db, &net, T).await.unwrap(), Next::Now);
    assert!(
        matches!(first_email(&db, wang).await.delivery, Delivery::Failed { error } if error.contains("登录被拒绝"))
    );
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn emails_left_sending_may_have_gone_out(db: SqlitePool) {
    let (_, _) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", T).await;
    let id = first_email(&db, wang).await.id;
    let mut conn = db.acquire().await.unwrap();
    assert!(
        email::start_sending(&mut conn, id, 1, ADDRESS, "<x@example.com>", T)
            .await
            .unwrap()
    );
    drop(conn);

    email::interrupted(&db, T + 1).await.unwrap();
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::MaybeSent {
            from_address: ADDRESS.into(),
            sent_at: T,
            error: email::INTERRUPTED.into()
        }
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mentor_in_the_trash_gets_nothing_more(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let hangup = approve(&db, "hangup@example.edu.cn", T).await;
    send::step(&db, &net, T).await.unwrap();
    let wang = approve(&db, "wang@example.edu.cn", T).await;
    mentor::trash::trash(&db, &[hangup, wang], T + 1)
        .await
        .unwrap();

    let cut_off = first_email(&db, hangup).await;
    assert!(!email::retry(&db, cut_off.id, T + 2).await.unwrap());
    assert_eq!(send::step(&db, &net, T + DAY).await.unwrap(), Next::Idle);
    assert_eq!(inbox.lock().unwrap().len(), 1);
}
