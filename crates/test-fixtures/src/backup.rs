//! An in-memory cloud backup server following the official server's rules (the server
//! repository's docs/backup.md), without accounts: every request acts for one account.
use std::{
    collections::BTreeMap,
    io,
    sync::{Arc, Mutex},
};

use cloud_api::backup::{Commit, Committed, Current, LIMIT, Status};
use serde_json::json;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};

#[derive(Default)]
pub struct State {
    pub now: i64,
    pub sponsor_until: Option<i64>,
    pub objects: BTreeMap<String, Vec<u8>>,
    pub current: Option<Current>,
    /// The ids of every upload, in order.
    pub uploads: Vec<String>,
}

pub struct Server {
    pub address: String,
    pub state: Arc<Mutex<State>>,
    task: JoinHandle<()>,
}

impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Server {
    #[expect(
        clippy::unwrap_used,
        reason = "test fixture listener failures must panic at their source"
    )]
    pub async fn start(state: State) -> io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let address = format!("http://127.0.0.1:{}", listener.local_addr()?.port());
        let state = Arc::new(Mutex::new(state));
        let shared = state.clone();
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                tokio::spawn(serve(stream, shared.clone()));
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
async fn serve(stream: TcpStream, state: Arc<Mutex<State>>) {
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

fn error(status: u16, code: &str) -> (u16, &'static str, Vec<u8>) {
    (
        status,
        "application/json",
        json!({ "code": code }).to_string().into_bytes(),
    )
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture request failures must panic at their source"
)]
fn respond(
    state: &mut State,
    method: &str,
    path: &str,
    body: Vec<u8>,
) -> (u16, &'static str, Vec<u8>) {
    let ok = |value: serde_json::Value| (200, "application/json", value.to_string().into_bytes());
    let sponsored = state.sponsor_until.is_some_and(|until| until > state.now);
    match (method, path.strip_prefix("/v1/backup")) {
        ("GET", Some("")) => ok(serde_json::to_value(Status {
            current: state.current.as_ref().map(|current| Current {
                device_id: current.device_id.clone(),
                manifest: current.manifest.clone(),
                committed_at: current.committed_at,
                size: current.size,
            }),
            sponsor_until: state.sponsor_until,
        })
        .unwrap()),
        ("GET", Some(object)) => match state.objects.get(object.trim_start_matches("/objects/")) {
            Some(data) => (200, "application/octet-stream", data.clone()),
            None => error(404, "backup_not_found"),
        },
        ("PUT", Some(object)) => {
            if !sponsored {
                return error(403, "sponsor_required");
            }
            let object = object.trim_start_matches("/objects/").to_owned();
            state.uploads.push(object.clone());
            state.objects.entry(object).or_insert(body);
            ok(json!({}))
        }
        ("POST", Some("")) => {
            if !sponsored {
                return error(403, "sponsor_required");
            }
            let commit: Commit = serde_json::from_slice(&body).unwrap();
            let mut referenced = commit.objects;
            referenced.push(commit.manifest.clone());
            let Some(sizes) = referenced
                .iter()
                .map(|id| state.objects.get(id).map(|data| data.len() as u64))
                .collect::<Option<Vec<_>>>()
            else {
                return error(409, "backup_incomplete");
            };
            let size = sizes.iter().sum();
            if size > LIMIT {
                return error(413, "backup_too_large");
            }
            state.now += 1;
            state.objects.retain(|id, _| referenced.contains(id));
            state.current = Some(Current {
                device_id: commit.device_id,
                manifest: commit.manifest,
                committed_at: state.now,
                size,
            });
            ok(serde_json::to_value(Committed {
                committed_at: state.now,
            })
            .unwrap())
        }
        ("DELETE", Some("")) => {
            state.objects.clear();
            state.current = None;
            ok(json!({}))
        }
        _ => error(404, "not_found"),
    }
}
