#![cfg(test)]

use api::Context;
use email::tracking::State as Tracked;
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::provider::{Provider, Response};
use test_fixtures::tracking::{Server, State};

const NOW: i64 = 1_800_000_000_000;
const DAY: i64 = 24 * 60 * 60 * 1000;

struct Setup {
    _dir: tempfile::TempDir,
    ctx: Context,
    server: Server,
}

impl Setup {
    async fn new(db: SqlitePool, left: u32) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let server = Server::start(State {
            now: NOW,
            plus_until: Some(NOW + DAY),
            left,
            ..Default::default()
        })
        .await
        .unwrap();
        let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), dir.path());
        ctx.account = account::Client::with_address(&ctx.net, server.address.clone());
        sqlx::raw_sql(
            r#"INSERT INTO account (terms, id, email, token)
            VALUES ('9999-12-31', 1, 'student@example.invalid', 'token');
            UPDATE profile SET email_tracking = 1;
            INSERT INTO mentor (name, created_at, updated_at) VALUES ('王伟', 0, 0);"#,
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

    /// A first letter that went out at `at` with a tracking image `id` on the quota of the
    /// account logged in first, and is not registered yet.
    async fn sent_at(&self, id: &str, at: i64) {
        sqlx::query(
            r#"INSERT INTO email (mentor_id, kind, state, planned_at, subject, body_md, html, text,
                message_id, sent_at, from_address, to_address, tracking_id, tracking_state,
                tracking_account, tracking_chosen_at, created_at, updated_at)
            VALUES (1, 'first', 'sent', 0, '申请', '您好', '<p>您好</p>', '您好', ?1, ?2,
                'me@qq.com', 'wang@example.edu.cn', ?3, 'pending', 'student@example.invalid', ?2,
                0, 0)"#,
        )
        .bind(format!("<{id}@qq.com>"))
        .bind(at)
        .bind(id)
        .execute(&self.ctx.db)
        .await
        .unwrap();
    }

    async fn sent(&self, id: &str) {
        self.sent_at(id, NOW).await;
    }

    /// Logs in on this computer as `email`, which the server then answers for.
    async fn log_in(&self, email: &str) {
        let db = &self.ctx.db;
        sqlx::query("DELETE FROM account")
            .execute(db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO account (terms, id, email, token) VALUES ('9999-12-31', 1, ?, ?)")
            .bind(email)
            .bind(format!("{email}-token"))
            .execute(db)
            .await
            .unwrap();
        sqlx::query("UPDATE profile SET email_tracking = 1")
            .execute(db)
            .await
            .unwrap();
        self.server().email = email.into();
    }

    /// Tracking states and opens, by tracking id.
    async fn emails(&self) -> Vec<(String, Tracked, Option<u32>, Option<i64>)> {
        sqlx::query_as(
            r#"SELECT tracking_id, tracking_state, open_count, last_opened_at FROM email
            ORDER BY id"#,
        )
        .fetch_all(&self.ctx.db)
        .await
        .unwrap()
    }

    async fn left(&self) -> i64 {
        sqlx::query_scalar("SELECT tracking_left FROM account")
            .fetch_one(&self.ctx.db)
            .await
            .unwrap()
    }

    fn server(&self) -> std::sync::MutexGuard<'_, State> {
        self.server.state.lock().unwrap()
    }

    /// Talks to `provider` instead, which answers each request as a test says.
    fn answered_by(&mut self, provider: &Provider) {
        let address = provider.address.strip_suffix("/v1").unwrap().into();
        self.ctx.account = account::Client::with_address(&self.ctx.net, address);
    }
}

fn reply(body: serde_json::Value) -> Response {
    Response::Raw {
        status: 200,
        content: body.to_string(),
    }
}

