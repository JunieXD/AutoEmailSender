#![cfg(test)]

use std::sync::Arc;

use api::Context;
use cloud_api::{ApiError, ErrorCode};
use sqlx::SqlitePool;
use test_fixtures::provider::{Provider, Request, Response};

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn background_library_expiration_notifies_account_subscribers(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    ctx.account =
        account::Client::with_address(&ctx.net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (terms, id, email, token)
        VALUES ('9999-12-31', 1, 'student@example.invalid', 'test-token')"#,
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
                    delete_at: None,
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

async fn signed_in(db: SqlitePool) -> (Provider, Arc<Context>) {
    let server = Provider::start().await.unwrap();
    let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    ctx.account =
        account::Client::with_address(&ctx.net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (terms, id, email, token)
        VALUES ('9999-12-31', 1, 'student@example.invalid', 'test-token')"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    (server, Arc::new(ctx))
}

/// Answers the request with a server error, and waits for the synchronization to finish.
async fn fail(request: Request, ctx: &Context) {
    request
        .respond
        .send(Response::Raw {
            status: 500,
            content: serde_json::to_string(&ApiError {
                code: ErrorCode::Internal,
                retry_after_seconds: None,
                delete_at: None,
            })
            .unwrap(),
        })
        .unwrap();
    request.completed.await.unwrap();
    drop(ctx.library.acquire().await);
}

async fn failure(ctx: &Context) -> serde_json::Value {
    api::call(ctx, "libraryFailure", serde_json::json!({}))
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_failing_library_synchronization_is_kept_until_one_succeeds(db: SqlitePool) {
    let (mut server, ctx) = signed_in(db).await;
    let checking = async {
        fail(server.requests.recv().await.unwrap(), &ctx).await;
        let failed = failure(&ctx).await;
        assert!(
            failed["since"].is_i64() && failed["message"].is_string(),
            "{failed}"
        );
        let responding = async {
            let request = server.requests.recv().await.unwrap();
            request
                .respond
                .send(Response::Raw {
                    status: 200,
                    content: r#"{"changes":[],"revision":0,"more":false}"#.into(),
                })
                .unwrap();
        };
        let (synced, ()) = tokio::join!(
            api::call(&ctx, "syncLibrary", serde_json::json!({})),
            responding
        );
        synced.unwrap();
        assert!(failure(&ctx).await.is_null());
    };
    tokio::select! {
        () = api::libraries::run(ctx.clone()) => panic!("background synchronization must keep running"),
        () = checking => {},
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_library_failure_is_not_kept_for_a_session_that_came_after(db: SqlitePool) {
    let (mut server, ctx) = signed_in(db).await;
    let checking = async {
        let request = server.requests.recv().await.unwrap();
        // Signed in again while the request was on its way.
        sqlx::query("UPDATE account SET token = 'next-token'")
            .execute(&ctx.db)
            .await
            .unwrap();
        fail(request, &ctx).await;
        assert!(failure(&ctx).await.is_null());
    };
    tokio::select! {
        () = api::libraries::run(ctx.clone()) => panic!("background synchronization must keep running"),
        () = checking => {},
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn logging_out_tells_account_subscribers(db: SqlitePool) {
    let (mut server, ctx) = signed_in(db).await;
    let changed = ctx.account.subscribe();
    let answering = async {
        let request = server.requests.recv().await.unwrap();
        assert!(request.line.starts_with("POST /v1/account/logout "));
        request
            .respond
            .send(Response::Raw {
                status: 200,
                content: "{}".into(),
            })
            .unwrap();
    };
    let (logged_out, ()) = tokio::join!(
        api::call(&ctx, "logoutAccount", serde_json::json!({})),
        answering
    );
    logged_out.unwrap();
    assert!(changed.has_changed().unwrap());
}
