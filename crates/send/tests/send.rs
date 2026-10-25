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
use mentor::MentorFields;
use net::Net;
use send::Next;
use sqlx::SqlitePool;

const T: i64 = 1_800_000_000_000;
const MINUTE: i64 = 60_000;
const DAY: i64 = 24 * 60 * MINUTE;

/// A mailbox on a fake server that sends at most two emails a day, a minute apart.
async fn mailbox(db: &SqlitePool, secret: &str) -> (Net, Inbox) {
    mailbox_with_rcpt_hook(db, secret, || {}).await
}

async fn mailbox_with_rcpt_hook(
    db: &SqlitePool,
    secret: &str,
    on_rcpt: impl Fn() + Send + Sync + 'static,
) -> (Net, Inbox) {
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp_with_rcpt_hook(&pki, false, on_rcpt).await;
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
    let fields = MentorFields {
        name: "王伟".into(),
        emails: vec![address.into()],
        ..MentorFields::default()
    };
    let mentor = mentor::create(db, &fields, now).await.unwrap();
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
        email::approve_first(db, mentor, &approval, None, now)
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
            to_address: "wang@example.edu.cn".into(),
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
async fn mail_goes_to_the_first_address_when_handed_over(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    // Put first after the email was approved.
    let fields = MentorFields {
        name: "王伟".into(),
        emails: vec!["wangwei@gmail.com".into(), "wang@example.edu.cn".into()],
        ..MentorFields::default()
    };
    assert!(mentor::update(&db, wang, &fields, T).await.unwrap());

    send::step(&db, &net, nowhere(), || T).await.unwrap();
    assert!(matches!(
        first_email(&db, wang).await.delivery,
        Delivery::Sent { to_address, .. } if to_address == "wangwei@gmail.com"
    ));
    assert!(header(&inbox.lock().unwrap()[0], "To: ").ends_with(" <wangwei@gmail.com>"));
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
        email::approve_first(&db, hangup, &changed, None, T + 3)
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
            to_address: "wang@example.edu.cn".into(),
            sent_at: T,
            error: email::INTERRUPTED.into(),
            cancel_reason: None,
        }
    );
    assert_eq!(own_mailbox(&db, wang).await, Some(1));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_email_left_sending_to_no_address_failed(db: SqlitePool) {
    let (_, _) = mailbox(&db, SECRET).await;
    let wang = approve(&db, "wang@example.edu.cn", &[], T).await;
    sqlx::query("DELETE FROM mentor_email WHERE mentor_id = ?")
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
    // A late bounce naming no one, of a try before the address was taken out, leaves it be.
    assert!(
        !email::bounced(&mut conn, "<x@example.com>", &[], "邮件被退回", T)
            .await
            .unwrap()
    );
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
            to_address: "wang@example.edu.cn".into(),
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_bounced_email_goes_again_once_as_another_letter(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let dir = tempfile::tempdir().unwrap();
    let resume = material(&db, dir.path(), "简历.pdf", b"%PDF-1.7").await;
    let wang = approve(&db, "wang@example.edu.cn", &[resume], T).await;
    send::step(&db, &net, dir.path(), || T).await.unwrap();
    let bounced = first_email(&db, wang).await.id;
    sqlx::query("UPDATE email SET state = 'bounced', error = '收件地址不存在' WHERE id = ?")
        .bind(bounced)
        .execute(&db)
        .await
        .unwrap();
    let fields = MentorFields {
        name: "王伟".into(),
        emails: vec!["wangwei@gmail.com".into()],
        ..MentorFields::default()
    };
    assert!(mentor::update(&db, wang, &fields, T).await.unwrap());

    // A second click sends nothing more.
    for outcome in [email::Approved::Yes, email::Approved::Refused] {
        assert_eq!(email::resend(&db, bounced, T).await.unwrap(), outcome);
    }
    let again = email::list(&db, wang).await.unwrap()[1].id;
    assert!(!email::withdraw(&db, again, T).await.unwrap());
    send::step(&db, &net, dir.path(), || T + 10 * MINUTE)
        .await
        .unwrap();
    let emails = email::list(&db, wang).await.unwrap();
    assert!(matches!(
        &emails[0].delivery,
        Delivery::Bounced { to_address, .. } if to_address == "wang@example.edu.cn"
    ));
    assert!(matches!(
        &emails[1].delivery,
        Delivery::Sent { to_address, .. } if to_address == "wangwei@gmail.com"
    ));
    assert!(emails[0].resent && !emails[1].resent);
    assert_eq!(emails[1].attachments[0].file_name, "简历.pdf");
    let mails = inbox.lock().unwrap().clone();
    // Under a Message-ID of its own, which no mailbox drops as a repeat of the first.
    assert_ne!(
        header(&mails[0], "Message-ID: "),
        header(&mails[1], "Message-ID: ")
    );

    // With a file deleted since, it cannot go as it went.
    sqlx::query("DELETE FROM material WHERE id = ?")
        .bind(resume)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE email SET state = 'bounced', error = '收件地址不存在' WHERE id = ?")
        .bind(emails[1].id)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::resend(&db, emails[1].id, T).await.unwrap(),
        email::Approved::MaterialGone
    );
    assert_eq!(email::list(&db, wang).await.unwrap().len(), 2);
}

