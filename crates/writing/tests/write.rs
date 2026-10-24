// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Writing letters with a small HTTP server that runs in the test and answers like a provider.

use std::sync::{Arc, Mutex};

use mentor::MentorForm;
use model::ModelForm;
use net::Net;
use serde_json::Value;
use sqlx::SqlitePool;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use writing::{Error, Letter};

const T: i64 = 1_800_000_000_000;

/// What the fake provider was asked: the system and user messages of each request.
type Received = Arc<Mutex<Vec<(String, String)>>>;

/// Starts a fake provider that streams these answers, one for each request in turn, and returns
/// its address.
async fn provider(answers: Vec<&'static str>) -> (String, Received) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = format!(
        "http://127.0.0.1:{}/v1",
        listener.local_addr().unwrap().port()
    );
    let received = Received::default();
    let log = received.clone();
    tokio::spawn(async move {
        for answer in answers {
            let (stream, _) = listener.accept().await.unwrap();
            let mut io = BufReader::new(stream);
            let mut length = 0;
            loop {
                let mut header = String::new();
                io.read_line(&mut header).await.unwrap();
                if header == "\r\n" {
                    break;
                }
                if let Some((name, value)) = header.trim_end().split_once(": ")
                    && name.eq_ignore_ascii_case("content-length")
                {
                    length = value.parse().unwrap();
                }
            }
            let mut body = vec![0; length];
            io.read_exact(&mut body).await.unwrap();
            let body: Value = serde_json::from_slice(&body).unwrap();
            let content = |i: usize| body["messages"][i]["content"].as_str().unwrap().to_owned();
            log.lock().unwrap().push((content(0), content(1)));
            let chunk = serde_json::json!({"choices": [{"delta": {"content": answer}, "finish_reason": "stop"}]});
            let usage = r#"{"choices":[],"usage":{"prompt_tokens":2000,"completion_tokens":400,"prompt_tokens_details":{"cached_tokens":1500}}}"#;
            let reply = format!("data: {chunk}\n\ndata: {usage}\n\ndata: [DONE]\n\n");
            let head = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                reply.len()
            );
            io.write_all((head + &reply).as_bytes()).await.unwrap();
        }
    });
    (address, received)
}

/// A user with a profile and two materials, one of them for writing, and a mentor with a note;
/// returns the mentor and the material for writing.
async fn setup(db: &SqlitePool) -> (i64, i64) {
    let me = profile::ProfileForm {
        name: "林晓".into(),
        research_interests: "多模态学习".into(),
        rewrite_preference: "语气再正式一些".into(),
    };
    profile::update(db, &me).await.unwrap();
    let material = async |name: &str, for_writing: bool, text: &str| {
        sqlx::query_scalar!(
            r#"INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default,
                 created_at, extracted_text)
               VALUES (?, 1, 0, ?, 0, 0, ?) RETURNING id AS "id!""#,
            name,
            for_writing,
            text,
        )
        .fetch_one(db)
        .await
        .unwrap()
    };
    let resume = material("简历.pdf", true, "在 ACL 2025 发表论文一篇").await;
    material("成绩单.pdf", false, "GPA 3.9").await;
    let form = MentorForm {
        name: "王伟".into(),
        title: "教授".into(),
        email: "wangwei@example.edu.cn".into(),
        university: "清华大学".into(),
        school: String::new(),
        department: String::new(),
        homepage_url: String::new(),
        research: "视觉语言模型".into(),
    };
    let mentor = mentor::create(db, &form.check().unwrap(), T).await.unwrap();
    mentor::set_note(db, mentor, Some("师兄说回信很慢"))
        .await
        .unwrap();
    (mentor, resume)
}

/// A model at this address, which the user has agreed to or not.
async fn model(db: &SqlitePool, address: &str, agreed: bool) {
    let form = ModelForm {
        name: String::new(),
        provider: model::providers::CUSTOM.into(),
        base_url: address.into(),
        model: "m".into(),
    };
    let id = model::create(db, &form.check().unwrap(), "", T)
        .await
        .unwrap();
    if agreed {
        model::agree(db, id).await.unwrap();
    }
}

fn blank() -> Letter {
    Letter {
        subject: " ".into(),
        body_md: "\n".into(),
    }
}

