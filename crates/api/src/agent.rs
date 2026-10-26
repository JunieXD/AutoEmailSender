//! Token authentication and the lifetime of the desktop's local Agent channel.

use std::future::pending;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde::Deserialize;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, oneshot};
use tokio::task::{JoinHandle, JoinSet};
use ts_rs::TS;

use crate::{CallError, Context, Nothing};

#[derive(Debug, Deserialize, TS)]
pub struct AgentAccess {
    pub enabled: bool,
}

#[derive(Default)]
pub struct Access {
    commands: OnceLock<mpsc::Sender<Change>>,
}

struct Change {
    enabled: bool,
    reply: oneshot::Sender<Result<(), CallError>>,
}

pub async fn get_agent_access(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    if ctx
        .agent_access
        .commands
        .get()
        .is_some_and(mpsc::Sender::is_closed)
    {
        return Err(CallError::internal("agent listener stopped"));
    }
    Ok(profile::agent_access(&ctx.db).await?)
}

pub async fn set_agent_access(ctx: &Context, input: AgentAccess) -> Result<(), CallError> {
    let commands = ctx
        .agent_access
        .commands
        .get()
        .ok_or_else(|| CallError::invalid("请在桌面应用中设置 Agent 连接。".into()))?;
    let (reply, result) = oneshot::channel();
    commands
        .send(Change {
            enabled: input.enabled,
            reply,
        })
        .await
        .map_err(CallError::internal)?;
    result.await.map_err(CallError::internal)?
}

/// Kept by the desktop host, separately from the context owned by the serving task.
pub struct Server(JoinHandle<()>);

impl Drop for Server {
    fn drop(&mut self) {
        self.0.abort();
    }
}

pub async fn start(ctx: Arc<Context>, dir: PathBuf) -> Result<Server, CallError> {
    let running = if profile::agent_access(&ctx.db).await? {
        Some(listen(&dir).map_err(CallError::internal)?)
    } else {
        Files(dir.clone()).remove().map_err(CallError::internal)?;
        None
    };
    let (commands, changes) = mpsc::channel(1);
    ctx.agent_access
        .commands
        .set(commands)
        .map_err(|_| CallError::internal("agent host already started"))?;
    Ok(Server(tokio::spawn(async move {
        if let Err(error) = serve(ctx, dir, running, changes).await {
            tracing::error!(code = ?error.code, "agent listener stopped");
        }
    })))
}

struct Running {
    listener: ::agent::Listener,
    token: String,
    files: Files,
}

impl Running {
    fn close(self) -> io::Result<()> {
        let Self {
            listener, files, ..
        } = self;
        drop(listener);
        files.remove()
    }
}

struct Files(PathBuf);

impl Files {
    fn remove(&self) -> io::Result<()> {
        let token = ::agent::remove_if_present(&::agent::token_path(&self.0));
        #[cfg(unix)]
        let socket = ::agent::remove_if_present(&::agent::socket_path(&self.0));
        #[cfg(windows)]
        let socket = Ok(());
        token.and(socket)
    }
}

impl Drop for Files {
    fn drop(&mut self) {
        if let Err(error) = self.remove() {
            tracing::warn!("remove agent files: {error}");
        }
    }
}

fn listen(dir: &Path) -> io::Result<Running> {
    let files = Files(dir.to_owned());
    let credentials = ::agent::Credentials::generate();
    let listener = ::agent::listen(dir, &credentials)?;
    // NamedTempFile creates mode 0600 on Unix; on Windows it inherits the user's data ACL.
    let mut file = tempfile::NamedTempFile::new_in(dir)?;
    file.write_all(serde_json::to_string(&credentials)?.as_bytes())?;
    file.persist(::agent::token_path(dir))?;
    Ok(Running {
        listener,
        token: credentials.token,
        files,
    })
}

