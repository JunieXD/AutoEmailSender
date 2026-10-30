#![cfg(test)]

use api::{Context, ErrorCode, call};
use serde_json::{Value, json};
use sqlx::SqlitePool;

fn context(db: SqlitePool) -> Context {
    Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""))
}

async fn mentor(db: &SqlitePool) -> i64 {
    mentor::create(
        db,
        &mentor::MentorFields {
            name: "王老师".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn todo_operations_validate_inputs_return_fields_and_preserve_manual_control(db: SqlitePool) {
    let m = mentor(&db).await;
    let ctx = context(db);
    assert_eq!(
        call(&ctx, "getAiReplyTodos", json!({})).await.unwrap(),
        false
    );
    assert_eq!(
        call(&ctx, "getTodoReminders", json!({})).await.unwrap(),
        true
    );
    for on in [false, true] {
        call(&ctx, "setTodoReminders", json!({"on":on}))
            .await
            .unwrap();
        assert_eq!(call(&ctx, "getTodoReminders", json!({})).await.unwrap(), on);
    }
    for bad in [
        json!({"text":" ","dueDate":null,"dueTime":null}),
        json!({"text":"事","dueDate":"2026-02-30","dueTime":null}),
        json!({"text":"事","dueDate":null,"dueTime":"09:00"}),
    ] {
        let mut input = bad;
        input["mentorId"] = json!(m);
        assert!(matches!(
            call(&ctx, "addTodo", input).await.unwrap_err().code,
            ErrorCode::Invalid
        ));
    }
    let id = call(
        &ctx,
        "addTodo",
        json!({"mentorId":m,"text":"  发材料  ","dueDate":"2030-01-01","dueTime":"17:00"}),
    )
    .await
    .unwrap();
    let list = call(&ctx, "listTodos", json!({"page":1})).await.unwrap();
    assert_eq!(list["total"], 1);
    let row = &list["todos"][0];
    assert_eq!(row["id"], id);
    assert_eq!(row["text"], "发材料");
    assert_eq!(row["dueDate"], "2030-01-01");
    assert_eq!(row["dueTime"], "17:00");
    assert_eq!(row["mentorId"], m);
    assert_eq!(row["mentorName"], "王老师");
    assert_eq!(row["messageId"], Value::Null);
    assert_eq!(row["done"], false);
    assert!(row["createdAt"].is_i64());
    call(
        &ctx,
        "updateTodo",
        json!({"id":id,"text":"准备面谈","dueDate":null,"dueTime":null}),
    )
    .await
    .unwrap();
    call(&ctx, "setTodoDone", json!({"id":id,"done":true}))
        .await
        .unwrap();
    assert_eq!(
        call(&ctx, "listTodos", json!({"page":1})).await.unwrap()["total"],
        0
    );
    let correspondence = call(&ctx, "listCorrespondence", json!({"id":m}))
        .await
        .unwrap();
    assert_eq!(correspondence[0]["todo"]["text"], "准备面谈");
    assert_eq!(correspondence[0]["todo"]["done"], true);
    call(&ctx, "setTodoDone", json!({"id":id,"done":false}))
        .await
        .unwrap();
    call(&ctx, "deleteTodo", json!({"id":id})).await.unwrap();
    assert!(matches!(
        call(
            &ctx,
            "updateTodo",
            json!({"id":id,"text":"旧内容","dueDate":null,"dueTime":null})
        )
        .await
        .unwrap_err()
        .code,
        ErrorCode::NotFound
    ));
    let kept = call(
        &ctx,
        "addTodo",
        json!({"mentorId":m,"text":"寄成绩单","dueDate":null,"dueTime":null}),
    )
    .await
    .unwrap();
    mentor::trash::trash(&ctx.db, &[m], 1).await.unwrap();
    assert!(matches!(
        call(
            &ctx,
            "addTodo",
            json!({"mentorId":m,"text":"事","dueDate":null,"dueTime":null})
        )
        .await
        .unwrap_err()
        .code,
        ErrorCode::Invalid
    ));
    // A trashed mentor's correspondence is read-only, including an editor left open.
    for (op, input) in [
        (
            "updateTodo",
            json!({"id":kept,"text":"改了","dueDate":null,"dueTime":null}),
        ),
        ("setTodoDone", json!({"id":kept,"done":true})),
        ("deleteTodo", json!({"id":kept})),
    ] {
        assert!(matches!(
            call(&ctx, op, input).await.unwrap_err().code,
            ErrorCode::NotFound
        ));
    }
    let correspondence = call(&ctx, "listCorrespondence", json!({"id":m}))
        .await
        .unwrap();
    assert_eq!(correspondence[0]["todo"]["text"], "寄成绩单");
    assert_eq!(correspondence[0]["todo"]["done"], false);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn ai_switch_requires_model_consent_and_does_not_backfill(db: SqlitePool) {
    let ctx = context(db);
    assert!(matches!(
        call(&ctx, "setAiReplyTodos", json!({"enabled":true}))
            .await
            .unwrap_err()
            .code,
        ErrorCode::Invalid
    ));
    sqlx::query(
        r#"INSERT INTO model_config (provider, model, api_key, agreed, created_at)
        VALUES ('deepseek', 'fixture', '', 0, 0)"#,
    )
    .execute(&ctx.db)
    .await
    .unwrap();
    assert!(matches!(
        call(&ctx, "setAiReplyTodos", json!({"enabled":true}))
            .await
            .unwrap_err()
            .code,
        ErrorCode::NeedsAgreement
    ));
    sqlx::query("UPDATE model_config SET agreed=1")
        .execute(&ctx.db)
        .await
        .unwrap();
    call(&ctx, "setAiReplyTodos", json!({"enabled":true}))
        .await
        .unwrap();
    assert_eq!(
        call(&ctx, "getAiReplyTodos", json!({})).await.unwrap(),
        true
    );
    call(&ctx, "setAiReplyTodos", json!({"enabled":false}))
        .await
        .unwrap();
    assert_eq!(
        call(&ctx, "getAiReplyTodos", json!({})).await.unwrap(),
        false
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn extracted_todos_follow_their_reply_and_manual_todos_use_creation_time(db: SqlitePool) {
    let m = mentor(&db).await;
    sqlx::query(
        r#"INSERT INTO mailbox (address, sender_name, provider, smtp_host, smtp_port,
            smtp_security, imap_host, imap_port, imap_security, auth, secret,
            daily_limit, min_interval_s, rotation_order, created_at)
        VALUES ('me@example.com', '学生', 'custom', 'localhost', 465, 'tls',
            'localhost', 993, 'tls', 'password', '', 50, 60, 1, 0)"#,
    )
    .execute(&db)
    .await
    .unwrap();
    let mut replies = Vec::new();
    for (at, uid) in [(10, 1), (20, 2)] {
        let id = sqlx::query(
            r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, sent_at,
                from_address, truncated)
            VALUES (1, 'INBOX', 1, ?, 'reply', ?, 'mentor@edu', 0)"#,
        )
        .bind(uid)
        .bind(at)
        .execute(&db)
        .await
        .unwrap()
        .last_insert_rowid();
        sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
            .bind(m)
            .bind(id)
            .execute(&db)
            .await
            .unwrap();
        replies.push(id);
    }
    for (message, at, text) in [
        (Some(replies[0]), 30, "回信一"),
        (Some(replies[1]), 31, "回信二"),
        (None, 15, "手动"),
    ] {
        sqlx::query(
            r#"INSERT INTO todo (mentor_id, message_id, text, created_at, due_changed_at)
            VALUES (?, ?, ?, ?, ?)"#,
        )
        .bind(m)
        .bind(message)
        .bind(text)
        .bind(at)
        .bind(at)
        .execute(&db)
        .await
        .unwrap();
    }
    let ctx = context(db);
    let entries = call(&ctx, "listCorrespondence", json!({"id":m}))
        .await
        .unwrap();
    let entries = entries.as_array().unwrap();
    assert_eq!(entries.len(), 5);
    assert_eq!(entries[0]["message"]["id"], replies[0]);
    assert_eq!(entries[1]["todo"]["text"], "回信一");
    assert_eq!(entries[2]["todo"]["text"], "手动");
    assert_eq!(entries[3]["message"]["id"], replies[1]);
    assert_eq!(entries[4]["todo"]["text"], "回信二");
    assert!(entries[0]["message"]["todoExtraction"].is_null());
}
