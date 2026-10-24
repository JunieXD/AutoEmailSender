// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Sending approved emails through the fake SMTP server.

use std::borrow::Cow;
use std::cell::Cell;
use std::path::Path;

use base64::Engine;
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

/// Writes a first email to a new mentor at this address and approves it with these materials
/// attached; returns the mentor.
async fn approve(db: &SqlitePool, address: &str, attachments: &[i64], now: i64) -> i64 {
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
    email::save_first_draft(db, mentor, "申请硕士", "王老师：您好！", attachments, now)
        .await
        .unwrap();
    let approval = Approval {
        subject: "申请硕士",
        body_md: "王老师：您好！",
        html: "<p>王老师：您好！</p>",
        text: "王老师：您好！",
        attachments,
    };
    assert_eq!(
        email::approve_first(db, mentor, &approval, now)
            .await
            .unwrap(),
        email::Approved::Yes
    );
    mentor
}

/// Where materials are kept, for emails without attachments, which never look there.
fn nowhere() -> &'static Path {
    Path::new("nowhere")
}

/// A material with this file kept in `dir`.
async fn material(db: &SqlitePool, dir: &Path, file_name: &str, data: &[u8]) -> i64 {
    let id = sqlx::query_scalar(
        "INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
         VALUES (?, ?, 0, 0, 0, 0) RETURNING id",
    )
    .bind(file_name)
    .bind(data.len() as i64)
    .fetch_one(db)
    .await
    .unwrap();
    std::fs::write(material::file(dir, id), data).unwrap();
    id
}

async fn first_email(db: &SqlitePool, mentor: i64) -> email::Email {
    email::list(db, mentor).await.unwrap().remove(0)
}

