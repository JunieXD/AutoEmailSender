#![cfg(test)]

use api::{Context, ErrorCode, call};
use base64::prelude::*;
use office_oxide::docx::write::{DocxWriter, Run};
use serde_json::json;
use sqlx::SqlitePool;
use std::io::Cursor;

fn context() -> Context {
    Context {
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
    }
}

#[tokio::test]
async fn damaged_word_upload_returns_an_actionable_error() {
    let ctx = context();
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

#[tokio::test]
async fn word_upload_returns_its_name_and_formatted_html() {
    let mut writer = DocxWriter::new();
    writer.add_ir_paragraph(
        &[
            Run::new("重点").bold(),
            Run {
                hyperlink: Some("https://example.com/cv".into()),
                ..Run::new("简历")
            },
        ],
        None,
    );
    let mut data = Cursor::new(Vec::new());
    writer.write_to(&mut data).unwrap();
    let result = call(
        &context(),
        "importWordTemplate",
        json!({"fileName": "申请.DOC", "data": BASE64_STANDARD.encode(data.into_inner())}),
    )
    .await
    .unwrap();
    assert_eq!(result["name"], "申请");
    let html = result["html"].as_str().unwrap();
    assert!(html.contains("<strong>"));
    assert!(html.contains("href=\"https://example.com/cv\""));
}
