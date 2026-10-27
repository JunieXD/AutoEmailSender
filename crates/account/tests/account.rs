use account::{Client, Error, State, login_key};
use cloud_api::{Account, ApiError, ErrorCode, LoginRequest, Session, path};
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::provider::{Provider, Response};

#[tokio::test]
async fn derivation_matches_an_independent_argon2_and_hkdf_vector_and_normalizes_email() {
    let password = "fixed-password-123";
    let expected = "pA21weRBxCiXsgzURi-C1Ry8eGXU3408uxsQSdp-hRY";
    for email in [
        "student@example.invalid",
        "  Student@EXAMPLE.invalid  ",
        "Ｓｔｕｄｅｎｔ＠ＥＸＡＭＰＬＥ．ｉｎｖａｌｉｄ",
    ] {
        assert_eq!(login_key(email, password.into()).await.unwrap(), expected);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn login_and_expiration_keep_email_but_never_expose_the_token(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    let calls = async {
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedOut
        ));
        let state = client
            .login(
                &db,
                " Student@example.invalid ",
                "fixed-password-123".into(),
            )
            .await
            .unwrap();
        let public = serde_json::to_value(&state).unwrap();
        assert_eq!(
            public,
            json!({"kind":"loggedIn", "email":"student@example.invalid"})
        );
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedIn { .. }
        ));
        let error = client
            .delete(&db, "fixed-password-123".into())
            .await
            .unwrap_err();
        assert!(matches!(error, Error::SignedOut));
        let expired = account::state(&db).await.unwrap();
        assert!(matches!(expired, State::Expired { email } if email == "student@example.invalid"));
        let (email, token): (String, Option<String>) =
            sqlx::query_as("SELECT email, token FROM account")
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(email, "student@example.invalid");
        assert!(token.is_none());
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::Expired { .. }
        ));
        client.logout(&db).await.unwrap();
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedOut
        ));
    };
    let replies = async {
        let request = server.requests.recv().await.unwrap();
        assert!(request.line.starts_with(&format!("POST {} ", path::LOGIN)));
        assert!(request.authorization.is_empty());
        let input: LoginRequest = serde_json::from_value(request.body).unwrap();
        assert_eq!(input.email, "student@example.invalid");
        assert_eq!(
            input.login_key,
            "pA21weRBxCiXsgzURi-C1Ry8eGXU3408uxsQSdp-hRY"
        );
        let body = serde_json::to_string(&Session {
            token: "test-session-token".into(),
            account: Account {
                email: input.email,
                sponsor_until: Some(1234),
            },
        })
        .unwrap();
        request
            .respond
            .send(Response::Raw {
                status: 200,
                content: body,
            })
            .unwrap();
        request.completed.await.unwrap();
        let request = server.requests.recv().await.unwrap();
        assert!(
            request
                .line
                .starts_with(&format!("DELETE {} ", path::ACCOUNT))
        );
        assert!(request.authorization == "Bearer test-session-token");
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
    };
    tokio::join!(calls, replies);
}

#[tokio::test]
async fn wire_errors_become_actionable_messages() {
    const CODES: [ErrorCode; 7] = [
        ErrorCode::EmailRegistered,
        ErrorCode::EmailNotRegistered,
        ErrorCode::InvalidCode,
        ErrorCode::InvalidCredentials,
        ErrorCode::RateLimited,
        ErrorCode::MailUnavailable,
        ErrorCode::Internal,
    ];
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    let calls = async {
        for code in CODES {
            let error = client
                .send_code("student@example.invalid", false)
                .await
                .unwrap_err();
            assert!(matches!(&error, Error::Server(error) if error.code == code));
            assert!(!error.message().contains("Internal"));
            if code == ErrorCode::RateLimited {
                assert!(error.message().contains("2 分钟"));
            }
        }
    };
    let replies = async {
        for code in CODES {
            let request = server.requests.recv().await.unwrap();
            request
                .respond
                .send(Response::Raw {
                    status: 400,
                    content: serde_json::to_string(&ApiError {
                        code,
                        retry_after_seconds: Some(61),
                    })
                    .unwrap(),
                })
                .unwrap();
            request.completed.await.unwrap();
        }
    };
    tokio::join!(calls, replies);
    drop(server);
    assert!(matches!(
        client
            .send_code("student@example.invalid", false)
            .await
            .unwrap_err(),
        Error::Network(_)
    ));
}

#[tokio::test]
async fn redirects_cannot_forward_account_credentials() {
    let mut origin = Provider::start().await.unwrap();
    let mut target = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, origin.address.strip_suffix("/v1").unwrap().into());
    let calling = client.send_code("student@example.invalid", false);
    let redirect = async {
        let request = origin.requests.recv().await.unwrap();
        request
            .respond
            .send(Response::Redirect {
                location: target.address.clone(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    let unexpected = async {
        target.requests.recv().await.unwrap();
        panic!("account credentials followed a redirect");
    };
    let expected = async {
        let (result, ()) = tokio::join!(calling, redirect);
        assert!(matches!(result, Err(Error::Network(_))));
    };
    tokio::select! {
        () = expected => {},
        () = unexpected => {},
    }
}
