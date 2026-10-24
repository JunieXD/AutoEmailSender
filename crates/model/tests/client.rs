// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Calls against a small HTTP server that runs in the test and answers like a provider.

use std::sync::{Arc, Mutex};

use model::providers::NoThinking;
use model::usage::Usage;
use model::{Access, ModelError};
use net::Net;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;

/// A request the fake provider received.
#[derive(Debug, Default)]
struct Request {
    line: String,
    authorization: String,
    body: String,
}

type Received = Arc<Mutex<Vec<Request>>>;

/// Starts a fake provider that answers every request with `status` and `reply`, and returns its
/// address and what it received.
async fn provider(status: u16, reply: &'static str) -> (String, Received) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = format!(
        "http://127.0.0.1:{}/v1",
        listener.local_addr().unwrap().port()
    );
    let received = Received::default();
    let log = received.clone();
    tokio::spawn(async move {
        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let mut io = BufReader::new(stream);
            let mut request = Request::default();
            io.read_line(&mut request.line).await.unwrap();
            let mut length = 0;
            loop {
                let mut header = String::new();
                io.read_line(&mut header).await.unwrap();
                let Some((name, value)) = header.trim_end().split_once(": ") else {
                    break;
                };
                match name.to_ascii_lowercase().as_str() {
                    "authorization" => request.authorization = value.into(),
                    "content-length" => length = value.parse().unwrap(),
                    _ => {}
                }
            }
            let mut body = vec![0; length];
            io.read_exact(&mut body).await.unwrap();
            request.body = String::from_utf8(body).unwrap();
            log.lock().unwrap().push(request);
            let head = format!(
                "HTTP/1.1 {status} X\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                reply.len()
            );
            io.write_all((head + reply).as_bytes()).await.unwrap();
        }
    });
    (address, received)
}

fn access(base_url: String, no_thinking: Option<NoThinking>) -> Access {
    Access {
        base_url,
        api_key: "sk-test".into(),
        no_thinking,
    }
}

#[tokio::test]
async fn a_test_asks_the_model_not_to_think() {
    let (address, received) = provider(
        200,
        concat!(
            r#"data: {"choices":[{"delta":{"role":"assistant","content":""}}]}"#,
            "\n\n",
            r#"data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}"#,
            "\n\n",
            r#"data: {"choices":[],"usage":{"total_tokens":9}}"#,
            "\n\ndata: [DONE]\n\n",
        ),
    )
    .await;
    let net = Net::new(Vec::new()).unwrap();
    let access = access(address, Some(NoThinking::ThinkingDisabled));
    model::test(&net, &access, "deepseek-chat").await.unwrap();

    let received = received.lock().unwrap();
    assert_eq!(received[0].line, "POST /v1/chat/completions HTTP/1.1\r\n");
    assert_eq!(received[0].authorization, "Bearer sk-test");
    let body: Value = serde_json::from_str(&received[0].body).unwrap();
    assert_eq!(body["model"], "deepseek-chat");
    assert_eq!(body["stream"], true);
    assert_eq!(body["thinking"], json!({"type": "disabled"}));
}

#[tokio::test]
async fn a_refusal_says_what_the_provider_said() {
    let (address, _) = provider(
        401,
        r#"{"error":{"message":"Authentication Fails","type":"authentication_error"}}"#,
    )
    .await;
    let net = Net::new(Vec::new()).unwrap();
    let result = model::test(&net, &access(address, None), "deepseek-chat").await;
    assert!(
        matches!(result, Err(ModelError::Refused { status: 401, ref reply }) if reply == "Authentication Fails"),
        "{result:?}"
    );
    assert!(result.unwrap_err().message().contains("API Key"));
}

