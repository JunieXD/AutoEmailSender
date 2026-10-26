//! Local OpenAI-compatible HTTP fixture with controlled or raw responses.
use serde_json::{Value, json};
use std::{
    io,
    sync::{Arc, Mutex},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::{mpsc, oneshot},
    task::{JoinHandle, JoinSet},
};

pub struct Request {
    pub line: String,
    pub authorization: String,
    pub body: Value,
    pub completed: oneshot::Receiver<()>,
    pub respond: oneshot::Sender<Response>,
}
#[derive(Debug)]
pub enum Response {
    Raw {
        status: u16,
        content: String,
    },
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
    #[expect(
        clippy::unwrap_used,
        reason = "test fixture listener failures must panic at their source"
    )]
    pub async fn start() -> io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let address = format!("http://127.0.0.1:{}/v1", listener.local_addr()?.port());
        let (tx, requests) = mpsc::unbounded_channel();
        let task = tokio::spawn(async move {
            let mut connections = JoinSet::new();
            loop {
                tokio::select! {
                    accepted = listener.accept() => {
                        let (stream, _) = accepted.unwrap();
                        connections.spawn(serve(stream, tx.clone()));
                    },
                    Some(_) = connections.join_next() => {},
                }
            }
        });
        Ok(Self {
            address,
            requests,
            task,
        })
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture request failures must panic at their source"
)]
async fn serve(stream: TcpStream, requests: mpsc::UnboundedSender<Request>) {
    let mut io = BufReader::new(stream);
    let mut line = String::new();
    if io.read_line(&mut line).await.unwrap() == 0 {
        return;
    }
    let mut length = 0;
    let mut authorization = String::new();
    loop {
        let mut header = String::new();
        assert_ne!(
            io.read_line(&mut header).await.unwrap(),
            0,
            "incomplete request headers"
        );
        if header == "\r\n" {
            break;
        }
        if let Some((name, value)) = header.trim_end().split_once(": ") {
            match name.to_ascii_lowercase().as_str() {
                "content-length" => length = value.parse().unwrap(),
                "authorization" => authorization = value.into(),
                _ => {}
            }
        }
    }
    let mut body = vec![0; length];
    io.read_exact(&mut body).await.unwrap();
    let body = if body.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&body).unwrap()
    };
    let (respond, response) = oneshot::channel();
    let (finished, completed) = oneshot::channel();
    if requests
        .send(Request {
            line,
            authorization,
            body,
            respond,
            completed,
        })
        .is_err()
    {
        return;
    }
    let Ok(response) = response.await else {
        return;
    };
    let (status, content, extra) = match response {
        Response::Raw { status, content } => (status, content, String::new()),
        Response::Answer {
            text,
            finish_reason,
        } => {
            let chunk =
                json!({"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]});
            let usage = json!({
                "choices": [],
                "usage": {
                    "prompt_tokens": 2000,
                    "completion_tokens": 400,
                    "prompt_tokens_details": {"cached_tokens": 1500},
                },
            });
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
        "HTTP/1.1 {status} Reply\r\n\
        content-type: text/event-stream\r\n\
        content-length: {}\r\n\
        connection: close\r\n{extra}\r\n",
        content.len()
    );
    // Canceled calls may disconnect; a failed write closes completed without signaling success.
    if io.write_all((head + &content).as_bytes()).await.is_ok() {
        let _ = finished.send(());
    }
}

pub type Received = Arc<Mutex<Vec<(String, String)>>>;
#[expect(
    clippy::unwrap_used,
    reason = "scripted test data and response failures must panic at their source"
)]
pub async fn scripted(
    answers: Vec<(&'static str, &'static str)>,
) -> io::Result<(String, Received)> {
    let mut provider = Provider::start().await?;
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
                .unwrap();
            request.completed.await.unwrap();
        }
    });
    Ok((address, received))
}
