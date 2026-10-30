#![cfg(test)]
use api::{Context, ErrorCode, call};
use provider::{Provider, Response};
use serde_json::{Value, json};
use sqlx::SqlitePool;
use test_fixtures::provider;
const T: i64 = 1_800_000_000_000;

fn context(db: SqlitePool) -> Context {
    Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""))
}
async fn config(db: &SqlitePool, address: &str) -> i64 {
    sqlx::query_scalar(
        r#"INSERT INTO model_config (provider, base_url, model, api_key, agreed,
            created_at)
        VALUES ('custom', ?, 'fixture', '', 1, 0)
        RETURNING id"#,
    )
    .bind(address)
    .fetch_one(db)
    .await
    .unwrap()
}
async fn mentor(db: &SqlitePool) -> i64 {
    mentor::create(
        db,
        &mentor::MentorFields {
            name: "王伟".into(),
            ..Default::default()
        },
        T,
    )
    .await
    .unwrap()
}
async fn draft(db: &SqlitePool) -> i64 {
    email::save_first_draft(
        db,
        mentor(db).await,
        "申请",
        "王老师：发表了 3 篇论文。",
        &[],
        T,
    )
    .await
    .unwrap()
    .unwrap()
    .id
}
async fn stored(db: &SqlitePool, id: i64) -> Value {
    let value: Option<String> = sqlx::query_scalar("SELECT fact_check FROM email WHERE id = ?")
        .bind(id)
        .fetch_one(db)
        .await
        .unwrap();
    value
        .map(|s| serde_json::from_str(&s).unwrap())
        .unwrap_or(Value::Null)
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn check_errors_are_saved_and_retry_replaces_them(db: SqlitePool) {
    let ctx = context(db);
    let id = draft(&ctx.db).await;
    assert_eq!(call(&ctx, "getAiFactCheck", json!({})).await.unwrap(), true);
    call(&ctx, "setAiFactCheck", json!({"on":false}))
        .await
        .unwrap();
    assert_eq!(
        call(&ctx, "getAiFactCheck", json!({})).await.unwrap(),
        false
    );
    call(&ctx, "setAiFactCheck", json!({"on":true}))
        .await
        .unwrap();
    let missing = call(&ctx, "factCheckEmail", json!({"id":id}))
        .await
        .unwrap_err();
    assert!(matches!(missing.code, ErrorCode::Invalid));
    assert_eq!(stored(&ctx.db, id).await["state"], "failed");
    let mut p = Provider::start().await.unwrap();
    config(&ctx.db, &p.address).await;
    let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
        for _ in 0..2 {
            p.requests
                .recv()
                .await
                .unwrap()
                .respond
                .send(Response::answer("不是核对结果"))
                .unwrap();
        }
    });
    let error = result.unwrap_err();
    assert!(matches!(error.code, ErrorCode::External));
    assert_eq!(stored(&ctx.db, id).await["reason"], error.message);
    let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
        p.requests
            .recv()
            .await
            .unwrap()
            .respond
            .send(Response::answer("[]"))
            .ok()
            .unwrap();
    });
    assert_eq!(result.unwrap(), json!({"state":"completed","findings":[]}));
    assert_eq!(
        stored(&ctx.db, id).await,
        json!({"state":"completed","findings":[]})
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn manual_writing_and_checks_report_usage_storage_errors(db: SqlitePool) {
    let ctx = context(db);
    let id = draft(&ctx.db).await;
    let mentor: i64 = sqlx::query_scalar("SELECT mentor_id FROM email WHERE id = ?")
        .bind(id)
        .fetch_one(&ctx.db)
        .await
        .unwrap();
    let mut p = Provider::start().await.unwrap();
    config(&ctx.db, &p.address).await;
    sqlx::query(
        r#"CREATE TRIGGER block_usage BEFORE INSERT ON usage
        BEGIN
            SELECT RAISE(FAIL, 'usage storage unavailable');
        END"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    for (op, input, answer) in [
        (
            "writeLetter",
            json!({"mentorId":mentor,"subject":"","bodyMd":"","attachments":[]}),
            "<subject>申请</subject><body>王老师：您好。</body>",
        ),
        ("factCheckEmail", json!({"id":id}), "[]"),
    ] {
        let (result, ()) = tokio::join!(call(&ctx, op, input), async {
            p.requests
                .recv()
                .await
                .unwrap()
                .respond
                .send(Response::answer(answer))
                .unwrap();
        });
        assert!(matches!(result.unwrap_err().code, ErrorCode::Internal));
        assert_eq!(ctx.writes.failure(), None);
        assert!(p.requests.try_recv().is_err());
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn only_review_drafts_accept_results_even_if_state_changes_during_check(db: SqlitePool) {
    let ctx = context(db);
    let mut p = Provider::start().await.unwrap();
    config(&ctx.db, &p.address).await;
    for state in ["approved", "canceled", "deleted"] {
        let id = draft(&ctx.db).await;
        let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
            let request = p.requests.recv().await.unwrap();
            if state == "deleted" {
                sqlx::query("DELETE FROM email WHERE id=?")
                    .bind(id)
                    .execute(&ctx.db)
                    .await
                    .unwrap();
            } else {
                sqlx::query(
                    r#"UPDATE email
                    SET state = ?, html = '<p>正文</p>', text = '正文', planned_at = 1,
                        cancel_reason = CASE WHEN ? = 'canceled' THEN '用户取消' END
                    WHERE id = ?"#,
                )
                .bind(state)
                .bind(state)
                .bind(id)
                .execute(&ctx.db)
                .await
                .unwrap();
            }
            request.respond.send(Response::answer("[]")).ok().unwrap();
        });
        assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
        if state != "deleted" {
            assert_eq!(stored(&ctx.db, id).await, Value::Null);
        }
        assert!(matches!(
            call(&ctx, "factCheckEmail", json!({"id":id}))
                .await
                .unwrap_err()
                .code,
            ErrorCode::Invalid
        ));
        assert!(p.requests.try_recv().is_err());
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn waiting_for_capacity_rechecks_consent_before_reading_configuration(db: SqlitePool) {
    use std::{future::Future, task::Poll};
    let ctx = context(db);
    let mut p = Provider::start().await.unwrap();
    config(&ctx.db, &p.address).await;
    let id = draft(&ctx.db).await;
    let a = ctx.model_limit.acquire().await;
    let _b = ctx.model_limit.acquire().await;
    let _c = ctx.model_limit.acquire().await;
    let mut checking = std::pin::pin!(call(&ctx, "factCheckEmail", json!({"id":id})));
    std::future::poll_fn(|cx| {
        assert!(checking.as_mut().poll(cx).is_pending());
        Poll::Ready(())
    })
    .await;
    sqlx::query("UPDATE model_config SET agreed=0")
        .execute(&ctx.db)
        .await
        .unwrap();
    drop(a);
    let error = checking.await.unwrap_err();
    assert!(matches!(error.code, ErrorCode::NeedsAgreement));
    assert_eq!(stored(&ctx.db, id).await["reason"], error.message);
    assert!(p.requests.try_recv().is_err());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn disabled_checks_return_null_without_reading_model_or_changing_results(db: SqlitePool) {
    let ctx = context(db);
    let id = draft(&ctx.db).await;
    let previous = json!({"state":"completed","findings":[]});
    sqlx::query("UPDATE email SET fact_check=? WHERE id=?")
        .bind(sqlx::types::Json(&previous))
        .bind(id)
        .execute(&ctx.db)
        .await
        .unwrap();
    profile::set_ai_fact_check(&ctx.db, false).await.unwrap();
    assert_eq!(
        call(&ctx, "factCheckEmail", json!({"id":id}))
            .await
            .unwrap(),
        Value::Null
    );
    assert_eq!(stored(&ctx.db, id).await, previous);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn disabling_while_waiting_for_capacity_skips_the_check(db: SqlitePool) {
    use std::{future::Future, task::Poll};
    let ctx = context(db);
    let mut p = Provider::start().await.unwrap();
    config(&ctx.db, &p.address).await;
    let id = draft(&ctx.db).await;
    let a = ctx.model_limit.acquire().await;
    let _b = ctx.model_limit.acquire().await;
    let _c = ctx.model_limit.acquire().await;
    let mut checking = std::pin::pin!(call(&ctx, "factCheckEmail", json!({"id":id})));
    std::future::poll_fn(|cx| {
        assert!(checking.as_mut().poll(cx).is_pending());
        Poll::Ready(())
    })
    .await;
    profile::set_ai_fact_check(&ctx.db, false).await.unwrap();
    drop(a);
    let result = tokio::select! {
        result = &mut checking => result,
        request = p.requests.recv() => {
            request
                .unwrap()
                .respond
                .send(Response::answer("[]"))
                .ok()
                .unwrap();
            checking.await
        }
    };
    assert_eq!(result.unwrap(), Value::Null);
    assert_eq!(stored(&ctx.db, id).await, Value::Null);
    let usage: i64 = sqlx::query_scalar("SELECT count(*) FROM usage")
        .fetch_one(&ctx.db)
        .await
        .unwrap();
    assert_eq!(usage, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn manual_checks_cannot_overwrite_a_regenerated_drafts_findings(db: SqlitePool) {
    let ctx = context(db);
    let mut p = Provider::start().await.unwrap();
    let config = config(&ctx.db, &p.address).await;
    for answer in ["[]", "不是核对结果"] {
        let id = draft(&ctx.db).await;
        let task: i64 = sqlx::query_scalar(
            r#"INSERT INTO task (name, kind, writer, model_config_id, schedule, state,
                created_at)
            VALUES ('核对', 'first', 'ai', ?, 'now', 'active', 0)
            RETURNING id"#,
        )
        .bind(config)
        .fetch_one(&ctx.db)
        .await
        .unwrap();
        sqlx::query("UPDATE email SET task_id=? WHERE id=?")
            .bind(task)
            .bind(id)
            .execute(&ctx.db)
            .await
            .unwrap();
        let fresh = email::fact_check::FactCheck::Completed {
            findings: vec![email::fact_check::Finding {
                quote: "新增成果".into(),
                reason: "材料未提到".into(),
            }],
        };
        let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
            let request = p.requests.recv().await.unwrap();
            assert_eq!(
                task::regenerate(&ctx.db, task, Some(id), false, T)
                    .await
                    .unwrap(),
                1
            );
            let claim = email::tasks::next(&ctx.db).await.unwrap().unwrap();
            assert!(
                email::tasks::complete(
                    &mut ctx.db.acquire().await.unwrap(),
                    claim,
                    "新主题",
                    "王老师：新增成果",
                    "fixture",
                    Some(&fresh),
                    T
                )
                .await
                .unwrap()
            );
            request.respond.send(Response::answer(answer)).ok().unwrap();
            if answer != "[]" {
                p.requests
                    .recv()
                    .await
                    .unwrap()
                    .respond
                    .send(Response::answer(answer))
                    .unwrap();
            }
        });
        assert!(matches!(result.unwrap_err().code, ErrorCode::Invalid));
        assert_eq!(
            stored(&ctx.db, id).await,
            serde_json::to_value(&fresh).unwrap()
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn all_draft_checks_filter_saved_quotes_against_current_edits(db: SqlitePool) {
    let ctx = context(db);
    let result = json!({"state":"completed","findings":[
        {"quote":"3 篇论文","reason":"材料未提到"},
        {"quote":"已经删掉的成果","reason":"材料未提到"}
    ]});
    for (kind, get, check) in [
        ("first", "getFirstDraft", "checkFirstDraft"),
        ("follow_up", "getFollowUpDraft", "checkFollowUpDraft"),
        ("reply", "getReplyDraft", "checkReplyDraft"),
    ] {
        let mentor_id = mentor(&ctx.db).await;
        let id: i64 = sqlx::query_scalar(
            r#"INSERT INTO email (mentor_id, kind, reply_to, state, subject, body_md,
                fact_check, created_at, updated_at)
            VALUES (?, ?, ?, 'review', '申请', '王老师：发表了 3 篇论文。', ?, 0, 0)
            RETURNING id"#,
        )
        .bind(mentor_id)
        .bind(kind)
        .bind((kind != "first").then_some("<target@example.com>"))
        .bind(sqlx::types::Json(&result))
        .fetch_one(&ctx.db)
        .await
        .unwrap();
        let loaded = call(&ctx, get, json!({"id":mentor_id})).await.unwrap();
        assert_eq!(loaded["id"], id);
        assert_eq!(loaded["factCheck"], result);
        for (body, count) in [
            ("王老师：发表了 3 篇论文。", 1),
            ("王老师：参加了课程项目。", 0),
        ] {
            let problems = call(
                &ctx,
                check,
                json!({
                    "mentorId": mentor_id,
                    "replyTo": "<target@example.com>",
                    "subject": "申请",
                    "bodyMd": body,
                    "attachments": []
                }),
            )
            .await
            .unwrap();
            assert_eq!(
                problems
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|p| p["kind"] == "unsupported_claim")
                    .count(),
                count
            );
        }
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn task_checks_use_task_model_and_references_then_fall_back_after_deletion(db: SqlitePool) {
    let ctx = context(db);
    let mut default = Provider::start().await.unwrap();
    config(&ctx.db, &default.address).await;
    let mut selected = Provider::start().await.unwrap();
    let chosen = config(&ctx.db, &selected.address).await;
    let mentor_id = mentor(&ctx.db).await;
    let task: i64 = sqlx::query_scalar(
        r#"INSERT INTO task (name, kind, writer, model_config_id, schedule, state,
            created_at)
        VALUES ('核对', 'first', 'ai', ?, 'now', 'active', 0)
        RETURNING id"#,
    )
    .bind(chosen)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    let id: i64 = sqlx::query_scalar(
        r#"INSERT INTO email (mentor_id, task_id, kind, state, subject, body_md,
            created_at, updated_at)
        VALUES (?, ?, 'first', 'review', '申请', '王老师：3 篇论文', 0, 0)
        RETURNING id"#,
    )
    .bind(mentor_id)
    .bind(task)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    sqlx::query(
        r#"INSERT INTO material (id, file_name, size, for_matching, for_writing,
            attach_by_default, created_at, extracted_text)
        VALUES (1, '固定.pdf', 1, 0, 0, 0, 0, '固定任务资料'),
            (2, '当前.pdf', 1, 0, 1, 0, 0, '当前全局资料')"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    sqlx::query("INSERT INTO task_material VALUES(?,1,'reference')")
        .bind(task)
        .execute(&ctx.db)
        .await
        .unwrap();
    let answer = r#"[{"quote":"3 篇论文","reason":"固定资料未提到论文"}]"#;
    let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
        let request = selected.requests.recv().await.unwrap();
        let message = request.body["messages"][1]["content"].as_str().unwrap();
        assert!(message.contains("固定任务资料"));
        assert!(!message.contains("当前全局资料"));
        request.respond.send(Response::answer(answer)).ok().unwrap();
    });
    let result = result.unwrap();
    assert_eq!(
        call(&ctx, "getTaskDraft", json!({"taskId":task,"id":id}))
            .await
            .unwrap()["factCheck"],
        result
    );
    let key = json!({"taskId":task,"id":id,"subject":"申请","bodyMd":"王老师：3 篇论文","attachments":[]});
    let problems = call(&ctx, "checkTaskDraft", key).await.unwrap();
    let list = call(
        &ctx,
        "listTaskDrafts",
        json!({"taskId":task,"filter":"review","page":1}),
    )
    .await
    .unwrap();
    assert_eq!(
        list["drafts"][0]["problems"],
        problems.as_array().unwrap().len()
    );
    assert!(
        problems
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["kind"] == "unsupported_claim")
    );
    let problems = call(
        &ctx,
        "checkTaskDraft",
        json!({
            "taskId": task,
            "id": id,
            "subject": "申请",
            "bodyMd": "王老师：课程项目",
            "attachments": []
        }),
    )
    .await
    .unwrap();
    assert!(
        !problems
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["kind"] == "unsupported_claim")
    );
    sqlx::query("DELETE FROM model_config WHERE id=?")
        .bind(chosen)
        .execute(&ctx.db)
        .await
        .unwrap();
    let (result, ()) = tokio::join!(call(&ctx, "factCheckEmail", json!({"id":id})), async {
        default
            .requests
            .recv()
            .await
            .unwrap()
            .respond
            .send(Response::answer("[]"))
            .ok()
            .unwrap();
    });
    assert_eq!(result.unwrap()["findings"], json!([]));
}
