// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Receiving from a mailbox on the fake IMAP server.

use std::borrow::Cow;

use email::Delivery;
use fakemail::{ADDRESS, SECRET, Store};
use mailbox::{MailboxForm, Security, Server};
use mentor::{Contact, MentorForm};
use net::Net;
use receive::{Message, MessageKind, Round};
use sqlx::SqlitePool;

const T: i64 = 1_800_000_000_000;
const MINUTE: i64 = 60_000;
const DAY: i64 = 24 * 60 * MINUTE;
/// The sent folder, named 已发送 in modified UTF-7.
const SENT: &str = "&XfJT0ZAB-";

/// A mailbox on a fake IMAP server with an inbox and a sent folder.
async fn mailbox(db: &SqlitePool, secret: &str) -> (Net, Store) {
    let pki = fakemail::pki();
    let (port, store) = fakemail::imap(&pki, false).await;
    store.create(SENT, true);
    mailbox::create(db, &form(port).check().unwrap(), secret, 0)
        .await
        .unwrap();
    (Net::new(vec![pki.root.clone()]).unwrap(), store)
}

fn form(port: u16) -> MailboxForm {
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security: Security::Tls,
    };
    MailboxForm {
        address: ADDRESS.into(),
        sender_name: "林晓".into(),
        provider: mailbox::providers::CUSTOM.into(),
        smtp: server(465),
        imap: server(port),
        daily_limit: 50,
        min_interval_s: 60,
    }
}

async fn mentor(db: &SqlitePool, name: &str, address: &str) -> i64 {
    let form = MentorForm {
        name: name.into(),
        title: String::new(),
        email: address.into(),
        university: String::new(),
        school: String::new(),
        department: String::new(),
        homepage_url: String::new(),
        research: String::new(),
    };
    mentor::create(db, &form.check().unwrap(), 0).await.unwrap()
}

/// An email the app sent to the mentor, in this state, with this Message-ID.
async fn sent_email(db: &SqlitePool, mentor: i64, state: &str, message_id: &str) {
    sqlx::query(
        "INSERT INTO email (mentor_id, kind, state, planned_at, mailbox_id, from_address, message_id,
           sent_at, error, created_at, updated_at, subject, body_md, html, text)
         VALUES (?, 'first', ?, ?, 1, ?, ?, ?, '发送途中应用退出。', 0, 0, '申请读博', '老师您好', '<p>老师您好</p>', '老师您好')",
    )
    .bind(mentor)
    .bind(state)
    .bind(T - DAY)
    .bind(ADDRESS)
    .bind(message_id)
    .bind(T - DAY)
    .execute(db)
    .await
    .unwrap();
}

async fn round(db: &SqlitePool, net: &Net, now: i64) -> Round {
    receive::round(db, net, now).await.unwrap()
}

async fn messages(db: &SqlitePool, mentor: i64) -> Vec<Message> {
    receive::list(db, mentor).await.unwrap()
}

async fn contact(db: &SqlitePool, mentor: i64) -> Contact {
    mentor::get(db, mentor).await.unwrap().unwrap().contact
}

/// The UIDs whose messages were fetched beyond their headers.
fn opened(commands: &[String]) -> Vec<u32> {
    commands
        .iter()
        .filter(|c| c.contains("BODY.PEEK[]"))
        .flat_map(|c| c.split(' ').nth(2).unwrap().split(','))
        .map(|uid| uid.parse().unwrap())
        .collect()
}

const REPLY: &str = "From: =?UTF-8?B?5p2O5piO?= <Li@pku.edu.cn>
To: 林晓 <me@example.com>
Subject: =?UTF-8?B?5Zue5aSN77ya55Sz6K+36K+75Y2a?=
Message-ID: <r1@pku.edu.cn>
In-Reply-To: <e1@example.com>
References: <e1@example.com>
Date: Fri, 15 Jan 2027 10:00:00 +0800
Content-Type: multipart/alternative; boundary=b

--b
Content-Type: text/plain; charset=utf-8

林晓同学你好：
欢迎报考，下周可以线上聊一聊。

李明

------------------ 原始邮件 ------------------
发件人: \"林晓\"<me@example.com>;
发送时间: 2027年1月14日(星期四) 上午9:00
收件人: \"李明\"<li@pku.edu.cn>;
主题: 申请读博

李老师：您好！
--b
Content-Type: text/html; charset=utf-8

