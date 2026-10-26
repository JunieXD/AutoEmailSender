#![cfg(test)]
use email::tasks::{self as queue, DraftState};
use mentor::MentorFields;
use sqlx::SqlitePool;
use task::*;
const T: i64 = 1_800_000_000_000;
async fn mentor(db: &SqlitePool, n: i64, email: bool) -> i64 {
    mentor::create(
        db,
        &MentorFields {
            name: format!("导师{n}"),
            emails: if email {
                vec![format!("m{n}@example.com")]
            } else {
                vec![]
            },
            ..Default::default()
        },
        T,
    )
    .await
    .unwrap()
}
async fn form(db: &SqlitePool, ids: Vec<i64>) -> NewTask {
    let t = template::create(
        db,
        &template::TemplateForm {
            name: "首封".into(),
            subject: "给{导师姓名}".into(),
            body_md: "{导师姓名}老师：我是{我的姓名}。".into(),
        }
        .check()
        .unwrap(),
        T,
    )
    .await
    .unwrap();
    NewTask {
        kind: task::Kind::First,
        schedule: email::schedule::Schedule::Now,
        name: "首封 10月25日".into(),
        ids,
        writer: Writer::Template,
        template_id: Some(t),
        template_source_id: None,
        source_task_id: None,
        model_config_id: None,
        references: vec![],
        attachments: vec![],
    }
}
async fn items(db: &SqlitePool, task: i64) -> Vec<TaskDraft> {
    let page = drafts(
        db,
        &DraftQuery {
            task_id: task,
            filter: DraftFilter::All,
            page: std::num::NonZeroU32::MIN,
        },
        T,
        |_| None,
    )
    .await
    .unwrap();
    let mut result = vec![];
    for d in page.drafts {
        result.push(
            draft(
                db,
                &TaskEmail {
                    task_id: task,
                    id: d.id,
                },
            )
            .await
            .unwrap()
            .unwrap(),
        );
    }
    result
}
fn approval(d: &TaskDraft) -> RenderedApproval {
    RenderedApproval {
        attachments: d.attachments.clone(),
        id: d.id,
        subject: d.subject.clone(),
        body_md: d.body_md.clone(),
        html: format!("<p>{}</p>", d.body_md),
        text: d.body_md.clone(),
    }
}

async fn previous(db: &SqlitePool, mentor: i64, external: bool, at: i64) -> String {
    let mid = format!("<previous-{mentor}-{at}@example.com>");
    if external {
        let id = sqlx::query(
            r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind,
                message_id, sent_at, from_address, from_name, subject, body_text, refs,
                truncated)
            VALUES (1, 'Sent', 1, ?, 'sent', ?, ?, 'me@example.com', '林晓',
                'RE：申请', '之前的信 <script>data</script>', '<root@example.com>', 0)"#,
        )
        .bind(mentor)
        .bind(&mid)
        .bind(at)
        .execute(db)
        .await
        .unwrap()
        .last_insert_rowid();
        sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
            .bind(mentor)
            .bind(id)
            .execute(db)
            .await
            .unwrap();
    } else {
        sqlx::query(
            r#"INSERT INTO email (mentor_id, kind, state, planned_at, mailbox_id,
                from_address, to_address, message_id, sent_at, subject, body_md, html,
                text, refs, created_at, updated_at)
            VALUES (?, 'first', 'sent', ?, 1, 'me@example.com', 'mentor@example.com',
                ?, ?, '申请', '之前的信', '<p>之前的信</p>',
                '之前的信 <script>data</script>', '<root@example.com>', ?, ?)"#,
        )
        .bind(mentor)
        .bind(at)
        .bind(&mid)
        .bind(at)
        .bind(at)
        .bind(at)
        .execute(db)
        .await
        .unwrap();
    }
    mid
}