/// The mailbox the mentor is contacted from.
async fn own_mailbox(db: &SqlitePool, mentor: i64) -> Option<i64> {
    sqlx::query_scalar("SELECT mailbox_id FROM mentor WHERE id = ?")
        .bind(mentor)
        .fetch_one(db)
        .await
        .unwrap()
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
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;

    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::Sent {
            from_address: ADDRESS.into(),
            sent_at: T
        }
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );
    let mail = inbox.lock().unwrap().clone();
    assert_eq!(mail.len(), 1);
    assert!(header(&mail[0], "To: ").ends_with(" <wang@example.edu.cn>"));
    assert!(header(&mail[0], "Content-Type: ").starts_with("multipart/alternative"));
    // Later emails to this mentor go from the same mailbox.
    assert_eq!(own_mailbox(&db, wang).await, Some(1));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mailbox_keeps_to_its_interval_and_daily_limit(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    for address in ["a@example.edu.cn", "b@example.edu.cn", "c@example.edu.cn"] {
        approve(&db, address, &[], T).await;
    }

    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + 1000).await.unwrap(),
        Next::At(T + MINUTE)
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE)
            .await
            .unwrap(),
        Next::Now
    );
    // Two in the last 24 hours: the third waits until the first is a day old.
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + 2 * MINUTE)
            .await
            .unwrap(),
        Next::At(T + DAY)
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Now
    );
    assert_eq!(inbox.lock().unwrap().len(), 3);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_email_fails_and_a_cut_off_one_may_have_gone_out(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let unknown = approve(&db, "unknown@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let refused = first_email(&db, unknown).await.delivery;
    assert!(
        matches!(refused, Delivery::Failed { ref error } if error.contains("550")),
        "{refused:?}"
    );
    // Never contacted, so a retry may still go from any mailbox.
    assert_eq!(own_mailbox(&db, unknown).await, None);

    let hangup = approve(&db, "hangup@example.edu.cn", &[], T + MINUTE).await;
    send::step(&db, &net, nowhere(), || T + MINUTE)
        .await
        .unwrap();
    let cut_off = first_email(&db, hangup).await;
    assert!(matches!(cut_off.delivery, Delivery::MaybeSent { .. }));
    // Not sent again unless the user says so.
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );

    assert!(email::retry(&db, cut_off.id, T + DAY).await.unwrap());
    send::step(&db, &net, nowhere(), || T + DAY).await.unwrap();
    let inbox = inbox.lock().unwrap();
    assert_eq!(inbox.len(), 2);
    // The same Message-ID, so that a mailbox that got the first copy can drop the second.
    assert_eq!(
        header(&inbox[0], "Message-ID: "),
        header(&inbox[1], "Message-ID: ")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_email_taken_back_and_changed_is_another_letter(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let hangup = approve(&db, "hangup@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let id = first_email(&db, hangup).await.id;
    assert!(email::retry(&db, id, T + 1).await.unwrap());
    assert!(email::withdraw(&db, id, T + 2).await.unwrap());
    let changed = Approval {
        subject: "申请硕士",
        body_md: "王老师：您好！补充一句。",
        html: "<p>王老师：您好！补充一句。</p>",
        text: "王老师：您好！补充一句。",
        attachments: &[],
    };
    assert_eq!(
        email::approve_first(&db, hangup, &changed, T + 3)
            .await
            .unwrap(),
        email::Approved::Yes
    );

    send::step(&db, &net, nowhere(), || T + 3).await.unwrap();
    let inbox = inbox.lock().unwrap();
    assert_eq!(inbox.len(), 2);
    // Not dropped as a duplicate of the first by the mentor's mailbox.
    assert_ne!(
        header(&inbox[0], "Message-ID: "),
        header(&inbox[1], "Message-ID: ")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_login_fails_the_email(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, "wrong").await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;

    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert!(
        matches!(first_email(&db, wang).await.delivery, Delivery::Failed { error } if error.contains("登录被拒绝"))
    );
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn emails_left_sending_may_have_gone_out(db: SqlitePool) {
    let (_, _) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    let id = first_email(&db, wang).await.id;
    let mut conn = db.acquire().await.unwrap();
    assert!(
        email::start_sending(&mut conn, id, 1, ADDRESS, "<x@example.com>", T)
            .await
            .unwrap()
            .is_some()
    );
    drop(conn);

    send::recover(&db, T + 1).await.unwrap();
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::MaybeSent {
            from_address: ADDRESS.into(),
            sent_at: T,
            error: email::INTERRUPTED.into()
        }
    );
    assert_eq!(own_mailbox(&db, wang).await, Some(1));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_email_left_sending_to_no_address_failed(db: SqlitePool) {
    let (_, _) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    sqlx::query("UPDATE mentor SET email = NULL WHERE id = ?")
        .bind(wang)
        .execute(&db)
        .await
        .unwrap();
    let id = first_email(&db, wang).await.id;
    let mut conn = db.acquire().await.unwrap();
    email::start_sending(&mut conn, id, 1, ADDRESS, "<x@example.com>", T)
        .await
        .unwrap()
        .unwrap();
    drop(conn);

    send::recover(&db, T + 1).await.unwrap();
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::Failed {
            error: email::NO_ADDRESS.into()
        }
    );
    assert_eq!(own_mailbox(&db, wang).await, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mentor_in_the_trash_gets_nothing_more(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let hangup = approve(&db, "hangup@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    mentor::trash::trash(&db, &[hangup, wang], T + 1)
        .await
        .unwrap();

    let cut_off = first_email(&db, hangup).await;
    assert!(!email::retry(&db, cut_off.id, T + 2).await.unwrap());
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_time_sent_is_when_the_server_took_it(db: SqlitePool) {
    let (net, _) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    // Each look at the clock finds a second gone by, as logging in and handing over take time.
    let clock = Cell::new(T);
    let now = || {
        clock.set(clock.get() + 1000);
        clock.get()
    };

    send::step(&db, &net, nowhere(), now).await.unwrap();
    assert_eq!(
        first_email(&db, wang).await.delivery,
        Delivery::Sent {
            from_address: ADDRESS.into(),
            sent_at: clock.get()
        }
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn attachments_are_read_from_their_files_as_the_email_goes(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let dir = tempfile::tempdir().unwrap();
    // Not text, as a PDF is not, so that it goes as base64.
    let pdf = b"%PDF-1.7\n\x80\x81\x82";
    let resume = material(&db, dir.path(), "简历.pdf", pdf).await;
    let wang = approve(&db, "wang@example.edu.cn", &[resume], T).await;

    send::step(&db, &net, dir.path(), || T).await.unwrap();
    assert!(matches!(
        first_email(&db, wang).await.delivery,
        Delivery::Sent { .. }
    ));
    let mail = inbox.lock().unwrap().remove(0);
    assert!(header(&mail, "Content-Type: ").starts_with("multipart/mixed"));
    assert!(mail.contains(&base64::engine::general_purpose::STANDARD.encode(pdf)));

    // Gone from the disk before the email went.
    let transcript = material(&db, dir.path(), "成绩单.pdf", pdf).await;
    let li = approve(&db, "li@example.edu.cn", &[transcript], T).await;
    std::fs::remove_file(material::file(dir.path(), transcript)).unwrap();
    send::step(&db, &net, dir.path(), || T + MINUTE)
        .await
        .unwrap();
    let missing = first_email(&db, li).await.delivery;
    assert!(
        matches!(missing, Delivery::Failed { ref error } if error.contains("成绩单.pdf")),
        "{missing:?}"
    );
    assert!(inbox.lock().unwrap().is_empty());
}
