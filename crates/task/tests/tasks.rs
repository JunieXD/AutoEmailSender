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
        let id = sqlx::query("INSERT INTO message(mailbox_id,folder,uidvalidity,uid,kind,message_id,sent_at,from_address,from_name,subject,body_text,refs,truncated) VALUES(1,'Sent',1,?,'sent',?,?,'me@example.com','林晓','RE：申请','之前的信 <script>data</script>','<root@example.com>',0)")
            .bind(mentor).bind(&mid).bind(at).execute(db).await.unwrap().last_insert_rowid();
        sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
            .bind(mentor)
            .bind(id)
            .execute(db)
            .await
            .unwrap();
    } else {
        sqlx::query("INSERT INTO email(mentor_id,kind,state,planned_at,mailbox_id,from_address,to_address,message_id,sent_at,subject,body_md,html,text,refs,created_at,updated_at) VALUES(?,'first','sent',?,1,'me@example.com','mentor@example.com',?,?,'申请','之前的信','<p>之前的信</p>','之前的信 <script>data</script>','<root@example.com>',?,?)")
            .bind(mentor).bind(at).bind(&mid).bind(at).bind(at).bind(at).execute(db).await.unwrap();
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
    sqlx::query("INSERT INTO mailbox(address,sender_name,provider,smtp_host,smtp_port,smtp_security,imap_host,imap_port,imap_security,auth,secret,daily_limit,min_interval_s,rotation_order,created_at) VALUES('me@example.com','我','custom','localhost',465,'tls','localhost',993,'tls','password','test',50,0,1,0)").execute(&db).await.unwrap();
    let mut ids = vec![];
    for n in 1..=7 {
        let id = mentor(&db, n, n != 5).await;
        previous(&db, id, n == 7, T - 10 * 86_400_000).await;
        ids.push(id);
    }
    let reply = sqlx::query("INSERT INTO message(mailbox_id,folder,uidvalidity,uid,kind,message_id,sent_at,from_address,truncated) VALUES(1,'INBOX',1,1,'reply','<reply@example.com>',?,'m1@example.com',0)").bind(T).execute(&db).await.unwrap().last_insert_rowid();
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
        send::step(&db, &net, std::path::Path::new("unused"), || T)
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
        sqlx::query("UPDATE email SET state=?,generation_queued_at=?,planned_at=?,from_address='me@example.com',to_address='m@example.com',message_id=?,sent_at=?,html='<p>追问</p>',text='追问',error=? WHERE id=?")
            .bind(state).bind((state=="generating").then_some(T)).bind(T).bind(format!("<follow-{}@example.com>",d.id)).bind(T)
            .bind(matches!(state,"generation_failed"|"failed"|"maybe_sent").then_some("示例失败")).bind(d.id).execute(&db).await.unwrap();
    }
    let claim = queue::next(&db).await.unwrap().unwrap();
    for &m in &ids[..states.len()] {
        store.deliver("INBOX", T+1, &format!("From: m{m}@example.com\nMessage-ID: <reply-{m}@example.com>\nSubject: 回复\n\n收到，欢迎联系。"));
    }
    let automatic = *ids.last().unwrap();
    store.deliver("INBOX", T+1, &format!("From: m{automatic}@example.com\nMessage-ID: <auto@example.com>\nAuto-Submitted: auto-replied\nSubject: 自动回复\n\n暂时不在办公室。"));
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
            T + 3
        )
        .await
        .unwrap()
    );
    assert_eq!(
        send::step(&db, &net, std::path::Path::new("unused"), || T + 3)
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
        send::step(&db, &net, std::path::Path::new("unused"), || T + 5)
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
            send::step(&db, &net, std::path::Path::new("unused"), || T + 5)
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
            send::step(&db, &net, std::path::Path::new("unused"), || T)
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
        store.deliver("INBOX",T+1,&format!("From: MAILER-DAEMON@example.com\nMessage-ID: <bounce-{m}@example.com>\nContent-Type: multipart/report; report-type=delivery-status; boundary=b\n\n--b\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; example.com\n\nAction: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 address unknown\n--b\nContent-Type: text/rfc822-headers\n\nMessage-ID: {parent}\n--b--\n"));
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
    sqlx::query("UPDATE email SET state='sent',from_address='me@example.com',to_address='m5@example.com',message_id='<sent@example.com>',sent_at=? WHERE mentor_id=?").bind(T).bind(ids[4]).execute(&db).await.unwrap();
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
    assert!(sqlx::query("INSERT INTO email (task_id,mentor_id,kind,state,created_at,updated_at) VALUES (?,?,'first','review',0,0)").bind(task).bind(a).execute(&db).await.is_err());
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
        send::step(&db, &net, std::path::Path::new("unused"), || T)
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
        send::step(&db, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Now
    );
    assert_eq!(
        send::step(&db, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn review_expiry_restore_must_respect_stopping_during_commit(db: SqlitePool) {
    use std::cell::Cell;
    use std::sync::mpsc;
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
    let options = db.connect_options().as_ref().clone();
    let (request, requested) = mpsc::channel();
    let (finish, finished) = mpsc::channel();
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
                stop(&db, task, T).await.unwrap();
                db.close().await;
                finish.send(()).unwrap();
            });
    });
    let calls = Cell::new(0);
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    let nowhere = std::path::Path::new("unused");
    send::step(&db, &net, nowhere, || {
        calls.set(calls.get() + 1);
        if calls.get() == 4 {
            request.send(()).unwrap();
            finished.recv().unwrap();
        }
        if calls.get() < 4 { T } else { T + 3_600_000 }
    })
    .await
    .unwrap();
    writer.join().unwrap();
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
        send::step(&db, &net, nowhere, || T + 86_400_000)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert!(inbox.lock().unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_task_time_defers_handover_and_retry_reserves_again(db: SqlitePool) {
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
        send::step(&db, &net, nowhere, || T).await.unwrap(),
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
        send::step(&db, &net, nowhere, || at).await.unwrap(),
        send::Next::Now
    );
    assert_eq!(
        send::step(&db, &net, nowhere, || at).await.unwrap(),
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
    sqlx::query("UPDATE email SET state='sending',from_address='me@example.com',to_address='m1@example.com',message_id='<out@example.com>',sent_at=? WHERE id=?").bind(T).bind(ds[0].id).execute(&db).await.unwrap();
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
    let config=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','m','',1,0) RETURNING id").fetch_one(&db).await.unwrap();
    f.writer = Writer::Ai;
    f.model_config_id = Some(config);
    let task = create(&db, &f, T).await.unwrap();
    let old = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    let new = queue::next(&db).await.unwrap().unwrap();
    assert_ne!(old, new);
    assert!(
        !queue::complete(&mut db.acquire().await.unwrap(), old, "旧", "旧稿", "m", T)
            .await
            .unwrap()
    );
    assert!(
        !generation::requeue(&db, old, Some(("旧错误", None)), T)
            .await
            .unwrap()
    );
    assert!(
        queue::complete(&mut db.acquire().await.unwrap(), new, "新", "新稿", "m", T)
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
        body_md: "导师1老师：您好".into(),
        attachments: vec![],
    };
    save(&db, &edit, T).await.unwrap();
    let p = check(&db, &edit, T).await.unwrap();
    assert!(
        p.iter()
            .any(|p| matches!(p,check::Problem::OtherMentor{name} if name=="导师2"))
    );
    let page = drafts(
        &db,
        &DraftQuery {
            task_id: task,
            filter: DraftFilter::Problems,
            page: std::num::NonZeroU32::MIN,
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(page.total, 1);
    assert_eq!(page.drafts[0].problems, p.len());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regeneration_is_local_and_deleted_model_uses_the_new_default(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let old=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','old','',1,0) RETURNING id").fetch_one(&db).await.unwrap();
    let new=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','new','',1,1) RETURNING id").fetch_one(&db).await.unwrap();
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
    let material=sqlx::query_scalar::<_,i64>("INSERT INTO material(file_name,size,for_matching,for_writing,attach_by_default,created_at) VALUES('新材料.pdf',10,0,0,0,0) RETURNING id").fetch_one(&db).await.unwrap();
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
    assert!(
        matches!(create(&db, &f, now).await, Err(Error::Invalid(message)) if message.contains("容量不足"))
    );
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
    assert!(
        matches!(create(&db, &f, now).await, Err(Error::Invalid(message)) if message.contains("还差 1 封"))
    );
    w.per_window = 2;
    f.schedule = Schedule::Window(w);
    assert!(create(&db, &f, now).await.is_ok());
}
