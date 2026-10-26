#![cfg(test)]

use api::{Context, ErrorCode, call};
use base64::prelude::*;
use serde_json::json;
use sqlx::SqlitePool;

#[tokio::test]
async fn damaged_word_upload_returns_an_actionable_error() {
    let ctx = Context {
        writes: db::Writes::default(),
        db: SqlitePool::connect_lazy("sqlite::memory:").unwrap(),
        net: net::Net::new(vec![]).unwrap(),
        materials: Default::default(),
        sender: Default::default(),
        receiver: Default::default(),
        matcher: Default::default(),
        crawler: Default::default(),
        generator: Default::default(),
        model_limit: Default::default(),
        reply_processor: Default::default(),
        todo_reminders: Default::default(),
    };
    for data in [b"PK broken Word file".as_slice(), b"not a Word file"] {
        let error = call(
            &ctx,
            "importWordTemplate",
            json!({"fileName": "申请.docx", "data": BASE64_STANDARD.encode(data)}),
        )
        .await
        .unwrap_err();
        assert!(matches!(error.code, ErrorCode::Invalid));
        assert!(error.message.contains("请换一个"), "{}", error.message);
    }
}