/// The purpose and tokens of each call recorded.
async fn usage(db: &SqlitePool) -> Vec<(String, i64, i64, i64)> {
    sqlx::query!(
        "SELECT purpose, input_tokens, cached_tokens, output_tokens FROM usage ORDER BY id"
    )
    .fetch_all(db)
    .await
    .unwrap()
    .into_iter()
    .map(|r| (r.purpose, r.input_tokens, r.cached_tokens, r.output_tokens))
    .collect()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_blank_letter_is_drafted_from_the_user_and_the_mentor(db: SqlitePool) {
    let (mentor, resume) = setup(&db).await;
    let (address, received) = provider(vec![
        "<subject>申请攻读博士学位</subject>\n<body>\n王伟老师：\n\n您好！\n</body>",
    ])
    .await;
    model(&db, &address, true).await;
    let net = Net::new(Vec::new()).unwrap();

    let letter = writing::write(&db, &net, mentor, &blank(), &[resume], T)
        .await
        .unwrap();
    assert_eq!(letter.subject, "申请攻读博士学位");
    assert_eq!(letter.body_md, "王伟老师：\n\n您好！");

    let (_, message) = received.lock().unwrap().pop().unwrap();
    for sent in [
        "姓名：林晓",
        "意向研究方向：多模态学习",
        "语气再正式一些",
        "在 ACL 2025 发表论文一篇",
        "<附件>\n简历.pdf\n</附件>",
        "姓名：王伟",
        "研究方向：视觉语言模型",
    ] {
        assert!(message.contains(sent), "{sent} not in {message}");
    }
    // Only materials for writing, and nothing of the mentor's the letter has no use for.
    for kept in ["GPA", "wangwei@", "师兄", "<现在的信>"] {
        assert!(!message.contains(kept), "{kept} in {message}");
    }
    assert!(message.ends_with("请写这封信。"));
    assert_eq!(usage(&db).await, [("draft".into(), 2000, 1500, 400)]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_written_letter_is_rewritten_and_must_come_back_as_one(db: SqlitePool) {
    let (mentor, _) = setup(&db).await;
    let (address, received) = provider(vec![
        // As a model that thinks aloud before answering.
        "先想想格式：<subject>主题</subject><body>正文</body>。\n<subject>\n申请攻读\n博士学位\n</subject>\n<body>\n王伟老师：您好！\n</body>",
        "王伟老师：您好！",
    ])
    .await;
    model(&db, &address, true).await;
    let net = Net::new(Vec::new()).unwrap();
    let current = Letter {
        subject: "申请读研".into(),
        body_md: "老师您好，我是林晓。".into(),
    };

    let letter = writing::write(&db, &net, mentor, &current, &[], T)
        .await
        .unwrap();
    assert_eq!(letter.subject, "申请攻读 博士学位");
    assert_eq!(letter.body_md, "王伟老师：您好！");
    let (_, message) = received.lock().unwrap().pop().unwrap();
    assert!(
        message.contains("<现在的信>\n主题：申请读研\n正文：\n老师您好，我是林晓。\n</现在的信>")
    );
    assert!(!message.contains("<附件>"), "{message}");
    assert!(message.ends_with("请改写<现在的信>。"));

    let result = writing::write(&db, &net, mentor, &current, &[], T).await;
    assert!(
        matches!(result, Err(Error::NotALetter { .. })),
        "{result:?}"
    );
    // The tokens were spent all the same.
    assert_eq!(usage(&db).await.len(), 2);
    assert_eq!(usage(&db).await[0].0, "rewrite");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn nothing_is_sent_until_the_user_agrees(db: SqlitePool) {
    let (mentor, _) = setup(&db).await;
    let net = Net::new(Vec::new()).unwrap();
    let result = writing::write(&db, &net, mentor, &blank(), &[], T).await;
    assert!(matches!(result, Err(Error::NoModel)), "{result:?}");

    let (address, received) = provider(vec![]).await;
    model(&db, &address, false).await;
    let result = writing::write(&db, &net, mentor, &blank(), &[], T).await;
    assert!(matches!(result, Err(Error::NotAgreed)), "{result:?}");
    assert!(received.lock().unwrap().is_empty());
}
