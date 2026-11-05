#![cfg(test)]

use std::future::pending;

use api::{Context, ErrorCode, call, call_agent};
use base64::prelude::{BASE64_STANDARD, Engine};
use serde_json::{Value, json};
use sqlx::SqlitePool;

fn library_record(emails: &[&str]) -> cloud_api::library::Record {
    cloud_api::library::Record {
        preview: cloud_api::library::Preview {
            summary: cloud_api::library::Summary {
                id: "mentor_one".into(),
                name: "导师".into(),
                aliases: vec![],
                unit: Default::default(),
                research: None,
                email_count: emails.len() as u32,
                fetched: true,
            },
            units: vec![],
            papers: vec![],
            homepages: vec![],
            source_url: None,
            verified_at: None,
        },
        emails: emails.iter().map(|e| (*e).into()).collect(),
        revision: 1,
    }
}

async fn signed_in(db: SqlitePool) -> (Context, test_fixtures::provider::Provider) {
    let server = test_fixtures::provider::Provider::start().await.unwrap();
    let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    ctx.account =
        account::Client::with_address(&ctx.net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        "INSERT INTO account (terms,id,email,token) VALUES ('9999-12-31',1,'student@example.invalid','test-token')",
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    (ctx, server)
}

async fn library_context(db: SqlitePool) -> (Context, test_fixtures::provider::Provider, i64) {
    let (ctx, server) = signed_in(db).await;
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "导师".into(),
            emails: vec!["old@example.com".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 10)
        .await
        .unwrap();
    (ctx, server, mentor)
}

async fn library_response(
    request: test_fixtures::provider::Request,
    record: cloud_api::library::Record,
) {
    request
        .respond
        .send(test_fixtures::provider::Response::Raw {
            status: 200,
            content: serde_json::to_string(&cloud_api::library::Fetched {
                records: vec![record],
                missing: vec![],
                remaining: 199,
            })
            .unwrap(),
        })
        .unwrap();
    request.completed.await.unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn library_addition_executes_the_snapshot_checked_without_confirmation(db: SqlitePool) {
    let (ctx, mut server, mentor) = library_context(db).await;
    let calling = call_agent(
        &ctx,
        "addLibraryMentors",
        json!({"ids":["mentor_one"]}),
        |_| panic!("the checked record did not change the recipient"),
        pending(),
    );
    let replies = async {
        let request = server.requests.recv().await.unwrap();
        library_response(request, library_record(&["old@example.com"])).await;
        loop {
            let request = server.requests.recv().await.unwrap();
            let mut next = library_record(&["new@demo.edu.example", "old@example.com"]);
            next.revision = 2;
            library_response(request, next).await;
        }
    };
    tokio::select! {
        result = calling => { result.unwrap(); },
        () = replies => unreachable!("the response loop stays available until the call ends"),
    }
    assert_eq!(
        mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails,
        ["old@example.com"]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn library_addition_executes_the_rechecked_snapshot_after_confirmation(db: SqlitePool) {
    let (ctx, mut server, mentor) = library_context(db).await;
    let calling = allow_after_change(
        &ctx,
        "addLibraryMentors",
        json!({"ids":["mentor_one"]}),
        async {},
    );
    let replies = async {
        for _ in 0..2 {
            let request = server.requests.recv().await.unwrap();
            library_response(
                request,
                library_record(&["checked@demo.edu.example", "old@example.com"]),
            )
            .await;
        }
        loop {
            let request = server.requests.recv().await.unwrap();
            let mut changed = library_record(&["unchecked@demo.edu.example", "old@example.com"]);
            changed.revision = 2;
            library_response(request, changed).await;
        }
    };
    tokio::select! {
        result = calling => { result.unwrap(); },
        () = replies => unreachable!("the response loop stays available until the call ends"),
    }
    assert_eq!(
        mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
        "checked@demo.edu.example"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn library_addition_rejects_a_changed_confirmation_before_writing(db: SqlitePool) {
    let (ctx, mut server, mentor) = library_context(db).await;
    let calling = allow_after_change(
        &ctx,
        "addLibraryMentors",
        json!({"ids":["mentor_one"]}),
        async {},
    );
    let replies = async {
        for email in ["checked@demo.edu.example", "changed@demo.edu.example"] {
            let request = server.requests.recv().await.unwrap();
            library_response(request, library_record(&[email, "old@example.com"])).await;
        }
    };
    let (result, ()) = tokio::join!(calling, replies);
    assert!(matches!(
        result,
        Err(api::CallError {
            code: ErrorCode::Invalid,
            ..
        })
    ));
    assert_eq!(
        mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails,
        ["old@example.com"]
    );
}

async fn addition_and_removal_race(db: SqlitePool, initially_linked: bool) {
    use std::future::Future;
    let (ctx, mut server, mentor) = library_context(db).await;
    if initially_linked {
        let (result, ()) = tokio::join!(
            call(&ctx, "addLibraryMentors", json!({"ids":["mentor_one"]})),
            async {
                library_response(
                    server.requests.recv().await.unwrap(),
                    library_record(&["old@example.com"]),
                )
                .await;
            }
        );
        result.unwrap();
    }
    let adding = call(&ctx, "addLibraryMentors", json!({"ids":["mentor_one"]}));
    tokio::pin!(adding);
    let old = tokio::select! {
        request = server.requests.recv() => request.unwrap(),
        result = &mut adding => panic!("addition finished without its response: {result:?}"),
    };
    let blocked = {
        let lease = ctx.library.acquire();
        tokio::pin!(lease);
        std::future::poll_fn(|cx| std::task::Poll::Ready(lease.as_mut().poll(cx).is_pending()))
            .await
    };
    let checking = call(&ctx, "syncLibrary", json!({}));
    tokio::pin!(checking);
    std::future::poll_fn(|cx| {
        assert!(checking.as_mut().poll(cx).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    let synchronize = async {
        let reply = async {
            let request = server.requests.recv().await.unwrap();
            assert!(request.line.contains("/library/changes?after=0"));
            request
                .respond
                .send(test_fixtures::provider::Response::Raw {
                    status: 200,
                    content: serde_json::to_string(&cloud_api::library::Changes {
                        changes: vec![cloud_api::library::Change {
                            id: "mentor_one".into(),
                            revision: 2,
                            data: cloud_api::library::ChangeData::Removed(()),
                        }],
                        revision: 2,
                        more: false,
                    })
                    .unwrap(),
                })
                .unwrap();
            request.completed.await.unwrap();
        };
        let (result, ()) = tokio::join!(checking, reply);
        result.unwrap();
    };
    if blocked {
        library_response(old, library_record(&["old@example.com"])).await;
        adding.await.unwrap();
        synchronize.await;
    } else {
        synchronize.await;
        library_response(old, library_record(&["old@example.com"])).await;
        adding.await.unwrap();
    }
    let detail = mentor::get(&ctx.db, mentor).await.unwrap().unwrap();
    assert_eq!(
        detail.library[0].status,
        cloud_api::library::Status::Removed
    );
    let cursor: i64 = sqlx::query_scalar("SELECT library_revision FROM account")
        .fetch_one(&ctx.db)
        .await
        .unwrap();
    assert_eq!(cursor, 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn first_library_addition_does_not_lose_a_concurrent_removed_change(db: SqlitePool) {
    addition_and_removal_race(db, false).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn delayed_library_addition_cannot_reactivate_a_removed_record(db: SqlitePool) {
    addition_and_removal_race(db, true).await;
}

async fn refused(ctx: &Context, op: &str, input: Value) {
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        ctx,
        op,
        input,
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let answer = async {
        waiting.await.unwrap();
        let requests = call(ctx, "listAgentRequests", json!({})).await.unwrap();
        let id = requests[0]["id"].clone();
        call(ctx, "answerAgentRequest", json!({"id":id, "allow":false}))
            .await
            .unwrap();
    };
    let (result, ()) = tokio::join!(calling, answer);
    let error = result.unwrap_err();
    assert!(matches!(error.code, ErrorCode::Invalid));
    assert!(error.message.contains("在应用里拒绝"));
}

async fn allow_after_change(
    ctx: &Context,
    op: &str,
    input: Value,
    change: impl std::future::Future<Output = ()>,
) -> Result<Value, api::CallError> {
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        ctx,
        op,
        input,
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let answer = async {
        waiting.await.unwrap();
        let requests = call(ctx, "listAgentRequests", json!({})).await.unwrap();
        change.await;
        call(
            ctx,
            "answerAgentRequest",
            json!({"id":requests[0]["id"], "allow":true}),
        )
        .await
        .unwrap();
    };
    tokio::join!(calling, answer).0
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn account_confirmations_use_the_input_email_without_the_password(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    // Resetting leaves the old cloud backup unreadable, so it is confirmed like deletion.
    for (op, input) in [
        (
            "deleteAccount",
            json!({"email":"first@example.invalid", "password":"private-test-password"}),
        ),
        (
            "resetAccountPassword",
            json!({"email":"first@example.invalid", "code":"123456",
                "password":"private-test-password"}),
        ),
        (
            "registerAccount",
            json!({"email":"first@example.invalid", "code":"123456",
                "password":"private-test-password"}),
        ),
    ] {
        let (registered, waiting) = tokio::sync::oneshot::channel();
        let calling = call_agent(
            &ctx,
            op,
            input,
            |description| {
                assert_eq!(description.targets, ["first@example.invalid"]);
                assert!(
                    !serde_json::to_string(description)
                        .unwrap()
                        .contains("private-test-password")
                );
                registered.send(()).unwrap();
            },
            pending(),
        );
        let answer = async {
            waiting.await.unwrap();
            let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
            call(
                &ctx,
                "answerAgentRequest",
                json!({"id":requests[0]["id"], "allow":false}),
            )
            .await
            .unwrap();
        };
        let error = tokio::join!(calling, answer).0.unwrap_err();
        assert!(error.message.contains("在应用里拒绝"));
    }
}

/// Answers the next request to the server, which must start with `line`, and returns its body.
async fn reply(server: &mut test_fixtures::provider::Provider, line: &str, body: Value) -> Value {
    let request = server.requests.recv().await.unwrap();
    assert!(request.line.starts_with(line), "{}", request.line);
    request
        .respond
        .send(test_fixtures::provider::Response::Raw {
            status: 200,
            content: body.to_string(),
        })
        .unwrap();
    request.completed.await.unwrap();
    request.body
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_plus_order_is_confirmed_at_the_price_the_server_sells_it_for(db: SqlitePool) {
    let (ctx, mut server) = signed_in(db).await;
    let items = |price: u32| {
        json!({"tiers":[{"item":"1m","duration":{"months":1},"price":price,
            "until":1_800_000_000_000_i64,"available":true}],
            "packs":[],"latest":1_900_000_000_000_i64,"refunded":false})
    };
    for later in [1990, 1880] {
        let (shown, waiting) = tokio::sync::oneshot::channel();
        let calling = call_agent(
            &ctx,
            "createPlusOrder",
            json!({"item":"1m", "method":"wxpay"}),
            |description| shown.send(description.targets.clone()).unwrap(),
            pending(),
        );
        let answering =
            async {
                reply(&mut server, "GET /v1/plus/items ", items(1880)).await;
                let [target] = &waiting.await.unwrap()[..] else {
                    panic!("one order to confirm")
                };
                assert!(target.starts_with(
                    "账号 student@example.invalid：Plus 一个月，18.8 元，开通后 Plus 到"
                ));
                assert!(target.ends_with("，用微信支付付款"));
                let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
                call(
                    &ctx,
                    "answerAgentRequest",
                    json!({"id":requests[0]["id"], "allow":true}),
                )
                .await
                .unwrap();
                reply(&mut server, "GET /v1/plus/items ", items(later)).await;
                if later == 1880 {
                    let order = reply(
                        &mut server,
                        "POST /v1/plus/orders ",
                        json!({"order":7, "payUrl":"https://pay.example.invalid/AES7"}),
                    )
                    .await;
                    assert_eq!(
                        (&order["item"], &order["method"]),
                        (&json!("1m"), &json!("wxpay"))
                    );
                }
            };
        let result = tokio::join!(calling, answering).0;
        if later == 1880 {
            assert_eq!(result.unwrap()["order"], 7);
        } else {
            assert!(result.unwrap_err().message.contains("情况变了"));
        }
    }
    assert!(server.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn enabling_cloud_backup_is_bound_to_the_account_it_was_confirmed_for(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    sqlx::query(
        "INSERT INTO account (terms,id,email,token) VALUES ('9999-12-31',1,'student@example.invalid','test-token')",
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    let result = allow_after_change(
        &ctx,
        "enableCloudBackup",
        json!({"password":null, "replace":null}),
        async {
            sqlx::query("DELETE FROM account")
                .execute(&ctx.db)
                .await
                .unwrap();
            sqlx::query(
                "INSERT INTO account (terms,id,email,token) VALUES ('9999-12-31',1,'other@example.invalid','other')",
            )
            .execute(&ctx.db)
            .await
            .unwrap();
        },
    )
    .await;
    assert!(result.unwrap_err().message.contains("情况变了"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn library_addition_confirms_before_changing_an_approved_recipient(db: SqlitePool) {
    use test_fixtures::provider::{Provider, Response};
    let mut server = Provider::start().await.unwrap();
    let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    ctx.account =
        account::Client::with_address(&ctx.net, server.address.strip_suffix("/v1").unwrap().into());
    sqlx::query(
        "INSERT INTO account (terms,id,email,token) VALUES ('9999-12-31',1,'student@example.invalid','test-token')",
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "导师".into(),
            emails: vec!["old@example.com".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 10)
        .await
        .unwrap();
    let record = library_record(&["new@demo.edu.example", "old@example.com"]);
    let calling = refused(&ctx, "addLibraryMentors", json!({"ids":["mentor_one"]}));
    let response = async {
        let request = server.requests.recv().await.unwrap();
        let body = cloud_api::library::Fetched {
            records: vec![record],
            missing: vec![],
            remaining: 199,
        };
        request
            .respond
            .send(Response::Raw {
                status: 200,
                content: serde_json::to_string(&body).unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    tokio::join!(calling, response);
    let local = mentor::get(&ctx.db, mentor).await.unwrap().unwrap();
    assert_eq!(local.emails, ["old@example.com"]);
    assert!(local.library.is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn library_update_confirms_before_reordering_an_approved_recipient(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "导师".into(),
            emails: vec!["old@demo.edu.example".into(), "new@demo.edu.example".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 10)
        .await
        .unwrap();
    sqlx::query(
        r#"INSERT INTO mentor_library(library_id, mentor_id, status, record)
        VALUES ('mentor_one', ?, 'active', ?)"#,
    )
    .bind(mentor)
    .bind(sqlx::types::Json(library_record(&[
        "old@demo.edu.example",
        "new@demo.edu.example",
    ])))
    .execute(&ctx.db)
    .await
    .unwrap();
    sqlx::query(
        r#"INSERT INTO library_update(library_id,field,original,incoming)
        VALUES ('mentor_one','primaryEmail','"old@demo.edu.example"','"new@demo.edu.example"')"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    let groups = call(&ctx, "listLibraryUpdates", json!({})).await.unwrap();
    let input = json!({"choices":groups[0]["differences"]});
    refused(&ctx, "applyLibraryUpdates", input.clone()).await;
    let address: String = sqlx::query_scalar(
        "SELECT address FROM mentor_email WHERE mentor_id=? ORDER BY position LIMIT 1",
    )
    .bind(mentor)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    assert_eq!(address, "old@demo.edu.example");
    call(&ctx, "applyLibraryUpdates", input).await.unwrap();
    let address: String = sqlx::query_scalar(
        "SELECT address FROM mentor_email WHERE mentor_id=? ORDER BY position LIMIT 1",
    )
    .bind(mentor)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    assert_eq!(address, "new@demo.edu.example");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn task_candidates_warn_about_library_status_but_not_removed_records(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "导师".into(),
            emails: vec!["one@demo.edu.example".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    sqlx::query(
        r#"INSERT INTO mentor_library (library_id, mentor_id, status, record)
        VALUES ('mentor_one', ?, 'active', ?)"#,
    )
    .bind(mentor)
    .bind(sqlx::types::Json(library_record(&["one@demo.edu.example"])))
    .execute(&ctx.db)
    .await
    .unwrap();
    let input = json!({"sourceTaskId":null,"kind":"first","name":"任务","schedule":{"kind":"now"},
        "ids":[mentor],"writer":"ai","templateId":null,"templateSourceId":null,
        "modelConfigId":null,"references":[],"attachments":[]});
    for status in ["active", "retired", "departed", "deceased", "removed"] {
        sqlx::query("UPDATE mentor_library SET status=?")
            .bind(status)
            .execute(&ctx.db)
            .await
            .unwrap();
        let plan = call(&ctx, "planTask", input.clone()).await.unwrap();
        let expected = if matches!(status, "active" | "removed") {
            Value::Null
        } else {
            json!(status)
        };
        assert_eq!(plan["mentors"][0]["libraryStatus"], expected);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn empty_save_does_not_delete_a_draft_edited_while_waiting(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    email::save_first_draft(&ctx.db, mentor, "申请", "旧草稿", &[], 0)
        .await
        .unwrap();
    let result = allow_after_change(
        &ctx,
        "saveFirstDraft",
        json!({"mentorId":mentor, "subject":"", "bodyMd":"", "attachments":[]}),
        async {
            // Same subject and saved time: only the body tells the drafts apart.
            email::save_first_draft(&ctx.db, mentor, "申请", "新草稿", &[], 0)
                .await
                .unwrap();
        },
    )
    .await;
    assert!(matches!(
        result,
        Err(api::CallError {
            code: ErrorCode::Invalid,
            ..
        })
    ));
    let saved = call(&ctx, "getFirstDraft", json!({"id":mentor}))
        .await
        .unwrap();
    assert_eq!(saved["bodyMd"], "新草稿");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn empty_save_does_not_delete_a_draft_whose_attachment_was_swapped(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let material = |size: i64| {
        sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
            VALUES ('简历.pdf', ?, 0, 0, 0, 0) RETURNING id"#,
        )
        .bind(size)
        .fetch_one(&ctx.db)
    };
    let (old, new) = (material(3).await.unwrap(), material(5).await.unwrap());
    email::save_first_draft(&ctx.db, mentor, "申请", "草稿", &[old], 0)
        .await
        .unwrap();
    let result = allow_after_change(
        &ctx,
        "saveFirstDraft",
        json!({"mentorId":mentor, "subject":"", "bodyMd":"", "attachments":[]}),
        async {
            // Same name, subject, body and saved time: only the attachment differs.
            email::save_first_draft(&ctx.db, mentor, "申请", "草稿", &[new], 0)
                .await
                .unwrap();
        },
    )
    .await;
    assert!(result.is_err());
    let saved = call(&ctx, "getFirstDraft", json!({"id":mentor}))
        .await
        .unwrap();
    assert_eq!(saved["bodyMd"], "草稿");
}

/// A paused task holding one approved first email to the mentor.
/// The profile dialog's save of the mentor with only its emails changed.
async fn emails_edit(ctx: &Context, id: i64, emails: &[&str]) -> Value {
    let m = mentor::get(&ctx.db, id).await.unwrap().unwrap();
    let before = json!({"name":m.name, "aliases":m.aliases, "emails":m.emails,
        "affiliations":m.affiliations, "homepages":m.homepages, "research":m.research});
    json!({"id":id, "before":before, "name":m.name, "aliases":m.aliases, "emails":emails,
        "affiliations":m.affiliations, "homepages":m.homepages,
        "research":m.research.unwrap_or_default()})
}

async fn paused_task(ctx: &Context, mentor: i64) -> (i64, i64) {
    let task = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
        VALUES ('申请', 'first', 'ai', 'now', 'paused', 0)
        RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    sqlx::query("UPDATE email SET task_id = ? WHERE id = ?")
        .bind(task)
        .bind(email)
        .execute(&ctx.db)
        .await
        .unwrap();
    (task, email)
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resume_task_rechecks_the_pending_recipient_address(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["old@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let (task, _) = paused_task(&ctx, mentor).await;
    let result = allow_after_change(&ctx, "resumeTask", json!({"id":task}), async {
        call(
            &ctx,
            "updateMentor",
            emails_edit(&ctx, mentor, &["new@example.invalid"]).await,
        )
        .await
        .unwrap();
    })
    .await;
    assert!(matches!(
        result,
        Err(api::CallError {
            code: ErrorCode::Invalid,
            ..
        })
    ));
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM task WHERE id = ?")
            .bind(task)
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        "paused"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resume_task_rechecks_a_plan_changed_to_immediate_sending(db: SqlitePool) {
    const AT: i64 = 4_000_000_000_000;
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let (task, email) = paused_task(&ctx, mentor).await;
    sqlx::query("UPDATE email SET planned_at = ? WHERE id = ?")
        .bind(AT)
        .bind(email)
        .execute(&ctx.db)
        .await
        .unwrap();
    let result = allow_after_change(&ctx, "resumeTask", json!({"id":task}), async {
        call(&ctx, "sendEmailNow", json!({"id":email}))
            .await
            .unwrap();
    })
    .await;
    assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM task WHERE id = ?")
            .bind(task)
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        "paused"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resume_task_rechecks_the_sender_mailbox(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    let other = call(
        &ctx,
        "addMailbox",
        json!({
            "address":"other@example.invalid", "senderName":"学生", "provider":"custom",
            "smtp":{"host":"localhost", "port":1, "security":"tls"},
            "imap":{"host":"localhost", "port":1, "security":"tls"},
            "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
        }),
    )
    .await
    .unwrap()["id"]
        .clone();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    call(
        &ctx,
        "setMentorMailbox",
        json!({"id":mentor, "mailboxId":1}),
    )
    .await
    .unwrap();
    let (task, _) = paused_task(&ctx, mentor).await;
    let result = allow_after_change(&ctx, "resumeTask", json!({"id":task}), async {
        call(
            &ctx,
            "setMentorMailbox",
            json!({"id":mentor, "mailboxId":other}),
        )
        .await
        .unwrap();
    })
    .await;
    assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_a_mailbox_address_used_by_a_paused_task_requires_confirmation(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mut edit = json!({
        "address":"before@example.invalid", "senderName":"学生", "provider":"custom",
        "smtp":{"host":"localhost", "port":1, "security":"tls"},
        "imap":{"host":"localhost", "port":1, "security":"tls"},
        "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
    });
    let mailbox = call(&ctx, "addMailbox", edit.clone()).await.unwrap()["id"].clone();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    call(
        &ctx,
        "setMentorMailbox",
        json!({"id":mentor, "mailboxId":mailbox}),
    )
    .await
    .unwrap();
    paused_task(&ctx, mentor).await;
    edit["id"] = mailbox;
    edit["secret"] = json!("");
    edit["address"] = json!("after@example.invalid");
    refused(&ctx, "updateMailbox", edit).await;
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT address FROM mailbox")
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        "before@example.invalid"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resuming_without_approved_emails_needs_no_confirmation(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    sqlx::query("UPDATE mailbox SET paused_until = ?, pause_reason = 'fixture' WHERE id = 1")
        .bind(i64::MAX)
        .execute(&ctx.db)
        .await
        .unwrap();
    let task = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
        VALUES ('申请', 'first', 'ai', 'now', 'paused', 0)
        RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    for (op, id) in [("resumeTask", task), ("resumeMailbox", 1)] {
        call_agent(
            &ctx,
            op,
            json!({"id":id}),
            |_| panic!("{op} sends nothing"),
            pending(),
        )
        .await
        .unwrap();
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_an_active_mailbox_address_requires_confirmation_for_approved_mail(
    db: SqlitePool,
) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    for assigned in [true, false] {
        let before = format!("before{assigned}@example.invalid");
        let after = format!("after{assigned}@example.invalid");
        let mut edit = json!({
            "address":before, "senderName":"学生", "provider":"custom",
            "smtp":{"host":"localhost", "port":1, "security":"tls"},
            "imap":{"host":"localhost", "port":1, "security":"tls"},
            "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
        });
        let mailbox = call(&ctx, "addMailbox", edit.clone()).await.unwrap()["id"].clone();
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: "王伟".into(),
                emails: vec![format!("mentor{assigned}@example.invalid")],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        if assigned {
            call(
                &ctx,
                "setMentorMailbox",
                json!({"id":mentor, "mailboxId":mailbox}),
            )
            .await
            .unwrap();
        }
        let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
            .await
            .unwrap();
        edit["id"] = mailbox.clone();
        edit["address"] = json!(after);
        edit["secret"] = json!("");
        refused(&ctx, "updateMailbox", edit.clone()).await;
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT address FROM mailbox WHERE id = ?")
                .bind(mailbox.as_i64().unwrap())
                .fetch_one(&ctx.db)
                .await
                .unwrap(),
            before
        );
        sqlx::query("UPDATE email SET state = 'review' WHERE id = ?")
            .bind(email)
            .execute(&ctx.db)
            .await
            .unwrap();
        call_agent(&ctx, "updateMailbox", edit, |_| {}, pending())
            .await
            .unwrap();
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT address FROM mailbox WHERE id = ?")
                .bind(mailbox.as_i64().unwrap())
                .fetch_one(&ctx.db)
                .await
                .unwrap(),
            after
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn recipient_edits_recheck_schedules_including_unassigned_task_mail(db: SqlitePool) {
    const AT: i64 = 4_000_000_000_000;
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    for (n, (op, planned)) in [
        ("updateMentor", Some(AT)),
        ("updateMentor", None),
        ("acceptCandidates", Some(AT)),
        ("acceptCandidates", None),
    ]
    .into_iter()
    .enumerate()
    {
        let before = format!("before{n}@example.invalid");
        let after = format!("after{n}@school.edu");
        let name = format!("导师{n}");
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: name.clone(),
                emails: vec![before.clone()],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let task = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
            VALUES ('申请', 'first', 'ai', 'now', 'paused', 0)
            RETURNING id"#,
        )
        .fetch_one(&ctx.db)
        .await
        .unwrap();
        let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
            .await
            .unwrap();
        sqlx::query("UPDATE email SET task_id = ?, planned_at = ? WHERE id = ?")
            .bind(task)
            .bind(planned)
            .bind(email)
            .execute(&ctx.db)
            .await
            .unwrap();
        let input = if op == "updateMentor" {
            emails_edit(&ctx, mentor, &[&after]).await
        } else {
            let candidate = sqlx::query_scalar::<_, i64>(
                r#"INSERT INTO candidate (name, emails, review, enrich)
                VALUES (?, ?, 'pending', 'none') RETURNING id"#,
            )
            .bind(name)
            .bind(json!([before, after]).to_string())
            .fetch_one(&ctx.db)
            .await
            .unwrap();
            json!({"ids":[candidate], "decisions":{"take":[], "matches":[], "restore":[]}})
        };
        let result = allow_after_change(&ctx, op, input, async {
            call(&ctx, "sendEmailNow", json!({"id":email}))
                .await
                .unwrap();
        })
        .await;
        assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
        assert_eq!(
            mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
            before
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn retry_targets_tell_an_automatic_sender_from_a_reply_without_one(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    // The reply's original message, and with it the mailbox it came to, is gone.
    for (kind, reply_to, sender) in [
        ("first", None, "自动选择的邮箱"),
        (
            "reply",
            Some("<gone@example.invalid>"),
            "没有负责邮箱，要先在导师资料里指定",
        ),
    ] {
        let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
            .await
            .unwrap();
        sqlx::query(
            "UPDATE email SET kind = ?, reply_to = ?, state = 'failed', error = 'fixture'
            WHERE id = ?",
        )
        .bind(kind)
        .bind(reply_to)
        .bind(email)
        .execute(&ctx.db)
        .await
        .unwrap();
        let (registered, waiting) = tokio::sync::oneshot::channel();
        let calling = call_agent(
            &ctx,
            "retryEmail",
            json!({"id":email}),
            |description| registered.send(description.targets.clone()).unwrap(),
            pending(),
        );
        let answer = async {
            let targets = waiting.await.unwrap();
            let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
            call(
                &ctx,
                "answerAgentRequest",
                json!({"id":requests[0]["id"], "allow":false}),
            )
            .await
            .unwrap();
            targets
        };
        let (_, targets) = tokio::join!(calling, answer);
        assert!(
            targets[0].contains(&format!("发件邮箱：{sender}；")),
            "{targets:?}"
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn retry_rechecks_the_task_arrangement_that_will_assign_its_new_time(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let task = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
            window_end, per_window, state, created_at)
        VALUES ('申请', 'first', 'ai', 'window', '["2096-01-01"]', '09:00', '11:00', 10, 'active', 0)
        RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    sqlx::query("UPDATE email SET task_id = ?, state = 'failed', error = 'fixture' WHERE id = ?")
        .bind(task)
        .bind(email)
        .execute(&ctx.db)
        .await
        .unwrap();
    let result = allow_after_change(&ctx, "retryEmail", json!({"id":email}), async {
        call(
            &ctx,
            "changeTaskSchedule",
            json!({"id":task, "schedule":{"kind":"now"}}),
        )
        .await
        .unwrap();
    })
    .await;
    assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM email WHERE id = ?")
            .bind(email)
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        "failed"
    );
}

async fn mailbox_change_is_confirmed(db: SqlitePool, automatic: bool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    let other = call(
        &ctx,
        "addMailbox",
        json!({
            "address":"available@example.invalid", "senderName":"学生", "provider":"custom",
            "smtp":{"host":"localhost", "port":1, "security":"tls"},
            "imap":{"host":"localhost", "port":1, "security":"tls"},
            "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
        }),
    )
    .await
    .unwrap()["id"]
        .clone();
    sqlx::query("UPDATE mailbox SET paused_until = ?, pause_reason = 'fixture' WHERE id = 1")
        .bind(i64::MAX)
        .execute(&ctx.db)
        .await
        .unwrap();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    call(
        &ctx,
        "setMentorMailbox",
        json!({"id":mentor, "mailboxId":1}),
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    refused(
        &ctx,
        "setMentorMailbox",
        json!({"id":mentor, "mailboxId":if automatic { Value::Null } else { other }}),
    )
    .await;
    assert_eq!(
        sqlx::query_scalar::<_, Option<i64>>("SELECT mailbox_id FROM mentor WHERE id = ?")
            .bind(mentor)
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        Some(1)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_from_a_paused_mailbox_to_an_available_one_requires_confirmation(db: SqlitePool) {
    mailbox_change_is_confirmed(db, false).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changing_from_a_paused_mailbox_to_automatic_requires_confirmation(db: SqlitePool) {
    mailbox_change_is_confirmed(db, true).await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resuming_a_mailbox_is_not_invalidated_by_an_imap_port_change(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    sqlx::query("UPDATE mailbox SET paused_until = ?, pause_reason = 'fixture' WHERE id = 1")
        .bind(i64::MAX)
        .execute(&ctx.db)
        .await
        .unwrap();
    allow_after_change(&ctx, "resumeMailbox", json!({"id":1}), async {
        sqlx::query("UPDATE mailbox SET imap_port = 994 WHERE id = 1")
            .execute(&ctx.db)
            .await
            .unwrap();
    })
    .await
    .unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, Option<i64>>("SELECT paused_until FROM mailbox WHERE id = 1")
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        None
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn first_approval_previews_the_whole_letter_and_sends_the_dialog_rendering(db: SqlitePool) {
    const AT: i64 = 4_000_000_000_000;
    let dir = tempfile::tempdir().unwrap();
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let ctx = Context::new(db, net::Net::new(vec![pki.root]).unwrap(), dir.path());
    std::fs::create_dir(&ctx.materials).unwrap();
    call(
        &ctx,
        "addMailbox",
        json!({
            "address":fakemail::ADDRESS, "senderName":"学生", "provider":"custom",
            "smtp":{"host":"localhost", "port":port, "security":"tls"},
            "imap":{"host":"localhost", "port":993, "security":"tls"},
            "dailyLimit":50, "minIntervalS":60, "secret":fakemail::SECRET
        }),
    )
    .await
    .unwrap();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let material = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
        VALUES ('简历.pdf', 3, 0, 0, 0, 0) RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    std::fs::write(material::file(&ctx.materials, material), b"pdf").unwrap();
    let same_name = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
        VALUES ('简历.pdf', 5, 0, 0, 0, 0) RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let chosen_data = b"pdf\x80\x81";
    std::fs::write(material::file(&ctx.materials, same_name), chosen_data).unwrap();
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        &ctx,
        "approveFirstEmail",
        json!({
            "mentorId":mentor, "subject":"申请", "bodyMd":"老师**您好**", "attachments":[same_name, material],
            "plannedAt":AT
        }),
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let answer = async {
        waiting.await.unwrap();
        let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
        let letters = requests[0]["description"]["letters"].as_array().unwrap();
        assert_eq!(letters.len(), 1);
        let letter = &letters[0];
        assert_eq!(letter["mentorId"], mentor);
        assert_eq!(letter["mentorName"], "王伟");
        assert_eq!(letter["address"], "mentor@example.invalid");
        assert_eq!(letter["sendAt"], json!({"kind":"at", "at":AT}));
        assert_eq!(letter["subject"], "申请");
        assert_eq!(letter["bodyMd"], "老师**您好**");
        assert_eq!(
            letter["attachments"],
            json!([
                {"materialId":same_name, "fileName":"简历.pdf", "size":5}
            ])
        );
        sqlx::query("UPDATE material SET size = 4 WHERE id = ?")
            .bind(material)
            .execute(&ctx.db)
            .await
            .unwrap();
        call(
            &ctx,
            "answerAgentRequest",
            json!({
                "id":requests[0]["id"], "allow":true,
                "rendered":[{"html":"<p>dialog HTML</p>", "text":"dialog text"}]
            }),
        )
        .await
        .unwrap();
    };
    tokio::join!(calling, answer).0.unwrap();
    send::step(&ctx.db, &ctx.writes, &ctx.net, &ctx.materials, || AT)
        .await
        .unwrap();
    let attachments = sqlx::query_as::<_, (i64, String, i64)>(
        "SELECT material_id, file_name, size FROM email_attachment",
    )
    .fetch_all(&ctx.db)
    .await
    .unwrap();
    assert_eq!(attachments, vec![(same_name, "简历.pdf".into(), 5)]);
    let mail = inbox.lock().unwrap();
    assert_eq!(mail.len(), 1);
    assert!(mail[0].contains("<p>dialog HTML</p>"));
    assert!(mail[0].contains("dialog text"));
    assert_eq!(
        mail[0].matches("Content-Disposition: attachment;").count(),
        1
    );
    assert!(mail[0].contains(&BASE64_STANDARD.encode(chosen_data)));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn missing_or_mismatched_rendering_cannot_approve_a_letter(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["mentor@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        &ctx,
        "approveFirstEmail",
        json!({
            "mentorId":mentor, "subject":"申请", "bodyMd":"信", "attachments":[],
            "html":"untrusted", "text":"untrusted"
        }),
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let answer = async {
        waiting.await.unwrap();
        let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
        for rendered in [
            Value::Null,
            json!([]),
            json!([
                {"html":"one", "text":"one"}, {"html":"two", "text":"two"}
            ]),
        ] {
            let mut input = json!({"id":requests[0]["id"], "allow":true});
            if !rendered.is_null() {
                input["rendered"] = rendered;
            }
            assert!(matches!(
                call(&ctx, "answerAgentRequest", input)
                    .await
                    .unwrap_err()
                    .code,
                ErrorCode::Invalid
            ));
            assert_eq!(
                sqlx::query_scalar::<_, i64>("SELECT count(*) FROM email WHERE state = 'approved'")
                    .fetch_one(&ctx.db)
                    .await
                    .unwrap(),
                0
            );
            assert_eq!(
                call(&ctx, "listAgentRequests", json!({}))
                    .await
                    .unwrap()
                    .as_array()
                    .unwrap()
                    .len(),
                1
            );
        }
        call(
            &ctx,
            "answerAgentRequest",
            json!({"id":requests[0]["id"], "allow":false}),
        )
        .await
        .unwrap();
    };
    assert!(tokio::join!(calling, answer).0.is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn task_approval_keeps_input_order_and_exposes_the_full_sending_window(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    let task = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
            window_end, per_window, state, created_at)
        VALUES ('申请', 'first', 'ai', 'window', '["2096-01-01"]', '09:00', '11:00', 10, 'active', 0)
        RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let mut ids = Vec::new();
    for name in ["甲", "乙"] {
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: name.into(),
                emails: vec![format!("mentor{}@example.invalid", ids.len())],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let id = test_fixtures::mail::first_email(&ctx.db, mentor, "review", 0)
            .await
            .unwrap();
        sqlx::query("UPDATE email SET task_id = ? WHERE id = ?")
            .bind(task)
            .bind(id)
            .execute(&ctx.db)
            .await
            .unwrap();
        ids.push(id);
    }
    ids.reverse();
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        &ctx,
        "approveTaskEmails",
        json!({"taskId":task, "emails":ids.iter().map(|id|
        json!({"id":id, "subject":"申请", "bodyMd":"信", "attachments":[]})
    ).collect::<Vec<_>>()}),
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let answer = async {
        waiting.await.unwrap();
        let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
        let letters = requests[0]["description"]["letters"].as_array().unwrap();
        assert_eq!(letters.len(), 2);
        assert_eq!(letters[0]["mentorName"], "乙");
        assert_eq!(letters[1]["mentorName"], "甲");
        for letter in letters {
            assert_eq!(
                letter["sendAt"],
                json!({"kind":"task", "taskId":task,
                "schedule":{"kind":"window", "dates":["2096-01-01"], "start":"09:00", "end":"11:00", "perWindow":10}})
            );
        }
        call(
            &ctx,
            "answerAgentRequest",
            json!({"id":requests[0]["id"], "allow":true, "rendered":[
                {"html":"<p>second</p>", "text":"second"}, {"html":"<p>first</p>", "text":"first"}
            ]}),
        )
        .await
        .unwrap();
    };
    assert_eq!(tokio::join!(calling, answer).0.unwrap(), 2);
    for (id, text) in ids.into_iter().zip(["second", "first"]) {
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT text FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&ctx.db)
                .await
                .unwrap(),
            text
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn reply_and_follow_up_preview_the_subject_and_quote_and_use_dialog_rendering(
    db: SqlitePool,
) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    for follow_up in [false, true] {
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: "王伟".into(),
                emails: vec![format!("mentor{follow_up}@example.invalid")],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let (draft, op) = if follow_up {
            test_fixtures::mail::first_email(&ctx.db, mentor, "sent", 0)
                .await
                .unwrap();
            (
                call(&ctx, "createFollowUpDraft", json!({"id":mentor}))
                    .await
                    .unwrap(),
                "approveFollowUp",
            )
        } else {
            let message = sqlx::query_scalar::<_, i64>(
                r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id,
                    sent_at, from_address, subject, body_text, truncated)
                VALUES (1, 'INBOX', 1, 1, 'reply', '<reply@example.invalid>', 0,
                    'mentor@example.invalid', '申请', '收到', 0) RETURNING id"#,
            )
            .fetch_one(&ctx.db)
            .await
            .unwrap();
            sqlx::query("INSERT INTO message_mentor (message_id, mentor_id) VALUES (?, ?)")
                .bind(message)
                .bind(mentor)
                .execute(&ctx.db)
                .await
                .unwrap();
            (
                call(
                    &ctx,
                    "saveReplyDraft",
                    json!({"mentorId":mentor, "replyTo":"<reply@example.invalid>",
                "bodyMd":"感谢", "attachments":[]}),
                )
                .await
                .unwrap(),
                "approveReply",
            )
        };
        let (registered, waiting) = tokio::sync::oneshot::channel();
        let calling = call_agent(
            &ctx,
            op,
            json!({"mentorId":mentor, "draftId":draft["id"],
            "replyTo":draft["replyTo"], "bodyMd":"感谢", "attachments":[]}),
            |_| {
                let _ = registered.send(());
            },
            pending(),
        );
        let answer = async {
            waiting.await.unwrap();
            let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
            let letter = &requests[0]["description"]["letters"][0];
            assert_eq!(letter["subject"], "Re: 申请");
            assert_eq!(letter["bodyMd"], "感谢");
            assert_eq!(letter["sendAt"], json!({"kind":"now"}));
            assert_eq!(
                letter["previous"]["bodyText"],
                if follow_up { "信" } else { "收到" }
            );
            call(
                &ctx,
                "answerAgentRequest",
                json!({"id":requests[0]["id"], "allow":true,
                "rendered":[{"html":"<p>dialog</p>", "text":"dialog"}]}),
            )
            .await
            .unwrap();
        };
        tokio::join!(calling, answer).0.unwrap();
        let (html, text) =
            sqlx::query_as::<_, (String, String)>("SELECT html, text FROM email WHERE id = ?")
                .bind(draft["id"].as_i64().unwrap())
                .fetch_one(&ctx.db)
                .await
                .unwrap();
        assert!(html.starts_with("<p>dialog</p>"));
        assert!(text.starts_with("dialog\n\n"));
        assert!(text.ends_with(if follow_up { "> 信" } else { "> 收到" }));
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn resume_mailbox_rechecks_recipients_and_deleting_a_task_rechecks_its_email_set(
    db: SqlitePool,
) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    test_fixtures::mail::mailbox(&mut ctx.db.acquire().await.unwrap())
        .await
        .unwrap();
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec!["before@example.invalid".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    call(
        &ctx,
        "setMentorMailbox",
        json!({"id":mentor, "mailboxId":1}),
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    sqlx::query("UPDATE mailbox SET paused_until = ?, pause_reason = 'fixture' WHERE id = 1")
        .bind(i64::MAX)
        .execute(&ctx.db)
        .await
        .unwrap();
    let result = allow_after_change(&ctx, "resumeMailbox", json!({"id":1}), async {
        call(
            &ctx,
            "updateMentor",
            emails_edit(&ctx, mentor, &["after@example.invalid"]).await,
        )
        .await
        .unwrap();
    })
    .await;
    assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
    assert_eq!(
        sqlx::query_scalar::<_, Option<i64>>("SELECT paused_until FROM mailbox WHERE id = 1")
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        Some(i64::MAX)
    );
    let task = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at)
        VALUES ('申请', 'first', 'ai', 'now', 'paused', 0) RETURNING id"#,
    )
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let result = allow_after_change(&ctx, "deleteTask", json!({"id":task}), async {
        sqlx::query("UPDATE email SET task_id = ? WHERE mentor_id = ?")
            .bind(task)
            .bind(mentor)
            .execute(&ctx.db)
            .await
            .unwrap();
    })
    .await;
    assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM task WHERE id = ?")
            .bind(task)
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        1
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn editing_a_mailbox_requires_confirmation_only_while_its_pause_is_active(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mut edit = json!({
        "address":"me@example.invalid", "senderName":"学生", "provider":"custom",
        "smtp":{"host":"localhost", "port":1, "security":"tls"},
        "imap":{"host":"localhost", "port":1, "security":"tls"},
        "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
    });
    let id = call(&ctx, "addMailbox", edit.clone()).await.unwrap()["id"].clone();
    edit["id"] = id;
    edit["secret"] = json!("");
    // Resuming would send it.
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    for until in [Some(i64::MAX), Some(0), None] {
        sqlx::query("UPDATE mailbox SET paused_until = ?, pause_reason = ?")
            .bind(until)
            .bind(until.map(|_| "fixture"))
            .execute(&ctx.db)
            .await
            .unwrap();
        if until == Some(i64::MAX) {
            refused(&ctx, "updateMailbox", edit.clone()).await;
            assert_eq!(
                sqlx::query_scalar::<_, Option<i64>>("SELECT paused_until FROM mailbox")
                    .fetch_one(&ctx.db)
                    .await
                    .unwrap(),
                until
            );
        } else {
            assert_eq!(
                call_agent(&ctx, "updateMailbox", edit.clone(), |_| {}, pending())
                    .await
                    .unwrap(),
                Value::Null
            );
            assert_eq!(
                sqlx::query_scalar::<_, Option<i64>>("SELECT paused_until FROM mailbox")
                    .fetch_one(&ctx.db)
                    .await
                    .unwrap(),
                None
            );
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn empty_saves_require_confirmation_only_when_they_would_delete_a_draft(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    for (op, kind) in [("saveFirstDraft", "first"), ("saveReplyDraft", "reply")] {
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: kind.into(),
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let id = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO email (mentor_id, kind, reply_to, state, subject, body_md,
                created_at, updated_at)
            VALUES (?, ?, ?, 'review', '申请', '保留的草稿', 0, 0)
            RETURNING id"#,
        )
        .bind(mentor)
        .bind(kind)
        .bind((kind == "reply").then_some("<target@example.invalid>"))
        .fetch_one(&ctx.db)
        .await
        .unwrap();
        let input = json!({
            "mentorId":mentor, "subject":"  ", "bodyMd":"\n", "attachments":[],
            "replyTo":"<target@example.invalid>"
        });
        refused(&ctx, op, input.clone()).await;
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT body_md FROM email WHERE id = ?")
                .bind(id)
                .fetch_one(&ctx.db)
                .await
                .unwrap(),
            "保留的草稿"
        );
        sqlx::query("DELETE FROM email WHERE id = ?")
            .bind(id)
            .execute(&ctx.db)
            .await
            .unwrap();
        assert_eq!(
            call_agent(&ctx, op, input, |_| {}, pending())
                .await
                .unwrap(),
            Value::Null
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn recipient_edits_require_confirmation_only_for_approved_mail(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    for (n, op) in [
        "updateMentor",
        "addMentorEmail",
        "importMentors",
        "acceptCandidates",
    ]
    .into_iter()
    .enumerate()
    {
        let name = format!("导师{n}");
        let before = format!("approved{n}@example.invalid");
        let after = format!("changed{n}@school.edu");
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: name.clone(),
                emails: vec![before.clone()],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
            .await
            .unwrap();
        let decisions = json!({"take":[], "matches":[], "restore":[]});
        let input = match op {
            "updateMentor" => emails_edit(&ctx, mentor, &[&after]).await,
            "addMentorEmail" => json!({"id":mentor, "address":after}),
            "importMentors" => json!({
                "fileName":"mentors.csv",
                "data":BASE64_STANDARD.encode(format!("姓名,邮箱\n{name},{before};{after}\n")),
                "setup":{"sheet":null,"columns":null}, "decisions":decisions
            }),
            "acceptCandidates" => {
                let candidate = sqlx::query_scalar::<_, i64>(
                    r#"INSERT INTO candidate (name, emails, review, enrich)
                    VALUES (?, ?, 'pending', 'none') RETURNING id"#,
                )
                .bind(&name)
                .bind(json!([before, after]).to_string())
                .fetch_one(&ctx.db)
                .await
                .unwrap();
                json!({"ids":[candidate], "decisions":decisions})
            }
            _ => unreachable!(),
        };
        refused(&ctx, op, input.clone()).await;
        assert_eq!(
            mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
            before
        );
        let tail = format!("tail{n}@example.invalid");
        let mut safe = input.clone();
        match op {
            "updateMentor" => safe["emails"] = json!([before, after]),
            "addMentorEmail" => safe["address"] = json!(tail),
            "importMentors" => {
                safe["data"] =
                    json!(BASE64_STANDARD.encode(format!("姓名,邮箱\n{name},{before};{tail}\n")));
            }
            "acceptCandidates" => {
                let candidate = sqlx::query_scalar::<_, i64>(
                    r#"INSERT INTO candidate (name, emails, review, enrich)
                    VALUES (?, ?, 'pending', 'none') RETURNING id"#,
                )
                .bind(&name)
                .bind(json!([before, tail]).to_string())
                .fetch_one(&ctx.db)
                .await
                .unwrap();
                safe["ids"] = json!([candidate]);
            }
            _ => unreachable!(),
        }
        call_agent(&ctx, op, safe, |_| {}, pending()).await.unwrap();
        assert_eq!(
            mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
            before
        );
        sqlx::query("UPDATE email SET state = 'review' WHERE id = ?")
            .bind(email)
            .execute(&ctx.db)
            .await
            .unwrap();
        // The safe edit changed the emails the first one began from.
        let input = if op == "updateMentor" {
            emails_edit(&ctx, mentor, &[&after]).await
        } else {
            input
        };
        call_agent(&ctx, op, input, |_| {}, pending())
            .await
            .unwrap();
        assert_eq!(
            mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
            after
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn approval_rechecks_changed_candidate_addresses_and_allows_effects_no_longer_needing_confirmation(
    db: SqlitePool,
) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let before = "old@example.invalid";
    let mentor = mentor::create(
        &ctx.db,
        &mentor::MentorFields {
            name: "王伟".into(),
            emails: vec![before.into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let email = test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
        .await
        .unwrap();
    let candidate = sqlx::query_scalar::<_, i64>(
        r#"INSERT INTO candidate (name, emails, review, enrich)
        VALUES ('王伟', ?, 'pending', 'none')
        RETURNING id"#,
    )
    .bind(json!([before, "shown@a.edu"]).to_string())
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let input = json!({"ids":[candidate], "decisions":{"take":[], "matches":[], "restore":[]}});
    for no_longer_approved in [false, true] {
        let (registered, waiting) = tokio::sync::oneshot::channel();
        let calling = call_agent(
            &ctx,
            "acceptCandidates",
            input.clone(),
            |_| {
                let _ = registered.send(());
            },
            pending(),
        );
        let change_and_allow = async {
            waiting.await.unwrap();
            let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
            sqlx::query("UPDATE candidate SET emails = ? WHERE id = ?")
                .bind(json!([before, "unshown@b.edu"]).to_string())
                .bind(candidate)
                .execute(&ctx.db)
                .await
                .unwrap();
            if no_longer_approved {
                sqlx::query("UPDATE email SET state = 'review' WHERE id = ?")
                    .bind(email)
                    .execute(&ctx.db)
                    .await
                    .unwrap();
            }
            call(
                &ctx,
                "answerAgentRequest",
                json!({"id":requests[0]["id"], "allow":true}),
            )
            .await
            .unwrap();
        };
        let (result, ()) = tokio::join!(calling, change_and_allow);
        if no_longer_approved {
            result.unwrap();
            assert_eq!(
                mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
                "unshown@b.edu"
            );
        } else {
            assert_eq!(
                result.unwrap_err().message,
                "等你确认期间情况变了，请重新发起"
            );
            assert_eq!(
                mentor::get(&ctx.db, mentor).await.unwrap().unwrap().emails[0],
                before
            );
            assert_eq!(
                sqlx::query_scalar::<_, String>("SELECT review FROM candidate WHERE id = ?")
                    .bind(candidate)
                    .fetch_one(&ctx.db)
                    .await
                    .unwrap(),
                "pending"
            );
        }
    }
}

async fn refuses_changed_candidate_recipient(
    db: SqlitePool,
    count: usize,
    shown: &str,
    changed: &str,
) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mut mentors = Vec::new();
    let mut candidates = Vec::new();
    for i in 0..count {
        let name = format!("导师{i}");
        let before = format!("old{i}@example.invalid");
        let mentor = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: name.clone(),
                emails: vec![before.clone()],
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        test_fixtures::mail::first_email(&ctx.db, mentor, "approved", 0)
            .await
            .unwrap();
        let candidate = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO candidate (name, emails, review, enrich)
            VALUES (?, ?, 'pending', 'none')
            RETURNING id"#,
        )
        .bind(name)
        .bind(json!([before, format!("{i}-{shown}")]).to_string())
        .fetch_one(&ctx.db)
        .await
        .unwrap();
        mentors.push((mentor, before));
        candidates.push(candidate);
    }
    let (registered, waiting) = tokio::sync::oneshot::channel();
    let calling = call_agent(
        &ctx,
        "acceptCandidates",
        json!({"ids":candidates, "decisions":{"take":[], "matches":[], "restore":[]}}),
        |_| {
            let _ = registered.send(());
        },
        pending(),
    );
    let change_and_allow = async {
        waiting.await.unwrap();
        let requests = call(&ctx, "listAgentRequests", json!({})).await.unwrap();
        let last = count - 1;
        sqlx::query("UPDATE candidate SET emails = ? WHERE id = ?")
            .bind(json!([mentors[last].1, format!("{last}-{changed}")]).to_string())
            .bind(candidates[last])
            .execute(&ctx.db)
            .await
            .unwrap();
        call(
            &ctx,
            "answerAgentRequest",
            json!({"id":requests[0]["id"], "allow":true}),
        )
        .await
        .unwrap();
    };
    let (result, ()) = tokio::join!(calling, change_and_allow);
    assert_eq!(
        result.unwrap_err().message,
        "等你确认期间情况变了，请重新发起"
    );
    for (id, before) in mentors {
        assert_eq!(
            mentor::get(&ctx.db, id).await.unwrap().unwrap().emails[0],
            before
        );
    }
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM candidate WHERE review = 'pending'")
            .fetch_one(&ctx.db)
            .await
            .unwrap(),
        count as i64
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn confirmation_detects_recipient_changes_after_the_first_forty_characters(db: SqlitePool) {
    let prefix = "a".repeat(45);
    refuses_changed_candidate_recipient(
        db,
        1,
        &format!("{prefix}1@a.edu"),
        &format!("{prefix}2@b.edu"),
    )
    .await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn confirmation_detects_a_change_to_only_the_sixth_mentor(db: SqlitePool) {
    refuses_changed_candidate_recipient(db, 6, "shown@a.edu", "unshown@b.edu").await;
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_merge_is_confirmed_only_with_the_drafts_it_discards(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mut ids = Vec::new();
    for name in ["王伟", "王伟伟"] {
        let id = mentor::create(
            &ctx.db,
            &mentor::MentorFields {
                name: name.into(),
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        email::save_first_draft(&ctx.db, id, "申请", "草稿", &[], 0)
            .await
            .unwrap();
        ids.push(id);
    }
    // A preview from before the other mentor's draft: the user would agree to discarding none.
    let error = call_agent(
        &ctx,
        "mergeMentors",
        json!({"keep":ids[0], "other":ids[1], "discardedDrafts":[]}),
        |_| panic!("a merge whose preview is out of date was put to the user"),
        pending(),
    )
    .await
    .unwrap_err();
    assert!(matches!(error.code, ErrorCode::Invalid));
    refused(
        &ctx,
        "mergeMentors",
        json!({"keep":ids[0], "other":ids[1], "discardedDrafts":["first"]}),
    )
    .await;
}