#[tokio::test]
async fn a_key_quoted_back_is_not_shown() {
    let (address, _) = provider(401, r#"{"error":{"message":"Invalid API key: sk-test"}}"#).await;
    let net = Net::new(Vec::new()).unwrap();
    let error = model::test(&net, &access(address, None), "gpt")
        .await
        .unwrap_err();
    assert!(error.message().contains("Invalid API key: ***"), "{error}");
    assert!(!error.message().contains("sk-test"), "{error}");
    assert!(!error.to_string().contains("sk-test"), "{error}");
}

#[tokio::test]
async fn a_reply_without_a_whole_answer_is_not_a_pass() {
    let net = Net::new(Vec::new()).unwrap();
    // As when a custom address is a website rather than its API.
    let (website, _) = provider(200, "<!doctype html><title>Home</title>").await;
    let result = model::test(&net, &access(website, None), "gpt").await;
    assert!(matches!(result, Err(ModelError::Broken(_))), "{result:?}");
    // As when a model spends all it may write on thinking.
    let (thinker, _) = provider(
        200,
        concat!(
            r#"data: {"choices":[{"delta":{"reasoning_content":"嗯……"}}]}"#,
            "\n\n",
            r#"data: {"choices":[{"delta":{},"finish_reason":"length"}]}"#,
            "\n\ndata: [DONE]\n\n",
        ),
    )
    .await;
    let result = model::test(&net, &access(thinker, None), "r1").await;
    assert!(
        matches!(result, Err(ModelError::NoAnswer { ref finish_reason }) if finish_reason.as_deref() == Some("length")),
        "{result:?}"
    );
    // As OpenRouter reports an upstream failure after it has started answering.
    let (failing, _) = provider(
        200,
        concat!(
            r#"data: {"choices":[{"delta":{"content":"好"},"finish_reason":null}]}"#,
            "\n\n",
            r#"data: {"error":{"code":"server_error","message":"Provider disconnected"},"choices":[{"delta":{"content":""},"finish_reason":"error"}]}"#,
            "\n\n",
        ),
    )
    .await;
    let result = model::test(&net, &access(failing, None), "gpt").await;
    assert!(
        matches!(result, Err(ModelError::Failed(ref reply)) if reply == "Provider disconnected"),
        "{result:?}"
    );
}

#[tokio::test]
async fn an_answer_says_what_it_cost_however_the_provider_counts() {
    let net = Net::new(Vec::new()).unwrap();
    let ask = async |reply: &'static str| {
        let (address, received) = provider(200, reply).await;
        let answer = model::ask(&net, &access(address, None), "m", "规则", "资料")
            .await
            .unwrap();
        let body: Value = serde_json::from_str(&received.lock().unwrap()[0].body).unwrap();
        assert_eq!(
            body["messages"],
            json!([{"role": "system", "content": "规则"}, {"role": "user", "content": "资料"}])
        );
        assert_eq!(body["stream_options"], json!({"include_usage": true}));
        assert_eq!(answer.text, "好的");
        answer.usage
    };
    let usage = |input, cached, output| {
        Some(Usage {
            input,
            cached,
            output,
        })
    };
    // As OpenAI, Qwen and most others count, in a last chunk of its own.
    let openai = concat!(
        r#"data: {"choices":[{"delta":{"content":"好的"},"finish_reason":"stop"}],"usage":null}"#,
        "\n\n",
        r#"data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":30,"prompt_tokens_details":{"cached_tokens":100}}}"#,
        "\n\ndata: [DONE]\n\n",
    );
    assert_eq!(ask(openai).await, usage(120, 100, 30));
    let deepseek = concat!(
        r#"data: {"choices":[{"delta":{"content":"好的"},"finish_reason":"stop"}],"usage":{"prompt_tokens":120,"completion_tokens":30,"prompt_cache_hit_tokens":64,"prompt_cache_miss_tokens":56}}"#,
        "\n\ndata: [DONE]\n\n",
    );
    assert_eq!(ask(deepseek).await, usage(120, 64, 30));
    // Kimi counts in the choice that finishes.
    let kimi = concat!(
        r#"data: {"choices":[{"delta":{"content":"好的"},"finish_reason":"stop","usage":{"prompt_tokens":120,"completion_tokens":30,"total_tokens":150,"cached_tokens":96}}]}"#,
        "\n\ndata: [DONE]\n\n",
    );
    assert_eq!(ask(kimi).await, usage(120, 96, 30));
    let silent = concat!(
        r#"data: {"choices":[{"delta":{"content":"好的"},"finish_reason":"stop"}]}"#,
        "\n\ndata: [DONE]\n\n",
    );
    assert_eq!(ask(silent).await, None);
}

#[tokio::test]
async fn models_are_listed_by_name() {
    let (address, received) = provider(
        200,
        r#"{"object":"list","data":[{"id":"qwen-plus","object":"model"},{"id":"deepseek-v3","object":"model"}]}"#,
    )
    .await;
    let net = Net::new(Vec::new()).unwrap();
    let models = model::list_models(&net, &access(address, None))
        .await
        .unwrap();
    assert_eq!(models, ["deepseek-v3", "qwen-plus"]);
    assert_eq!(
        received.lock().unwrap()[0].line,
        "GET /v1/models HTTP/1.1\r\n"
    );
}
