use account::{Client, Error, State, keys};
use cloud_api::{
    Account, ApiError, ErrorCode, LoginRequest, Session, path, plus::Method as PayMethod,
    tracking::Quota,
};
use serde_json::json;
use sqlx::SqlitePool;
use test_fixtures::provider::{Provider, Response};

const QUOTA: Quota = Quota {
    used: 0,
    limit: 500,
    pack_left: 0,
    pack_expires_at: None,
};

/// HKDF-SHA256 with info `backup` over the same Argon2id output as the login key.
const BACKUP_KEY: [u8; 32] = [
    0x21, 0x91, 0xd8, 0x48, 0x59, 0x0a, 0xf8, 0x12, 0x69, 0x12, 0x01, 0x2d, 0xf6, 0xfa, 0x04, 0x59,
    0xf7, 0x73, 0xf7, 0x21, 0xa4, 0x2a, 0x92, 0x5e, 0x9a, 0xe9, 0x5a, 0x18, 0x22, 0xee, 0xee, 0x1d,
];

fn account_json(email: &str, terms: &str) -> serde_json::Value {
    json!({
        "email": email,
        "plusUntil": null,
        "tracking": {"used": 0, "limit": 500, "packLeft": 0, "packExpiresAt": null},
        "terms": terms,
    })
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn orders_carry_the_plus_terms_and_older_terms_stop_the_official_services(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (terms, id, email, token)
        VALUES ('9999-12-31', 1, 'student@example.invalid', 'plus-token')"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let calls = async {
        let items = client.plus_items(&db).await.unwrap();
        assert_eq!(items.tiers[0].item, "1m");
        let created = client
            .create_plus_order(&db, "1m".into(), PayMethod::Wxpay)
            .await
            .unwrap();
        assert_eq!(created.order, 7);
        assert!(matches!(
            client
                .create_plus_order(&db, "1m".into(), PayMethod::Alipay)
                .await,
            Err(Error::Invalid(_))
        ));
        // The account agreed to older terms elsewhere: only what works without them goes out.
        let login = account::saved_login(&db).await.unwrap();
        assert_eq!(
            client.account(&db, &login).await.unwrap().terms.as_deref(),
            Some("2000-01-01")
        );
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedIn {
                terms_outdated: true,
                ..
            }
        ));
        assert!(matches!(
            client.invite(&db).await,
            Err(Error::TermsOutdated)
        ));
        client.accept_terms(&db).await.unwrap();
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedIn {
                terms_outdated: false,
                ..
            }
        ));
    };
    let replies = async {
        for (method, path, body) in [
            (
                "GET",
                path::PLUS_ITEMS.to_string(),
                json!({"tiers":[{"item":"1m","duration":{"months":1},"price":1880,"until":1,"available":true}],
                    "packs":[],"latest":2,"refunded":false}),
            ),
            (
                "POST",
                path::PLUS_ORDERS.to_string(),
                json!({"order":7,"payUrl":"https://pay.example.invalid/AES7"}),
            ),
            (
                "POST",
                path::PLUS_ORDERS.to_string(),
                json!({"order":8,"payUrl":"http://pay.example.invalid/AES8"}),
            ),
            (
                "GET",
                path::ACCOUNT.to_string(),
                account_json("student@example.invalid", "2000-01-01"),
            ),
            ("POST", path::ACCOUNT_TERMS.to_string(), json!({})),
        ] {
            let request = server.requests.recv().await.unwrap();
            assert!(request.line.starts_with(&format!("{method} {path} ")));
            assert_eq!(request.authorization, "Bearer plus-token");
            if path == path::PLUS_ORDERS {
                assert_eq!(request.body["terms"], account::plus_terms());
            }
            if path == path::ACCOUNT_TERMS {
                assert_eq!(request.body, json!({"terms": account::terms()}));
            }
            request
                .respond
                .send(Response::Raw {
                    status: 200,
                    content: body.to_string(),
                })
                .unwrap();
            request.completed.await.unwrap();
        }
    };
    tokio::join!(calls, replies);
    assert!(server.requests.try_recv().is_err());
}