async fn follow_form(db: &SqlitePool, ids: Vec<i64>) -> NewTask {
    let mut f = form(db, ids).await;
    f.kind = Kind::FollowUp;
    f.name = "追问".into();
    f
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn follow_up_eligibility_and_concurrent_task_creation(db: SqlitePool) {
    // A mailbox need not connect during planning.
    sqlx::query(
        r#"INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port,
            smtp_security, imap_host, imap_port, imap_security, auth, secret,
            daily_limit, min_interval_s, rotation_order, created_at)
        VALUES ('me@example.com', '我', 'custom', 'localhost', 465, 'tls', 'localhost',
            993, 'tls', 'password', 'test', 50, 0, 1, 0)"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let mut ids = vec![];
    for n in 1..=7 {
        let id = mentor(&db, n, n != 5).await;
        previous(&db, id, n == 7, T - 10 * 86_400_000).await;
        ids.push(id);
    }
    let reply = sqlx::query(
        r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id,
            sent_at, from_address, truncated)
        VALUES (1, 'INBOX', 1, 1, 'reply', '<reply@example.com>', ?, 'm1@example.com',
            0)"#,
    )
    .bind(T)
    .execute(&db)
    .await
    .unwrap()
    .last_insert_rowid();
    sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
        .bind(ids[0])
        .bind(reply)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE email SET state='bounced',error='退信' WHERE mentor_id=?")
        .bind(ids[1])
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE email SET state='maybe_sent',error='连接断开' WHERE mentor_id=?")
        .bind(ids[2])
        .execute(&db)
        .await
        .unwrap();
    email::save_first_draft(&db, ids[3], "草稿", "您好", &[], T)
        .await
        .unwrap();
    let p = plan(&db, &follow_form(&db, ids.clone()).await)
        .await
        .unwrap();
    for (i, reason) in [
        "已回信",
        "被退回",
        "说不清",
        "没发出的首封或追问",
        "尚未填写邮箱",
    ]
    .iter()
    .enumerate()
    {
        assert!(
            p.mentors
                .iter()
                .find(|m| m.id == ids[i])
                .unwrap()
                .reason
                .as_ref()
                .unwrap()
                .contains(reason)
        );
    }
    for id in &ids[5..] {
        let c = p.mentors.iter().find(|m| m.id == *id).unwrap();
        assert!(c.reason.is_none());
        assert_eq!(c.previous.as_ref().unwrap().sent_at, T - 10 * 86_400_000);
    }
    let follow = follow_form(&db, vec![ids[5]]).await;
    let first = form(&db, vec![ids[5]]).await;
    let (a, b) = tokio::join!(create(&db, &follow, T), create(&db, &first, T));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    let pending: i64 =
        sqlx::query_scalar("SELECT count(*) FROM email WHERE mentor_id=? AND state='review'")
            .bind(ids[5])
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(pending, 1);
    assert!(
        email::save_first_draft(&db, ids[5], "另一封", "正文", &[], T)
            .await
            .unwrap()
            .is_none()
    );
    stop(&db, a.or(b).unwrap(), T).await.unwrap();
    let (a, b) = tokio::join!(create(&db, &follow, T), create(&db, &follow, T));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn follow_up_sends_in_the_original_thread_for_app_and_external_mail(db: SqlitePool) {
    let writes = db::Writes::default();
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        &db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "林晓".into(),
            provider: "custom".into(),
            smtp: server(port),
            imap: server(993),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    for external in [false, true] {
        let m = mentor(&db, if external { 2 } else { 1 }, true).await;
        let parent = previous(&db, m, external, T - 1000).await;
        let f = follow_form(&db, vec![m]).await;
        let task = create(&db, &f, T).await.unwrap();
        if !external {
            sqlx::query(
                "UPDATE email SET copy_message_id='<renamed-copy@example.com>' WHERE message_id=?",
            )
            .bind(&parent)
            .execute(&db)
            .await
            .unwrap();
        }
        let d = items(&db, task).await.remove(0);
        assert_eq!(
            d.subject,
            if external {
                "RE：申请"
            } else {
                "Re: 申请"
            }
        );
        let edit = DraftEdit {
            task_id: task,
            id: d.id,
            subject: "不应改变主题".into(),
            body_md: "导师老师：想请问您是否方便查看之前的信。".into(),
            attachments: vec![],
        };
        save(&db, &edit, T).await.unwrap();
        reapply(
            &db,
            &TaskEmail {
                task_id: task,
                id: d.id,
            },
            T,
        )
        .await
        .unwrap();
        let d = items(&db, task).await.remove(0);
        assert_eq!(
            d.subject,
            if external {
                "RE：申请"
            } else {
                "Re: 申请"
            }
        );
        let checks = check(
            &db,
            &DraftEdit {
                task_id: task,
                id: d.id,
                subject: d.subject.clone(),
                body_md: d.body_md.clone(),
                attachments: vec![],
            },
            T,
        )
        .await
        .unwrap();
        assert!(
            !checks
                .iter()
                .any(|p| matches!(p, check::Problem::RecentlyContacted { .. }))
        );
        assert_eq!(
            approve(
                &db,
                &TaskApprovals {
                    task_id: task,
                    emails: vec![approval(&d)]
                },
                T
            )
            .await
            .unwrap(),
            1
        );
        let (refs, text, html): (String, String, String) =
            sqlx::query_as("SELECT refs,text,html FROM email WHERE id=?")
                .bind(d.id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert!(text.contains(&d.previous.as_ref().unwrap().attribution));
        assert_eq!(refs, format!("<root@example.com> {parent}"));
        assert!(text.contains("写道：\n> 之前的信 <script>data</script>"));
        assert!(html.contains("&lt;script&gt;data&lt;/script&gt;"));
        // Changing the source after approval cannot change the approved quote.
        sqlx::query("UPDATE message SET body_text='后来改了原信' WHERE message_id=?")
            .bind(&parent)
            .execute(&db)
            .await
            .unwrap();
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap();
        let raw = inbox.lock().unwrap().last().unwrap().clone();
        let headers = raw.split("\r\n\r\n").next().unwrap();
        assert!(
            headers.contains(&format!("In-Reply-To: {parent}")),
            "{headers}"
        );
        assert!(
            headers.contains(&format!("References: <root@example.com> {parent}")),
            "{headers}"
        );
        assert!(!raw.contains("后来改了原信"));
    }
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn receiving_reply_cancels_unsent_follow_ups_and_fences_inflight_generation(db: SqlitePool) {
    let writes = db::Writes::default();
    let pki = fakemail::pki();
    let (imap, store) = fakemail::imap(&pki, false).await;
    let (smtp, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        &db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "我".into(),
            provider: "custom".into(),
            smtp: server(smtp),
            imap: server(imap),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    receive::round(&db, &net, T).await.unwrap();
    let states = [
        "generating",
        "generation_failed",
        "review",
        "approved",
        "failed",
        "sending",
        "maybe_sent",
    ];
    let mut ids = vec![];
    for n in 1..=states.len() as i64 + 1 {
        let m = mentor(&db, n, true).await;
        previous(&db, m, false, T - 86_400_000).await;
        ids.push(m);
    }
    let t = create(&db, &follow_form(&db, ids.clone()).await, T)
        .await
        .unwrap();
    let ds = items(&db, t).await;
    for (d, state) in ds.iter().zip(states) {
        sqlx::query(
            r#"UPDATE email
            SET state = ?, generation_queued_at = ?, planned_at = ?,
                from_address = 'me@example.com', to_address = 'm@example.com',
                message_id = ?, sent_at = ?, html = '<p>追问</p>', text = '追问',
                error = ?
            WHERE id = ?"#,
        )
        .bind(state)
        .bind((state == "generating").then_some(T))
        .bind(T)
        .bind(format!("<follow-{}@example.com>", d.id))
        .bind(T)
        .bind(matches!(state, "generation_failed" | "failed" | "maybe_sent").then_some("示例失败"))
        .bind(d.id)
        .execute(&db)
        .await
        .unwrap();
    }
    let claim = queue::next(&db).await.unwrap().unwrap();
    for &m in &ids[..states.len()] {
        store.deliver(
            "INBOX",
            T + 1,
            &format!(
                "From: m{m}@example.com\n\
                Message-ID: <reply-{m}@example.com>\n\
                Subject: 回复\n\n\
                收到，欢迎联系。"
            ),
        );
    }
    let automatic = *ids.last().unwrap();
    store.deliver(
        "INBOX",
        T + 1,
        &format!(
            "From: m{automatic}@example.com\n\
            Message-ID: <auto@example.com>\n\
            Auto-Submitted: auto-replied\n\
            Subject: 自动回复\n\n\
            暂时不在办公室。"
        ),
    );
    receive::round(&db, &net, T + 2).await.unwrap();
    for d in items(&db, t).await {
        if d.mentor_id == automatic {
            assert_eq!(d.state, DraftState::Review);
            continue;
        }
        assert_eq!(d.cancel_reason.as_deref(), Some("导师已回信"));
        assert!(matches!(
            d.state,
            DraftState::Canceled | DraftState::Sending | DraftState::MaybeSent
        ));
    }
    assert!(
        !queue::complete(
            &mut db.acquire().await.unwrap(),
            claim,
            "迟到主题",
            "迟到正文",
            "fixed",
            None,
            T + 3
        )
        .await
        .unwrap()
    );
    assert_eq!(
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 3)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert!(inbox.lock().unwrap().is_empty());
    let maybe = ds.iter().find(|d| d.mentor_id == ids[6]).unwrap();
    assert!(!email::retry(&db, maybe.id, T + 3).await.unwrap());
    assert!(email::confirm_unsent(&db, maybe.id, T + 3).await.unwrap());
    assert_eq!(
        draft(
            &db,
            &TaskEmail {
                task_id: t,
                id: maybe.id
            }
        )
        .await
        .unwrap()
        .unwrap()
        .state,
        DraftState::Canceled
    );
    let sending = ds.iter().find(|d| d.mentor_id == ids[5]).unwrap();
    email::sent(&mut db.acquire().await.unwrap(), sending.id, T + 3)
        .await
        .unwrap();
    assert!(
        draft(
            &db,
            &TaskEmail {
                task_id: t,
                id: sending.id
            }
        )
        .await
        .unwrap()
        .unwrap()
        .cancel_reason
        .is_none()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resend_bounced_follow_up_respects_received_replies_in_both_orders(db: SqlitePool) {
    let writes = db::Writes::default();
    let pki = fakemail::pki();
    let (imap, store) = fakemail::imap(&pki, false).await;
    let (smtp, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        &db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "我".into(),
            provider: "custom".into(),
            smtp: server(smtp),
            imap: server(imap),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    receive::round(&db, &net, T).await.unwrap();
    for (n, reply_first, automatic) in [(1, true, false), (2, false, false), (3, true, true)] {
        let m = mentor(&db, n, true).await;
        previous(&db, m, false, T - 86_400_000).await;
        let t = create(&db, &follow_form(&db, vec![m]).await, T)
            .await
            .unwrap();
        let d = items(&db, t).await.remove(0);
        approve(
            &db,
            &TaskApprovals {
                task_id: t,
                emails: vec![approval(&d)],
            },
            T,
        )
        .await
        .unwrap();
        sqlx::query(
            "UPDATE email SET state = 'bounced', mailbox_id = 1, from_address = 'me@example.com',
               to_address = ?, message_id = ?, sent_at = ?, error = '550 address unknown'
             WHERE id = ?",
        )
        .bind(format!("m{n}@example.com"))
        .bind(format!("<bounced-follow-{n}@example.com>"))
        .bind(T)
        .bind(d.id)
        .execute(&db)
        .await
        .unwrap();
        if !reply_first {
            assert_eq!(
                email::resend(&db, d.id, T + 1).await.unwrap(),
                email::Approved::Yes
            );
        }
        let auto_header = if automatic {
            "Auto-Submitted: auto-replied\n"
        } else {
            ""
        };
        store.deliver("INBOX", T + 2, &format!(
            "From: m{n}@example.com\nMessage-ID: <reply-{n}@example.com>\n{auto_header}Subject: 回复\n\n收到，欢迎进一步交流。"
        ));
        receive::round(&db, &net, T + 3).await.unwrap();
        if reply_first {
            let result = email::resend(&db, d.id, T + 4).await.unwrap();
            if automatic {
                assert_eq!(result, email::Approved::Yes);
            } else {
                assert_eq!(result, email::Approved::FollowUpReplied);
            }
        }
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
            .await
            .unwrap();
        let state: Option<String> =
            sqlx::query_scalar("SELECT state FROM email WHERE resend_of = ?")
                .bind(d.id)
                .fetch_optional(&db)
                .await
                .unwrap();
        assert_eq!(
            state.as_deref(),
            if automatic {
                Some("sent")
            } else if reply_first {
                None
            } else {
                Some("canceled")
            }
        );
        assert_eq!(inbox.lock().unwrap().len(), usize::from(automatic));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resend_bounced_follow_up_respects_parent_bounces_in_both_orders(db: SqlitePool) {
    let writes = db::Writes::default();
    let pki = fakemail::pki();
    let (imap, store) = fakemail::imap(&pki, false).await;
    let (smtp, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        &db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "我".into(),
            provider: "custom".into(),
            smtp: server(smtp),
            imap: server(imap),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    receive::round(&db, &net, T).await.unwrap();
    let mut n = 0;
    for (external, copy) in [(false, false), (false, true), (true, false)] {
        for bounce_first in [false, true] {
            n += 1;
            let m = mentor(&db, n, true).await;
            let parent = previous(&db, m, external, T - 86_400_000).await;
            let reply_to = if copy {
                let copy_id = format!("<parent-copy-{n}@example.com>");
                sqlx::query("UPDATE email SET copy_message_id = ? WHERE message_id = ?")
                    .bind(&copy_id)
                    .bind(&parent)
                    .execute(&db)
                    .await
                    .unwrap();
                copy_id
            } else {
                parent.clone()
            };
            let t = create(&db, &follow_form(&db, vec![m]).await, T)
                .await
                .unwrap();
            let d = items(&db, t).await.remove(0);
            approve(
                &db,
                &TaskApprovals {
                    task_id: t,
                    emails: vec![approval(&d)],
                },
                T,
            )
            .await
            .unwrap();
            sqlx::query(
                "UPDATE email SET state = 'bounced', mailbox_id = 1, from_address = 'me@example.com',
                   to_address = ?, message_id = ?, sent_at = ?, error = '550 address unknown'
                 WHERE id = ?",
            )
            .bind(format!("m{n}@example.com"))
            .bind(format!("<bounced-follow-{n}@example.com>"))
            .bind(T)
            .bind(d.id)
            .execute(&db)
            .await
            .unwrap();
            let fixed_parent: String =
                sqlx::query_scalar("SELECT reply_to FROM email WHERE id = ?")
                    .bind(d.id)
                    .fetch_one(&db)
                    .await
                    .unwrap();
            assert_eq!(fixed_parent, reply_to);
            if !bounce_first {
                assert_eq!(
                    email::resend(&db, d.id, T + 1).await.unwrap(),
                    email::Approved::Yes
                );
            }
            let recipient = if external {
                format!("m{n}@example.com")
            } else {
                "mentor@example.com".into()
            };
            store.deliver("INBOX", T + 2, &format!(
                "From: MAILER-DAEMON@example.com\nMessage-ID: <parent-dsn-{n}@example.com>\nContent-Type: multipart/report; report-type=delivery-status; boundary=dsn\n\n--dsn\nContent-Type: text/plain\n\nDelivery failed\n--dsn\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; example.com\n\nFinal-Recipient: rfc822; {recipient}\nAction: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 user unknown\n\n--dsn\nContent-Type: message/rfc822\n\nFrom: me@example.com\nTo: {recipient}\nMessage-ID: {parent}\nSubject: 原信\n\n之前的信\n--dsn--\n"
            ));
            receive::round(&db, &net, T + 3).await.unwrap();
            let bounced: bool = if external {
                sqlx::query_scalar(
                    "SELECT mm.bounce_reason IS NOT NULL FROM message_mentor mm
                     JOIN message m ON m.id = mm.message_id
                     WHERE mm.mentor_id = ? AND m.message_id = ?",
                )
                .bind(m)
                .bind(&parent)
                .fetch_one(&db)
                .await
                .unwrap()
            } else {
                sqlx::query_scalar("SELECT state = 'bounced' FROM email WHERE message_id = ?")
                    .bind(&parent)
                    .fetch_one(&db)
                    .await
                    .unwrap()
            };
            assert!(bounced);
            if bounce_first {
                assert_eq!(
                    email::resend(&db, d.id, T + 4).await.unwrap(),
                    email::Approved::FollowUpParentBounced
                );
            }
            send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
                .await
                .unwrap();
            let resent: Option<(String, Option<String>)> =
                sqlx::query_as("SELECT state, cancel_reason FROM email WHERE resend_of = ?")
                    .bind(d.id)
                    .fetch_optional(&db)
                    .await
                    .unwrap();
            assert_eq!(
                resent,
                if bounce_first {
                    None
                } else {
                    Some(("canceled".into(), Some("上一封被退回".into())))
                }
            );
            assert!(inbox.lock().unwrap().is_empty());
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn missing_parent_mailbox_blocks_follow_up_in_plan_and_sender(db: SqlitePool) {
    let writes = db::Writes::default();
    for address in ["me@example.com", "backup@example.com"] {
        sqlx::query(
            "INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port,
               smtp_security, imap_host, imap_port, imap_security, auth, secret, daily_limit,
               min_interval_s, rotation_order, created_at)
             VALUES (?, '我', 'custom', 'localhost', 465, 'tls', 'localhost', 993, 'tls',
               'password', 'test', 50, 0, 1, 0)",
        )
        .bind(address)
        .execute(&db)
        .await
        .unwrap();
    }
    let mut ids = vec![];
    for (n, external) in [(1, false), (2, true)] {
        let m = mentor(&db, n, true).await;
        previous(&db, m, external, T - 86_400_000).await;
        ids.push(m);
    }
    let t = create(&db, &follow_form(&db, ids).await, T).await.unwrap();
    let ds = items(&db, t).await;
    approve(
        &db,
        &TaskApprovals {
            task_id: t,
            emails: ds.iter().map(approval).collect(),
        },
        T,
    )
    .await
    .unwrap();
    mailbox::delete(&db, 1).await.unwrap();
    let plan = send::plan::list(
        &db,
        &send::plan::PlanQuery {
            column: send::plan::PlanColumn::Upcoming,
            page: std::num::NonZeroU32::MIN,
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(plan.total, 2);
    for e in plan.emails {
        assert!(
            matches!(e.waiting, Some(send::plan::Waiting::NoMailbox)),
            "{:?}",
            e.waiting
        );
    }
    let net = net::Net::new(vec![]).unwrap();
    for _ in ds {
        assert_eq!(
            send::step(&db, &writes, &net, std::path::Path::new("unused"), || T)
                .await
                .unwrap(),
            send::Next::Now
        );
    }
    for d in items(&db, t).await {
        assert_eq!(d.state, DraftState::Failed);
        assert!(d.error.unwrap().contains("没有能发信的邮箱"));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn receiving_a_bounce_cancels_only_follow_ups_of_that_parent(db: SqlitePool) {
    let pki = fakemail::pki();
    let (port, store) = fakemail::imap(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        &db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "我".into(),
            provider: "custom".into(),
            smtp: server(465),
            imap: server(port),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    receive::round(&db, &net, T).await.unwrap();
    for external in [false, true] {
        let m = mentor(&db, if external { 2 } else { 1 }, true).await;
        let parent = previous(&db, m, external, T - 1000).await;
        let t = create(&db, &follow_form(&db, vec![m]).await, T)
            .await
            .unwrap();
        let d = items(&db, t).await.remove(0);
        store.deliver(
            "INBOX",
            T + 1,
            &format!(
                "From: MAILER-DAEMON@example.com\n\
                Message-ID: <bounce-{m}@example.com>\n\
                Content-Type: multipart/report; report-type=delivery-status; boundary=b\n\n\
                --b\n\
                Content-Type: message/delivery-status\n\n\
                Reporting-MTA: dns; example.com\n\n\
                Action: failed\n\
                Status: 5.1.1\n\
                Diagnostic-Code: smtp; 550 address unknown\n\
                --b\n\
                Content-Type: text/rfc822-headers\n\n\
                Message-ID: {parent}\n\
                --b--\n"
            ),
        );
        receive::round(&db, &net, T + 2).await.unwrap();
        let d = draft(
            &db,
            &TaskEmail {
                task_id: t,
                id: d.id,
            },
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(d.state, DraftState::Canceled);
        assert_eq!(d.cancel_reason.as_deref(), Some("上一封被退回"));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn approval_reserves_in_request_order_and_withdraw_releases_capacity(db: SqlitePool) {
    use chrono::{TimeZone, Utc};
    use email::schedule::{Schedule, Window};
    let base = (T / 86_400_000 + 1) * 86_400_000;
    let dates = (0..3)
        .map(|n| {
            Utc.timestamp_millis_opt(base + n * 86_400_000)
                .unwrap()
                .format("%Y-%m-%d")
                .to_string()
        })
        .collect::<Vec<_>>();
    let mut ids = vec![];
    for n in 1..=3 {
        ids.push(mentor(&db, n, true).await);
    }
    let mut f = form(&db, ids).await;
    f.schedule = Schedule::Window(Window {
        dates: dates.clone(),
        start: "09:00".into(),
        end: "11:00".into(),
        per_window: 1,
    });
    let task = create(&db, &f, T).await.unwrap();
    let ds = items(&db, task).await;
    let req = TaskApprovals {
        task_id: task,
        emails: vec![approval(&ds[2]), approval(&ds[0])],
    };
    assert_eq!(
        approve_in_zone(&db, &req, T, &Utc, &mut |_| 0)
            .await
            .unwrap(),
        2
    );
    let get = async |id| {
        draft(&db, &TaskEmail { task_id: task, id })
            .await
            .unwrap()
            .unwrap()
    };
    assert_eq!(get(ds[2].id).await.planned_at, Some(base + 9 * 3_600_000));
    assert_eq!(
        get(ds[0].id).await.planned_at,
        Some(base + 86_400_000 + 9 * 3_600_000)
    );
    email::withdraw(&db, ds[2].id, T).await.unwrap();
    assert_eq!(get(ds[2].id).await.planned_at, None);
    assert_eq!(get(ds[2].id).await.latest_at, None);
    approve_in_zone(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: vec![approval(&ds[1])],
        },
        T,
        &Utc,
        &mut |_| 0,
    )
    .await
    .unwrap();
    assert_eq!(get(ds[1].id).await.planned_at, Some(base + 9 * 3_600_000));
    approve_in_zone(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: vec![approval(&ds[2])],
        },
        T,
        &Utc,
        &mut |_| 0,
    )
    .await
    .unwrap();
    // Changing the calendar preserves approval order, including an approval made again after withdrawal.
    let changed = change_schedule(
        &db,
        &ScheduleEdit {
            id: task,
            schedule: Schedule::Window(Window {
                dates: vec![dates[2].clone()],
                start: "09:00".into(),
                end: "11:00".into(),
                per_window: 1,
            }),
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(changed, 3);
    assert!(get(ds[0].id).await.planned_at.is_some());
    for id in [ds[1].id, ds[2].id] {
        let d = get(id).await;
        assert_eq!(d.state, DraftState::Approved);
        assert_eq!(d.planned_at, None);
    }
    // Approving after all selected windows expired still freezes the content and enters pending.
    email::withdraw(&db, ds[2].id, T).await.unwrap();
    approve_in_zone(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: vec![approval(&ds[2])],
        },
        base + 4 * 86_400_000,
        &Utc,
        &mut |_| 0,
    )
    .await
    .unwrap();
    assert_eq!(get(ds[2].id).await.state, DraftState::Approved);
    assert_eq!(get(ds[2].id).await.planned_at, None);
    change_schedule(
        &db,
        &ScheduleEdit {
            id: task,
            schedule: Schedule::Now,
        },
        T,
    )
    .await
    .unwrap();
    for d in items(&db, task).await {
        assert_eq!(d.planned_at, Some(T));
        assert_eq!(d.latest_at, None);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn eligibility_is_explicit_and_contacted_mentors_remain_eligible(db: SqlitePool) {
    let mut ids = vec![];
    for n in 1..=6 {
        ids.push(mentor(&db, n, n != 1).await);
    }
    let f = form(&db, vec![ids[1]]).await;
    create(&db, &f, T).await.unwrap();
    email::save_first_draft(&db, ids[2], "申请", "老师好", &[], T)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[ids[3]], T).await.unwrap();
    let a = email::Approval {
        subject: "申请",
        body_md: "您好",
        html: "<p>您好</p>",
        text: "您好",
        attachments: &[],
    };
    email::approve_first(&db, ids[4], &a, None, T)
        .await
        .unwrap();
    sqlx::query(
        r#"UPDATE email
        SET state = 'sent', from_address = 'me@example.com',
            to_address = 'm5@example.com', message_id = '<sent@example.com>',
            sent_at = ?
        WHERE mentor_id = ?"#,
    )
    .bind(T)
    .bind(ids[4])
    .execute(&db)
    .await
    .unwrap();
    let f = form(&db, ids.clone()).await;
    let p = plan(&db, &f).await.unwrap();
    for id in &ids[..4] {
        assert!(
            p.mentors
                .iter()
                .find(|m| m.id == *id)
                .unwrap()
                .reason
                .is_some()
        );
    }
    let contacted = p.mentors.iter().find(|m| m.id == ids[4]).unwrap();
    assert_eq!(contacted.contact_count, 1);
    assert_eq!(contacted.last_contact_at, Some(T));
    assert!(contacted.reason.is_none());
    assert!(create(&db, &f, T).await.is_err());
    assert_eq!(
        list(
            &db,
            &TaskQuery {
                page: std::num::NonZeroU32::MIN
            }
        )
        .await
        .unwrap()
        .total,
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn template_snapshot_and_unique_members_and_reapply_only_one(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let f = form(&db, vec![a, b, a]).await;
    let task = create(&db, &f, T).await.unwrap();
    let ds = items(&db, task).await;
    assert_eq!(ds.len(), 2);
    assert!(ds.iter().all(|d| d.state == DraftState::Review));
    assert!(ds[0].body_md.starts_with("导师1老师："));
    template::update(
        &db,
        f.template_id.unwrap(),
        &template::TemplateForm {
            name: "改过".into(),
            subject: "新模板".into(),
            body_md: "另一份模板".into(),
        }
        .check()
        .unwrap(),
        T + 1,
    )
    .await
    .unwrap();
    for d in &ds {
        save(
            &db,
            &DraftEdit {
                task_id: task,
                id: d.id,
                subject: "人工主题".into(),
                body_md: "人工稿".into(),
                attachments: vec![],
            },
            T + 2,
        )
        .await
        .unwrap();
    }
    reapply(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T + 3,
    )
    .await
    .unwrap();
    let ds = items(&db, task).await;
    assert_eq!(ds[0].subject, "给导师1");
    assert_eq!(ds[1].subject, "人工主题");
    assert!(
        sqlx::query(
            r#"INSERT INTO email (task_id, mentor_id, kind, state, created_at, updated_at)
            VALUES (?, ?, 'first', 'review', 0, 0)"#
        )
        .bind(task)
        .bind(a)
        .execute(&db)
        .await
        .is_err()
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn group_approval_only_uses_listed_current_review_drafts(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let c = mentor(&db, 3, true).await;
    let task = create(&db, &form(&db, vec![a, b, c]).await, T)
        .await
        .unwrap();
    let ds = items(&db, task).await;
    let request = TaskApprovals {
        task_id: task,
        emails: vec![approval(&ds[0])],
    };
    assert_eq!(approve(&db, &request, T).await.unwrap(), 1);
    assert_eq!(approve(&db, &request, T).await.unwrap(), 0);
    let request = TaskApprovals {
        task_id: task,
        emails: vec![approval(&ds[0]), approval(&ds[1])],
    };
    assert_eq!(approve(&db, &request, T).await.unwrap(), 1);
    let ds = items(&db, task).await;
    assert_eq!(ds[2].state, DraftState::Review);
    let stale = approval(&ds[2]);
    save(
        &db,
        &DraftEdit {
            task_id: task,
            id: ds[2].id,
            subject: "新主题".into(),
            body_md: "新人工稿".into(),
            attachments: vec![],
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        approve(
            &db,
            &TaskApprovals {
                task_id: task,
                emails: vec![stale]
            },
            T
        )
        .await
        .is_err()
    );
    assert_eq!(items(&db, task).await[2].body_md, "新人工稿");
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pause_blocks_both_selection_and_final_handover_then_resume_sends_once(db: SqlitePool) {
    let writes = db::Writes::default();
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    let mailbox = mailbox::MailboxForm {
        address: fakemail::ADDRESS.into(),
        sender_name: "我".into(),
        provider: "custom".into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 50,
        min_interval_s: 60,
    };
    mailbox::create(&db, &mailbox.check().unwrap(), fakemail::SECRET, T)
        .await
        .unwrap();
    let a = mentor(&db, 1, true).await;
    let task = create(&db, &form(&db, vec![a]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    pause(&db, task, T).await.unwrap();
    assert_eq!(
        approve(
            &db,
            &TaskApprovals {
                task_id: task,
                emails: vec![approval(&ds[0])]
            },
            T
        )
        .await
        .unwrap(),
        1
    );
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    assert_eq!(
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert!(
        email::start_sending(
            &mut db.acquire().await.unwrap(),
            ds[0].id,
            1,
            fakemail::ADDRESS,
            "<test@example.com>",
            T
        )
        .await
        .unwrap()
        .is_none()
    );
    resume(&db, task).await.unwrap();
    assert_eq!(
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Now
    );
    assert_eq!(
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_expiry_restore_must_respect_stopping_during_envelope(db: SqlitePool) {
    use std::sync::{
        Arc,
        atomic::{AtomicI64, Ordering},
    };
    use tokio::sync::Notify;
    let clock = Arc::new(AtomicI64::new(T));
    let reached = Arc::new(Notify::new());
    let release = Arc::new(Notify::new());
    let signal = reached.clone();
    let proceed = release.clone();
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp_with_hooks(
        &pki,
        false,
        move || {
            let signal = signal.clone();
            let proceed = proceed.clone();
            async move {
                signal.notify_one();
                proceed.notified().await;
            }
        },
        || std::future::ready(()),
    )
    .await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    let mailbox = mailbox::MailboxForm {
        address: fakemail::ADDRESS.into(),
        sender_name: "我".into(),
        provider: "custom".into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 50,
        min_interval_s: 60,
    };
    mailbox::create(&db, &mailbox.check().unwrap(), fakemail::SECRET, T)
        .await
        .unwrap();
    let mentor = mentor(&db, 1, true).await;
    let task = create(&db, &form(&db, vec![mentor]).await, T)
        .await
        .unwrap();
    let draft = items(&db, task).await.remove(0);
    approve(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: vec![approval(&draft)],
        },
        T,
    )
    .await
    .unwrap();
    let id = draft.id;
    email::schedule::reschedule(&db, id, T, T).await.unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    let nowhere = std::path::Path::new("unused");
    let writes = db::Writes::default();
    let sending = send::step(&db, &writes, &net, nowhere, || clock.load(Ordering::SeqCst));
    let stopping = async {
        reached.notified().await;
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT state FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap(),
            "sending"
        );
        stop(&db, task, T).await.unwrap();
        clock.store(T + 3_600_000, Ordering::SeqCst);
        release.notify_one();
    };
    let (result, ()) = tokio::time::timeout(std::time::Duration::from_secs(60), async {
        tokio::join!(sending, stopping)
    })
    .await
    .expect("sending must finish");
    result.unwrap();
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
        ("canceled".into(), Some("任务停止".into()), None, None)
    );
    assert_eq!(
        send::step(&db, &writes, &net, nowhere, || T + 86_400_000)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_task_time_defers_handover_and_retry_reserves_again(db: SqlitePool) {
    let writes = db::Writes::default();
    use chrono::{Local, TimeZone};
    use email::schedule::{Schedule, Window};
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    let b = mailbox::MailboxForm {
        address: fakemail::ADDRESS.into(),
        sender_name: "我".into(),
        provider: "custom".into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 50,
        min_interval_s: 60,
    };
    mailbox::create(&db, &b.check().unwrap(), fakemail::SECRET, T)
        .await
        .unwrap();
    let m = mentor(&db, 1, true).await;
    let task = create(&db, &form(&db, vec![m]).await, T).await.unwrap();
    let d = items(&db, task).await.remove(0);
    approve(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: vec![approval(&d)],
        },
        T,
    )
    .await
    .unwrap();
    let date = Local
        .timestamp_millis_opt(T + 86_400_000)
        .unwrap()
        .date_naive();
    change_schedule(
        &db,
        &ScheduleEdit {
            id: task,
            schedule: Schedule::Window(Window {
                dates: vec![date.to_string()],
                start: "09:00".into(),
                end: "11:00".into(),
                per_window: 1,
            }),
        },
        T,
    )
    .await
    .unwrap();
    let new = items(&db, task).await.remove(0).planned_at.unwrap();
    assert!(new > T);
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    let nowhere = std::path::Path::new("unused");
    assert_eq!(
        send::step(&db, &writes, &net, nowhere, || T).await.unwrap(),
        send::Next::At(new)
    );
    assert!(inbox.lock().unwrap().is_empty());
    email::failed(&mut db.acquire().await.unwrap(), d.id, "连接失败", T)
        .await
        .unwrap();
    email::retry(&db, d.id, T).await.unwrap();
    let retried = items(&db, task).await.remove(0);
    assert!(retried.planned_at.unwrap() > T);
    assert!(retried.latest_at.unwrap() > retried.planned_at.unwrap());
    let at = retried.planned_at.unwrap();
    assert_eq!(
        send::step(&db, &writes, &net, nowhere, || at)
            .await
            .unwrap(),
        send::Next::Now
    );
    assert_eq!(
        send::step(&db, &writes, &net, nowhere, || at)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stop_remove_trash_and_delete_preserve_only_delivery_evidence(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(
        items(&db, task).await[0].cancel_reason.as_deref(),
        Some("移出任务")
    );
    stop(&db, task, T).await.unwrap();
    resume(&db, task).await.unwrap();
    assert_eq!(
        get(&db, task).await.unwrap().unwrap().state,
        TaskState::Stopped
    );
    assert_eq!(
        items(&db, task).await[1].cancel_reason.as_deref(),
        Some("任务停止")
    );
    delete(&db, task).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM email")
            .fetch_one(&db)
            .await
            .unwrap(),
        0
    );
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    approve(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: ds.iter().map(approval).collect(),
        },
        T,
    )
    .await
    .unwrap();
    // No mailbox row is required for an email's durable addresses.
    sqlx::query(
        r#"UPDATE email
        SET state = 'sending', from_address = 'me@example.com',
            to_address = 'm1@example.com', message_id = '<out@example.com>',
            sent_at = ?
        WHERE id = ?"#,
    )
    .bind(T)
    .bind(ds[0].id)
    .execute(&db)
    .await
    .unwrap();
    assert!(delete(&db, task).await.is_err());
    assert_eq!(items(&db, task).await.len(), 2);
    email::sent(&mut db.acquire().await.unwrap(), ds[0].id, T)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[b], T).await.unwrap();
    assert_eq!(items(&db, task).await[1].state, DraftState::Canceled);
    delete(&db, task).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM email WHERE task_id IS NULL AND state='sent'"
        )
        .fetch_one(&db)
        .await
        .unwrap(),
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stale_generation_results_cannot_revive_removed_or_regenerated_items(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let mut f = form(&db, vec![a]).await;
    let config = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://localhost:1/v1', 'm', '', 1, 0)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    f.writer = Writer::Ai;
    f.model_config_id = Some(config);
    let task = create(&db, &f, T).await.unwrap();
    let old = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    let new = queue::next(&db).await.unwrap().unwrap();
    assert_ne!(old, new);
    assert!(
        !queue::complete(
            &mut db.acquire().await.unwrap(),
            old,
            "旧",
            "旧稿",
            "m",
            None,
            T
        )
        .await
        .unwrap()
    );
    assert!(
        !generation::requeue(&db, old, Some(("旧错误", None)), T)
            .await
            .unwrap()
    );
    assert!(
        queue::complete(
            &mut db.acquire().await.unwrap(),
            new,
            "新",
            "新稿",
            "m",
            None,
            T
        )
        .await
        .unwrap()
    );
    regenerate(&db, task, Some(new.id), false, T).await.unwrap();
    let next = queue::next(&db).await.unwrap().unwrap();
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: new.id,
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        !queue::complete(
            &mut db.acquire().await.unwrap(),
            next,
            "迟到",
            "迟到稿",
            "m",
            None,
            T
        )
        .await
        .unwrap()
    );
    assert_eq!(
        get(&db, task).await.unwrap().unwrap().state,
        TaskState::Completed
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn task_name_checks_and_problem_counts_agree(db: SqlitePool) {
    sqlx::query("UPDATE profile SET name='林晓'")
        .execute(&db)
        .await
        .unwrap();
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    let edit = DraftEdit {
        task_id: task,
        id: ds[0].id,
        subject: "致导师2".into(),
        body_md: "导师1老师：您好。【请补充：时间】".into(),
        attachments: vec![],
    };
    save(&db, &edit, T).await.unwrap();
    let p = check(&db, &edit, T).await.unwrap();
    assert!(
        p.iter()
            .any(|p| matches!(p,check::Problem::OtherMentor{name} if name=="导师2"))
    );
    assert!(
        p.iter().any(
            |p| matches!(p, check::Problem::Placeholder { name } if name == "【请补充：时间】")
        )
    );
    let page = drafts(
        &db,
        &DraftQuery {
            task_id: task,
            filter: DraftFilter::Problems,
            page: std::num::NonZeroU32::MIN,
        },
        T,
        |_| None,
    )
    .await
    .unwrap();
    assert_eq!(page.total, 1);
    assert_eq!(page.drafts[0].problems, p.len());
    sqlx::query(
        r#"INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port,
            smtp_security, imap_host, imap_port, imap_security, auth, secret,
            daily_limit, min_interval_s, rotation_order, created_at)
        VALUES ('me@example.com', '我', 'custom', 'localhost', 465, 'tls', 'localhost',
            993, 'tls', 'password', 'test', 50, 0, 1, 0)"#,
    )
    .execute(&db)
    .await
    .unwrap();
    for (kind, n) in [(Kind::First, 11), (Kind::FollowUp, 21)] {
        let a = mentor(&db, n, true).await;
        let b = mentor(&db, n + 1, true).await;
        previous(&db, a, false, T - 1000).await;
        previous(&db, b, false, T - 1000).await;
        let mut form = form(&db, vec![a, b]).await;
        form.kind = kind;
        let task = create(&db, &form, T).await.unwrap();
        let d = items(&db, task).await.remove(0);
        let result = email::fact_check::FactCheck::Completed {
            findings: ["申请", "3 篇论文"]
                .into_iter()
                .map(|quote| email::fact_check::Finding {
                    quote: quote.into(),
                    reason: "没有材料依据".into(),
                })
                .collect(),
        };
        assert!(
            email::fact_check::save(&db, d.id, 0, &result, T)
                .await
                .unwrap()
        );
        let mut edit = DraftEdit {
            task_id: task,
            id: d.id,
            subject: "申请【请补充：主题】".into(),
            body_md: format!(
                "导师{n}老师：导师{}老师提到附件。【请补充：时间】我发表了 3 篇论文。",
                n + 1
            ),
            attachments: vec![],
        };
        for changed in [false, true] {
            if changed {
                edit.subject = "新的主题".into();
                edit.body_md = format!("导师{n}老师：您好。疑问已修改。");
            }
            let editor = check(&db, &edit, T).await.unwrap();
            assert_eq!(
                editor
                    .iter()
                    .filter(|p| matches!(p, check::Problem::UnsupportedClaim { .. }))
                    .count(),
                if changed {
                    usize::from(kind == Kind::FollowUp)
                } else {
                    2
                }
            );
            assert_eq!(
                editor
                    .iter()
                    .any(|p| matches!(p, check::Problem::RecentlyContacted { .. })),
                kind == Kind::First
            );
            assert_eq!(editor.iter().any(|p| matches!(p, check::Problem::Placeholder { name } if name == "【请补充：主题】")), !changed && kind == Kind::First);
            if changed {
                let saved = pending(&db, task, T, |_| None).await.unwrap();
                assert!(saved.iter().find(|r| r.draft.id == d.id).unwrap().problems > editor.len());
            }
            save(&db, &edit, T).await.unwrap();
            let list = drafts(
                &db,
                &DraftQuery {
                    task_id: task,
                    filter: DraftFilter::All,
                    page: std::num::NonZeroU32::MIN,
                },
                T,
                |_| None,
            )
            .await
            .unwrap();
            assert_eq!(
                list.drafts.iter().find(|r| r.id == d.id).unwrap().problems,
                editor.len()
            );
            let bulk = pending(&db, task, T, |_| None).await.unwrap();
            assert_eq!(
                bulk.iter().find(|r| r.draft.id == d.id).unwrap().problems,
                editor.len()
            );
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regeneration_is_local_and_deleted_model_uses_the_new_default(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let old = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://localhost:1/v1', 'old', '', 1, 0)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    let new = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://localhost:1/v1', 'new', '', 1, 1)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    let mut f = form(&db, vec![a, b]).await;
    f.writer = Writer::Ai;
    f.model_config_id = Some(old);
    let task = create(&db, &f, T).await.unwrap();
    for _ in 0..2 {
        let c = queue::next(&db).await.unwrap().unwrap();
        queue::complete(
            &mut db.acquire().await.unwrap(),
            c,
            "原主题",
            "人工稿",
            "old",
            None,
            T,
        )
        .await
        .unwrap();
    }
    let ds = items(&db, task).await;
    assert_eq!(
        regenerate(&db, task, Some(ds[0].id), false, T)
            .await
            .unwrap(),
        1
    );
    let after = items(&db, task).await;
    assert_eq!(after[0].state, DraftState::Generating);
    assert_eq!(after[1].state, DraftState::Review);
    assert_eq!(after[1].body_md, "人工稿");
    model::delete(&db, old).await.unwrap();
    assert_eq!(get(&db, task).await.unwrap().unwrap().model_config_id, None);
    assert_eq!(model::selected(&db, None).await.unwrap().unwrap().id, new);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn uploaded_material_is_added_only_to_the_current_task_draft_without_overwriting_text(
    db: SqlitePool,
) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    let material = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO material (file_name, size, for_matching, for_writing,
            attach_by_default, created_at)
        VALUES ('新材料.pdf', 10, 0, 0, 0, 0)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    save(
        &db,
        &DraftEdit {
            task_id: task,
            id: ds[0].id,
            subject: "新主题".into(),
            body_md: "上传时修改后的正文".into(),
            attachments: vec![],
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        queue::attach_material(&db, task, ds[0].id, material)
            .await
            .unwrap()
    );
    let after = items(&db, task).await;
    assert_eq!(after[0].body_md, "上传时修改后的正文");
    assert_eq!(after[0].attachments, [material]);
    assert!(after[1].attachments.is_empty());
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        !queue::attach_material(&db, task, ds[0].id, material)
            .await
            .unwrap()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn ended_today_window_must_not_pass_capacity(db: SqlitePool) {
    use chrono::{Local, TimeZone};
    use email::schedule::{Schedule, Window};
    let now = Local
        .with_ymd_and_hms(2026, 10, 25, 12, 0, 0)
        .unwrap()
        .timestamp_millis();
    let id = mentor(&db, 1, true).await;
    let mut f = form(&db, vec![id]).await;
    f.schedule = Schedule::Window(Window {
        dates: vec!["2026-10-25".into()],
        start: "09:00".into(),
        end: "11:00".into(),
        per_window: 1,
    });
    assert!(matches!(
        create(&db, &f, now).await,
        Err(Error::Invalid(message)) if message.contains("容量不足")
    ));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn ended_and_future_windows_must_have_enough_remaining_capacity(db: SqlitePool) {
    use chrono::{Local, TimeZone};
    use email::schedule::{Schedule, Window};
    let now = Local
        .with_ymd_and_hms(2026, 10, 25, 12, 0, 0)
        .unwrap()
        .timestamp_millis();
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let mut f = form(&db, vec![a, b]).await;
    let mut w = Window {
        dates: vec!["2026-10-25".into(), "2026-10-26".into()],
        start: "09:00".into(),
        end: "11:00".into(),
        per_window: 1,
    };
    f.schedule = Schedule::Window(w.clone());
    assert!(matches!(
        create(&db, &f, now).await,
        Err(Error::Invalid(message)) if message.contains("还差 1 封")
    ));
    w.per_window = 2;
    f.schedule = Schedule::Window(w);
    assert!(create(&db, &f, now).await.is_ok());
}

async fn detail_mail(db: &SqlitePool) -> (net::Net, fakemail::Store, fakemail::Inbox) {
    let pki = fakemail::pki();
    let (imap, store) = fakemail::imap(&pki, false).await;
    let (smtp, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    mailbox::create(
        db,
        &mailbox::MailboxForm {
            address: fakemail::ADDRESS.into(),
            sender_name: "林晓".into(),
            provider: "custom".into(),
            smtp: server(smtp),
            imap: server(imap),
            daily_limit: 50,
            min_interval_s: 0,
        }
        .check()
        .unwrap(),
        fakemail::SECRET,
        T,
    )
    .await
    .unwrap();
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    receive::round(db, &net, T).await.unwrap();
    (net, store, inbox)
}
fn detail_approval() -> email::Approval<'static> {
    email::Approval {
        subject: "ignored",
        body_md: "老师您好，想请问您是否方便查看之前的信。",
        html: "<p>老师您好，想请问您是否方便查看之前的信。</p>",
        text: "老师您好，想请问您是否方便查看之前的信。",
        attachments: &[],
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_first_and_follow_up_drafts_keeps_only_the_retained_mentors(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    for (n, keep_follow_up) in [(201, false), (203, true)] {
        let keep = mentor(&db, n, true).await;
        let other = mentor(&db, n + 1, true).await;
        let (first, follow) = if keep_follow_up {
            (other, keep)
        } else {
            (keep, other)
        };
        previous(&db, follow, false, T - 1000).await;
        email::save_first_draft(&db, first, "新的一封", "新信正文", &[], T)
            .await
            .unwrap()
            .unwrap();
        let d = email::follow_up::create(&db, follow, T).await.unwrap();
        let preview = mentor::merge::preview(&db, keep, other).await.unwrap();
        assert!(
            matches!(
                preview.discarded_drafts.as_slice(),
                [email::DraftKind::First]
            ) && keep_follow_up
                || matches!(
                    preview.discarded_drafts.as_slice(),
                    [email::DraftKind::FollowUp]
                ) && !keep_follow_up
        );
        mentor::merge::merge(&db, keep, other, T + 1).await.unwrap();
        let pending: Vec<(i64, String)> = sqlx::query_as(
            r#"SELECT id, kind
            FROM email
            WHERE mentor_id = ? AND state = 'review' AND kind IN ('first', 'follow_up')"#,
        )
        .bind(keep)
        .fetch_all(&db)
        .await
        .unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(
            pending[0].1,
            if keep_follow_up { "follow_up" } else { "first" }
        );
        let result = if keep_follow_up {
            email::follow_up::approve(&db, keep, d.id, &d.reply_to, &detail_approval(), T + 2)
                .await
                .unwrap()
        } else {
            assert_eq!(
                email::follow_up::approve(&db, keep, d.id, &d.reply_to, &detail_approval(), T + 2)
                    .await
                    .unwrap(),
                email::Approved::Refused
            );
            email::approve_first(&db, keep, &detail_approval(), None, T + 2)
                .await
                .unwrap()
        };
        assert_eq!(result, email::Approved::Yes);
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 3)
            .await
            .unwrap();
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 4)
            .await
            .unwrap();
    }
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_two_follow_up_drafts_keeps_only_the_retained_mentors(db: SqlitePool) {
    let _ = detail_mail(&db).await;
    let keep = mentor(&db, 211, true).await;
    let other = mentor(&db, 212, true).await;
    previous(&db, keep, false, T - 1000).await;
    previous(&db, other, false, T - 1000).await;
    let kept = email::follow_up::create(&db, keep, T).await.unwrap();
    email::follow_up::create(&db, other, T).await.unwrap();
    let preview = mentor::merge::preview(&db, keep, other).await.unwrap();
    assert!(matches!(
        preview.discarded_drafts.as_slice(),
        [email::DraftKind::FollowUp]
    ));
    mentor::merge::merge(&db, keep, other, T + 1).await.unwrap();
    let drafts: Vec<i64> = sqlx::query_scalar(
        r#"SELECT id
        FROM email
        WHERE mentor_id = ? AND kind IN ('first', 'follow_up')
        AND state = 'review'"#,
    )
    .bind(keep)
    .fetch_all(&db)
    .await
    .unwrap();
    assert_eq!(drafts, [kept.id]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merging_replied_mentors_cancels_follow_up_in_either_direction(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, store, inbox) = detail_mail(&db).await;
    for (n, reply_on_keep) in [(221, true), (223, false)] {
        let keep = mentor(&db, n, true).await;
        let other = mentor(&db, n + 1, true).await;
        let (replied, pending) = if reply_on_keep {
            (n, other)
        } else {
            (n + 1, keep)
        };
        previous(&db, pending, false, T - 1000).await;
        let d = email::follow_up::create(&db, pending, T).await.unwrap();
        email::follow_up::save(&db, pending, d.id, &d.reply_to, "已写好的追问", &[], T)
            .await
            .unwrap()
            .unwrap();
        store.deliver("INBOX", T + 1, &format!(
            "From: m{replied}@example.com\nMessage-ID: <merge-reply-{n}@example.com>\nSubject: 回复\n\n欢迎交流。"
        ));
        receive::round(&db, &net, T + 2).await.unwrap();
        mentor::merge::merge(&db, keep, other, T + 3).await.unwrap();
        assert!(email::follow_up::draft(&db, keep).await.unwrap().is_none());
        let canceled = email::list(&db, keep)
            .await
            .unwrap()
            .into_iter()
            .find(|e| e.id == d.id)
            .unwrap();
        assert!(matches!(
            canceled.delivery,
            email::Delivery::Canceled { cancel_reason } if cancel_reason == "导师已回信"
        ));
        assert_eq!(canceled.body_md, "已写好的追问");
        assert_eq!(
            email::follow_up::approve(&db, keep, d.id, &d.reply_to, &detail_approval(), T + 4)
                .await
                .unwrap(),
            email::Approved::Refused
        );
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
            .await
            .unwrap();
    }
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn adding_an_address_cancels_follow_ups_when_earlier_replies_are_linked(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, store, inbox) = detail_mail(&db).await;
    for (n, standalone, edit) in [
        (231, true, false),
        (232, false, false),
        (233, true, true),
        (234, false, true),
    ] {
        let m = mentor(&db, n, true).await;
        previous(&db, m, false, T - 1000).await;
        let id = if standalone {
            email::follow_up::create(&db, m, T).await.unwrap().id
        } else {
            let t = create(&db, &follow_form(&db, vec![m]).await, T)
                .await
                .unwrap();
            let d = items(&db, t).await.remove(0);
            approve(
                &db,
                &TaskApprovals {
                    task_id: t,
                    emails: vec![approval(&d)],
                },
                T,
            )
            .await
            .unwrap();
            d.id
        };
        let address = format!("old{n}@example.com");
        store.deliver("INBOX", T - 100, &format!(
            "From: {address}\nMessage-ID: <earlier-reply-{n}@example.com>\nSubject: 回复\n\n欢迎交流。"
        ));
        receive::round(&db, &net, T + 1).await.unwrap();
        if edit {
            mentor::update(
                &db,
                m,
                &MentorFields {
                    name: format!("导师{n}"),
                    emails: vec![format!("m{n}@example.com"), address],
                    ..Default::default()
                },
                T + 2,
            )
            .await
            .unwrap();
        } else {
            mentor::add_email(&db, m, &address, T + 2).await.unwrap();
        }
        receive::round(&db, &net, T + 3).await.unwrap();
        let state: (String, Option<String>) =
            sqlx::query_as("SELECT state,cancel_reason FROM email WHERE id=?")
                .bind(id)
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(state, ("canceled".into(), Some("导师已回信".into())));
        assert!(!email::retry(&db, id, T + 4).await.unwrap());
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
            .await
            .unwrap();
    }
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn standalone_follow_up_admission_and_concurrent_first_or_task(db: SqlitePool) {
    let (_net, _store, _inbox) = detail_mail(&db).await;
    for (n, state) in [
        "reply",
        "bounced",
        "maybe_sent",
        "pending",
        "no_address",
        "trashed",
        "missing_id",
    ]
    .iter()
    .enumerate()
    {
        let m = mentor(&db, n as i64, true).await;
        previous(&db, m, false, T - 1000).await;
        match *state {
            "reply" => {
                let r = sqlx::query(
                    r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind,
                        message_id, sent_at, from_address, truncated)
                    VALUES (1, 'INBOX', 1, ?, 'reply', ?, ?, 'mentor@example.com', 0)"#,
                )
                .bind(m)
                .bind(format!("<reply-{m}@example.com>"))
                .bind(T)
                .execute(&db)
                .await
                .unwrap()
                .last_insert_rowid();
                sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
                    .bind(m)
                    .bind(r)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            "bounced" | "maybe_sent" => {
                sqlx::query("UPDATE email SET state=?,error='failure' WHERE mentor_id=?")
                    .bind(state)
                    .bind(m)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            "pending" => {
                email::save_first_draft(&db, m, "新信", "正文", &[], T)
                    .await
                    .unwrap();
            }
            "no_address" => {
                sqlx::query("DELETE FROM mentor_email WHERE mentor_id=?")
                    .bind(m)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            "trashed" => {
                mentor::trash::trash(&db, &[m], T).await.unwrap();
            }
            "missing_id" => {
                // A newer external letter without a Message-ID must not fall back to the older email.
                let r = sqlx::query(
                    r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind,
                        sent_at, from_address, truncated)
                    VALUES (1, 'Sent', 1, ?, 'sent', ?, 'me@example.com', 0)"#,
                )
                .bind(m)
                .bind(T)
                .execute(&db)
                .await
                .unwrap()
                .last_insert_rowid();
                sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
                    .bind(m)
                    .bind(r)
                    .execute(&db)
                    .await
                    .unwrap();
            }
            _ => unreachable!(),
        }
        assert!(
            email::follow_up::create(&db, m, T).await.is_err(),
            "{state}"
        );
        assert!(email::follow_up::draft(&db, m).await.unwrap().is_none());
    }
    for n in 10..30 {
        let m = mentor(&db, n, true).await;
        previous(&db, m, false, T - 1000).await;
        let f = follow_form(&db, vec![m]).await;
        if n % 2 == 0 {
            let (a, b) = tokio::join!(
                email::follow_up::create(&db, m, T),
                email::save_first_draft(&db, m, "新信", "正文", &[], T)
            );
            let a = a.ok();
            let b = b.unwrap();
            assert_ne!(a.is_some(), b.is_some());
        } else {
            let (a, b) = tokio::join!(email::follow_up::create(&db, m, T), create(&db, &f, T));
            assert_ne!(a.is_ok(), b.is_ok());
        }
        let count: i64 =
            sqlx::query_scalar("SELECT count(*) FROM email WHERE mentor_id=? AND state='review'")
                .bind(m)
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(count, 1);
    }
    let m = mentor(&db, 50, true).await;
    previous(&db, m, false, T - 1000).await;
    let (a, b) = tokio::join!(
        email::follow_up::create(&db, m, T),
        email::follow_up::create(&db, m, T)
    );
    assert_eq!(a.unwrap().id, b.unwrap().id);
    let duplicate = sqlx::query(
        r#"INSERT INTO email (mentor_id, kind, reply_to, state, created_at, updated_at)
        VALUES (?, 'follow_up', '<parent@example.com>', 'review', 0, 0)"#,
    )
    .bind(m)
    .execute(&db)
    .await;
    assert!(
        duplicate
            .unwrap_err()
            .as_database_error()
            .unwrap()
            .is_unique_violation()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn standalone_follow_up_receiving_cancels_drafts_and_approved_mail(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, store, inbox) = detail_mail(&db).await;
    for (n, (bounce, approved, external)) in [
        (false, false, false),
        (false, true, true),
        (true, false, true),
        (true, true, false),
    ]
    .into_iter()
    .enumerate()
    {
        let m = mentor(&db, n as i64, true).await;
        let parent = previous(&db, m, external, T - 1000).await;
        let d = email::follow_up::create(&db, m, T).await.unwrap();
        email::follow_up::save(&db, m, d.id, &d.reply_to, "已写好的追问", &[], T)
            .await
            .unwrap();
        if approved {
            assert_eq!(
                email::follow_up::approve(&db, m, d.id, &d.reply_to, &detail_approval(), T)
                    .await
                    .unwrap(),
                email::Approved::Yes
            );
        }
        let raw = if bounce {
            format!(
                "From: MAILER-DAEMON@example.com\nMessage-ID: <bounce-{m}@example.com>\nContent-Type: multipart/report; report-type=delivery-status; boundary=b\n\n--b\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; example.com\n\nAction: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 address unknown\n--b\nContent-Type: text/rfc822-headers\n\nMessage-ID: {parent}\n--b--\n"
            )
        } else {
            format!(
                "From: m{n}@example.com\nMessage-ID: <reply-{m}@example.com>\nSubject: 回复\n\n欢迎交流。"
            )
        };
        store.deliver("INBOX", T + 1, &raw);
        receive::round(&db, &net, T + 2).await.unwrap();
        assert!(email::follow_up::draft(&db, m).await.unwrap().is_none());
        assert!(
            email::follow_up::save(&db, m, d.id, &d.reply_to, "过期的保存", &[], T + 3)
                .await
                .unwrap()
                .is_none()
        );
        assert_eq!(
            email::follow_up::approve(&db, m, d.id, &d.reply_to, &detail_approval(), T + 3)
                .await
                .unwrap(),
            email::Approved::Refused
        );
        let history = email::list(&db, m).await.unwrap();
        let canceled = history.iter().find(|e| e.id == d.id).unwrap();
        assert!(matches!(
            &canceled.delivery,
            email::Delivery::Canceled { cancel_reason }
                if cancel_reason
                    == if bounce {
                        "上一封被退回"
                    } else {
                        "导师已回信"
                    }
        ));
        assert_eq!(
            canceled.body_md,
            if approved {
                detail_approval().body_md
            } else {
                "已写好的追问"
            }
        );
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 4)
            .await
            .unwrap();
    }
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn standalone_follow_up_sends_frozen_thread_and_new_first_letter(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _store, inbox) = detail_mail(&db).await;
    for external in [false, true] {
        let m = mentor(&db, if external { 2 } else { 1 }, true).await;
        let parent = previous(&db, m, external, T - 1000).await;
        let d = email::follow_up::create(&db, m, T).await.unwrap();
        let saved =
            email::follow_up::save(&db, m, d.id, &d.reply_to, detail_approval().body_md, &[], T)
                .await
                .unwrap()
                .unwrap();
        assert_eq!(saved.reply_to, parent);
        assert_eq!(
            email::follow_up::approve(&db, m, d.id, &parent, &detail_approval(), T)
                .await
                .unwrap(),
            email::Approved::Yes
        );
        assert_eq!(
            email::follow_up::approve(&db, m, d.id, &parent, &detail_approval(), T)
                .await
                .unwrap(),
            email::Approved::Refused
        );
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || {
            T + m * 10 + 1
        })
        .await
        .unwrap();
        let raw = inbox.lock().unwrap().last().unwrap().clone();
        assert!(raw.contains(&format!("In-Reply-To: {parent}")));
        assert!(raw.contains(&format!("References: <root@example.com> {parent}")));
        let frozen: String = sqlx::query_scalar("SELECT text FROM email WHERE id=?")
            .bind(d.id)
            .fetch_one(&db)
            .await
            .unwrap();
        assert!(frozen.contains("> 之前的信 <script>data</script>"));
        assert!(
            email::save_first_draft(
                &db,
                m,
                "另一个研究方向",
                "老师您好，希望交流新的研究方向。",
                &[],
                T + 2
            )
            .await
            .unwrap()
            .is_some()
        );
        assert_eq!(
            email::approve_first(&db, m, &detail_approval(), None, T + 2)
                .await
                .unwrap(),
            email::Approved::Yes
        );
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || {
            T + m * 10 + 3
        })
        .await
        .unwrap();
        let raw = inbox.lock().unwrap().last().unwrap().clone();
        assert!(!raw.contains("In-Reply-To:"));
    }
    assert_eq!(inbox.lock().unwrap().len(), 4);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_selects_only_unsuccessful_and_reuses_saved_inputs(db: SqlitePool) {
    let (_net, _store, _inbox) = detail_mail(&db).await;
    let mut ids = vec![];
    for n in 1..=11 {
        ids.push(mentor(&db, n, true).await);
    }
    let mut f = form(&db, ids.clone()).await;
    let file = sqlx::query(
        r#"INSERT INTO material (file_name, size, extracted_text, for_matching,
            for_writing, attach_by_default, created_at)
        VALUES ('简历.pdf', 1, '我的研究经历', 0, 0, 0, 0)"#,
    )
    .execute(&db)
    .await
    .unwrap()
    .last_insert_rowid();
    f.references = vec![file];
    f.attachments = vec![file];
    let t = create(&db, &f, T).await.unwrap();
    for (m, state) in ids.iter().zip([
        "canceled",
        "failed",
        "generation_failed",
        "bounced",
        "generating",
        "review",
        "approved",
        "cloud",
        "sending",
        "maybe_sent",
        "sent",
    ]) {
        sqlx::query(
            r#"UPDATE email
            SET state = ?,
                cancel_reason = CASE WHEN ? = 'canceled' THEN '停止任务' END, error =
                CASE WHEN ? IN ('failed', 'generation_failed', 'bounced', 'maybe_sent')
                THEN 'failure' END, planned_at = ?, from_address = 'me@example.com',
                to_address = 'mentor@example.com', message_id = ?, sent_at = ?,
                html = '<p>草稿</p>', text = '草稿'
            WHERE task_id = ? AND mentor_id = ?"#,
        )
        .bind(state)
        .bind(state)
        .bind(state)
        .bind(T)
        .bind(format!("<restart-{m}@example.com>"))
        .bind(T)
        .bind(t)
        .bind(m)
        .execute(&db)
        .await
        .unwrap();
    }
    sqlx::query("UPDATE task SET state='stopped' WHERE id=?")
        .bind(t)
        .execute(&db)
        .await
        .unwrap();
    template::delete(&db, f.template_id.unwrap()).await.unwrap();
    let mut next = restart(&db, t).await.unwrap().unwrap();
    assert_eq!(next.ids, &ids[..4]);
    assert_eq!(next.source_task_id, Some(t));
    assert_eq!(next.name, format!("{} · 重新发起", f.name));
    assert_eq!(next.kind, f.kind);
    assert_eq!(next.writer, f.writer);
    assert_eq!(next.references, [file]);
    assert_eq!(next.attachments, [file]);
    assert_eq!(next.template_id, None);
    assert_eq!(next.template_source_id, Some(t));
    assert!(matches!(next.schedule, email::schedule::Schedule::Now));
    let p = plan(&db, &next).await.unwrap();
    assert_eq!(
        p.mentors
            .iter()
            .filter(|m| m.reason.is_none())
            .map(|m| m.id)
            .collect::<Vec<_>>(),
        ids[..4]
    );
    next.ids = ids[..4].to_vec();
    let new = create(&db, &next, T + 1).await.unwrap();
    let old = items(&db, t).await;
    assert_eq!(
        old.iter()
            .find(|d| d.mentor_id == ids[0])
            .unwrap()
            .cancel_reason
            .as_deref(),
        Some("停止任务")
    );
    assert_eq!(
        old.iter().find(|d| d.mentor_id == ids[3]).unwrap().state,
        DraftState::Bounced
    );
    for d in items(&db, new).await {
        assert!(d.subject.starts_with("给导师"));
        assert!(d.body_md.contains("老师：我是"));
        assert_eq!(d.attachments, [file]);
    }
    let empty = form(&db, vec![mentor(&db, 100, true).await]).await;
    let t = create(&db, &empty, T).await.unwrap();
    assert!(restart(&db, t).await.unwrap().unwrap().ids.is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_retains_follow_up_kind_and_falls_back_after_model_deletion(db: SqlitePool) {
    let (_net, _store, _inbox) = detail_mail(&db).await;
    let m = mentor(&db, 1, true).await;
    previous(&db, m, false, T - 1000).await;
    let old = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://localhost:1/v1', 'old', '', 1, 0)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    let new = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', 'http://localhost:1/v1', 'new', '', 1, 1)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    let mut f = follow_form(&db, vec![m]).await;
    f.writer = Writer::Ai;
    f.model_config_id = Some(old);
    let t = create(&db, &f, T).await.unwrap();
    stop(&db, t, T).await.unwrap();
    let next = restart(&db, t).await.unwrap().unwrap();
    assert_eq!(next.kind, Kind::FollowUp);
    assert_eq!(next.writer, Writer::Ai);
    assert_eq!(next.model_config_id, Some(old));
    model::delete(&db, old).await.unwrap();
    template::delete(&db, f.template_id.unwrap()).await.unwrap();
    // The dialog may have opened before either configuration was deleted.
    let p = plan(&db, &next).await.unwrap();
    assert_eq!(p.model_config_id, Some(new));
    let t = create(&db, &next, T + 1).await.unwrap();
    assert_eq!(
        get(&db, t).await.unwrap().unwrap().model_config_id,
        Some(new)
    );
    assert!(
        items(&db, t)
            .await
            .iter()
            .all(|d| d.subject.starts_with("Re:"))
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn standalone_follow_up_keeps_target_files_and_empty_draft_until_discarded(db: SqlitePool) {
    let (_net, _store, _inbox) = detail_mail(&db).await;
    let m = mentor(&db, 1, true).await;
    let parent = previous(&db, m, false, T - 1000).await;
    let d = email::follow_up::create(&db, m, T).await.unwrap();
    let file = sqlx::query(
        r#"INSERT INTO material (file_name, size, for_matching, for_writing,
            attach_by_default, created_at)
        VALUES ('简历.pdf', 10, 0, 0, 0, 0)"#,
    )
    .execute(&db)
    .await
    .unwrap()
    .last_insert_rowid();
    let d = email::follow_up::save(&db, m, d.id, &parent, "已写好的追问", &[file], T)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(d.attachments, [file]);
    previous(&db, m, true, T + 1).await;
    let loaded = email::follow_up::draft(&db, m).await.unwrap().unwrap();
    assert_eq!(loaded.reply_to, parent);
    assert_eq!(loaded.id, d.id);
    assert_eq!(loaded.body_md.as_deref(), Some("已写好的追问"));
    assert_eq!(loaded.attachments, [file]);
    let empty = email::follow_up::save(&db, m, d.id, &parent, "", &[], T + 2)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(empty.id, d.id);
    assert_eq!(empty.reply_to, parent);
    assert!(empty.attachments.is_empty());
    assert!(email::follow_up::discard(&db, d.id, T + 3).await.unwrap());
    assert!(!email::follow_up::discard(&db, d.id, T + 3).await.unwrap());
    assert!(
        email::save_first_draft(&db, m, "新的一封", "新的方向", &[], T + 4)
            .await
            .unwrap()
            .is_some()
    );
}

async fn failed_task(db: &SqlitePool, ids: Vec<i64>, generation: bool, kind: Kind) -> i64 {
    if kind == Kind::FollowUp {
        for &id in &ids {
            previous(db, id, false, T - 1000).await;
        }
    }
    let mut f = form(db, ids).await;
    f.kind = kind;
    if generation {
        f.writer = Writer::Ai;
        f.model_config_id = Some(
            sqlx::query_scalar(
                r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
                    created_at)
                VALUES ('custom', 'http://localhost:1/v1', 'fixed', '', 1, 0)
                RETURNING id"#,
            )
            .fetch_one(db)
            .await
            .unwrap(),
        );
    }
    let t = create(db, &f, T).await.unwrap();
    if generation {
        while let Some(claim) = queue::next(db).await.unwrap() {
            queue::fail(&mut db.acquire().await.unwrap(), claim, "模型失败", T + 1)
                .await
                .unwrap();
        }
    } else {
        let drafts = items(db, t).await;
        approve(
            db,
            &TaskApprovals {
                task_id: t,
                emails: drafts.iter().map(approval).collect(),
            },
            T,
        )
        .await
        .unwrap();
        for d in drafts {
            email::failed(&mut db.acquire().await.unwrap(), d.id, "连接失败", T + 1)
                .await
                .unwrap();
        }
    }
    t
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_replaces_only_selected_failed_letters_at_creation(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    for (n, generation, kind) in [
        (241, false, Kind::First),
        (243, true, Kind::First),
        (245, false, Kind::FollowUp),
        (247, true, Kind::FollowUp),
    ] {
        let selected = mentor(&db, n, true).await;
        let excluded = mentor(&db, n + 1, true).await;
        let t = failed_task(&db, vec![selected, excluded], generation, kind).await;
        let before = items(&db, t).await;
        let mut next = restart(&db, t).await.unwrap().unwrap();
        let p = plan(&db, &next).await.unwrap();
        assert!(p.mentors.iter().all(|m| m.reason.is_none()));
        assert!(items(&db, t).await.iter().all(|d| d.state
            == if generation {
                DraftState::GenerationFailed
            } else {
                DraftState::Failed
            }));
        next.ids = vec![selected];
        let new = create(&db, &next, T + 2).await.unwrap();
        let old = items(&db, t).await;
        let replaced = old.iter().find(|d| d.mentor_id == selected).unwrap();
        let untouched = old.iter().find(|d| d.mentor_id == excluded).unwrap();
        assert_eq!(replaced.state, DraftState::Canceled);
        assert_eq!(
            replaced.cancel_reason.as_deref(),
            Some("已在新任务重新发起")
        );
        assert_eq!(
            untouched.state,
            if generation {
                DraftState::GenerationFailed
            } else {
                DraftState::Failed
            }
        );
        assert_eq!(
            replaced.body_md,
            before.iter().find(|d| d.id == replaced.id).unwrap().body_md
        );
        assert!(!email::retry(&db, replaced.id, T + 3).await.unwrap());
        assert_eq!(
            regenerate(&db, t, Some(replaced.id), true, T + 3)
                .await
                .unwrap(),
            0
        );
        if generation {
            let claim = queue::next(&db).await.unwrap().unwrap();
            assert_eq!(claim.task, new);
            queue::complete(
                &mut db.acquire().await.unwrap(),
                claim,
                "申请",
                "固定草稿",
                "fixed",
                None,
                T + 3,
            )
            .await
            .unwrap();
        }
        let d = items(&db, new).await.remove(0);
        approve(
            &db,
            &TaskApprovals {
                task_id: new,
                emails: vec![approval(&d)],
            },
            T + 4,
        )
        .await
        .unwrap();
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
            .await
            .unwrap();
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 6)
            .await
            .unwrap();
    }
    assert_eq!(inbox.lock().unwrap().len(), 4);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_refuses_retried_original_letters_without_canceling_other_failures(db: SqlitePool) {
    let _ = detail_mail(&db).await;
    for (n, generation) in [(251, false), (253, true)] {
        let retried = mentor(&db, n, true).await;
        let failed = mentor(&db, n + 1, true).await;
        let t = failed_task(&db, vec![retried, failed], generation, Kind::First).await;
        let next = restart(&db, t).await.unwrap().unwrap();
        assert!(
            plan(&db, &next)
                .await
                .unwrap()
                .mentors
                .iter()
                .all(|m| m.reason.is_none())
        );
        let d = items(&db, t)
            .await
            .into_iter()
            .find(|d| d.mentor_id == retried)
            .unwrap();
        if generation {
            assert_eq!(
                regenerate(&db, t, Some(d.id), true, T + 2).await.unwrap(),
                1
            );
        } else {
            assert!(email::retry(&db, d.id, T + 2).await.unwrap());
        }
        let p = plan(&db, &next).await.unwrap();
        assert!(
            p.mentors
                .iter()
                .find(|m| m.id == retried)
                .unwrap()
                .reason
                .is_some()
        );
        assert!(matches!(
            create(&db, &next, T + 3).await,
            Err(Error::Invalid(_))
        ));
        let old = items(&db, t).await;
        assert_eq!(
            old.iter().find(|d| d.mentor_id == retried).unwrap().state,
            if generation {
                DraftState::Generating
            } else {
                DraftState::Approved
            }
        );
        assert_eq!(
            old.iter().find(|d| d.mentor_id == failed).unwrap().state,
            if generation {
                DraftState::GenerationFailed
            } else {
                DraftState::Failed
            }
        );
        assert!(old.iter().all(|d| d.cancel_reason.is_none()));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restarting_and_retrying_the_same_failure_can_only_send_once(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    for n in 261..271 {
        let generation = n % 2 == 0;
        let m = mentor(&db, n, true).await;
        let t = failed_task(&db, vec![m], generation, Kind::First).await;
        let d = items(&db, t).await.remove(0);
        let next = restart(&db, t).await.unwrap().unwrap();
        let retry = async {
            if generation {
                regenerate(&db, t, Some(d.id), true, T + 2).await.unwrap() == 1
            } else {
                email::retry(&db, d.id, T + 2).await.unwrap()
            }
        };
        let (created, retried) = tokio::join!(create(&db, &next, T + 2), retry);
        assert_ne!(created.is_ok(), retried);
        let winner = match created {
            Ok(id) => id,
            Err(Error::Invalid(_)) => t,
            Err(e) => panic!("{e:?}"),
        };
        if generation {
            let claim = queue::next(&db).await.unwrap().unwrap();
            assert_eq!(claim.task, winner);
            queue::complete(
                &mut db.acquire().await.unwrap(),
                claim,
                "申请",
                "固定草稿",
                "fixed",
                None,
                T + 3,
            )
            .await
            .unwrap();
        }
        let d = items(&db, winner).await.remove(0);
        if d.state == DraftState::Review {
            approve(
                &db,
                &TaskApprovals {
                    task_id: winner,
                    emails: vec![approval(&d)],
                },
                T + 4,
            )
            .await
            .unwrap();
        }
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 5)
            .await
            .unwrap();
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 6)
            .await
            .unwrap();
    }
    assert_eq!(inbox.lock().unwrap().len(), 10);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_refuses_an_original_retry_that_finished_sending(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    for (n, kind) in [(281, Kind::First), (282, Kind::FollowUp)] {
        let m = mentor(&db, n, true).await;
        let t = failed_task(&db, vec![m], false, kind).await;
        let next = restart(&db, t).await.unwrap().unwrap();
        let d = items(&db, t).await.remove(0);
        assert!(email::retry(&db, d.id, T + 2).await.unwrap());
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 3)
            .await
            .unwrap();
        assert_eq!(items(&db, t).await[0].state, DraftState::Sent);
        assert!(plan(&db, &next).await.unwrap().mentors[0].reason.is_some());
        assert!(matches!(
            create(&db, &next, T + 4).await,
            Err(Error::Invalid(_))
        ));
        assert_eq!(items(&db, t).await[0].state, DraftState::Sent);
    }
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_refuses_a_deleted_source_after_changing_template(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    for (n, kind) in [(501, Kind::First), (502, Kind::FollowUp)] {
        let at = T + (n - 501) * 100;
        let m = mentor(&db, n, true).await;
        let old = failed_task(&db, vec![m], false, kind).await;
        let mut next = restart(&db, old).await.unwrap().unwrap();
        assert!(plan(&db, &next).await.unwrap().mentors[0].reason.is_none());
        // Choosing a current template must preserve the source constraint.
        next.template_source_id = None;
        let letter = items(&db, old).await.remove(0);
        assert!(email::retry(&db, letter.id, at + 2).await.unwrap());
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || {
            at + 3
        })
        .await
        .unwrap();
        assert_eq!(items(&db, old).await[0].state, DraftState::Sent);
        assert!(plan(&db, &next).await.unwrap().mentors[0].reason.is_some());
        delete(&db, old).await.unwrap();
        assert!(get(&db, old).await.unwrap().is_none());

        let planned = plan(&db, &next).await;
        let created = create(&db, &next, at + 4).await;
        assert!(matches!(created, Err(Error::Invalid(_))));
        assert!(matches!(planned, Err(Error::Invalid(_))));
        assert_eq!(
            sqlx::query_scalar::<_, i64>("SELECT count(*) FROM task")
                .fetch_one(&db)
                .await
                .unwrap(),
            0
        );
        send::step(&db, &writes, &net, std::path::Path::new("unused"), || {
            at + 5
        })
        .await
        .unwrap();
    }
    assert_eq!(inbox.lock().unwrap().len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restart_requires_an_existing_source_and_all_selected_mentors(db: SqlitePool) {
    let writes = db::Writes::default();
    let (net, _, inbox) = detail_mail(&db).await;
    let a = mentor(&db, 511, true).await;
    let b = mentor(&db, 512, true).await;
    let c = mentor(&db, 513, true).await;
    let old = failed_task(&db, vec![a], false, Kind::First).await;
    let mut next = restart(&db, old).await.unwrap().unwrap();
    next.ids = vec![a, b];
    let mut missing = form(&db, vec![c]).await;
    missing.source_task_id = Some(999999);

    for form in [next, missing] {
        let planned = plan(&db, &form).await;
        let created = create(&db, &form, T + 2).await;
        assert!(matches!(created, Err(Error::Invalid(_))));
        assert!(matches!(planned, Err(Error::Invalid(_))));
        let original = items(&db, old).await.remove(0);
        assert_eq!(original.state, DraftState::Failed);
        assert!(original.cancel_reason.is_none());
        assert_eq!(
            sqlx::query_scalar::<_, i64>("SELECT count(*) FROM task")
                .fetch_one(&db)
                .await
                .unwrap(),
            1
        );
        assert_eq!(
            sqlx::query_scalar::<_, i64>("SELECT count(*) FROM email")
                .fetch_one(&db)
                .await
                .unwrap(),
            1
        );
    }
    send::step(&db, &writes, &net, std::path::Path::new("unused"), || T + 3)
        .await
        .unwrap();
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn background_work_only_includes_paused_generating_and_failed_tasks(db: SqlitePool) {
    sqlx::query(
        r#"WITH RECURSIVE n(id) AS (VALUES(1) UNION ALL SELECT id + 1 FROM n WHERE id < 1000)
        INSERT INTO task (id, name, kind, writer, schedule, state, created_at)
        SELECT id, '已结束', 'first', 'ai', 'now',
            CASE WHEN id % 2 = 0 THEN 'stopped' ELSE 'active' END, 0 FROM n"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let mentor = mentor(&db, 1, true).await;
    for (id, task_state, paused, email_state, count) in [
        (1001, "paused", false, "review", 1),
        (1002, "active", true, "generating", 2),
        (1003, "active", false, "generating", 3),
        (1004, "active", false, "generation_failed", 1),
        (1005, "stopped", false, "failed", 1),
        (1006, "stopped", true, "canceled", 1),
        (1007, "active", false, "review", 1),
        (1008, "active", false, "approved", 1),
        (1009, "paused", false, "canceled", 1),
        (1010, "active", false, "sending", 1),
        (1011, "active", false, "cloud", 1),
    ] {
        sqlx::query(
            r#"INSERT INTO task (id, name, kind, writer, schedule, state, created_at,
                generation_paused_at, generation_pause_reason)
            VALUES (?, '后台任务', 'first', 'ai', 'now', ?, 0, ?, ?)"#,
        )
        .bind(id)
        .bind(task_state)
        .bind(paused.then_some(T))
        .bind(paused.then_some("模型配置需要确认"))
        .execute(&db)
        .await
        .unwrap();
        for offset in 0..count {
            let member = if offset == 0 {
                mentor
            } else {
                self::mentor(&db, id + offset, true).await
            };
            sqlx::query(
                r#"INSERT INTO email (task_id, mentor_id, kind, state, subject, body_md,
                    html, text, error, cancel_reason, planned_at, message_id,
                    from_address, sent_at, created_at, updated_at)
                VALUES (?1, ?2, 'first', ?3, '主题', '正文', '<p>正文</p>', '正文', ?4,
                    CASE WHEN ?3 = 'canceled' THEN '任务停止' END, 0,
                    CASE WHEN ?3 = 'sending' THEN '<work-' || ?1 || '@example.com>' END,
                    CASE WHEN ?3 = 'sending' THEN 'me@example.com' END,
                    CASE WHEN ?3 = 'sending' THEN ?5 END, 0, 0)"#,
            )
            .bind(id)
            .bind(member)
            .bind(email_state)
            .bind(matches!(email_state, "generation_failed" | "failed").then_some("失败原因"))
            .bind(T)
            .execute(&db)
            .await
            .unwrap();
        }
    }
    let failed_mentor = self::mentor(&db, 2000, true).await;
    sqlx::query(
        r#"INSERT INTO email (task_id, mentor_id, kind, state, subject, body_md,
            html, text, error, planned_at, created_at, updated_at)
        SELECT id, ?, 'first', 'failed', '主题', '正文', '<p>正文</p>', '正文',
            '失败原因', 0, 0, 0
        FROM task WHERE id IN (1010, 1011)"#,
    )
    .bind(failed_mentor)
    .execute(&db)
    .await
    .unwrap();
    let tasks = work(&db).await.unwrap();
    let page = list(
        &db,
        &TaskQuery {
            page: std::num::NonZeroU32::MIN,
        },
    )
    .await
    .unwrap();
    assert_eq!(
        tasks.iter().map(|t| t.id).collect::<Vec<_>>(),
        [1011, 1010, 1009, 1005, 1004, 1003, 1002, 1001]
    );
    for (t, (state, generating)) in tasks.iter().zip([
        (TaskState::Active, 0),
        (TaskState::Active, 0),
        (TaskState::Paused, 0),
        (TaskState::Stopped, 0),
        (TaskState::Completed, 0),
        (TaskState::Active, 3),
        (TaskState::Paused, 2),
        (TaskState::Paused, 0),
    ]) {
        assert_eq!(t.state, state);
        assert_eq!(t.generating, generating);
        assert_eq!(t.state, get(&db, t.id).await.unwrap().unwrap().state);
        assert_eq!(
            t.state,
            page.tasks.iter().find(|row| row.id == t.id).unwrap().state
        );
    }
    assert_eq!(tasks[6].pause_reason.as_deref(), Some("模型配置需要确认"));
}
