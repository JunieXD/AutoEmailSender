#![cfg(test)]

use std::{future::Future, sync::Arc, time::Duration};

use api::{Context, phones::Event};
use cloud_api::relay::close;
use serde_json::{Value, json};
use sqlx::SqlitePool;
use test_fixtures::relay::{Closed, Pairing, Phone, Relay};
use tokio::{sync::mpsc, time::timeout};

const IPHONE: &str = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 \
    (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

#[derive(Debug, PartialEq, Eq)]
enum Seen {
    Pairing(Option<String>),
    Changed,
    Called,
}

struct Setup {
    ctx: Arc<Context>,
    relay: Relay,
    events: mpsc::UnboundedReceiver<Seen>,
    _dir: tempfile::TempDir,
}

impl Setup {
    async fn new(db: SqlitePool, token: &str) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let relay = Relay::start("token").await.unwrap();
        let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), dir.path());
        ctx.account = account::Client::with_address(&ctx.net, relay.address.clone());
        sqlx::query(
            "INSERT INTO account (id, email, token) VALUES (1, 'student@example.invalid', ?)",
        )
        .bind(token)
        .execute(&ctx.db)
        .await
        .unwrap();
        sqlx::query("UPDATE profile SET phone_access = 1")
            .execute(&ctx.db)
            .await
            .unwrap();
        let ctx = Arc::new(ctx);
        let (seen, events) = mpsc::unbounded_channel();
        api::phones::start(ctx.clone(), move |event| {
            let _ = seen.send(match event {
                Event::Pairing(name) => Seen::Pairing(name),
                Event::Changed => Seen::Changed,
                Event::Called => Seen::Called,
            });
        });
        Self {
            ctx,
            relay,
            events,
            _dir: dir,
        }
    }

    async fn call(&self, op: &str, input: Value) -> Value {
        api::call(&self.ctx, op, input)
            .await
            .unwrap_or_else(|e| panic!("{op}: {}", e.message))
    }

    /// Waits for the relay connection to reach `kind`.
    async fn until(&mut self, kind: &str) {
        timeout(Duration::from_secs(10), async {
            while self.call("getPhoneAccess", json!({})).await["status"]["kind"] != kind {
                self.events.recv().await.unwrap();
            }
        })
        .await
        .unwrap();
    }

    async fn asked(&mut self) -> String {
        timeout(Duration::from_secs(10), async {
            loop {
                if let Seen::Pairing(Some(name)) = self.events.recv().await.unwrap() {
                    return name;
                }
            }
        })
        .await
        .unwrap()
    }

    async fn code(&self) -> Pairing {
        Pairing::read(
            self.call("addPhone", json!({})).await["url"]
                .as_str()
                .unwrap(),
        )
    }

    /// A phone that scanned a new code and was allowed, with its welcome read.
    async fn paired(&mut self) -> (Phone, Pairing) {
        let pairing = self.code().await;
        let mut phone = Phone::connect(&self.relay.address, &pairing, IPHONE)
            .await
            .unwrap();
        assert_eq!(self.asked().await, "iPhone · Safari");
        self.call("answerPhonePairing", json!({ "allow": true }))
            .await;
        let welcome = phone.receive().await.unwrap();
        assert!(welcome["welcome"]["computer"].is_string());
        assert_eq!(welcome["welcome"]["version"], env!("CARGO_PKG_VERSION"));
        (phone, pairing)
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_allowed_phone_calls_what_the_window_may_and_hears_its_events(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let (mut phone, _) = setup.paired().await;
    let phones = setup.call("listPhones", json!({})).await;
    assert_eq!(phones[0]["name"], "iPhone · Safari");
    assert_eq!(phones[0]["online"], true);

    assert!(phone.call("getProfile", json!({})).await.is_ok());
    // A phone back from the background asks whether the computer still hears it.
    phone.send(&json!({ "ping": true })).await;
    assert_eq!(phone.receive().await.unwrap(), json!({ "pong": true }));
    // Its composers withdraw their AI writing like the window's.
    phone
        .call("cancelWriting", json!({ "request": "r" }))
        .await
        .unwrap();
    while setup.events.try_recv().is_ok() {}
    // A request longer than one Noise message arrives whole.
    let error = phone
        .call(
            "noSuchOperation",
            json!({ "padding": "长".repeat(100_000) }),
        )
        .await
        .unwrap_err();
    assert_eq!(error["code"], "bad_input");
    for op in ["addPhone", "answerAgentRequest", "setAgentAccess"] {
        let error = phone.call(op, json!({})).await.unwrap_err();
        assert_eq!(error["message"], "这个操作只能在电脑上做。");
    }
    phone.call("listTags", json!({})).await.unwrap();
    assert_eq!(setup.events.recv().await, Some(Seen::Called));

    setup.ctx.phones.push("tasks-changed", "null");
    assert_eq!(
        phone.receive().await.unwrap(),
        json!({ "event": "tasks-changed", "payload": null })
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn only_an_allowed_phone_with_a_fresh_code_pairs(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;

    let pairing = setup.code().await;
    let mut phone = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    setup.asked().await;
    setup
        .call("answerPhonePairing", json!({ "allow": false }))
        .await;
    assert_eq!(phone.receive().await, Err(Closed::Code(close::DENIED)));
    // The code is used up.
    assert_eq!(
        Phone::connect(&setup.relay.address, &pairing, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );

    let pairing = setup.code().await;
    setup.call("cancelPhonePairing", json!({})).await;
    assert_eq!(
        Phone::connect(&setup.relay.address, &pairing, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );

    // Turning phone access off voids the code on screen.
    let pairing = setup.code().await;
    setup
        .call("setPhoneAccess", json!({ "enabled": false }))
        .await;
    setup.until("off").await;
    setup
        .call("setPhoneAccess", json!({ "enabled": true }))
        .await;
    setup.until("online").await;
    assert_eq!(
        Phone::connect(&setup.relay.address, &pairing, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );

    let pairing = setup.code().await;
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(5 * 60 + 1)).await;
    tokio::time::resume();
    assert_eq!(
        Phone::connect(&setup.relay.address, &pairing, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );

    let stranger = Pairing {
        device: "0".repeat(32),
        ..pairing
    };
    assert_eq!(
        Phone::connect(&setup.relay.address, &stranger, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );
    assert_eq!(setup.call("listPhones", json!({})).await, json!([]));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_revoked_phone_is_cut_off_and_cannot_come_back(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let (mut phone, pairing) = setup.paired().await;
    // Paired phones connect again without asking.
    let mut again = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    assert!(again.receive().await.unwrap()["welcome"].is_object());

    setup
        .call("revokePhone", json!({ "id": pairing.device }))
        .await;
    assert_eq!(phone.receive().await, Err(Closed::Code(close::UNPAIRED)));
    assert_eq!(again.receive().await, Err(Closed::Code(close::UNPAIRED)));
    assert_eq!(
        Phone::connect(&setup.relay.address, &pairing, IPHONE)
            .await
            .err(),
        Some(Closed::Code(close::UNPAIRED))
    );
    assert_eq!(setup.call("listPhones", json!({})).await, json!([]));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn turning_access_off_disconnects_and_another_account_forgets_the_phones(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let (mut phone, pairing) = setup.paired().await;
    setup
        .call("setPhoneAccess", json!({ "enabled": false }))
        .await;
    setup.until("off").await;
    assert_eq!(phone.receive().await, Err(Closed::Code(close::OFFLINE)));
    assert_eq!(setup.relay.state.lock().unwrap().computer, None);

    // The same account logging in again keeps the room and the phone.
    let room = || async {
        sqlx::query_scalar::<_, Option<String>>("SELECT phone_room FROM profile")
            .fetch_one(&setup.ctx.db)
            .await
            .unwrap()
    };
    sqlx::raw_sql(
        "DELETE FROM account;
        INSERT INTO account (id, email, token) VALUES (1, 'student@example.invalid', 'token');",
    )
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    assert_eq!(room().await, Some(pairing.room.clone()));
    assert_eq!(
        setup.call("listPhones", json!({})).await[0]["id"],
        pairing.device
    );
    sqlx::raw_sql(
        "DELETE FROM account;
        INSERT INTO account (id, email, token) VALUES (1, 'other@example.invalid', 'token');",
    )
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    assert_eq!(room().await, None);
    assert_eq!(setup.call("listPhones", json!({})).await, json!([]));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_phone_nobody_answers_is_turned_away(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let pairing = setup.code().await;
    let mut phone = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    setup.asked().await;
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(5 * 60 + 1)).await;
    tokio::time::resume();
    let answer = timeout(Duration::from_secs(10), phone.receive()).await;
    assert_eq!(answer, Ok(Err(Closed::Code(close::DENIED))));
    timeout(Duration::from_secs(10), async {
        while setup.events.recv().await != Some(Seen::Pairing(None)) {}
    })
    .await
    .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_answer_being_saved_when_time_runs_out_still_decides(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let pairing = setup.code().await;
    let mut phone = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    setup.asked().await;
    // The user allows the phone just in time, and saving it waits for the database.
    let mut held = Vec::new();
    for _ in 0..setup.ctx.db.options().get_max_connections() {
        held.push(setup.ctx.db.acquire().await.unwrap());
    }
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(5 * 60 - 1)).await;
    let answer = api::call(&setup.ctx, "answerPhonePairing", json!({ "allow": true }));
    tokio::pin!(answer);
    std::future::poll_fn(|cx| {
        assert!(answer.as_mut().poll(cx).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    tokio::time::advance(Duration::from_secs(2)).await;
    tokio::time::resume();
    drop(held);
    let (answer, welcome) = tokio::join!(answer, phone.receive());
    assert!(answer.is_ok());
    assert!(welcome.unwrap()["welcome"].is_object());
    assert_eq!(
        setup.call("listPhones", json!({})).await[0]["id"],
        pairing.device
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_answer_after_another_account_logged_in_pairs_nothing(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let pairing = setup.code().await;
    let mut phone = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    setup.asked().await;
    // Logged in, and the old account's phones cleared, before phone remote hears of it.
    sqlx::raw_sql(
        "DELETE FROM account;
        INSERT INTO account (id, email, token) VALUES (1, 'other@example.invalid', 'token');",
    )
    .execute(&setup.ctx.db)
    .await
    .unwrap();
    let answer = api::call(&setup.ctx, "answerPhonePairing", json!({ "allow": true })).await;
    assert!(answer.is_err());
    assert_eq!(phone.receive().await, Err(Closed::Code(close::DENIED)));
    assert_eq!(setup.call("listPhones", json!({})).await, json!([]));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_computer_holds_its_room_again_after_the_relay_drops_it(db: SqlitePool) {
    let mut setup = Setup::new(db, "token").await;
    setup.until("online").await;
    let (_, pairing) = setup.paired().await;
    setup.relay.drop_computer();
    setup.until("failed").await;
    setup.until("online").await;
    let mut phone = Phone::connect(&setup.relay.address, &pairing, IPHONE)
        .await
        .unwrap();
    assert!(phone.receive().await.unwrap()["welcome"].is_object());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_login_the_relay_refuses_signs_out(db: SqlitePool) {
    let mut setup = Setup::new(db, "expired").await;
    let mut account = setup.ctx.account.subscribe();
    timeout(Duration::from_secs(10), account.changed())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        account::state(&setup.ctx.db).await.unwrap(),
        account::State::Expired { .. }
    ));
    setup.until("off").await;
}
