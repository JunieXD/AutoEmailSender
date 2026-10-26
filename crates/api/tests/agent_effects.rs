#![cfg(test)]

use std::future::pending;

use api::{Context, ErrorCode, call, call_agent};
use base64::prelude::{BASE64_STANDARD, Engine};
use serde_json::{Value, json};
use sqlx::SqlitePool;

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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn editing_a_mailbox_requires_confirmation_only_while_its_pause_is_active(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
    let mut edit = json!({
        "address":"me@example.invalid", "senderName":"学生", "provider":"custom",
        "smtp":{"host":"localhost", "port":1, "security":"tls"},
        "imap":{"host":"localhost", "port":1, "security":"tls"},
        "dailyLimit":50, "minIntervalS":60, "secret":"fixture"
    });
    let id = call(&ctx, "addMailbox", edit.clone()).await.unwrap()["id"].clone();
    edit["id"] = id;
    edit["secret"] = json!("");
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
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
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
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
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
            "updateMentor" => json!({
                "id":mentor, "name":name, "emails":[after], "aliases":[],
                "affiliations":[], "homepages":[], "research":""
            }),
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
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
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