#[test]
fn the_terms_versions_are_the_dates_the_texts_take_effect() {
    for version in [account::terms(), account::plus_terms()] {
        assert!(
            chrono::NaiveDate::parse_from_str(version, "%Y-%m-%d").is_ok(),
            "{version}"
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invitation_and_redemption_send_normalized_codes_and_preserve_results(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        "INSERT INTO account (terms, id, email, token) VALUES ('9999-12-31', 1, 'student@qq.com', 'code-token')",
    )
    .execute(&db)
    .await
    .unwrap();
    let calls = async {
        assert!(matches!(
            client.invite(&db).await.unwrap(),
            cloud_api::Invite::Eligible {
                invited: 1,
                fill_until: None,
                ..
            }
        ));
        assert_eq!(
            client
                .fill_invite(&db, "  ａｂｃｄ-２３４５ ")
                .await
                .unwrap()
                .plus_until,
            Some(1800000000000)
        );
        let redeemed = client
            .redeem(&db, " ａｂｃｄ ２３４５-ｅｆｇｈ ")
            .await
            .unwrap();
        assert_eq!(redeemed.days, 7);
        assert_eq!(redeemed.plus_until, 1800604800000);
        let error = client.fill_invite(&db, "ABCDEFGH").await.unwrap_err();
        assert!(matches!(error, Error::Server(ref error) if error.code == ErrorCode::InviteFull));
        assert!(error.message().contains("4 位"));
        let error = client.redeem(&db, "ABCDEFGH2345").await.unwrap_err();
        assert!(matches!(error, Error::Server(ref error) if error.code == ErrorCode::CodeUsed));
        assert!(error.message().contains("已经用过"));
        assert!(matches!(
            client.fill_invite(&db, " - ").await,
            Err(Error::Invalid(_))
        ));
        assert!(matches!(
            client.redeem(&db, " - ").await,
            Err(Error::Invalid(_))
        ));
    };
    let replies = async {
        for (method, path, code, status, body) in [
            (
                "GET",
                path::INVITE,
                None,
                200,
                json!({"status":"eligible","code":"ABCDEFGH","invited":1,"fillUntil":null}),
            ),
            (
                "POST",
                path::INVITE,
                Some("ABCD2345"),
                200,
                json!({"plusUntil":1800000000000_i64}),
            ),
            (
                "POST",
                path::REDEEM,
                Some("ABCD2345EFGH"),
                200,
                json!({"days":7,"plusUntil":1800604800000_i64}),
            ),
            (
                "POST",
                path::INVITE,
                Some("ABCDEFGH"),
                409,
                json!({"code":"invite_full"}),
            ),
            (
                "POST",
                path::REDEEM,
                Some("ABCDEFGH2345"),
                409,
                json!({"code":"code_used"}),
            ),
        ] {
            let request = server.requests.recv().await.unwrap();
            assert!(request.line.starts_with(&format!("{method} {path} ")));
            assert_eq!(request.authorization, "Bearer code-token");
            if let Some(code) = code {
                assert_eq!(request.body, json!({"code":code}));
            }
            request
                .respond
                .send(Response::Raw {
                    status,
                    content: body.to_string(),
                })
                .unwrap();
            request.completed.await.unwrap();
        }
    };
    tokio::join!(calls, replies);
    assert!(server.requests.try_recv().is_err());
}

#[tokio::test]
async fn derivation_matches_an_independent_argon2_and_hkdf_vector_and_normalizes_email() {
    let password = "fixed-password-123";
    for email in [
        "student@example.invalid",
        "  Student@EXAMPLE.invalid  ",
        "Ｓｔｕｄｅｎｔ＠ＥＸＡＭＰＬＥ．ｉｎｖａｌｉｄ",
    ] {
        let keys = keys(email, password.into()).await.unwrap();
        assert_eq!(keys.login, "pA21weRBxCiXsgzURi-C1Ry8eGXU3408uxsQSdp-hRY");
        assert_eq!(keys.backup, BACKUP_KEY);
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
                false,
            )
            .await
            .unwrap();
        let public = serde_json::to_value(&state).unwrap();
        assert_eq!(
            public,
            json!({"kind":"loggedIn", "email":"student@example.invalid", "termsOutdated":false})
        );
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedIn { .. }
        ));
        let error = client
            .delete(
                &db,
                " ＳＴＵＤＥＮＴ＠ＥＸＡＭＰＬＥ．ｉｎｖａｌｉｄ ",
                "fixed-password-123".into(),
            )
            .await
            .unwrap_err();
        assert!(matches!(error, Error::SignedOut));
        let expired = account::state(&db).await.unwrap();
        assert!(matches!(expired, State::Expired { email } if email == "student@example.invalid"));
        let (email, token, backup_key): (String, Option<String>, Vec<u8>) =
            sqlx::query_as("SELECT email, token, backup_key FROM account")
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(email, "student@example.invalid");
        assert!(token.is_none());
        assert_eq!(backup_key, BACKUP_KEY);
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::Expired { .. }
        ));
        assert!(matches!(
            client
                .delete(&db, "another@example.invalid", "fixed-password-123".into())
                .await,
            Err(Error::SignedOut)
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
                plus_until: Some(1234),
                tracking: QUOTA,
                terms: Some("9999-12-31".into()),
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
                    delete_at: None,
                })
                .unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    tokio::join!(calls, replies);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_account_being_deleted_logs_in_only_to_restore_it(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    let delete_at = 1_800_000_000_000_i64;
    let calls = async {
        let error = client
            .login(
                &db,
                "student@example.invalid",
                "fixed-password-123".into(),
                false,
            )
            .await
            .unwrap_err();
        assert!(matches!(error, Error::Deleting(at) if at == delete_at));
        assert!(error.message().contains("正在注销"), "{}", error.message());
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedOut
        ));
        client
            .login(
                &db,
                "student@example.invalid",
                "fixed-password-123".into(),
                true,
            )
            .await
            .unwrap();
        assert_eq!(
            client
                .delete(&db, "student@example.invalid", "fixed-password-123".into())
                .await
                .unwrap(),
            delete_at
        );
        assert!(matches!(
            account::state(&db).await.unwrap(),
            State::LoggedOut
        ));
    };
    let replies = async {
        let deleting = json!({"code": "account_deleting", "deleteAt": delete_at});
        let session = json!({
            "token": "restored-token",
            "account": account_json("student@example.invalid", "9999-12-31"),
        });
        for (path, restore, status, body) in [
            (path::LOGIN, Some(false), 409, deleting),
            (path::LOGIN, Some(true), 200, session),
            (path::ACCOUNT, None, 200, json!({"deleteAt": delete_at})),
        ] {
            let request = server.requests.recv().await.unwrap();
            assert!(
                request.line.contains(&format!(" {path} ")),
                "{}",
                request.line
            );
            if let Some(restore) = restore {
                let input: LoginRequest = serde_json::from_value(request.body).unwrap();
                assert_eq!(input.restore, restore);
            }
            request
                .respond
                .send(Response::Raw {
                    status,
                    content: body.to_string(),
                })
                .unwrap();
            request.completed.await.unwrap();
        }
    };
    tokio::join!(calls, replies);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn deletion_rejects_an_account_replaced_while_waiting_for_login(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (terms, id, email, token)
        VALUES ('9999-12-31', 1, 'first@example.invalid', 'first-token')"#,
    )
    .execute(&db)
    .await
    .unwrap();

    let login = client.login(
        &db,
        "second@example.invalid",
        "shared-password".into(),
        false,
    );
    tokio::pin!(login);
    let request = tokio::select! {
        request = server.requests.recv() => request.unwrap(),
        result = &mut login => panic!("login finished before its response: {result:?}"),
    };
    assert!(request.line.starts_with(&format!("POST {} ", path::LOGIN)));
    assert!(matches!(
        account::state(&db).await.unwrap(),
        State::LoggedIn { email, .. } if email == "first@example.invalid"
    ));

    // The deletion of A must wait behind B's unfinished login before reading local state.
    let deleting = client.delete(&db, "first@example.invalid", "shared-password".into());
    tokio::pin!(deleting);
    std::future::poll_fn(|cx| {
        assert!(deleting.as_mut().poll(cx).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    request
        .respond
        .send(Response::Raw {
            status: 200,
            content: serde_json::to_string(&Session {
                token: "second-token".into(),
                account: Account {
                    email: "second@example.invalid".into(),
                    plus_until: None,
                    tracking: QUOTA,
                    terms: Some("9999-12-31".into()),
                },
            })
            .unwrap(),
        })
        .unwrap();
    request.completed.await.unwrap();

    let (logged_in, deleted) = tokio::select! {
        result = async { tokio::join!(login, deleting) } => result,
        request = server.requests.recv() => {
            panic!("deletion sent an unexpected request: {}", request.unwrap().line);
        },
    };
    assert!(matches!(
        logged_in.unwrap(),
        State::LoggedIn { email, .. } if email == "second@example.invalid"
    ));
    assert!(matches!(deleted, Err(Error::AccountChanged)));
    let saved: (String, String) = sqlx::query_as("SELECT email, token FROM account")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(
        saved,
        ("second@example.invalid".into(), "second-token".into())
    );
    assert!(server.requests.try_recv().is_err());
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
                        delete_at: None,
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

#[test]
fn refused_orders_and_a_deleting_address_say_why() {
    let message = |code, delete_at| {
        Error::Server(ApiError {
            code,
            retry_after_seconds: None,
            delete_at,
        })
        .message()
    };
    assert!(message(ErrorCode::PlusTooLong, None).contains("一年"));
    assert!(message(ErrorCode::PlusRefunded, None).contains("无理由退款"));
    assert!(message(ErrorCode::PlusRequired, None).contains("Plus"));
    let deleting = message(ErrorCode::AccountDeleting, Some(1_800_000_000_000));
    assert!(deleting.contains("后方可重新注册"), "{deleting}");
}

#[test]
fn a_busy_server_is_waited_for_in_seconds() {
    let wait = |seconds| {
        Error::Server(ApiError {
            code: ErrorCode::RateLimited,
            retry_after_seconds: Some(seconds),
            delete_at: None,
        })
        .message()
    };
    assert!(wait(5).contains("5 秒"), "{}", wait(5));
    assert!(wait(60).contains("1 分钟"), "{}", wait(60));
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn logging_into_another_account_resets_library_progress(db: SqlitePool) {
    let mut server = Provider::start().await.unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let client = Client::with_address(&net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        r#"INSERT INTO account (terms, id, email, token, library_revision, library_checked_at)
        VALUES ('9999-12-31', 1, 'first@example.invalid', 'old-token', 42, 100)"#,
    )
    .execute(&db)
    .await
    .unwrap();
    for (email, expected) in [
        ("first@example.invalid", (42, Some(100))),
        ("second@example.invalid", (0, None)),
    ] {
        let login = client.login(&db, email, "fixed-password-123".into(), false);
        let reply = async {
            let request = server.requests.recv().await.unwrap();
            request
                .respond
                .send(Response::Raw {
                    status: 200,
                    content: serde_json::to_string(&Session {
                        token: "new-test-token".into(),
                        account: Account {
                            email: email.into(),
                            plus_until: None,
                            tracking: QUOTA,
                            terms: Some("9999-12-31".into()),
                        },
                    })
                    .unwrap(),
                })
                .unwrap();
            request.completed.await.unwrap();
        };
        let (result, ()) = tokio::join!(login, reply);
        result.unwrap();
        let progress: (i64, Option<i64>) =
            sqlx::query_as("SELECT library_revision, library_checked_at FROM account")
                .fetch_one(&db)
                .await
                .unwrap();
        assert_eq!(progress, expected);
    }
}