fn id(n: u8) -> String {
    format!("{n:032x}")
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn letters_are_registered_while_the_quota_lasts_and_opens_come_back(db: SqlitePool) {
    let setup = Setup::new(db, 1).await;
    setup.sent(&id(1)).await;
    setup.sent(&id(2)).await;
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    let emails = setup.emails().await;
    assert_eq!(
        (emails[0].1, emails[1].1),
        (Tracked::Tracked, Tracked::NoQuota)
    );
    assert_eq!(setup.left().await, 0);

    {
        let mut server = setup.server();
        server.now = NOW + 1000;
        server.open(&id(1));
        server.now = NOW + 2000;
        server.open(&id(1));
    }
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    assert_eq!(setup.emails().await[0].2, Some(2));
    assert_eq!(setup.emails().await[0].3, Some(NOW + 2000));

    // Without Plus, letters are not registered; their opens would not count.
    setup.server().plus_until = Some(NOW - 1);
    setup.sent(&id(3)).await;
    api::tracking::sync(&setup.ctx, false, NOW).await.unwrap();
    assert_eq!(setup.emails().await[2].1, Tracked::NoPlus);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn registering_waits_without_failing_until_the_terms_are_agreed_to(db: SqlitePool) {
    let setup = Setup::new(db, 1).await;
    setup.sent(&id(1)).await;
    let agree = |terms: &'static str| {
        sqlx::query("UPDATE account SET terms = ?")
            .bind(terms)
            .execute(&setup.ctx.db)
    };
    agree("2000-01-01").await.unwrap();
    assert!(!api::tracking::sync(&setup.ctx, true, NOW).await.unwrap());
    let failure: Option<String> = sqlx::query_scalar("SELECT tracking_error FROM account")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert_eq!(failure, None);
    assert_eq!(setup.emails().await[0].1, Tracked::Pending);

    agree("9999-12-31").await.unwrap();
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    assert_eq!(setup.emails().await[0].1, Tracked::Tracked);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn opens_come_in_pages_and_a_failed_page_is_fetched_again(db: SqlitePool) {
    let setup = Setup::new(db, 5).await;
    for n in 1..=3 {
        setup.sent(&id(n)).await;
    }
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    {
        let mut server = setup.server();
        server.page = 1;
        for n in 1..=3 {
            server.open(&id(n));
        }
        // The page after the first open fails.
        server.fail_after = Some(1);
    }
    assert!(api::tracking::sync(&setup.ctx, true, NOW).await.is_err());
    let opens: Vec<_> = setup.emails().await.into_iter().map(|e| e.2).collect();
    assert_eq!(opens, [Some(1), None, None]);
    let failure: Option<String> = sqlx::query_scalar("SELECT tracking_error FROM account")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert!(failure.is_some());

    setup.server().fail_after = None;
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    let opens: Vec<_> = setup.emails().await.into_iter().map(|e| e.2).collect();
    assert_eq!(opens, [Some(1), Some(1), Some(1)]);
    let failure: Option<String> = sqlx::query_scalar("SELECT tracking_error FROM account")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert_eq!(failure, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn another_account_fetches_opens_from_the_start(db: SqlitePool) {
    let setup = Setup::new(db, 5).await;
    setup.sent(&id(1)).await;
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    setup.server().open(&id(1));
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    let revision: i64 = sqlx::query_scalar("SELECT tracking_revision FROM account")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert_eq!(revision, 1);
    // Logging in to another account replaces the row, progress and all, and turns tracking off.
    sqlx::raw_sql(
        r#"DELETE FROM account;
        INSERT INTO account (terms, id, email, token) VALUES ('9999-12-31', 1, 'other@example.invalid', 'other');"#,
    )
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    let on: bool = sqlx::query_scalar("SELECT email_tracking FROM profile")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert!(!on);
    let revision: i64 = sqlx::query_scalar("SELECT tracking_revision FROM account")
        .fetch_one(&setup.ctx.db)
        .await
        .unwrap();
    assert_eq!(revision, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn letters_wait_for_the_account_whose_quota_they_use(db: SqlitePool) {
    let setup = Setup::new(db, 5).await;
    setup.sent(&id(1)).await;
    setup.log_in("other@example.invalid").await;
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    assert_eq!(setup.emails().await[0].1, Tracked::Pending);
    assert_eq!(setup.server().left, 5);

    setup.log_in("student@example.invalid").await;
    api::tracking::sync(&setup.ctx, true, NOW).await.unwrap();
    assert_eq!(setup.emails().await[0].1, Tracked::Tracked);
    assert_eq!(setup.server().left, 4);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn letters_not_registered_within_thirty_days_are_given_up(db: SqlitePool) {
    let setup = Setup::new(db, 5).await;
    let within = cloud_api::tracking::REGISTER_WITHIN;
    setup.sent_at(&id(1), NOW - within - 1).await;
    setup.sent_at(&id(2), NOW - within).await;
    api::tracking::sync(&setup.ctx, false, NOW).await.unwrap();
    let states: Vec<_> = setup.emails().await.into_iter().map(|e| e.1).collect();
    assert_eq!(states, [Tracked::Expired, Tracked::Tracked]);
    assert_eq!(setup.server().left, 4);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_recovery_or_a_new_quota_is_a_change_to_show(db: SqlitePool) {
    let setup = Setup::new(db, 5).await;
    assert!(api::tracking::sync(&setup.ctx, true, NOW).await.unwrap());
    assert!(!api::tracking::sync(&setup.ctx, true, NOW).await.unwrap());
    setup.server().fail_after = Some(0);
    assert!(api::tracking::sync(&setup.ctx, true, NOW).await.is_err());
    setup.server().fail_after = None;
    assert!(api::tracking::sync(&setup.ctx, true, NOW).await.unwrap());
    setup.server().left = 9;
    assert!(api::tracking::sync(&setup.ctx, true, NOW).await.unwrap());
    assert_eq!(setup.left().await, 9);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_registration_answered_after_logging_in_again_is_not_noted(db: SqlitePool) {
    let mut provider = Provider::start().await.unwrap();
    let mut setup = Setup::new(db, 5).await;
    setup.answered_by(&provider);
    setup.sent(&id(1)).await;
    let replies = async {
        let request = provider.requests.recv().await.unwrap();
        setup.log_in("other@example.invalid").await;
        request
            .respond
            .send(reply(
                json!({"accepted": [id(1)], "rejected": [], "left": 4}),
            ))
            .unwrap();
    };
    let (synced, ()) = tokio::join!(api::tracking::sync(&setup.ctx, false, NOW), replies);
    assert!(synced.is_err());
    assert_eq!(setup.emails().await[0].1, Tracked::Pending);
    assert_eq!(setup.left().await, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tracking_is_not_turned_on_by_the_plus_of_the_login_before(db: SqlitePool) {
    let mut provider = Provider::start().await.unwrap();
    let mut setup = Setup::new(db, 5).await;
    setup.answered_by(&provider);
    let db = &setup.ctx.db;
    sqlx::query("UPDATE profile SET email_tracking = 0")
        .execute(db)
        .await
        .unwrap();
    let replies = async {
        let request = provider.requests.recv().await.unwrap();
        // Logged in again to the same account: the token, and so the login, is another.
        sqlx::query("UPDATE account SET token = 'token-again'")
            .execute(db)
            .await
            .unwrap();
        request
            .respond
            .send(reply(json!({
                "email": "student@example.invalid",
                "plusUntil": 4_000_000_000_000_i64,
                "tracking": {"used": 0, "limit": 500, "packLeft": 0, "packExpiresAt": null},
                "terms": "9999-12-31"
            })))
            .unwrap();
    };
    let switch = api::tracking::TrackingSwitch { enabled: true };
    let (enabled, ()) = tokio::join!(api::tracking::set_tracking(&setup.ctx, switch), replies);
    assert!(enabled.is_err());
    let on: bool = sqlx::query_scalar("SELECT email_tracking FROM profile")
        .fetch_one(db)
        .await
        .unwrap();
    assert!(!on);
}
