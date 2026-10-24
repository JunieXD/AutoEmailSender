#![expect(
    dead_code,
    reason = "shared HTTP fixture consumers exercise different response variants"
)]
//! Local OpenAI-compatible HTTP fixture, shared by writing and matching tests.
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    sync::{mpsc, oneshot},
    task::JoinHandle,
};

pub struct Request {
    pub body: Value,
    pub respond: oneshot::Sender<Response>,
}
pub enum Response {
    Answer {
        text: String,
        finish_reason: String,
    },
    Refused {
        status: u16,
        message: String,
        retry_after: Option<u64>,
    },
}
impl Response {
    pub fn answer(text: &str) -> Self {
        Self::Answer {
            text: text.into(),
            finish_reason: "stop".into(),
        }
    }
}
pub struct Provider {
    pub address: String,
    pub requests: mpsc::UnboundedReceiver<Request>,
    task: JoinHandle<()>,
}
impl Drop for Provider {
    fn drop(&mut self) {
        self.task.abort();
    }
}
impl Provider {
    pub async fn start() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = format!(
            "http://127.0.0.1:{}/v1",
            listener.local_addr().unwrap().port()
        );
        let (tx, requests) = mpsc::unbounded_channel();
        let task = tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let tx = tx.clone();
                tokio::spawn(async move {
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
                    let (respond, response) = oneshot::channel();
                    if tx
                        .send(Request {
                            body: serde_json::from_slice(&body).unwrap(),
                            respond,
                        })
                        .is_err()
                    {
                        return;
                    }
                    let Ok(response) = response.await else {
                        return;
                    };
                    let (status, content, extra) = match response {
                        Response::Answer {
                            text,
                            finish_reason,
                        } => {
                            let chunk = json!({"choices":[{"delta":{"content":text},"finish_reason":finish_reason}]});
                            let usage = r#"{"choices":[],"usage":{"prompt_tokens":2000,"completion_tokens":400,"prompt_tokens_details":{"cached_tokens":1500}}}"#;
                            (
                                200,
                                format!("data: {chunk}\n\ndata: {usage}\n\ndata: [DONE]\n\n"),
                                String::new(),
                            )
                        }
                        Response::Refused {
                            status,
                            message,
                            retry_after,
                        } => (
                            status,
                            json!({"error":{"message":message}}).to_string(),
                            retry_after
                                .map(|n| format!("Retry-After: {n}\r\n"))
                                .unwrap_or_default(),
                        ),
                    };
                    let head = format!(
                        "HTTP/1.1 {status} Reply\r\ncontent-type: text/event-stream\r\ncontent-length: {}\r\nconnection: close\r\n{extra}\r\n",
                        content.len()
                    );
                    io.write_all((head + &content).as_bytes()).await.unwrap();
                });
            }
        });
        Self {
            address,
            requests,
            task,
        }
    }
}

pub type Received = Arc<Mutex<Vec<(String, String)>>>;
pub async fn scripted(answers: Vec<(&'static str, &'static str)>) -> (String, Received) {
    let mut provider = Provider::start().await;
    let address = provider.address.clone();
    let received = Received::default();
    let log = received.clone();
    tokio::spawn(async move {
        for (text, finish_reason) in answers {
            let request = provider.requests.recv().await.unwrap();
            let content = |i: usize| {
                request.body["messages"][i]["content"]
                    .as_str()
                    .unwrap()
                    .to_owned()
            };
            log.lock().unwrap().push((content(0), content(1)));
            request
                .respond
                .send(Response::Answer {
                    text: text.into(),
                    finish_reason: finish_reason.into(),
                })
                .ok()
                .unwrap();
        }
    });
    (address, received)
}
