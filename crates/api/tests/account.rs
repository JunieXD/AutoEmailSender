#![cfg(test)]

use std::sync::Arc;

use api::Context;
use cloud_api::{ApiError, ErrorCode};
use sqlx::SqlitePool;
use test_fixtures::provider::{Provider, Response};

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn background_library_expiration_notifies_account_subscribers(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    ctx.account =
        account::Client::with_address(&ctx.net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (id, email, token)
        VALUES (1, 'student@example.invalid', 'test-token')"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    let changed = ctx.account.subscribe();
    let ctx = Arc::new(ctx);
    let responding = async {
        let request = server.requests.recv().await.unwrap();
        assert!(request.line.starts_with("GET /v1/library/changes?after=0 "));
        request
            .respond
            .send(Response::Raw {
                status: 401,
                content: serde_json::to_string(&ApiError {
                    code: ErrorCode::SessionExpired,
                    retry_after_seconds: None,
                })
                .unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
        // The synchronization lock is released after the failed request has been handled.
        let _checked = ctx.library.acquire().await;
        assert!(matches!(
            account::state(&ctx.db).await.unwrap(),
            account::State::Expired { email } if email == "student@example.invalid"
        ));
        assert!(changed.has_changed().unwrap());
    };
    tokio::select! {
        () = api::libraries::run(ctx.clone()) => panic!("background synchronization must keep running"),
        () = responding => {},
    }
}
