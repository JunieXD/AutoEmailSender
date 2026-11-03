//! The HTTP side of the in-memory official servers: requests in order on each connection, each
//! answered from the state under its lock.
use std::{
    io,
    sync::{Arc, Mutex},
};

use serde_json::json;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};

/// Status, content type and body.
pub type Reply = (u16, &'static str, Vec<u8>);
/// Answers a request from its method, path with the query and body.
pub type Respond<S> = fn(&mut S, &str, &str, Vec<u8>) -> Reply;

pub struct Server<S> {
    pub address: String,
    pub state: Arc<Mutex<S>>,
    task: JoinHandle<()>,
}

impl<S> Drop for Server<S> {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl<S: Send + 'static> Server<S> {
    #[expect(
        clippy::unwrap_used,
        reason = "test fixture listener failures must panic at their source"
    )]
    pub(crate) async fn serve(state: S, respond: Respond<S>) -> io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let address = format!("http://127.0.0.1:{}", listener.local_addr()?.port());
        let state = Arc::new(Mutex::new(state));
        let shared = state.clone();
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                tokio::spawn(connection(stream, shared.clone(), respond));
            }
        });
        Ok(Self {
            address,
            state,
            task,
        })
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture request failures must panic at their source"
)]
async fn connection<S>(stream: TcpStream, state: Arc<Mutex<S>>, respond: Respond<S>) {
    let mut io = BufReader::new(stream);
    loop {
        let mut line = String::new();
        if io.read_line(&mut line).await.unwrap() == 0 {
            return;
        }
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
        let mut words = line.split(' ');
        let (method, path) = (words.next().unwrap(), words.next().unwrap());
        let (status, content_type, content) =
            respond(&mut state.lock().unwrap(), method, path, body);
        let head = format!(
            "HTTP/1.1 {status} Reply\r\ncontent-type: {content_type}\r\ncontent-length: {}\r\n\r\n",
            content.len()
        );
        io.write_all(&[head.as_bytes(), &content].concat())
            .await
            .unwrap();
    }
}

pub(crate) fn error(status: u16, code: &str) -> Reply {
    (
        status,
        "application/json",
        json!({ "code": code }).to_string().into_bytes(),
    )
}

pub(crate) fn ok(value: serde_json::Value) -> Reply {
    (200, "application/json", value.to_string().into_bytes())
}
