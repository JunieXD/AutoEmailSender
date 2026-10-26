#![cfg(test)]

use std::borrow::Cow;
use std::path::Path;

use api::{Context, ErrorCode, call};
use base64::prelude::{BASE64_STANDARD, Engine};
use fakemail::{ADDRESS, Inbox, SECRET};
use mailbox::{MailboxForm, Security, Server};
use serde_json::json;
use sqlx::SqlitePool;

fn context(db: SqlitePool, materials: &Path) -> Context {
    Context::new(db, net::Net::new(Vec::new()).unwrap(), materials.to_owned())
}

async fn mailbox(ctx: &mut Context, secret: &str) -> (i64, Inbox) {
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security: Security::Tls,
    };
    let fields = MailboxForm {
        address: ADDRESS.into(),
        sender_name: "林晓".into(),
        provider: mailbox::providers::CUSTOM.into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 1,
        min_interval_s: 60,
    }
    .check()
    .unwrap();
    let id = mailbox::create(&ctx.db, &fields, secret, 0).await.unwrap();
    ctx.net = net::Net::new(vec![pki.root]).unwrap();
    (id, inbox)
}

async fn material(ctx: &Context, name: &str, data: &[u8], default: bool) -> i64 {
    let id = sqlx::query_scalar(
        "INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default, created_at)
         VALUES (?, ?, 0, 0, ?, 0) RETURNING id",
    )
    .bind(name)
    .bind(data.len() as i64)
    .bind(default)
    .fetch_one(&ctx.db)
    .await
    .unwrap();
    std::fs::write(material::file(&ctx.materials, id), data).unwrap();
    id
}

async fn template(ctx: &Context, name: &str, subject: &str, body: &str) -> i64 {
    let fields = template::TemplateForm {
        name: name.into(),
        subject: subject.into(),
        body_md: body.into(),
    }
    .check()
    .unwrap();
    template::create(&ctx.db, &fields, 0).await.unwrap()
}

fn letter_input(mailbox_id: i64, attachments: &[i64]) -> serde_json::Value {
    json!({
        "mailboxId": mailbox_id,
        "subject": "Test letter",
        "html": "<p>HTML test body.</p>",
        "text": "Plain test body.",
        "attachments": attachments,
    })
}

