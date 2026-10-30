#![cfg(test)]

use api::{Context, call};
use serde_json::json;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restored_credentials_must_be_refilled_before_editing_or_testing(db: SqlitePool) {
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    let mailbox = json!({
        "address": "me@example.invalid",
        "senderName": "学生",
        "provider": "custom",
        "smtp": {"host": "localhost", "port": 1, "security": "tls"},
        "imap": {"host": "localhost", "port": 1, "security": "tls"},
        "dailyLimit": 50,
        "minIntervalS": 60,
        "secret": "fixture"
    });
    let id = call(&ctx, "addMailbox", mailbox.clone()).await.unwrap()["id"].clone();
    sqlx::query("UPDATE mailbox SET secret=NULL")
        .execute(&ctx.db)
        .await
        .unwrap();
    let list = call(&ctx, "listMailboxes", json!({})).await.unwrap();
    assert_eq!(list[0]["needsSecret"], true);
    assert!(list[0].get("secret").is_none());
    assert_eq!(
        receive::round(&ctx.db, &ctx.net, 0).await.unwrap(),
        receive::Round::default()
    );
    let mut edit = mailbox;
    edit["id"] = id.clone();
    edit["secret"] = json!("");
    for op in ["updateMailbox", "testSending", "testReceiving"] {
        assert!(
            call(&ctx, op, edit.clone())
                .await
                .unwrap_err()
                .message
                .contains("授权码")
        );
    }
    assert!(
        call(&ctx, "sendTestMail", json!({"id":id}))
            .await
            .unwrap_err()
            .message
            .contains("授权码")
    );
    edit["secret"] = json!("replacement");
    call(&ctx, "updateMailbox", edit).await.unwrap();
    assert!(!mailbox::list(&ctx.db, 0).await.unwrap()[0].needs_secret);

    let model = json!({
        "name": "模型",
        "provider": "custom",
        "baseUrl": "http://127.0.0.1:1/v1",
        "model": "m",
        "apiKey": "fixture"
    });
    let id = call(&ctx, "addModelConfig", model.clone()).await.unwrap()["id"].clone();
    sqlx::query("UPDATE model_config SET api_key=NULL")
        .execute(&ctx.db)
        .await
        .unwrap();
    let list = call(&ctx, "listModelConfigs", json!({})).await.unwrap();
    assert_eq!(list[0]["needsApiKey"], true);
    assert!(list[0].get("apiKey").is_none());
    let mut edit = model;
    edit["id"] = id;
    edit["apiKey"] = json!("");
    for op in ["updateModelConfig", "testModel", "listProviderModels"] {
        assert!(
            call(&ctx, op, edit.clone())
                .await
                .unwrap_err()
                .message
                .contains("重新填写")
        );
    }
    edit["apiKey"] = json!("replacement");
    call(&ctx, "updateModelConfig", edit).await.unwrap();
    assert!(!model::list(&ctx.db).await.unwrap()[0].needs_api_key);
}
