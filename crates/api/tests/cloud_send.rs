#![cfg(test)]

use api::Context;
use api::cloud_send::{self, Blocked, Choice, HandOff, PreviewInput, Reason, Withdraw};
use cloud_api::cloud_send::{Outcome, RETENTION};
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::cloud_send::{Sending, Server, State};

const NOW: i64 = 1_800_000_000_000;
const MINUTE: i64 = 60 * 1000;
const HOUR: i64 = 60 * MINUTE;
const DAY: i64 = 24 * HOUR;
const QQ: &str = "me@qq.com";

struct Setup {
    _dir: tempfile::TempDir,
    ctx: Context,
    server: Server,
}

impl Setup {
    /// Logged in with Plus, with a QQ mailbox (id 1) and a Gmail one (id 2).
    async fn new(db: SqlitePool) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let server = Server::start(State {
            now: NOW,
            plus_until: Some(NOW + 30 * DAY),
            ..Default::default()
        })
        .await
        .unwrap();
        let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), dir.path());
        ctx.account = account::Client::with_address(&ctx.net, server.address.clone());
        test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
            .await
            .unwrap();
        sqlx::raw_sql(
            r#"INSERT INTO account (terms, id, email, token)
            VALUES ('9999-12-31', 1, 'student@example.invalid', 'token');
            INSERT INTO mailbox (id, address, sender_name, provider, smtp_host, smtp_port,
                smtp_security, imap_host, imap_port, imap_security, auth, secret, daily_limit,
                min_interval_s, rotation_order, created_at)
              VALUES (2, 'me@gmail.com', '我', 'gmail', 'smtp.gmail.com', 465, 'tls',
                'imap.gmail.com', 993, 'tls', 'password', 'secret', 50, 60, 2, 0);"#,
        )
        .execute(&ctx.db)
        .await
        .unwrap();
        Self {
            _dir: dir,
            ctx,
            server,
        }
    }

    /// An approved first letter to a new mentor, to go at `at` or within the hour after.
    async fn scheduled(&self, name: &str, at: i64) -> i64 {
        let mentor = sqlx::query_scalar::<_, i64>(
            "INSERT INTO mentor (name, created_at, updated_at) VALUES (?, 0, 0) RETURNING id",
        )
        .bind(name)
        .fetch_one(&self.ctx.db)
        .await
        .unwrap();
        sqlx::query("INSERT INTO mentor_email (address, mentor_id, position) VALUES (?, ?, 0)")
            .bind(format!("{mentor}@example.edu.cn"))
            .bind(mentor)
            .execute(&self.ctx.db)
            .await
            .unwrap();
        sqlx::query_scalar(
            r#"INSERT INTO email (mentor_id, kind, state, planned_at, latest_at, subject, body_md,
                html, text, created_at, updated_at)
            VALUES (?1, 'first', 'approved', ?2, ?2 + 3600000, '申请硕士', '您好',
                '<p>您好</p>', '您好', 0, 0)
            RETURNING id"#,
        )
        .bind(mentor)
        .bind(at)
        .fetch_one(&self.ctx.db)
        .await
        .unwrap()
    }

    async fn preview(&self, emails: Option<Vec<i64>>) -> cloud_send::Preview {
        cloud_send::preview(&self.ctx, PreviewInput { emails, task: None }, NOW)
            .await
            .unwrap()
    }

    /// Hands over all that can go, agreeing to give the QQ mailbox's secret.
    async fn hand_off(&self) -> cloud_send::HandedOff {
        let preview = self.preview(None).await;
        let emails = preview.emails.iter().map(|p| p.choice.clone()).collect();
        cloud_send::hand_off(
            &self.ctx,
            HandOff {
                emails,
                consents: vec![1],
            },
            NOW,
        )
        .await
        .unwrap()
    }

    /// State, cloud id and whether the official server sent it.
    async fn email(&self, id: i64) -> (String, Option<String>, bool) {
        sqlx::query_as("SELECT state, cloud_id, by_cloud FROM email WHERE id = ?")
            .bind(id)
            .fetch_one(&self.ctx.db)
            .await
            .unwrap()
    }

    async fn cloud_id(&self, id: i64) -> String {
        self.email(id).await.1.unwrap()
    }

    /// Logs in on this computer as `email`.
    async fn log_in(&self, email: &str) {
        sqlx::query("DELETE FROM account")
            .execute(&self.ctx.db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO account (terms, id, email, token) VALUES ('9999-12-31', 1, ?, ?)")
            .bind(email)
            .bind(format!("{email}-token"))
            .execute(&self.ctx.db)
            .await
            .unwrap();
    }

    /// Whether what the UI shows changed.
    async fn sync(&self) -> bool {
        let mut changed = false;
        cloud_send::sync(&self.ctx, NOW, &mut changed)
            .await
            .unwrap();
        changed
    }

    fn server(&self) -> std::sync::MutexGuard<'_, State> {
        self.server.state.lock().unwrap()
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn only_scheduled_first_emails_a_domestic_mailbox_can_send_are_handed_over(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let first = setup.scheduled("王伟", NOW + DAY).await;
    let second = setup.scheduled("李娜", NOW + DAY).await;
    let soon = setup.scheduled("张敏", NOW + MINUTE).await;
    let late = setup.scheduled("刘洋", NOW + 40 * DAY).await;
    let foreign = setup.scheduled("陈静", NOW + DAY).await;
    sqlx::query(
        "UPDATE mentor SET mailbox_id = 2 WHERE id = (SELECT mentor_id FROM email WHERE id = ?)",
    )
    .bind(foreign)
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    let paused = setup.scheduled("杨帆", NOW + DAY).await;
    sqlx::query(
        r#"INSERT INTO task (id, name, kind, writer, schedule, state, created_at)
          VALUES (7, '暂停的任务', 'first', 'ai', 'now', 'paused', 0)"#,
    )
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    sqlx::query("UPDATE email SET task_id = 7 WHERE id = ?")
        .bind(paused)
        .execute(&setup.ctx.db)
        .await
        .unwrap();

    let preview = setup.preview(None).await;
    assert_eq!(preview.blocked, None);
    // The second keeps the mailbox's interval after the first, from the same mailbox: the Gmail
    // one cannot send from the server.
    let planned: Vec<Choice> = preview.emails.iter().map(|p| p.choice.clone()).collect();
    assert_eq!(
        planned,
        [
            Choice {
                id: first,
                mailbox_id: 1,
                at: NOW + DAY
            },
            Choice {
                id: second,
                mailbox_id: 1,
                at: NOW + DAY + MINUTE
            }
        ]
    );
    let mut unfit: Vec<(i64, Reason)> = preview.unfit.iter().map(|u| (u.id, u.reason)).collect();
    unfit.sort_by_key(|(id, _)| *id);
    assert_eq!(
        unfit,
        [
            (soon, Reason::TooSoon),
            (late, Reason::AfterPlus),
            (foreign, Reason::ForeignMailbox),
            (paused, Reason::TaskPaused)
        ]
    );
    assert_eq!(preview.mailboxes.len(), 1);
    assert!(preview.mailboxes[0].needs_consent);

    setup.server().plus_until = None;
    assert_eq!(setup.preview(None).await.blocked, Some(Blocked::NoPlus));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn handing_over_uploads_the_message_and_the_secret_once_agreed_to(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let id = setup.scheduled("王伟", NOW + DAY).await;
    let preview = setup.preview(None).await;
    let emails: Vec<Choice> = preview.emails.iter().map(|p| p.choice.clone()).collect();
    let refused = cloud_send::hand_off(
        &setup.ctx,
        HandOff {
            emails: emails.clone(),
            consents: vec![],
        },
        NOW,
    )
    .await;
    assert!(refused.is_err());
    assert_eq!(setup.email(id).await.0, "approved");

    let handed = setup.hand_off().await;
    assert_eq!(handed.handed, [id]);
    assert_eq!(setup.email(id).await.0, "cloud");
    let message_id: String = sqlx::query_scalar("SELECT message_id FROM email WHERE id = ?")
        .bind(id)
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    {
        let server = setup.server();
        assert_eq!(server.mailboxes[QQ].upload.secret, "secret");
        let email = server.emails.values().next().unwrap();
        assert_eq!(email.upload.planned_at, NOW + DAY);
        assert!(email.message.contains(&format!("Message-ID: {message_id}")));
        // The server dates it as it goes out.
        assert!(!email.message.contains("\r\nDate:"));
    }
    // Once the server has the secret, nothing more is asked.
    setup.scheduled("李娜", NOW + 2 * DAY).await;
    assert!(!setup.preview(None).await.mailboxes[0].needs_consent);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_mailbox_the_server_cannot_log_in_to_keeps_its_emails_here(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let id = setup.scheduled("王伟", NOW + DAY).await;
    setup
        .server()
        .refused
        .insert(QQ.into(), "授权码不对".into());
    let handed = setup.hand_off().await;
    assert!(handed.handed.is_empty());
    assert_eq!(handed.login_failed[0].address, QQ);
    assert_eq!(setup.email(id).await, ("approved".into(), None, false));
    assert!(setup.server().emails.is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn what_became_of_the_emails_comes_back_once(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let sent = setup.scheduled("王伟", NOW + DAY).await;
    let failed = setup.scheduled("李娜", NOW + DAY).await;
    let waiting = setup.scheduled("张敏", NOW + 2 * DAY).await;
    setup.hand_off().await;
    let (sent_id, failed_id) = (setup.cloud_id(sent).await, setup.cloud_id(failed).await);
    {
        let mut server = setup.server();
        server.finish(&sent_id, Outcome::Sent { at: NOW + DAY });
        server.finish(
            &failed_id,
            Outcome::Failed {
                at: NOW + DAY,
                reason: "收件人不存在".into(),
            },
        );
        server.mailboxes.get_mut(QQ).unwrap().pause = Some(transport::pause::Pause {
            until: NOW + HOUR,
            reason: "发信太频繁".into(),
        });
        server.fail_ack = true;
    }
    // Written down before the server is told: told again next time, they change nothing.
    let mut changed = false;
    assert!(
        cloud_send::sync(&setup.ctx, NOW, &mut changed)
            .await
            .is_err()
    );
    assert!(changed);
    assert_eq!(setup.email(sent).await, ("sent".into(), None, true));
    assert_eq!(setup.email(failed).await, ("failed".into(), None, true));
    assert_eq!(setup.email(waiting).await.0, "cloud");
    let mentor_mailbox: Option<i64> = sqlx::query_scalar(
        "SELECT mailbox_id FROM mentor WHERE id = (SELECT mentor_id FROM email WHERE id = ?)",
    )
    .bind(sent)
    .fetch_one(&setup.ctx.db)
    .await
    .unwrap();
    assert_eq!(mentor_mailbox, Some(1));
    let mailboxes = mailbox::list(&setup.ctx.db, NOW).await.unwrap();
    assert_eq!(mailboxes[0].cloud_emails, 1);
    assert_eq!(
        mailboxes[0].cloud_pause.as_ref().map(|p| p.reason.as_str()),
        Some("发信太频繁")
    );

    setup.server().fail_ack = false;
    setup.sync().await;
    assert!(!setup.server().emails.contains_key(&sent_id));
    assert!(!setup.server().emails.contains_key(&failed_id));
    assert!(!setup.sync().await);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_upload_the_server_may_have_kept_is_withdrawn_not_left_to_go_twice(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let id = setup.scheduled("王伟", NOW + DAY).await;
    // Kept, though the answer says otherwise.
    setup.server().fail_after_keeping = true;
    let preview = setup.preview(None).await;
    let emails = preview.emails.iter().map(|p| p.choice.clone()).collect();
    let handed = cloud_send::hand_off(
        &setup.ctx,
        HandOff {
            emails,
            consents: vec![1],
        },
        NOW,
    )
    .await;
    assert!(handed.is_err());
    assert_eq!(setup.email(id).await.0, "cloud");
    assert_eq!(setup.server().emails.len(), 1);

    setup.sync().await;
    assert_eq!(setup.email(id).await, ("approved".into(), None, false));
    assert!(setup.server().emails.is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_limits_count_only_what_can_go(db: SqlitePool) {
    let setup = Setup::new(db).await;
    // 45 MB each: four come to 180 MB of the 200 allowed, a fifth would not fit.
    let mut ids = Vec::new();
    for name in ["陈静", "王伟", "李娜", "张敏", "刘洋"] {
        ids.push(setup.scheduled(name, NOW + DAY).await);
    }
    for id in &ids {
        sqlx::query(
            r#"INSERT INTO email_attachment (email_id, material_id, file_name, size)
            VALUES (?, NULL, '论文.pdf', 45 * 1024 * 1024)"#,
        )
        .bind(id)
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    }
    // The first goes from the Gmail mailbox, which the server cannot use.
    sqlx::query(
        "UPDATE mentor SET mailbox_id = 2 WHERE id = (SELECT mentor_id FROM email WHERE id = ?)",
    )
    .bind(ids[0])
    .execute(&setup.ctx.db)
    .await
    .unwrap();

    let preview = setup.preview(None).await;
    let planned: Vec<i64> = preview.emails.iter().map(|p| p.choice.id).collect();
    assert_eq!(planned, ids[1..]);
    let unfit: Vec<(i64, Reason)> = preview.unfit.iter().map(|u| (u.id, u.reason)).collect();
    assert_eq!(unfit, [(ids[0], Reason::ForeignMailbox)]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn withdrawing_takes_back_what_is_not_being_sent(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let back = setup.scheduled("王伟", NOW + DAY).await;
    let going = setup.scheduled("李娜", NOW + DAY).await;
    let gone = setup.scheduled("张敏", NOW + DAY).await;
    setup.hand_off().await;
    let (going_id, gone_id) = (setup.cloud_id(going).await, setup.cloud_id(gone).await);
    {
        let mut server = setup.server();
        server.emails.get_mut(&going_id).unwrap().state = Sending::Sending;
        server.finish(&gone_id, Outcome::Sent { at: NOW + DAY });
    }
    let withdraw = |id| cloud_send::withdraw(&setup.ctx, Withdraw::Email(id), NOW);
    assert_eq!(withdraw(back).await.unwrap().sending, 0);
    assert_eq!(setup.email(back).await, ("approved".into(), None, false));
    assert_eq!(withdraw(going).await.unwrap().sending, 1);
    assert_eq!(setup.email(going).await.0, "cloud");
    withdraw(gone).await.unwrap();
    assert_eq!(setup.email(gone).await, ("sent".into(), None, true));
    assert_eq!(setup.server().emails.len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn canceled_emails_are_withdrawn_at_the_next_sync(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let back = setup.scheduled("王伟", NOW + DAY).await;
    let gone = setup.scheduled("李娜", NOW + DAY).await;
    setup.hand_off().await;
    let gone_id = setup.cloud_id(gone).await;
    setup
        .server()
        .finish(&gone_id, Outcome::Sent { at: NOW + DAY });
    let mentors: Vec<i64> = sqlx::query_scalar("SELECT mentor_id FROM email ORDER BY id")
        .fetch_all(&setup.ctx.db)
        .await
        .unwrap();
    api::call(&setup.ctx, "trashMentors", json!({ "ids": mentors }))
        .await
        .unwrap();
    // Still with the server until withdrawn, so not deleted for good.
    assert_eq!(setup.email(back).await.0, "cloud");
    assert!(
        api::call(&setup.ctx, "deleteMentors", json!({ "ids": mentors }))
            .await
            .is_err()
    );

    setup.sync().await;
    assert_eq!(setup.email(back).await, ("canceled".into(), None, false));
    // One the server sent before counts as sent.
    assert_eq!(setup.email(gone).await, ("sent".into(), None, true));
    assert!(setup.server().emails.is_empty());
    api::call(&setup.ctx, "deleteMentors", json!({ "ids": mentors }))
        .await
        .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_upload_cut_short_goes_back_without_being_sent_twice(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let up = setup.scheduled("王伟", NOW + DAY).await;
    let never = setup.scheduled("李娜", NOW + DAY).await;
    // The app quit after marking both, with only the first up.
    setup.hand_off().await;
    let never_id = setup.cloud_id(never).await;
    setup.server().emails.remove(&never_id);
    sqlx::query("UPDATE email SET cloud_uploading = 1")
        .execute(&setup.ctx.db)
        .await
        .unwrap();

    setup.sync().await;
    assert_eq!(setup.email(up).await, ("approved".into(), None, false));
    assert_eq!(setup.email(never).await, ("approved".into(), None, false));
    assert!(setup.server().emails.is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn one_the_server_no_longer_knows_may_have_gone(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let up = setup.scheduled("王伟", NOW + DAY).await;
    let uploading = setup.scheduled("李娜", NOW + DAY).await;
    setup.hand_off().await;
    // Sent, and their outcomes kept until the server dropped them, while this computer was away.
    for id in [up, uploading] {
        let cloud_id = setup.cloud_id(id).await;
        setup.server().emails.remove(&cloud_id);
    }
    sqlx::query("UPDATE email SET cloud_uploading = 1 WHERE id = ?")
        .bind(uploading)
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    // Past what the server keeps of either.
    let later = NOW + 2 * DAY + RETENTION;
    cloud_send::withdraw(&setup.ctx, Withdraw::Email(up), later)
        .await
        .unwrap();
    assert_eq!(setup.email(up).await, ("maybe_sent".into(), None, true));
    let mut changed = false;
    cloud_send::sync(&setup.ctx, later, &mut changed)
        .await
        .unwrap();
    assert_eq!(
        setup.email(uploading).await,
        ("maybe_sent".into(), None, true)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn emails_wait_for_the_account_that_handed_them_over(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let id = setup.scheduled("王伟", NOW + DAY).await;
    setup.hand_off().await;
    let cloud_id = setup.cloud_id(id).await;
    setup
        .server()
        .finish(&cloud_id, Outcome::Sent { at: NOW + DAY });
    setup.log_in("other@example.invalid").await;
    assert!(!setup.sync().await);
    assert_eq!(setup.email(id).await.0, "cloud");
    // Its mailbox goes from the server for that account until then.
    let mine = setup.scheduled("李娜", NOW + 2 * DAY).await;
    let any = setup.scheduled("张敏", NOW + 2 * DAY).await;
    sqlx::query(
        "UPDATE mentor SET mailbox_id = 1 WHERE id = (SELECT mentor_id FROM email WHERE id = ?)",
    )
    .bind(mine)
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    let unfit: Vec<(i64, Reason)> = setup
        .preview(None)
        .await
        .unfit
        .iter()
        .map(|u| (u.id, u.reason))
        .collect();
    assert_eq!(
        unfit,
        [(mine, Reason::OtherAccount), (any, Reason::NoMailbox)]
    );
    assert!(
        cloud_send::withdraw(&setup.ctx, Withdraw::Email(id), NOW)
            .await
            .is_err()
    );

    setup.log_in("student@example.invalid").await;
    assert!(setup.sync().await);
    assert_eq!(setup.email(id).await.0, "sent");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_changed_secret_goes_up_while_emails_wait(db: SqlitePool) {
    let setup = Setup::new(db).await;
    setup.scheduled("王伟", NOW + DAY).await;
    setup.hand_off().await;
    setup.server().mailboxes.get_mut(QQ).unwrap().pause = Some(transport::pause::Pause {
        until: NOW + DAY,
        reason: "云端登录被拒".into(),
    });
    setup.sync().await;
    let edit = |address: &str| {
        json!({
            "id": 1, "address": address, "senderName": "我", "provider": "qq",
            "smtp": { "host": "smtp.qq.com", "port": 465, "security": "tls" },
            "imap": { "host": "imap.qq.com", "port": 993, "security": "tls" },
            "dailyLimit": 50, "minIntervalS": 60, "secret": "new-secret",
        })
    };
    // The server knows the mailbox by its address.
    assert!(
        api::call(&setup.ctx, "updateMailbox", edit("you@qq.com"))
            .await
            .is_err()
    );
    api::call(&setup.ctx, "updateMailbox", edit(QQ))
        .await
        .unwrap();
    setup.sync().await;
    assert_eq!(setup.server().mailboxes[QQ].upload.secret, "new-secret");
    // The new secret ends the pause, as the same sync brings back.
    let mailboxes = mailbox::list(&setup.ctx.db, NOW).await.unwrap();
    assert!(mailboxes[0].cloud_pause.is_none());
    let stale: bool = sqlx::query_scalar("SELECT cloud_stale FROM mailbox WHERE id = 1")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert!(!stale);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn its_copy_in_the_sent_folder_shows_the_server_sent_it(db: SqlitePool) {
    let setup = Setup::new(db).await;
    let id = setup.scheduled("王伟", NOW + DAY).await;
    setup.hand_off().await;
    let (message_id, to): (String, String) =
        sqlx::query_as("SELECT message_id, to_address FROM email WHERE id = ?")
            .bind(id)
            .fetch_one(&setup.ctx.db)
            .await
            .unwrap();
    // Kept an hour and a half late, after a pause on the server.
    let late = NOW + DAY + 90 * MINUTE;
    sqlx::query("UPDATE email SET latest_at = ? WHERE id = ?")
        .bind(NOW + DAY + 3 * HOUR)
        .bind(id)
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    let mut conn = setup.ctx.db.acquire().await.unwrap();
    // As QQ keeps it, under a Message-ID of its own.
    let candidates = email::copy_candidates(
        &mut conn,
        &email::Copy {
            from_address: QQ,
            recipient: &to,
            subject: "申请硕士",
            at: late,
        },
    )
    .await
    .unwrap();
    assert_eq!(candidates.iter().map(|c| c.id).collect::<Vec<_>>(), [id]);
    let found = email::sent_copy(&mut conn, &message_id, &[to], late, late)
        .await
        .unwrap();
    assert!(matches!(found, email::SentCopy::FromCloud(_)));
    drop(conn);
    assert_eq!(setup.email(id).await, ("sent".into(), None, true));
    let sent_at: i64 = sqlx::query_scalar("SELECT sent_at FROM email WHERE id = ?")
        .bind(id)
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert_eq!(sent_at, late);
    // The server's result is no longer wanted.
    let cloud_id = setup.server().emails.keys().next().unwrap().clone();
    setup
        .server()
        .finish(&cloud_id, Outcome::Sent { at: NOW + DAY });
    setup.sync().await;
    assert!(setup.server().emails.is_empty());
}