async fn email_count(ctx: &Context) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM email")
        .fetch_one(&ctx.db)
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn finishing_onboarding_is_persistent_and_repeatable(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let ctx = context(db, dir.path());
    let profile = call(&ctx, "getProfile", json!({})).await.unwrap();
    assert_eq!(profile["onboarded"], false);
    for _ in 0..2 {
        assert_eq!(
            call(&ctx, "finishOnboarding", json!({})).await.unwrap(),
            json!(null)
        );
    }
    call(
        &ctx,
        "updateProfile",
        json!({
            "name": "林晓", "researchInterests": "机器学习", "rewritePreference": "",
        }),
    )
    .await
    .unwrap();
    let profile = call(&ctx, "getProfile", json!({})).await.unwrap();
    assert_eq!(profile["onboarded"], true);
    assert_eq!(profile["name"], "林晓");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn no_template_means_no_test_letter(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let ctx = context(db, dir.path());
    assert_eq!(
        call(&ctx, "testLetter", json!({})).await.unwrap(),
        json!(null)
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_default_template_is_filled_with_example_values_and_default_attachments(
    db: SqlitePool,
) {
    let dir = tempfile::tempdir().unwrap();
    let ctx = context(db, dir.path());
    template(&ctx, "最早的模板", "最早的主题", "最早的正文").await;
    let chosen = template(
        &ctx,
        "默认模板",
        "{学校}{学院} {导师姓名}{导师职称} {我的姓名}",
        "{导师姓名}|{导师职称}|{学校}|{学院}|{系所}|{导师研究方向}|{我的姓名}",
    )
    .await;
    call(
        &ctx,
        "updateProfile",
        json!({
            "name": "林*晓", "researchInterests": "", "rewritePreference": "",
        }),
    )
    .await
    .unwrap();
    profile::set_default_template(&ctx.db, Some(chosen))
        .await
        .unwrap();
    let resume = material(&ctx, "简历.pdf", b"pdf", true).await;
    material(&ctx, "不默认附上的材料.docx", b"docx", false).await;
    let transcript = material(&ctx, "成绩单.pdf", b"pdf", true).await;

    assert_eq!(
        call(&ctx, "testLetter", json!({})).await.unwrap(),
        json!({
            "templateName": "默认模板",
            "subject": "示例大学计算机学院 张明教授 林*晓",
            "bodyMd": "张明|教授|示例大学|计算机学院||机器学习|林\\*晓",
            "attachments": [resume, transcript],
        })
    );
    profile::set_default_template(&ctx.db, None).await.unwrap();
    let fallback = call(&ctx, "testLetter", json!({})).await.unwrap();
    assert_eq!(fallback["templateName"], "最早的模板");
    assert_eq!(fallback["subject"], "最早的主题");
    assert_eq!(fallback["bodyMd"], "最早的正文");
    assert_eq!(fallback["attachments"], json!([resume, transcript]));
    assert_eq!(email_count(&ctx).await, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_test_letter_goes_to_self_with_its_content_and_files_without_an_email_row(
    db: SqlitePool,
) {
    let dir = tempfile::tempdir().unwrap();
    let mut ctx = context(db, dir.path());
    let (id, inbox) = mailbox(&mut ctx, SECRET).await;
    let pdf = b"%PDF-1.7\n\x80\x81\x82";
    let resume = material(&ctx, "简历.pdf", pdf, true).await;
    let duplicate = material(&ctx, "简历.pdf", b"different", true).await;
    let docx = material(&ctx, "plan.docx", b"docx\x80", false).await;
    let doc = material(&ctx, "notes.DOC", b"doc\x81", false).await;
    // Read the current file at send time, rather than anything cached in the preview.
    let updated_pdf = b"%PDF-1.7\n\x82\x81\x80";
    std::fs::write(material::file(&ctx.materials, resume), updated_pdf).unwrap();
    call(
        &ctx,
        "sendTestLetter",
        letter_input(id, &[resume, duplicate, resume, docx, doc]),
    )
    .await
    .unwrap();
    let mail = inbox.lock().unwrap().clone();
    assert_eq!(mail.len(), 1);
    let mail = &mail[0];
    let headers = mail.split("\r\n\r\n").next().unwrap();
    for name in ["From: ", "To: "] {
        let header = headers.lines().find_map(|l| l.strip_prefix(name)).unwrap();
        assert!(header.ends_with(&format!("<{ADDRESS}>")), "{header}");
    }
    assert!(headers.lines().any(|l| l == "Subject: Test letter"));
    assert!(mail.contains("Content-Type: multipart/alternative;"));
    assert!(mail.contains("\r\n\r\nPlain test body.\r\n"));
    assert!(mail.contains("\r\n\r\n<p>HTML test body.</p>\r\n"));
    assert_eq!(mail.matches("Content-Disposition: attachment;").count(), 3);
    for (mime, data) in [
        ("application/pdf", updated_pdf.as_slice()),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"docx\x80".as_slice(),
        ),
        ("application/msword", b"doc\x81".as_slice()),
    ] {
        assert!(mail.contains(mime));
        assert!(mail.contains(&BASE64_STANDARD.encode(data)));
    }
    assert!(mail.contains("%E7%AE%80%E5%8E%86.pdf"));
    assert_eq!(email_count(&ctx).await, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_unreadable_or_deleted_attachment_prevents_sending(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let mut ctx = context(db, dir.path());
    let (id, inbox) = mailbox(&mut ctx, SECRET).await;
    let resume = material(&ctx, "简历.pdf", b"pdf", true).await;
    std::fs::remove_file(material::file(&ctx.materials, resume)).unwrap();
    let error = call(&ctx, "sendTestLetter", letter_input(id, &[resume]))
        .await
        .unwrap_err();
    assert!(matches!(error.code, ErrorCode::Invalid));
    assert!(error.message.contains("简历.pdf"));
    assert!(error.message.contains("重新添加"));

    material::delete(&ctx.db, dir.path(), resume, 0)
        .await
        .unwrap();
    let error = call(&ctx, "sendTestLetter", letter_input(id, &[resume]))
        .await
        .unwrap_err();
    assert!(matches!(error.code, ErrorCode::Invalid));
    assert!(error.message.contains("已被删除"));
    assert!(inbox.lock().unwrap().is_empty());
    assert_eq!(email_count(&ctx).await, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_refused_smtp_login_has_a_chinese_reason(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    let mut ctx = context(db, dir.path());
    let (id, inbox) = mailbox(&mut ctx, "wrong").await;
    let error = call(&ctx, "sendTestLetter", letter_input(id, &[]))
        .await
        .unwrap_err();
    assert!(matches!(error.code, ErrorCode::External));
    assert!(error.message.contains("登录被拒绝"));
    assert!(inbox.lock().unwrap().is_empty());
    assert_eq!(email_count(&ctx).await, 0);
}