async fn approve_reply(db: &SqlitePool) -> i64 {
    let mentor = mentor::create(
        db,
        &MentorFields {
            name: "王伟".into(),
            emails: vec!["wang@example.edu.cn".into()],
            ..Default::default()
        },
        T,
    )
    .await
    .unwrap();
    sqlx::query("INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id, sent_at,
      from_address, subject, refs, body_text, truncated)
      VALUES (1, 'INBOX', 1, 1, 'reply', '<parent@example.edu>', 1, 'wang@example.edu.cn', 'RE: 申请',
        '<root@example.edu> <previous@example.edu>', '欢迎', 0)").execute(db).await.unwrap();
    sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, 1)")
        .bind(mentor)
        .execute(db)
        .await
        .unwrap();
    let draft =
        email::save_reply_draft(db, mentor, "<parent@example.edu>", "王老师：谢谢。", &[], T)
            .await
            .unwrap()
            .unwrap();
    let approval = Approval {
        subject: "",
        body_md: "王老师：谢谢。",
        html: "<p>王老师：谢谢。</p>",
        text: "王老师：谢谢。",
        attachments: &[],
    };
    assert_eq!(
        email::approve_reply(db, mentor, draft.id, "<parent@example.edu>", &approval, T)
            .await
            .unwrap(),
        email::Approved::Yes
    );
    mentor
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_reply_goes_out_in_its_parent_thread_and_assigns_the_receiving_mailbox(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve_reply(&db).await;
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert_eq!(own_mailbox(&db, mentor).await, Some(1));
    let mail = inbox.lock().unwrap().clone();
    assert_eq!(mail.len(), 1);
    assert_eq!(header(&mail[0], "In-Reply-To: "), "<parent@example.edu>");
    let headers = mail[0]
        .split("\r\n\r\n")
        .next()
        .unwrap()
        .replace("\r\n ", " ");
    assert!(
        headers
            .contains("References: <root@example.edu> <previous@example.edu> <parent@example.edu>")
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_reply_never_rotates_to_an_unrelated_mailbox_after_its_sender_is_deleted(db: SqlitePool) {
    let (net, original_inbox) = mailbox(&db, SECRET).await;
    let mentor = approve_reply(&db).await;
    sqlx::query("DELETE FROM mailbox")
        .execute(&db)
        .await
        .unwrap();
    let (replacement_net, other_inbox) = mailbox(&db, SECRET).await;
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert!(matches!(first_email(&db, mentor).await.delivery,
        Delivery::Failed { error } if error.contains("没有能发信的邮箱")));
    assert!(original_inbox.lock().unwrap().is_empty());
    assert!(other_inbox.lock().unwrap().is_empty());
    sqlx::query("UPDATE mentor SET mailbox_id = (SELECT max(id) FROM mailbox) WHERE id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    let failed = first_email(&db, mentor).await.id;
    assert!(email::retry(&db, failed, T).await.unwrap());
    assert_eq!(
        send::step(&db, &replacement_net, nowhere(), || T)
            .await
            .unwrap(),
        Next::Now
    );
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::Sent { .. }
    ));
    assert_eq!(other_inbox.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn bounced_reply_uses_a_replacement_responsible_mailbox(db: SqlitePool) {
    let (net, original) = mailbox(&db, SECRET).await;
    let mentor = approve_reply(&db).await;
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    let bounced = first_email(&db, mentor).await.id;
    sqlx::query("UPDATE email SET state = 'bounced', error = '收件地址不存在' WHERE id = ?")
        .bind(bounced)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("DELETE FROM mailbox")
        .execute(&db)
        .await
        .unwrap();
    let (net, replacement) = mailbox(&db, SECRET).await;
    sqlx::query("UPDATE mentor SET mailbox_id = (SELECT max(id) FROM mailbox) WHERE id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::resend(&db, bounced, T + MINUTE).await.unwrap(),
        email::Approved::Yes
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE)
            .await
            .unwrap(),
        Next::Now
    );
    let emails = email::list(&db, mentor).await.unwrap();
    let resent = emails
        .iter()
        .find(|e| e.resend_of == Some(bounced))
        .unwrap();
    assert!(
        matches!(resent.delivery, Delivery::Sent { .. }),
        "{:?}",
        resent.delivery
    );
    assert_eq!(original.lock().unwrap().len(), 1);
    let received = replacement.lock().unwrap();
    assert_eq!(received.len(), 1);
    assert_eq!(
        header(&received[0], "In-Reply-To: "),
        "<parent@example.edu>"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_existing_reply_resend_can_retry_after_its_mailbox_is_replaced(db: SqlitePool) {
    let (net, _) = mailbox(&db, SECRET).await;
    let mentor = approve_reply(&db).await;
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    let bounced = first_email(&db, mentor).await.id;
    sqlx::query("UPDATE email SET state = 'bounced', error = '收件地址不存在' WHERE id = ?")
        .bind(bounced)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        email::resend(&db, bounced, T + MINUTE).await.unwrap(),
        email::Approved::Yes
    );
    sqlx::query("DELETE FROM mailbox")
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE)
            .await
            .unwrap(),
        Next::Now
    );
    let emails = email::list(&db, mentor).await.unwrap();
    let resent = emails
        .iter()
        .find(|e| e.resend_of == Some(bounced))
        .unwrap();
    assert!(
        matches!(&resent.delivery, Delivery::Failed { error } if error.contains("没有能发信的邮箱"))
    );
    assert!(!email::withdraw(&db, resent.id, T + MINUTE).await.unwrap());
    let (net, replacement) = mailbox(&db, SECRET).await;
    sqlx::query("UPDATE mentor SET mailbox_id = (SELECT max(id) FROM mailbox) WHERE id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert!(email::retry(&db, resent.id, T + MINUTE).await.unwrap());
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE)
            .await
            .unwrap(),
        Next::Now
    );
    assert_eq!(replacement.lock().unwrap().len(), 1);
    assert!(matches!(
        email::list(&db, mentor)
            .await
            .unwrap()
            .iter()
            .find(|e| e.id == resent.id)
            .unwrap()
            .delivery,
        Delivery::Sent { .. }
    ));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_reply_uses_the_responsible_mailbox_selected_after_approval(db: SqlitePool) {
    let (_, receiving) = mailbox(&db, SECRET).await;
    let mentor = approve_reply(&db).await;
    sqlx::query("UPDATE mailbox SET address = 'old@example.com' WHERE id = 1")
        .execute(&db)
        .await
        .unwrap();
    let (net, responsible) = mailbox(&db, SECRET).await;
    sqlx::query("UPDATE mentor SET mailbox_id = (SELECT max(id) FROM mailbox) WHERE id = ?")
        .bind(mentor)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::Now
    );
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::Sent { .. }
    ));
    assert!(receiving.lock().unwrap().is_empty());
    assert_eq!(responsible.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn scheduled_mail_obeys_time_and_deadline_and_immediate_send_is_once(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "scheduled@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T + MINUTE, T)
        .await
        .unwrap();
    assert_eq!(
        send::step(&db, &net, nowhere(), || T).await.unwrap(),
        Next::At(T + MINUTE)
    );
    assert!(inbox.lock().unwrap().is_empty());
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE)
            .await
            .unwrap(),
        Next::Now
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + 2 * MINUTE)
            .await
            .unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
    let mentor = approve(&db, "missed@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T + MINUTE, T)
        .await
        .unwrap();
    let later = T + MINUTE + 3_600_000;
    assert_eq!(
        send::step(&db, &net, nowhere(), || later).await.unwrap(),
        Next::Idle
    );
    let pending = send::plan::list(
        &db,
        &send::plan::PlanQuery {
            column: send::plan::PlanColumn::Pending,
            page: std::num::NonZeroU32::MIN,
        },
        later,
    )
    .await
    .unwrap();
    assert_eq!(pending.emails[0].id, id);
    assert_eq!(inbox.lock().unwrap().len(), 1);
    email::schedule::send_now(&db, id, later).await.unwrap();
    assert_eq!(
        send::step(&db, &net, nowhere(), || later).await.unwrap(),
        Next::Now
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || later).await.unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn crossing_the_deadline_during_login_cannot_hand_the_letter_over(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let m = approve(&db, "late@example.edu.cn", &[], T).await;
    let id = first_email(&db, m).await.id;
    email::schedule::reschedule(&db, id, T + MINUTE, T)
        .await
        .unwrap();
    let calls = Cell::new(0);
    let now = || {
        let n = calls.get();
        calls.set(n + 1);
        if n == 0 {
            T + MINUTE
        } else {
            T + MINUTE + 3_600_000
        }
    };
    send::step(&db, &net, nowhere(), now).await.unwrap();
    assert!(inbox.lock().unwrap().is_empty());
    assert!(matches!(
        first_email(&db, m).await.delivery,
        Delivery::Approved
    ));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn expired_task_mail_rolls_only_into_selected_capacity_and_manual_dates_stay_pending(
    db: SqlitePool,
) {
    use chrono::{TimeZone, Utc};
    let (net, inbox) = mailbox(&db, SECRET).await;
    let base = T / DAY * DAY;
    let dates = [base, base + DAY].map(|at| {
        Utc.timestamp_millis_opt(at)
            .unwrap()
            .format("%Y-%m-%d")
            .to_string()
    });
    let task = sqlx::query(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
             window_end, per_window, state, created_at)
           VALUES ('定时', 'first', 'ai', 'window', ?, '09:00', '11:00', 1, 'paused', ?)"#,
    )
    .bind(sqlx::types::Json(dates))
    .bind(T)
    .execute(&db)
    .await
    .unwrap()
    .last_insert_rowid();
    let mentor = approve(&db, "task@example.edu.cn", &[], base).await;
    let id = first_email(&db, mentor).await.id;
    sqlx::query(
        r#"UPDATE email SET task_id = ?, planned_at = ?, latest_at = ?
           WHERE id = ?"#,
    )
    .bind(task)
    .bind(base + 9 * 3_600_000)
    .bind(base + 11 * 3_600_000)
    .bind(id)
    .execute(&db)
    .await
    .unwrap();
    let next = base + DAY + 9 * 3_600_000;
    send::step_in_zone(
        &db,
        &net,
        nowhere(),
        || base + 12 * 3_600_000,
        &Utc,
        &mut |_| 0,
    )
    .await
    .unwrap();
    assert_eq!(first_email(&db, mentor).await.planned_at, Some(next));
    send::step_in_zone(&db, &net, nowhere(), || next, &Utc, &mut |_| 0)
        .await
        .unwrap();
    assert!(inbox.lock().unwrap().is_empty());
    let expired = base + DAY + 11 * 3_600_000;
    send::step_in_zone(&db, &net, nowhere(), || expired, &Utc, &mut |_| 0)
        .await
        .unwrap();
    assert_eq!(first_email(&db, mentor).await.planned_at, Some(next));
    let pending = send::plan::list(
        &db,
        &send::plan::PlanQuery {
            column: send::plan::PlanColumn::Pending,
            page: std::num::NonZeroU32::MIN,
        },
        expired,
    )
    .await
    .unwrap();
    assert_eq!(pending.total, 1);
    // A manual date's one-hour deadline does not silently return to task windows.
    email::schedule::reschedule(&db, id, base + 12 * 3_600_000, base)
        .await
        .unwrap();
    send::step_in_zone(
        &db,
        &net,
        nowhere(),
        || base + 14 * 3_600_000,
        &Utc,
        &mut |_| 0,
    )
    .await
    .unwrap();
    assert_eq!(
        first_email(&db, mentor).await.planned_at,
        Some(base + 12 * 3_600_000)
    );
    assert!(inbox.lock().unwrap().is_empty());
    sqlx::query(
        r#"UPDATE task SET state = 'active'
           WHERE id = ?"#,
    )
    .bind(task)
    .execute(&db)
    .await
    .unwrap();
    email::schedule::send_now(&db, id, expired).await.unwrap();
    assert_eq!(
        send::step_in_zone(&db, &net, nowhere(), || expired, &Utc, &mut |_| 0)
            .await
            .unwrap(),
        Next::Now
    );
    assert_eq!(
        send::step_in_zone(&db, &net, nowhere(), || expired, &Utc, &mut |_| 0)
            .await
            .unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn plan_columns_explain_waiting_and_history_is_limited_and_paged(db: SqlitePool) {
    use send::plan::{PlanColumn as C, PlanQuery, Waiting};
    let (_net, _inbox) = mailbox(&db, SECRET).await;
    let mut ids = vec![];
    for name in ["time", "limit", "paused", "missed", "failed", "unknown"] {
        let m = approve(&db, &format!("{name}@example.edu.cn"), &[], T).await;
        ids.push(first_email(&db, m).await.id);
    }
    email::schedule::reschedule(&db, ids[0], T + MINUTE, T)
        .await
        .unwrap();
    email::schedule::reschedule(&db, ids[3], T - 2 * 3_600_000, T - 3 * 3_600_000)
        .await
        .unwrap();
    email::failed(&mut db.acquire().await.unwrap(), ids[4], "拒收", T)
        .await
        .unwrap();
    sqlx::query(
        r#"UPDATE email SET state = 'maybe_sent', mailbox_id = 1, from_address =
             'me@example.com', to_address = 'unknown@example.edu.cn', message_id =
             '<unknown@example.com>', sent_at = ?, error = '连接中断'
           WHERE id = ?"#,
    )
    .bind(T)
    .bind(ids[5])
    .execute(&db)
    .await
    .unwrap();
    let task = sqlx::query(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
           VALUES ('暂停任务', 'first', 'ai', 'now', 'paused', ?)"#,
    )
    .bind(T)
    .execute(&db)
    .await
    .unwrap()
    .last_insert_rowid();
    sqlx::query(
        r#"UPDATE email SET task_id = ?
           WHERE id = ?"#,
    )
    .bind(task)
    .bind(ids[2])
    .execute(&db)
    .await
    .unwrap();
    sqlx::query(r#"UPDATE mailbox SET daily_limit = 1"#)
        .execute(&db)
        .await
        .unwrap();
    let q = |column, page| PlanQuery {
        column,
        page: std::num::NonZeroU32::new(page).unwrap(),
    };
    let upcoming = send::plan::list(&db, &q(C::Upcoming, 1), T).await.unwrap();
    assert_eq!(upcoming.total, 3);
    assert!(matches!(
        upcoming
            .emails
            .iter()
            .find(|e| e.id == ids[0])
            .unwrap()
            .waiting,
        Some(Waiting::Time)
    ));
    assert!(matches!(
        upcoming
            .emails
            .iter()
            .find(|e| e.id == ids[1])
            .unwrap()
            .waiting,
        Some(Waiting::MailboxLimit)
    ));
    assert!(matches!(
        upcoming
            .emails
            .iter()
            .find(|e| e.id == ids[2])
            .unwrap()
            .waiting,
        Some(Waiting::TaskPaused)
    ));
    sqlx::query(r#"UPDATE mailbox SET paused_until = ?, pause_reason = '服务商限流'"#)
        .bind(T + MINUTE)
        .execute(&db)
        .await
        .unwrap();
    let paused = send::plan::list(&db, &q(C::Upcoming, 1), T).await.unwrap();
    assert!(matches!(
        paused
            .emails
            .iter()
            .find(|e| e.id == ids[1])
            .unwrap()
            .waiting,
        Some(Waiting::MailboxPaused)
    ));
    let pending = send::plan::list(&db, &q(C::Pending, 1), T).await.unwrap();
    assert_eq!(pending.total, 3);
    assert_eq!(
        pending
            .emails
            .iter()
            .map(|e| e.id)
            .collect::<std::collections::HashSet<_>>(),
        ids[3..].iter().copied().collect()
    );
    assert!(
        send::plan::list(&db, &q(C::History, 1), T)
            .await
            .unwrap()
            .emails
            .is_empty()
    );
    for n in 0..110 {
        sqlx::query(
            r#"INSERT INTO email (mentor_id, kind, state, subject, body_md, html, text, planned_at,
                 mailbox_id, from_address, to_address, message_id, sent_at, created_at, updated_at)
                 SELECT mentor_id, 'first', 'sent', '历史', '正文', '<p>正文</p>', '正文', ?, 1,
                 'me@example.com', 'mentor@example.edu.cn', ?, ?, ?, ?
               FROM email
               WHERE id = ?"#,
        )
        .bind(T)
        .bind(format!("<history{n}@example.com>"))
        .bind(T + n)
        .bind(T)
        .bind(T)
        .bind(ids[0])
        .execute(&db)
        .await
        .unwrap();
    }
    let history = send::plan::list(&db, &q(C::History, 1), T).await.unwrap();
    assert_eq!(history.total, 100);
    assert_eq!(history.emails.len(), 50);
    assert_eq!(history.emails[0].sent_at, Some(T + 109));
    assert_eq!(
        send::plan::list(&db, &q(C::History, 2), T)
            .await
            .unwrap()
            .emails
            .len(),
        50
    );
    assert!(
        send::plan::list(&db, &q(C::History, 3), T)
            .await
            .unwrap()
            .emails
            .is_empty()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deadline_crossed_while_acquiring_write_lock_must_not_send(db: SqlitePool) {
    use sqlx::Connection;
    use std::sync::{
        Arc,
        atomic::{AtomicI64, Ordering},
        mpsc,
    };
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "lock@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    let clock = Arc::new(AtomicI64::new(T));
    let (request, requested) = mpsc::channel();
    let (released, release) = mpsc::channel();
    let options = db
        .connect_options()
        .as_ref()
        .clone()
        .busy_timeout(std::time::Duration::ZERO);
    let advancing = clock.clone();
    let writer = std::thread::spawn(move || {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let mut conn = sqlx::SqliteConnection::connect_with(&options)
                    .await
                    .unwrap();
                requested.recv().unwrap();
                let lock = conn.begin_with("BEGIN IMMEDIATE").await;
                advancing.store(T + 3_600_000, Ordering::SeqCst);
                match lock {
                    Ok(tx) => {
                        tx.commit().await.unwrap();
                        released.send(true).unwrap();
                    }
                    Err(e) => {
                        // A clock read made under the sender's write lock sees the new time directly.
                        assert_eq!(e.as_database_error().unwrap().code().as_deref(), Some("5"));
                        released.send(false).unwrap();
                    }
                }
            });
    });
    let calls = Cell::new(0);
    let now = || {
        let captured = clock.load(Ordering::SeqCst);
        calls.set(calls.get() + 1);
        if calls.get() == 2 {
            request.send(()).unwrap();
            if release.recv().unwrap() {
                request.send(()).unwrap();
                return captured;
            }
        }
        clock.load(Ordering::SeqCst)
    };
    send::step(&db, &net, nowhere(), now).await.unwrap();
    writer.join().unwrap();
    assert_eq!(inbox.lock().unwrap().len(), 0);
    let email = first_email(&db, mentor).await;
    assert_eq!(email.delivery, Delivery::Approved);
    assert_eq!(email.latest_at, Some(T + 3_600_000));
}

#[cfg(unix)]
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deadline_crossed_while_reading_attachment_must_not_send(db: SqlitePool) {
    use std::io::Write;
    use std::sync::{
        Arc,
        atomic::{AtomicI64, Ordering},
    };
    let (net, inbox) = mailbox(&db, SECRET).await;
    let dir = tempfile::tempdir().unwrap();
    let attachment = material(&db, dir.path(), "简历.pdf", b"resume").await;
    let mentor = approve(&db, "disk@example.edu.cn", &[attachment], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    let path = material::file(dir.path(), attachment);
    std::fs::remove_file(&path).unwrap();
    assert!(
        std::process::Command::new("mkfifo")
            .arg(&path)
            .status()
            .unwrap()
            .success()
    );
    let clock = Arc::new(AtomicI64::new(T));
    let advancing = clock.clone();
    let writer = std::thread::spawn(move || {
        // Opening the writer waits for the sender to start reading, without a timed sleep.
        let mut file = std::fs::OpenOptions::new().write(true).open(path).unwrap();
        advancing.store(T + 3_600_000, Ordering::SeqCst);
        file.write_all(b"resume").unwrap();
    });
    send::step(&db, &net, dir.path(), || clock.load(Ordering::SeqCst))
        .await
        .unwrap();
    writer.join().unwrap();
    assert_eq!(inbox.lock().unwrap().len(), 0);
    let email = first_email(&db, mentor).await;
    assert_eq!(email.delivery, Delivery::Approved);
    assert_eq!(email.latest_at, Some(T + 3_600_000));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn expiry_after_durable_claim_restores_the_previous_attempt(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "hangup@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let id = first_email(&db, mentor).await.id;
    email::retry(&db, id, T + MINUTE).await.unwrap();
    email::schedule::reschedule(&db, id, T + MINUTE, T + MINUTE)
        .await
        .unwrap();
    let evidence = async || {
        sqlx::query_as::<
            _,
            (
                Option<i64>,
                Option<String>,
                Option<String>,
                Option<String>,
                Option<i64>,
                i64,
            ),
        >(
            r#"SELECT mailbox_id, from_address, to_address, message_id, sent_at, updated_at
               FROM email
               WHERE id = ?"#,
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap()
    };
    let before = evidence().await;
    let calls = Cell::new(0);
    send::step(&db, &net, nowhere(), || {
        calls.set(calls.get() + 1);
        if calls.get() < 4 {
            T + MINUTE
        } else {
            T + MINUTE + 3_600_000
        }
    })
    .await
    .unwrap();
    assert_eq!(inbox.lock().unwrap().len(), 1);
    assert_eq!(evidence().await, before);
    let email = first_email(&db, mentor).await;
    assert_eq!(email.delivery, Delivery::Approved);
    assert_eq!(email.latest_at, Some(T + MINUTE + 3_600_000));
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + MINUTE + 3_600_000)
            .await
            .unwrap(),
        Next::Idle
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_deadline_crossed_during_envelope_must_not_upload(db: SqlitePool) {
    use std::sync::{
        Arc,
        atomic::{AtomicI64, Ordering},
    };
    let clock = Arc::new(AtomicI64::new(T));
    let advancing = clock.clone();
    let (net, inbox) = mailbox_with_rcpt_hook(&db, SECRET, move || {
        advancing.store(T + 3_600_000, Ordering::SeqCst);
    })
    .await;
    let mentor = approve(&db, "envelope@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    send::step(&db, &net, nowhere(), || clock.load(Ordering::SeqCst))
        .await
        .unwrap();
    assert!(inbox.lock().unwrap().is_empty());
    let email = first_email(&db, mentor).await;
    assert_eq!(email.delivery, Delivery::Approved);
    assert_eq!(email.latest_at, Some(T + 3_600_000));
    assert_eq!(
        sqlx::query_as::<
            _,
            (
                Option<i64>,
                Option<String>,
                Option<String>,
                Option<String>,
                Option<i64>
            ),
        >(
            r#"SELECT mailbox_id, from_address, to_address, message_id, sent_at
               FROM email
               WHERE id = ?"#
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap(),
        (None, None, None, None, None)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deadline_crossed_after_body_upload_keeps_the_uncertain_attempt(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "hangup@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    let calls = Cell::new(0);
    send::step(&db, &net, nowhere(), || {
        calls.set(calls.get() + 1);
        if calls.get() <= 4 { T } else { T + 3_600_000 }
    })
    .await
    .unwrap();
    assert_eq!(inbox.lock().unwrap().len(), 1);
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::MaybeSent { .. }
    ));
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}

/// Changes business state after the claim commits, with no timed sleeps.
async fn change_during_commit<F: Future<Output = ()>>(
    db: &SqlitePool,
    net: &Net,
    id: i64,
    at: i64,
    after: i64,
    change: impl FnOnce(SqlitePool) -> F + Send + 'static,
) {
    let options = db.connect_options().as_ref().clone();
    let (request, requested) = std::sync::mpsc::channel();
    let (finish, finished) = std::sync::mpsc::channel();
    let writer = std::thread::spawn(move || {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let db = sqlx::sqlite::SqlitePoolOptions::new()
                    .max_connections(1)
                    .connect_with(options)
                    .await
                    .unwrap();
                requested.recv().unwrap();
                assert_eq!(
                    sqlx::query_scalar::<_, String>(
                        r#"SELECT state
                           FROM email
                           WHERE id = ?"#
                    )
                    .bind(id)
                    .fetch_one(&db)
                    .await
                    .unwrap(),
                    "sending"
                );
                change(db.clone()).await;
                db.close().await;
                finish.send(()).unwrap();
            });
    });
    let calls = Cell::new(0);
    let result = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        send::step_in_zone(
            db,
            net,
            nowhere(),
            || {
                calls.set(calls.get() + 1);
                if calls.get() == 4 {
                    request.send(()).unwrap();
                    finished.recv().unwrap();
                }
                if calls.get() < 4 { at } else { after }
            },
            &chrono::Utc,
            &mut |_| 0,
        ),
    )
    .await;
    writer.join().unwrap();
    result.expect("sending must finish").unwrap();
}

async fn scheduled_task(db: &SqlitePool, email: i64) -> (i64, i64) {
    use chrono::{TimeZone, Utc};
    let base = T / DAY * DAY;
    let dates = [base, base + DAY].map(|at| {
        Utc.timestamp_millis_opt(at)
            .unwrap()
            .format("%Y-%m-%d")
            .to_string()
    });
    let task = sqlx::query(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
             window_end, per_window, state, created_at)
           VALUES ('定时', 'first', 'ai', 'window', ?, '09:00', '11:00', 1, 'active', ?)"#,
    )
    .bind(sqlx::types::Json(dates))
    .bind(T)
    .execute(db)
    .await
    .unwrap()
    .last_insert_rowid();
    sqlx::query(
        r#"UPDATE email SET task_id = ?, planned_at = ?, latest_at = ?
           WHERE id = ?"#,
    )
    .bind(task)
    .bind(base + 9 * 3_600_000)
    .bind(base + 11 * 3_600_000)
    .bind(email)
    .execute(db)
    .await
    .unwrap();
    (task, base + 10 * 3_600_000)
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_expiry_restore_must_respect_trashing_during_commit(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "trash@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    let (_, at) = scheduled_task(&db, id).await;
    change_during_commit(&db, &net, id, at, at + 3_600_000, move |db| async move {
        mentor::trash::trash(&db, &[mentor], at).await.unwrap();
    })
    .await;
    let canceled = sqlx::query_as::<_, (String, Option<String>, Option<i64>, Option<i64>)>(
        r#"SELECT state, cancel_reason, planned_at, latest_at
           FROM email
           WHERE id = ?"#,
    )
    .bind(id)
    .fetch_one(&db)
    .await
    .unwrap();
    assert_eq!(
        canceled,
        (
            "canceled".into(),
            Some(mentor::trash::CANCELED.into()),
            None,
            None
        )
    );
    send::step_in_zone(&db, &net, nowhere(), || at + DAY, &chrono::Utc, &mut |_| 0)
        .await
        .unwrap();
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review3_trash_then_restore_must_not_revive_canceled_contact(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "restored@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    let (_, at) = scheduled_task(&db, id).await;
    change_during_commit(&db, &net, id, at, at + 3_600_000, move |db| async move {
        mentor::trash::trash(&db, &[mentor], at).await.unwrap();
        mentor::trash::restore(&db, &[mentor], at).await.unwrap();
    })
    .await;
    assert!(inbox.lock().unwrap().is_empty());
    let restored = sqlx::query_as::<_, (String, Option<String>, Option<i64>, Option<i64>)>(
        r#"SELECT state, cancel_reason, planned_at, latest_at
           FROM email
           WHERE id = ?"#,
    )
    .bind(id)
    .fetch_one(&db)
    .await
    .unwrap();
    send::step_in_zone(&db, &net, nowhere(), || at + DAY, &chrono::Utc, &mut |_| 0)
        .await
        .unwrap();
    assert_eq!(
        (restored, inbox.lock().unwrap().len()),
        (
            (
                "canceled".into(),
                Some(mentor::trash::CANCELED.into()),
                None,
                None
            ),
            0
        )
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_during_handover_keeps_success_and_uncertainty_but_cancels_rejection(
    db: SqlitePool,
) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    for (offset, address, expected) in [
        (0, "sent@example.edu.cn", "sent"),
        (DAY, "hangup@example.edu.cn", "maybe_sent"),
        (2 * DAY, "unknown@example.edu.cn", "canceled"),
    ] {
        let at = T + offset;
        let mentor = approve(&db, address, &[], at).await;
        let id = first_email(&db, mentor).await.id;
        email::schedule::reschedule(&db, id, at, at).await.unwrap();
        change_during_commit(&db, &net, id, at, at, move |db| async move {
            mentor::trash::trash(&db, &[mentor], at).await.unwrap();
            assert_eq!(
                sqlx::query_as::<_, (String, Option<String>)>(
                    r#"SELECT state, cancel_reason
                       FROM email
                       WHERE id = ?"#
                )
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap(),
                ("sending".into(), Some(mentor::trash::CANCELED.into()))
            );
            mentor::trash::restore(&db, &[mentor], at).await.unwrap();
        })
        .await;
        let (state, error, cancel_reason) =
            sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
                r#"SELECT state, error, cancel_reason
               FROM email
               WHERE id = ?"#,
            )
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap();
        assert_eq!(state, expected);
        assert_eq!(
            cancel_reason.as_deref(),
            (expected != "sent").then_some(mentor::trash::CANCELED)
        );
        if expected == "maybe_sent" {
            assert!(error.is_some());
            assert_ne!(error.as_deref(), Some(mentor::trash::CANCELED));
        } else {
            assert_eq!(error, None);
        }
    }
    assert_eq!(inbox.lock().unwrap().len(), 2);
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + 3 * DAY)
            .await
            .unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_expiry_restore_after_mailbox_deleted_must_finish(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "hangup@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let id = first_email(&db, mentor).await.id;
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::MaybeSent { .. }
    ));
    email::retry(&db, id, T + MINUTE).await.unwrap();
    email::schedule::reschedule(&db, id, T + MINUTE, T + MINUTE)
        .await
        .unwrap();
    let evidence = async || {
        sqlx::query_as::<
            _,
            (
                Option<String>,
                Option<String>,
                Option<String>,
                Option<i64>,
                i64,
            ),
        >(
            r#"SELECT from_address, to_address, message_id, sent_at, updated_at
               FROM email
               WHERE id = ?"#,
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap()
    };
    let before = evidence().await;
    change_during_commit(
        &db,
        &net,
        id,
        T + MINUTE,
        T + MINUTE + 3_600_000,
        |db| async move {
            mailbox::delete(&db, 1).await.unwrap();
        },
    )
    .await;
    assert_eq!(first_email(&db, mentor).await.delivery, Delivery::Approved);
    assert_eq!(evidence().await, before);
    assert_eq!(
        sqlx::query_scalar::<_, Option<i64>>(
            r#"SELECT mailbox_id
               FROM email
               WHERE id = ?"#
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap(),
        None
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
    // The expired retry stays pending while the same sender can process another email.
    let (net, next_inbox) = mailbox(&db, SECRET).await;
    let next = approve(&db, "next@example.edu.cn", &[], T + DAY).await;
    send::step(&db, &net, nowhere(), || T + DAY).await.unwrap();
    assert!(
        matches!(first_email(&db, next).await.delivery, Delivery::Sent { sent_at, .. } if sent_at == T + DAY)
    );
    assert_eq!(next_inbox.lock().unwrap().len(), 1);
    assert_eq!(first_email(&db, mentor).await.delivery, Delivery::Approved);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn plan_returns_resend_origin_for_approved_and_failed_resends(db: SqlitePool) {
    use send::plan::{PlanColumn, PlanQuery};
    let (net, _) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "bounced@example.edu.cn", &[], T).await;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    let original = first_email(&db, mentor).await.id;
    sqlx::query(
        r#"UPDATE email SET state = 'bounced', error = '退信'
           WHERE id = ?"#,
    )
    .bind(original)
    .execute(&db)
    .await
    .unwrap();
    assert_eq!(
        email::resend(&db, original, T).await.unwrap(),
        email::Approved::Yes
    );
    let again = email::list(&db, mentor).await.unwrap()[1].id;
    email::schedule::reschedule(&db, again, T + DAY, T)
        .await
        .unwrap();
    for column in [PlanColumn::Upcoming, PlanColumn::Pending] {
        if matches!(column, PlanColumn::Pending) {
            email::failed(&mut db.acquire().await.unwrap(), again, "拒收", T)
                .await
                .unwrap();
        }
        let page = send::plan::list(
            &db,
            &PlanQuery {
                column,
                page: std::num::NonZeroU32::MIN,
            },
            T,
        )
        .await
        .unwrap();
        let row = page.emails.iter().find(|e| e.id == again).unwrap();
        assert_eq!(row.resend_of, Some(original));
        assert!(!email::withdraw(&db, again, T).await.unwrap());
    }
}

#[tokio::test]
async fn cancellation_crash_child() {
    let Some(path) = std::env::var_os("AES_CANCELLATION_CRASH_DB") else {
        return;
    };
    let db = db::open(Path::new(&path)).await.unwrap();
    let (net, inbox) = mailbox(&db, SECRET).await;
    let (id, mentor) = sqlx::query_as::<_, (i64, i64)>("SELECT id, mentor_id FROM email")
        .fetch_one(&db)
        .await
        .unwrap();
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    change_during_commit(&db, &net, id, T, T, move |db| async move {
        mentor::trash::trash(&db, &[mentor], T).await.unwrap();
        mentor::trash::restore(&db, &[mentor], T).await.unwrap();
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT state FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap(),
            "sending"
        );
        assert!(inbox.lock().unwrap().is_empty());
        std::process::exit(73);
    })
    .await;
    panic!("child must exit after the durable claim");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_survives_crash_recovery(db: SqlitePool) {
    let mentor = approve(&db, "crash@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    let path = db.connect_options().get_filename().to_path_buf();
    db.close().await;
    let status = tokio::task::spawn_blocking(move || {
        std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "cancellation_crash_child", "--nocapture"])
            .env("AES_CANCELLATION_CRASH_DB", &path)
            .status()
            .unwrap()
    })
    .await
    .unwrap();
    assert_eq!(status.code(), Some(73));
    let db = db::open(db.connect_options().get_filename()).await.unwrap();
    send::recover(&db, T + MINUTE).await.unwrap();
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::MaybeSent { .. }
    ));
    let page = send::plan::list(
        &db,
        &send::plan::PlanQuery {
            column: send::plan::PlanColumn::Pending,
            page: std::num::NonZeroU32::MIN,
        },
        T + MINUTE,
    )
    .await
    .unwrap();
    assert_eq!(
        page.emails[0].cancel_reason.as_deref(),
        Some(mentor::trash::CANCELED)
    );
    assert!(page.emails[0].error.is_some());
    assert!(!email::retry(&db, id, T + MINUTE).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_survives_unknown_outcome(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "hangup@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    change_during_commit(&db, &net, id, T, T, move |db| async move {
        mentor::trash::trash(&db, &[mentor], T).await.unwrap();
        mentor::trash::restore(&db, &[mentor], T).await.unwrap();
    })
    .await;
    assert_eq!(inbox.lock().unwrap().len(), 1);
    assert!(matches!(
        first_email(&db, mentor).await.delivery,
        Delivery::MaybeSent { .. }
    ));
    let page = send::plan::list(
        &db,
        &send::plan::PlanQuery {
            column: send::plan::PlanColumn::Pending,
            page: std::num::NonZeroU32::MIN,
        },
        T + MINUTE,
    )
    .await
    .unwrap();
    assert_eq!(
        page.emails[0].cancel_reason.as_deref(),
        Some(mentor::trash::CANCELED)
    );
    assert!(page.emails[0].error.is_some());
    assert!(!email::retry(&db, id, T + MINUTE).await.unwrap());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_accepts_late_delivery_evidence_after_retry(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mut confirmed = Vec::new();
    for (offset, proof) in ["original", "replacement", "bounce", "rejected"]
        .into_iter()
        .enumerate()
    {
        let at = T + offset as i64 * DAY;
        let address = format!("hangup@case{offset}.edu.cn");
        let mentor = approve(&db, &address, &[], at).await;
        let id = first_email(&db, mentor).await.id;
        send::step(&db, &net, nowhere(), || at).await.unwrap();
        assert!(matches!(
            first_email(&db, mentor).await.delivery,
            Delivery::MaybeSent { .. }
        ));
        let message_id =
            sqlx::query_scalar::<_, String>("SELECT message_id FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap();
        if proof == "rejected" {
            mentor::update(
                &db,
                mentor,
                &MentorFields {
                    name: "王伟".into(),
                    emails: vec![format!("unknown@case{offset}.edu.cn")],
                    ..MentorFields::default()
                },
                at,
            )
            .await
            .unwrap();
        }
        assert!(email::retry(&db, id, at + MINUTE).await.unwrap());
        assert!(
            email::schedule::reschedule(&db, id, at + MINUTE, at)
                .await
                .unwrap()
        );
        change_during_commit(
            &db,
            &net,
            id,
            at + MINUTE,
            if proof == "rejected" {
                at + MINUTE
            } else {
                at + MINUTE + 3_600_000
            },
            move |db| async move {
                mentor::trash::trash(&db, &[mentor], at).await.unwrap();
                mentor::trash::restore(&db, &[mentor], at).await.unwrap();
            },
        )
        .await;
        assert_eq!(
            sqlx::query_as::<_, (String, Option<i64>)>(
                "SELECT state, sent_at FROM email WHERE id = ?"
            )
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap(),
            ("canceled".into(), Some(at))
        );
        let recipients = [address];
        let mut conn = db.acquire().await.unwrap();
        let found = match proof {
            "original" | "rejected" => {
                email::sent_copy(&mut conn, &message_id, &recipients, at + MINUTE)
                    .await
                    .unwrap()
                    == email::SentCopy::Confirmed
            }
            "replacement" => {
                let candidates = email::copy_candidates(
                    &mut conn,
                    &email::Copy {
                        mailbox_id: 1,
                        recipient: &recipients[0],
                        subject: "申请硕士",
                        at,
                    },
                )
                .await
                .unwrap();
                if candidates.iter().any(|c| c.id == id) {
                    email::copied(
                        &mut conn,
                        id,
                        "<qq-copy@example.com>",
                        &recipients,
                        at + MINUTE,
                    )
                    .await
                    .unwrap()
                        == email::SentCopy::Confirmed
                } else {
                    false
                }
            }
            _ => email::bounced(
                &mut conn,
                &message_id,
                &recipients,
                "地址不存在",
                at + MINUTE,
            )
            .await
            .unwrap(),
        };
        drop(conn);
        confirmed.push(found);
        let visible = email::list(&db, mentor).await.unwrap();
        assert_eq!(visible.len(), 1);
        assert_eq!(
            matches!(visible[0].delivery, Delivery::Bounced { .. }),
            proof == "bounce"
        );
        assert_eq!(sqlx::query_scalar::<_, Option<String>>(
            "SELECT cancel_reason FROM email WHERE id = ?",
        ).bind(id).fetch_one(&db).await.unwrap(), None);
        assert!(
            sqlx::query_scalar::<_, bool>(
                "SELECT EXISTS (SELECT 1 FROM mentor_contact WHERE mentor_id = ?)",
            )
            .bind(mentor)
            .fetch_one(&db)
            .await
            .unwrap()
        );
        let history = send::plan::list(
            &db,
            &send::plan::PlanQuery {
                column: send::plan::PlanColumn::History,
                page: std::num::NonZeroU32::MIN,
            },
            at + MINUTE,
        )
        .await
        .unwrap();
        assert!(history.emails.iter().any(|email| email.id == id));
        assert!(!email::retry(&db, id, at + DAY).await.unwrap());
    }
    assert_eq!(inbox.lock().unwrap().len(), 4);
    assert_eq!(confirmed, vec![true, true, true, true]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_without_a_handover_cannot_gain_delivery_from_a_copy(db: SqlitePool) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    for (offset, expired) in [false, true].into_iter().enumerate() {
        let at = T + offset as i64 * DAY;
        let address = format!("unknown@never{offset}.edu.cn");
        let mentor = approve(&db, &address, &[], at).await;
        let id = first_email(&db, mentor).await.id;
        email::schedule::reschedule(&db, id, at, at).await.unwrap();
        let after = if expired { at + 3_600_000 } else { at };
        change_during_commit(&db, &net, id, at, after, move |db| async move {
            mentor::trash::trash(&db, &[mentor], at).await.unwrap();
            mentor::trash::restore(&db, &[mentor], at).await.unwrap();
        })
        .await;
        let (state, sent_at, message_id) =
            sqlx::query_as::<_, (String, Option<i64>, Option<String>)>(
                "SELECT state, sent_at, message_id FROM email WHERE id = ?",
            )
            .bind(id)
            .fetch_one(&db)
            .await
            .unwrap();
        assert_eq!((state.as_str(), sent_at), ("canceled", None));
        let mut conn = db.acquire().await.unwrap();
        let candidates = email::copy_candidates(
            &mut conn,
            &email::Copy {
                mailbox_id: 1,
                recipient: &address,
                subject: "申请硕士",
                at,
            },
        )
        .await
        .unwrap();
        assert!(candidates.is_empty());
        let recipients = [address];
        assert_ne!(
            email::copied(
                &mut conn,
                id,
                &format!("<unrelated-copy{offset}@example.com>"),
                &recipients,
                at
            )
            .await
            .unwrap(),
            email::SentCopy::Confirmed
        );
        if let Some(message_id) = message_id {
            assert_ne!(
                email::sent_copy(&mut conn, &message_id, &recipients, at)
                    .await
                    .unwrap(),
                email::SentCopy::Confirmed
            );
            assert!(
                !email::bounced(&mut conn, &message_id, &recipients, "退信", at)
                    .await
                    .unwrap()
            );
        }
    }
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn cancellation_of_uncertain_mail_finishes_without_retry_after_mentor_restored(
    db: SqlitePool,
) {
    let (net, inbox) = mailbox(&db, SECRET).await;
    let mentor = approve(&db, "hangup@example.edu.cn", &[], T).await;
    let id = first_email(&db, mentor).await.id;
    send::step(&db, &net, nowhere(), || T).await.unwrap();
    assert!(!email::confirm_unsent(&db, id, T).await.unwrap());
    mentor::trash::trash(&db, &[mentor], T + MINUTE)
        .await
        .unwrap();
    mentor::trash::restore(&db, &[mentor], T + MINUTE)
        .await
        .unwrap();
    let email = first_email(&db, mentor).await;
    assert!(
        matches!(email.delivery, Delivery::MaybeSent { cancel_reason: Some(ref reason), .. }
        if reason == mentor::trash::CANCELED)
    );
    assert!(!email::retry(&db, id, T + MINUTE).await.unwrap());
    assert!(email::confirm_unsent(&db, id, T + MINUTE).await.unwrap());
    assert!(!email::confirm_unsent(&db, id, T + MINUTE).await.unwrap());
    assert_eq!(
        sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
            "SELECT state, cancel_reason, error FROM email WHERE id = ?",
        )
        .bind(id)
        .fetch_one(&db)
        .await
        .unwrap(),
        (
            "canceled".into(),
            Some(mentor::trash::CANCELED.into()),
            None
        )
    );
    assert!(!email::retry(&db, id, T + MINUTE).await.unwrap());
    assert!(!email::withdraw(&db, id, T + MINUTE).await.unwrap());
    assert!(
        !email::schedule::reschedule(&db, id, T + DAY, T + MINUTE)
            .await
            .unwrap()
    );
    assert!(
        !email::schedule::send_now(&db, id, T + MINUTE)
            .await
            .unwrap()
    );
    assert_eq!(
        send::step(&db, &net, nowhere(), || T + DAY).await.unwrap(),
        Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}