async fn serve(
    ctx: Arc<Context>,
    dir: PathBuf,
    mut running: Option<Running>,
    mut changes: mpsc::Receiver<Change>,
) -> Result<(), CallError> {
    let mut clients = JoinSet::new();
    loop {
        let accept = async {
            match &mut running {
                Some(running) => ::agent::accept(&mut running.listener)
                    .await
                    .map(|stream| (stream, running.token.clone())),
                None => pending().await,
            }
        };
        tokio::select! {
            biased;
            change = changes.recv() => {
                let Some(change) = change else { return Ok(()) };
                let result = change_access(&ctx, &dir, &mut running, &mut clients, change.enabled).await;
                // The caller may have closed its window while the setting was being saved.
                let _ = change.reply.send(result);
            }
            result = clients.join_next(), if !clients.is_empty() => {
                match result {
                    Some(Ok(Ok(()))) => {}
                    Some(Ok(Err(error))) => {
                        tracing::warn!(kind = ?error.kind(), "agent connection ended");
                    }
                    Some(Err(error)) => return Err(CallError::internal(error)),
                    None => {}
                }
            }
            stream = accept => {
                let (stream, token) = stream.map_err(CallError::internal)?;
                let ctx = ctx.clone();
                clients.spawn(async move { session(&ctx, stream, &token).await });
            }
        }
    }
}

async fn change_access(
    ctx: &Context,
    dir: &Path,
    running: &mut Option<Running>,
    clients: &mut JoinSet<io::Result<()>>,
    enabled: bool,
) -> Result<(), CallError> {
    if enabled {
        if running.is_none() {
            let started = listen(dir).map_err(CallError::internal)?;
            profile::set_agent_access(&ctx.db, true).await?;
            *running = Some(started);
        }
    } else {
        profile::set_agent_access(&ctx.db, false).await?;
        clients.abort_all();
        while let Some(result) = clients.join_next().await {
            match result {
                Err(error) if !error.is_cancelled() => {
                    tracing::error!("agent connection task failed: {error}");
                }
                Ok(Err(error)) => {
                    tracing::warn!(kind = ?error.kind(), "agent connection ended");
                }
                _ => {}
            }
        }
        if let Some(running) = running.take() {
            running.close().map_err(CallError::internal)?;
        } else {
            Files(dir.to_owned())
                .remove()
                .map_err(CallError::internal)?;
        }
    }
    Ok(())
}

#[derive(Deserialize)]
struct Request {
    op: String,
    input: serde_json::Value,
}

async fn session(ctx: &Context, stream: ::agent::Incoming, token: &str) -> io::Result<()> {
    let mut stream = BufReader::new(stream);
    let authentication = tokio::time::timeout(Duration::from_secs(5), line(&mut stream, 128)).await;
    match authentication {
        Ok(Ok(Some(line))) if line == token.as_bytes() => {}
        // Each Windows pipe instance has its own client. Never write before authentication,
        // including to a client that opened its instance read-only under the default DACL.
        Ok(Ok(_)) | Err(_) => return Ok(()),
        Ok(Err(error)) => return Err(error),
    }
    // Matches the preview host's limit, including a base64-encoded 20 MiB material upload.
    while let Some(line) = line(&mut stream, 28 * 1024 * 1024).await? {
        let result = match serde_json::from_slice::<Request>(&line) {
            Ok(request) => crate::call_agent(ctx, &request.op, request.input).await,
            Err(error) => Err(CallError::bad_input(error)),
        };
        let response = match result {
            Ok(output) => serde_json::json!({ "ok": output }),
            Err(error) => serde_json::json!({ "error": error }),
        };
        stream
            .get_mut()
            .write_all(format!("{response}\n").as_bytes())
            .await?;
    }
    Ok(())
}

async fn line(reader: &mut (impl AsyncBufRead + Unpin), limit: u64) -> io::Result<Option<Vec<u8>>> {
    let mut bytes = Vec::new();
    let count = reader.take(limit + 1).read_until(b'\n', &mut bytes).await?;
    if count == 0 {
        return Ok(None);
    }
    if count as u64 > limit || bytes.pop() != Some(b'\n') {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid agent frame",
        ));
    }
    if bytes.last() == Some(&b'\r') {
        bytes.pop();
    }
    Ok(Some(bytes))
}