<div>林晓同学你好：</div>
--b--
";

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn mail_with_mentors_is_linked_to_them(db: SqlitePool) {
    let (net, store) = mailbox(&db, SECRET).await;
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    let wang = mentor(&db, "王伟", "wang@thu.edu.cn").await;
    let zhang = mentor(&db, "张华", "zhang@fudan.edu.cn").await;
    sent_email(&db, li, "sent", "<e1@example.com>").await;
    round(&db, &net, T).await;

    let reply = store.deliver("INBOX", T + MINUTE, REPLY);
    // Left out of the answers for several messages, and given alone.
    store.leave_out(reply);
    // The mentor's secretary, answering in the thread.
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: secretary@pku.edu.cn\nSubject: Re: 申请读博\nMessage-ID: <r2@pku.edu.cn>\nIn-Reply-To: <r1@pku.edu.cn>\n\n李老师请你周五来面谈。",
    );
    let newsletter = store.deliver(
        "INBOX",
        T + 3 * MINUTE,
        "From: news@example.org\nSubject: 本周推荐\nMessage-ID: <n1@example.org>\n\n订阅内容",
    );
    store.deliver(
        "INBOX",
        T + 4 * MINUTE,
        "From: wang@thu.edu.cn\nSubject: Automatic reply: 申请读博\nAuto-Submitted: auto-replied\nMessage-ID: <a1@thu.edu.cn>\n\n我正在休假，1月20日回来。",
    );
    store.deliver(
        "INBOX",
        T + 4 * MINUTE,
        "From: wang@thu.edu.cn\nSubject: 已读: 申请读博\nMessage-ID: <m1@thu.edu.cn>\nContent-Type: multipart/report; report-type=disposition-notification; boundary=r\n\n--r\nContent-Type: text/plain\n\n您的邮件已被阅读。\n--r\nContent-Type: message/disposition-notification\n\nFinal-Recipient: rfc822; wang@thu.edu.cn\nOriginal-Message-ID: <s1@example.com>\nDisposition: manual-action/MDN-sent-manually; displayed\n--r--\n",
    );
    // The user copying themselves.
    store.deliver(
        "INBOX",
        T + 5 * MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 补充材料\nMessage-ID: <s0@example.com>\n\n李老师：补充一份成绩单。",
    );
    // Sent from the mailbox's website, to two mentors at once.
    store.deliver(
        SENT,
        T + 6 * MINUTE,
        "From: me@example.com\nTo: wang@thu.edu.cn\nCc: Zhang Hua <ZHANG@fudan.edu.cn>\nSubject: 申请读博\nMessage-ID: <s1@example.com>\n\n两位老师好！",
    );
    store.commands();
    assert_eq!(
        round(&db, &net, T + 10 * MINUTE).await,
        Round {
            changed: true,
            more: false
        }
    );
    assert!(!opened(&store.commands()).contains(&newsletter));

    let li_mail = messages(&db, li).await;
    assert_eq!(
        li_mail
            .iter()
            .map(|m| (m.from_address.as_str(), m.subject.as_deref(), &m.kind))
            .collect::<Vec<_>>(),
        [
            (
                "li@pku.edu.cn",
                Some("回复：申请读博"),
                &MessageKind::Reply { unread: true }
            ),
            (
                "secretary@pku.edu.cn",
                Some("Re: 申请读博"),
                &MessageKind::Reply { unread: true }
            ),
        ]
    );
    assert_eq!(li_mail[0].sent_at, T + MINUTE);
    assert_eq!(
        li_mail[0].text,
        "林晓同学你好：\n欢迎报考，下周可以线上聊一聊。\n\n李明"
    );
    assert!(
        li_mail[0]
            .quote
            .as_deref()
            .unwrap()
            .contains("李老师：您好！")
    );
    assert!(matches!(
        contact(&db, li).await,
        Contact::Replied { unread: true, .. }
    ));

    let wang_mail = messages(&db, wang).await;
    assert_eq!(
        wang_mail.iter().map(|m| &m.kind).collect::<Vec<_>>(),
        [&MessageKind::AutoReply, &MessageKind::Sent { bounce: None }]
    );
    // An automatic reply is no reply.
    assert!(matches!(
        contact(&db, wang).await,
        Contact::Contacted { .. }
    ));
    assert_eq!(messages(&db, zhang).await[0].id, wang_mail[1].id);
    assert!(matches!(
        contact(&db, zhang).await,
        Contact::Contacted { .. }
    ));

    receive::mark_read(&db, li, T + 20 * MINUTE).await.unwrap();
    assert!(matches!(
        contact(&db, li).await,
        Contact::Replied { unread: false, .. }
    ));
    // Nothing is taken twice.
    assert_eq!(round(&db, &net, T + 30 * MINUTE).await, Round::default());
    assert_eq!(messages(&db, li).await.len(), 2);
    assert!(!li_mail[0].truncated);

    // Only the start of a large message is read.
    store.deliver(
        "INBOX",
        T + 31 * MINUTE,
        &format!(
            "From: li@pku.edu.cn\nSubject: 材料\nMessage-ID: <r3@pku.edu.cn>\n\n请看下面的说明。\n{}",
            "说明".repeat(100_000)
        ),
    );
    round(&db, &net, T + 35 * MINUTE).await;
    let large = &messages(&db, li).await[2];
    assert!(large.truncated && large.text.starts_with("请看下面的说明。"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_apps_own_mail_is_confirmed_or_bounced(db: SqlitePool) {
    let (net, store) = mailbox(&db, SECRET).await;
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    let wang = mentor(&db, "王伟", "wang@thu.edu.cn").await;
    let zhang = mentor(&db, "张华", "zhang@fudan.edu.cn").await;
    sent_email(&db, li, "maybe_sent", "<e1@example.com>").await;
    sent_email(&db, wang, "sent", "<e2@example.com>").await;
    round(&db, &net, T).await;

    store.deliver(
        SENT,
        T + MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 申请读博\nMessage-ID: <e1@example.com>\n\n老师您好",
    );
    store.deliver(
        SENT,
        T + MINUTE,
        "From: me@example.com\nTo: zhang@fudan.edu.cn\nSubject: 申请读博\nMessage-ID: <w1@example.com>\n\n张老师您好",
    );
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: Mail Delivery System <MAILER-DAEMON@mx.example.com>
Subject: Undelivered Mail Returned to Sender
Message-ID: <b1@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: text/plain

I'm sorry to have to inform you that your message could not be delivered.
--r
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.example.com

Final-Recipient: rfc822; wang@thu.edu.cn
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 <wang@thu.edu.cn>: Recipient address rejected: User unknown
--r
Content-Type: text/rfc822-headers

From: me@example.com
To: wang@thu.edu.cn
Subject: 申请读博
Message-ID: <e2@example.com>
--r--
",
    );
    // Only late, which says nothing about the mail being lost.
    store.deliver(
        "INBOX",
        T + 3 * MINUTE,
        "From: MAILER-DAEMON@mx.example.com
Subject: Delayed Mail (still being retried)
Message-ID: <b2@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Final-Recipient: rfc822; li@pku.edu.cn
Action: delayed
Status: 4.4.1
--r
Content-Type: message/rfc822

From: me@example.com
Message-ID: <e1@example.com>

老师您好
--r--
",
    );
    // As some Chinese providers write them: no report, only a letter with the mail attached.
    store.deliver(
        "INBOX",
        T + 4 * MINUTE,
        "From: PostMaster@example.com
Subject: 系统退信
Message-ID: <b3@example.com>
Content-Type: multipart/mixed; boundary=q

--q
Content-Type: text/plain; charset=utf-8

很抱歉，您的邮件未能发送成功：收件人邮箱空间不足。
--q
Content-Type: message/rfc822

From: me@example.com
To: zhang@fudan.edu.cn
Subject: 申请读博
Message-ID: <w1@example.com>

张老师您好
--q--
",
    );
    round(&db, &net, T + 10 * MINUTE).await;

    let li_email = email::list(&db, li).await.unwrap();
    assert!(matches!(li_email[0].delivery, Delivery::Sent { .. }));
    // The copy of the app's own email is no extra message.
    assert!(messages(&db, li).await.is_empty());
    assert_eq!(
        email::list(&db, wang).await.unwrap()[0].delivery,
        Delivery::Bounced {
            from_address: ADDRESS.into(),
            sent_at: T - DAY,
            error: "收件地址不存在（550 5.1.1 <wang@thu.edu.cn>: Recipient address rejected: User unknown）"
                .into()
        }
    );
    assert!(matches!(contact(&db, wang).await, Contact::Bounced { .. }));
    assert_eq!(
        messages(&db, zhang).await[0].kind,
        MessageKind::Sent {
            bounce: Some("对方邮箱已满".into())
        }
    );
    assert!(matches!(contact(&db, zhang).await, Contact::Bounced { .. }));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn earlier_mail_is_looked_up_once_per_mentor(db: SqlitePool) {
    let pki = fakemail::pki();
    let (port, store) = fakemail::imap(&pki, false).await;
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    store.create(SENT, true);
    store.deliver(
        SENT,
        T - 31 * DAY,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 申请读博\nMessage-ID: <s1@example.com>\n\n李老师您好",
    );
    store.deliver(
        "INBOX",
        T - 30 * DAY,
        "From: li@pku.edu.cn\nSubject: Re: 申请读博\nMessage-ID: <r1@pku.edu.cn>\nIn-Reply-To: <s1@example.com>\n\n欢迎报考。",
    );
    // From before the two years looked back on.
    store.deliver(
        "INBOX",
        T - 800 * DAY,
        "From: li@pku.edu.cn\nSubject: 课程作业\nMessage-ID: <old@pku.edu.cn>\n\n作业已批改。",
    );
    // Mentors added before the mailbox are looked up once it is.
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    mailbox::create(&db, &form(port).check().unwrap(), SECRET, 0)
        .await
        .unwrap();
    round(&db, &net, T).await;
    let found = messages(&db, li).await;
    assert_eq!(
        found.iter().map(|m| m.sent_at).collect::<Vec<_>>(),
        [T - 31 * DAY, T - 30 * DAY]
    );
    assert!(matches!(contact(&db, li).await, Contact::Replied { .. }));
    let own: Option<i64> = sqlx::query_scalar("SELECT mailbox_id FROM mentor WHERE id = ?")
        .bind(li)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(own, Some(1));

    // Mail with someone not yet a mentor is passed over, and found once they become one.
    store.deliver(
        "INBOX",
        T + MINUTE,
        "From: wang@thu.edu.cn\nSubject: 招生通知\nMessage-ID: <w1@thu.edu.cn>\n\n欢迎报名夏令营。",
    );
    round(&db, &net, T + 5 * MINUTE).await;
    let wang = mentor(&db, "王伟", "wang@thu.edu.cn").await;
    round(&db, &net, T + 10 * MINUTE).await;
    assert_eq!(messages(&db, wang).await.len(), 1);

    // A renumbered folder is started afresh without taking anything twice.
    store.renumber("INBOX");
    store.renumber(SENT);
    round(&db, &net, T + 15 * MINUTE).await;
    store.deliver(
        "INBOX",
        T + 16 * MINUTE,
        "From: li@pku.edu.cn\nSubject: 面谈时间\nMessage-ID: <r2@pku.edu.cn>\n\n周五下午三点。",
    );
    round(&db, &net, T + 20 * MINUTE).await;
    assert_eq!(messages(&db, li).await.len(), 3);
    assert_eq!(messages(&db, wang).await.len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_login_waits_unless_the_mailbox_changes(db: SqlitePool) {
    let (net, store) = mailbox(&db, "wrong").await;
    round(&db, &net, T).await;
    let error = mailbox::list(&db).await.unwrap()[0].receive_error.clone();
    assert!(error.unwrap().starts_with("登录被拒绝"));
    store.commands();
    round(&db, &net, T + 10 * MINUTE).await;
    assert!(store.commands().is_empty());

    let port: u16 = sqlx::query_scalar("SELECT imap_port FROM mailbox")
        .fetch_one(&db)
        .await
        .unwrap();
    mailbox::update(&db, 1, &form(port).check().unwrap(), SECRET)
        .await
        .unwrap();
    round(&db, &net, T + 11 * MINUTE).await;
    assert_eq!(mailbox::list(&db).await.unwrap()[0].receive_error, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_copy_under_another_message_id_is_still_the_apps(db: SqlitePool) {
    let (net, store) = mailbox(&db, SECRET).await;
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    let wang = mentor(&db, "王伟", "wang@thu.edu.cn").await;
    sent_email(&db, li, "maybe_sent", "<e1@example.com>").await;
    sent_email(&db, wang, "sending", "<e2@example.com>").await;
    round(&db, &net, T).await;

    // Another letter with the same subject, sent from the website.
    store.deliver(
        SENT,
        T - DAY + MINUTE / 2,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 申请读博\nMessage-ID: <web@qq.com>\n\n老师您好，补充一份成绩单。",
    );
    // As QQ keeps it, under a Message-ID of its own.
    store.deliver(
        SENT,
        T - DAY + MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 申请读博\nMessage-ID: <tencent_1@qq.com>\n\n老师您好",
    );
    // The same letter sent again from the website.
    store.deliver(
        SENT,
        T - DAY + 2 * MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn\nSubject: 申请读博\nMessage-ID: <tencent_2@qq.com>\n\n老师您好",
    );
    // Kept before SMTP has answered.
    store.deliver(
        SENT,
        T - DAY + MINUTE,
        "From: me@example.com\nTo: wang@thu.edu.cn\nSubject: 申请读博\nMessage-ID: <e2@example.com>\n\n老师您好",
    );
    store.deliver(
        "INBOX",
        T,
        "From: secretary@pku.edu.cn\nSubject: Re: 申请读博\nMessage-ID: <r1@pku.edu.cn>\nIn-Reply-To: <tencent_1@qq.com>\n\n李老师请你周五来面谈。",
    );
    // Answering the Message-ID the app gave.
    store.deliver(
        "INBOX",
        T + MINUTE,
        "From: secretary@pku.edu.cn\nSubject: Re: 申请读博\nMessage-ID: <r2@pku.edu.cn>\nIn-Reply-To: <e1@example.com>\n\n地点在理科楼。",
    );
    round(&db, &net, T + 10 * MINUTE).await;

    assert!(matches!(
        email::list(&db, li).await.unwrap()[0].delivery,
        Delivery::Sent { .. }
    ));
    let li_mail = messages(&db, li).await;
    assert_eq!(
        li_mail
            .iter()
            .map(|m| (m.from_address.as_str(), &m.kind))
            .collect::<Vec<_>>(),
        [
            ("me@example.com", &MessageKind::Sent { bounce: None }),
            ("me@example.com", &MessageKind::Sent { bounce: None }),
            ("secretary@pku.edu.cn", &MessageKind::Reply { unread: true }),
            ("secretary@pku.edu.cn", &MessageKind::Reply { unread: true }),
        ]
    );
    assert_eq!(li_mail[0].text, "老师您好，补充一份成绩单。");

    let id: i64 = sqlx::query_scalar("SELECT id FROM email WHERE mentor_id = ?")
        .bind(wang)
        .fetch_one(&db)
        .await
        .unwrap();
    let mut conn = db.acquire().await.unwrap();
    email::maybe_sent(&mut conn, id, "发送途中断开。", T + 11 * MINUTE)
        .await
        .unwrap();
    assert!(matches!(
        email::list(&db, wang).await.unwrap()[0].delivery,
        Delivery::Sent { .. }
    ));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_bounce_marks_only_the_mail_and_mentors_it_names(db: SqlitePool) {
    let (net, store) = mailbox(&db, SECRET).await;
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    let wang = mentor(&db, "王伟", "wang@thu.edu.cn").await;
    let zhang = mentor(&db, "张华", "zhang@fudan.edu.cn").await;
    sent_email(&db, zhang, "sent", "<e1@example.com>").await;
    sent_email(&db, zhang, "sent", "<e2@example.com>").await;
    round(&db, &net, T).await;

    store.deliver(
        SENT,
        T + MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn, wang@thu.edu.cn\nSubject: 申请读博\nMessage-ID: <s1@example.com>\n\n两位老师好！",
    );
    store.deliver(
        SENT,
        T + MINUTE,
        "From: me@example.com\nTo: li@pku.edu.cn, wang@thu.edu.cn\nSubject: 夏令营\nMessage-ID: <s2@example.com>\n\n两位老师好！",
    );
    store.deliver(
        SENT,
        T + 2 * MINUTE,
        "From: me@example.com\nTo: wang@thu.edu.cn\nCc: peer@example.org\nSubject: 推荐信\nMessage-ID: <s3@example.com>\n\n王老师好！",
    );
    // Neither reached, for different reasons, one going on over two lines.
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: MAILER-DAEMON@mx.example.com
Subject: Undelivered Mail Returned to Sender
Message-ID: <b4@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Final-Recipient: rfc822; li@pku.edu.cn
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp;
 550 5.1.1 User unknown

Final-Recipient: rfc822; wang@thu.edu.cn
Action: failed
Status: 5.2.2
Diagnostic-Code: smtp; 552 5.2.2 Mailbox full
--r
Content-Type: text/rfc822-headers

Message-ID: <s2@example.com>
--r--
",
    );
    // Only someone else's address failed.
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: MAILER-DAEMON@mx.example.com
Subject: Undelivered Mail Returned to Sender
Message-ID: <b5@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Final-Recipient: rfc822; peer@example.org
Action: failed
Status: 5.1.1
--r
Content-Type: text/rfc822-headers

Message-ID: <s3@example.com>
--r--
",
    );
    // A letter the app does not know of came back, carrying one it sent.
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: MAILER-DAEMON@mx.example.com
Subject: Undelivered Mail Returned to Sender
Message-ID: <b6@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Final-Recipient: rfc822; peer@example.org
Action: failed
--r
Content-Type: message/rfc822

From: me@example.com
Message-ID: <untracked@example.com>
Content-Type: multipart/mixed; boundary=f

--f
Content-Type: text/plain

请看附件。
--f
Content-Type: message/rfc822

From: me@example.com
Message-ID: <e1@example.com>

张老师您好
--f--
--r--
",
    );
    // Delivered to one of the two.
    store.deliver(
        "INBOX",
        T + 2 * MINUTE,
        "From: MAILER-DAEMON@mx.example.com
Subject: Delivery Status Notification
Message-ID: <b1@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.example.com

Final-Recipient: rfc822; wang@thu.edu.cn
Action: delivered
Status: 2.0.0

Final-Recipient: rfc822; li@pku.edu.cn
Action: failed
Status: 5.2.2
Diagnostic-Code: smtp; 552 5.2.2 Mailbox full
--r
Content-Type: text/rfc822-headers

From: me@example.com
Message-ID: <s1@example.com>
--r--
",
    );
    // The bounced email carries an earlier one, forwarded; the same bounce comes twice.
    let bounce = "From: MAILER-DAEMON@mx.example.com
Subject: Undelivered Mail Returned to Sender
Message-ID: <b2@mx.example.com>
Content-Type: multipart/report; report-type=delivery-status; boundary=r

--r
Content-Type: message/delivery-status

Final-Recipient: rfc822; zhang@fudan.edu.cn
Action: failed
Status: 5.1.1
--r
Content-Type: message/rfc822

From: me@example.com
Message-ID: <e2@example.com>
Content-Type: multipart/mixed; boundary=f

--f
Content-Type: text/plain

张老师您好
--f
Content-Type: message/rfc822

From: me@example.com
Message-ID: <e1@example.com>

张老师您好
--f--
--r--
";
    store.deliver("INBOX", T + 3 * MINUTE, bounce);
    store.deliver("INBOX", T + 4 * MINUTE, bounce);
    round(&db, &net, T + 10 * MINUTE).await;

    let bounces = |mail: Vec<Message>| {
        mail.into_iter()
            .map(|m| match m.kind {
                MessageKind::Sent { bounce } => bounce,
                _ => None,
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(
        bounces(messages(&db, li).await),
        [
            Some("对方邮箱已满（552 5.2.2 Mailbox full）".into()),
            Some("收件地址不存在（550 5.1.1 User unknown）".into()),
        ]
    );
    assert!(matches!(contact(&db, li).await, Contact::Bounced { .. }));
    assert_eq!(
        bounces(messages(&db, wang).await),
        [
            None,
            Some("对方邮箱已满（552 5.2.2 Mailbox full）".into()),
            None
        ]
    );
    assert!(matches!(
        contact(&db, wang).await,
        Contact::Contacted { .. }
    ));
    let states: Vec<String> =
        sqlx::query_scalar("SELECT state FROM email WHERE mentor_id = ? ORDER BY message_id")
            .bind(zhang)
            .fetch_all(&db)
            .await
            .unwrap();
    assert_eq!(states, ["sent", "bounced"]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_search_is_tried_again(db: SqlitePool) {
    let (net, store) = mailbox(&db, SECRET).await;
    store.deliver(
        "INBOX",
        T - DAY,
        "From: li@pku.edu.cn\nSubject: Re: 申请读博\nMessage-ID: <r1@pku.edu.cn>\n\n欢迎报考。",
    );
    store.refuse("UID SEARCH SINCE");
    let li = mentor(&db, "李明", "li@pku.edu.cn").await;
    round(&db, &net, T).await;
    assert!(messages(&db, li).await.is_empty());
    assert!(mailbox::list(&db).await.unwrap()[0].receive_error.is_some());

    round(&db, &net, T + 61 * MINUTE).await;
    assert_eq!(messages(&db, li).await.len(), 1);
    assert_eq!(mailbox::list(&db).await.unwrap()[0].receive_error, None);
}
