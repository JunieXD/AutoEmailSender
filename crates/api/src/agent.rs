//! Token authentication and the lifetime of the desktop's local Agent channel.

use std::future::pending;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde::Deserialize;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc, oneshot, watch};
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::Instant;
use ts_rs::TS;

use crate::{CallError, Context, Nothing};

// Four slots let a few tools connect together while keeping untrusted pipe resources small.
const MAX_AUTHENTICATING: usize = 4;

mod confirmation;
pub use confirmation::Description;
pub(crate) use confirmation::confirmation;

const CONFIRM_FOR: Duration = Duration::from_secs(5 * 60);

pub enum Event {
    Requested(Description),
    RequestsChanged,
    Called,
}

#[derive(Clone, serde::Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct AgentRequest {
    pub id: u64,
    pub op: String,
    pub description: Description,
    pub created_at: i64,
}

#[derive(Deserialize, TS)]
pub struct AgentAnswer {
    pub id: u64,
    pub allow: bool,
}

#[derive(Default)]
struct Requests {
    next_id: u64,
    pending: std::collections::VecDeque<Pending>,
}

struct Pending {
    request: AgentRequest,
    expires: Instant,
    answer: oneshot::Sender<bool>,
}

struct Confirmation<'a> {
    access: &'a Access,
    id: u64,
    decision: &'static str,
}

impl Drop for Confirmation<'_> {
    fn drop(&mut self) {
        let removed = {
            let mut requests = self.access.requests();
            requests
                .pending
                .iter()
                .position(|p| p.request.id == self.id)
                .and_then(|index| requests.pending.remove(index))
        };
        if let Some(pending) = removed {
            tracing::info!(
                op = pending.request.op,
                decision = self.decision,
                "agent confirmation"
            );
            self.access.emit(Event::RequestsChanged);
        }
    }
}

impl Access {
    fn requests(&self) -> std::sync::MutexGuard<'_, Requests> {
        self.requests.lock().unwrap_or_else(|_| {
            unreachable!("agent request updates cannot panic while holding the lock")
        })
    }

    pub(crate) fn emit(&self, event: Event) {
        if let Some(emit) = self.events.get() {
            emit(event);
        }
    }

    pub(crate) async fn confirm(
        &self,
        op: &str,
        description: Description,
        waiting: impl FnOnce(&Description),
        cancelled: impl std::future::Future<Output = ()>,
    ) -> Result<(), CallError> {
        let (answer, result) = oneshot::channel();
        let expires = Instant::now() + CONFIRM_FOR;
        let id = {
            let mut requests = self.requests();
            requests.next_id += 1;
            let id = requests.next_id;
            requests.pending.push_back(Pending {
                request: AgentRequest {
                    id,
                    op: op.into(),
                    description: description.clone(),
                    created_at: crate::now(),
                },
                expires,
                answer,
            });
            id
        };
        let mut confirmation = Confirmation {
            access: self,
            id,
            decision: "cancelled",
        };
        self.emit(Event::Requested(description.clone()));
        waiting(&description);
        let answer = tokio::select! {
            // A recorded answer owns the operation even if the connection has just closed.
            biased;
            answer = tokio::time::timeout_at(expires, result) => answer,
            () = cancelled => return Err(CallError::invalid("这个请求已经失效".into())),
        };
        match answer {
            Ok(Ok(true)) => Ok(()),
            Ok(Ok(false)) => Err(CallError::invalid("你在应用里拒绝了这个操作".into())),
            Ok(Err(error)) => Err(CallError::internal(error)),
            Err(_) => {
                confirmation.decision = "timeout";
                Err(CallError::invalid("你没有在应用里确认".into()))
            }
        }
    }
}

pub async fn list_agent_requests(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<AgentRequest>, CallError> {
    Ok(ctx
        .agent_access
        .requests()
        .pending
        .iter()
        .filter(|p| p.expires > Instant::now())
        .map(|p| p.request.clone())
        .collect())
}

pub async fn answer_agent_request(ctx: &Context, input: AgentAnswer) -> Result<(), CallError> {
    let pending = {
        let mut requests = ctx.agent_access.requests();
        let index = requests
            .pending
            .iter()
            .position(|p| p.request.id == input.id && p.expires > Instant::now())
            .ok_or_else(|| CallError::invalid("这个请求已经失效".into()))?;
        requests
            .pending
            .remove(index)
            .unwrap_or_else(|| unreachable!("the index was just found"))
    };
    let answered = pending.answer.send(input.allow);
    tracing::info!(
        op = pending.request.op,
        decision = if answered.is_err() {
            "cancelled"
        } else if input.allow {
            "allowed"
        } else {
            "denied"
        },
        "agent confirmation"
    );
    ctx.agent_access.emit(Event::RequestsChanged);
    answered.map_err(|_| CallError::invalid("这个请求已经失效".into()))
}

#[derive(Debug, Deserialize, TS)]
pub struct AgentAccess {
    pub enabled: bool,
}

#[derive(Default)]
pub struct Access {
    commands: OnceLock<mpsc::Sender<Change>>,
    requests: std::sync::Mutex<Requests>,
    events: OnceLock<Box<dyn Fn(Event) + Send + Sync>>,
}

struct Change {
    enabled: bool,
    reply: oneshot::Sender<Result<(), CallError>>,
}

pub async fn get_agent_access(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
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

pub async fn start(
    ctx: Arc<Context>,
    dir: PathBuf,
    events: impl Fn(Event) + Send + Sync + 'static,
) -> Result<Server, CallError> {
    ctx.agent_access
        .events
        .set(Box::new(events))
        .map_err(|_| CallError::internal("agent host already started"))?;
    let running = if profile::agent_access(&ctx.db).await? {
        Some(listen(&dir)?)
    } else {
        Files(dir.clone()).remove().map_err(CallError::internal)?;
        None
    };
    let (commands, changes) = mpsc::channel(1);
    ctx.agent_access
        .commands
        .set(commands)
        .map_err(|_| CallError::internal("agent host already started"))?;
    Ok(Server(tokio::spawn(serve(ctx, dir, running, changes))))
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

fn listen(dir: &Path) -> Result<Running, CallError> {
    let files = Files(dir.to_owned());
    let credentials = ::agent::Credentials::generate();
    let listener = ::agent::listen(dir, &credentials).map_err(|error| {
        if error.kind() == io::ErrorKind::InvalidInput {
            CallError::invalid(format!("Agent 连接无法开启：{error}"))
        } else {
            CallError::internal(error)
        }
    })?;
    // NamedTempFile creates mode 0600 on Unix; on Windows it inherits the user's data ACL.
    let mut file =
        tempfile::NamedTempFile::new_in(::agent::directory(dir)).map_err(CallError::internal)?;
    file.write_all(
        serde_json::to_string(&credentials)
            .map_err(CallError::internal)?
            .as_bytes(),
    )
    .map_err(CallError::internal)?;
    file.persist(::agent::token_path(dir))
        .map_err(CallError::internal)?;
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
) {
    let mut clients = JoinSet::new();
    let (shutdown, _) = watch::channel(false);
    let authenticating = Arc::new(Semaphore::new(MAX_AUTHENTICATING));
    let mut accept_after = Instant::now();
    loop {
        let accept = async {
            match &mut running {
                Some(running) => {
                    tokio::time::sleep_until(accept_after).await;
                    let permit = authenticating
                        .clone()
                        .acquire_owned()
                        .await
                        .map_err(io::Error::other)?;
                    let stream = ::agent::accept(&mut running.listener).await?;
                    Ok::<_, io::Error>((stream, running.token.clone(), permit))
                }
                None => pending().await,
            }
        };
        tokio::select! {
            biased;
            change = changes.recv() => {
                let Some(change) = change else { return };
                let result = change_access(
                    &ctx,
                    &dir,
                    &mut running,
                    &mut clients,
                    &shutdown,
                    change.enabled,
                ).await;
                if result.is_ok() {
                    accept_after = Instant::now();
                }
                // The caller may have closed its window while the setting was being saved.
                let _ = change.reply.send(result);
            }
            result = clients.join_next(), if !clients.is_empty() => {
                match result {
                    Some(Ok(Ok(()))) => {}
                    Some(Ok(Err(error))) => {
                        tracing::warn!(kind = ?error.kind(), "agent connection ended");
                    }
                    Some(Err(error)) => {
                        tracing::error!("agent connection task failed: {error}");
                    }
                    None => {}
                }
            }
            stream = accept => {
                match stream {
                    Ok((stream, token, permit)) => {
                        let ctx = ctx.clone();
                        let shutdown = shutdown.subscribe();
                        clients.spawn(async move {
                            session(&ctx, stream, &token, permit, shutdown).await
                        });
                    }
                    Err(error) if matches!(error.kind(),
                        io::ErrorKind::ConnectionRefused | io::ErrorKind::ConnectionAborted
                        | io::ErrorKind::ConnectionReset) => {}
                    Err(error) => {
                        tracing::error!("accept agent connection: {error}");
                        // As in axum's accept loop, resource exhaustion may recover on its own.
                        accept_after = Instant::now() + Duration::from_secs(1);
                    }
                }
            }
        }
    }
}

async fn change_access(
    ctx: &Context,
    dir: &Path,
    running: &mut Option<Running>,
    clients: &mut JoinSet<io::Result<()>>,
    shutdown: &watch::Sender<bool>,
    enabled: bool,
) -> Result<(), CallError> {
    if enabled {
        if running.is_none() {
            let started = listen(dir)?;
            profile::set_agent_access(&ctx.db, true).await?;
            shutdown.send_replace(false);
            *running = Some(started);
        }
    } else {
        profile::set_agent_access(&ctx.db, false).await?;
        shutdown.send_replace(true);
        while let Some(result) = clients.join_next().await {
            match result {
                Err(error) => {
                    tracing::error!("agent connection task failed: {error}");
                }
                Ok(Err(error)) => {
                    tracing::warn!(kind = ?error.kind(), "agent connection ended");
                }
                Ok(Ok(())) => {}
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

async fn session(
    ctx: &Context,
    stream: ::agent::Incoming,
    token: &str,
    permit: OwnedSemaphorePermit,
    mut shutdown: watch::Receiver<bool>,
) -> io::Result<()> {
    let (reader, mut writer) = tokio::io::split(stream);
    let mut reader = BufReader::new(reader);
    let authentication = tokio::time::timeout(Duration::from_secs(5), line(&mut reader, 128));
    let authentication = tokio::select! {
        biased;
        () = wait_closed(&mut shutdown) => return Ok(()),
        authentication = authentication => authentication,
    };
    match authentication {
        Ok(Ok(Some(line))) if line == token.as_bytes() => {}
        // Each Windows pipe instance has its own client. Never write before authentication,
        // including to a client that opened its instance read-only under the default DACL.
        Ok(Ok(_)) | Err(_) => return Ok(()),
        Ok(Err(error)) => return Err(error),
    }
    drop(permit);
    let (frames, mut requests) = mpsc::channel(1);
    let (closed, mut cancelled) = watch::channel(false);
    let mut reader_closed = closed.subscribe();
    let reading = async {
        let result = async {
            loop {
                // Includes a base64-encoded 20 MiB material upload, like the preview host.
                let frame = tokio::select! {
                    biased;
                    () = wait_closed(&mut shutdown) => return Ok(()),
                    () = wait_closed(&mut reader_closed) => return Ok(()),
                    frame = line(&mut reader, 28 * 1024 * 1024) => frame?,
                };
                let Some(frame) = frame else { return Ok(()) };
                // Bound pipelining without blocking detection of a disconnected Agent.
                frames.try_send(frame).map_err(|error| match error {
                    mpsc::error::TrySendError::Full(_) => {
                        io::Error::new(io::ErrorKind::InvalidData, "too many pending agent frames")
                    }
                    mpsc::error::TrySendError::Closed(_) => {
                        io::Error::new(io::ErrorKind::BrokenPipe, "agent dispatcher closed")
                    }
                })?;
            }
        }
        .await;
        drop(frames);
        closed.send_replace(true);
        result
    };
    let calling = async {
        while let Some(frame) = requests.recv().await {
            if *closed.borrow() {
                break;
            }
            let (waiting, mut progress) = oneshot::channel();
            let call = async {
                match serde_json::from_slice::<Request>(&frame) {
                    Ok(request) => {
                        crate::call_agent(
                            ctx,
                            &request.op,
                            request.input,
                            |description| {
                                // The transport may already have been closed.
                                let _ = waiting.send(description.clone());
                            },
                            wait_closed(&mut cancelled),
                        )
                        .await
                    }
                    Err(error) => Err(CallError::bad_input(error)),
                }
            };
            tokio::pin!(call);
            // A recorded answer may finish in the same poll as registration. The intermediate
            // frame must precede the final result, and a write failure must not drop execution.
            let result = tokio::select! {
                result = &mut call => {
                    if let Ok(description) = progress.await {
                        write_frame(
                            &mut writer,
                            &closed,
                            serde_json::json!({ "waiting": description }),
                        ).await;
                    }
                    result
                }
                description = &mut progress => {
                    if let Ok(description) = description {
                        write_frame(
                            &mut writer,
                            &closed,
                            serde_json::json!({ "waiting": description }),
                        ).await;
                    }
                    call.await
                }
            };
            let response = match result {
                Ok(output) => serde_json::json!({ "ok": output }),
                Err(error) => serde_json::json!({ "error": error }),
            };
            write_frame(&mut writer, &closed, response).await;
        }
        closed.send_replace(true);
    };
    // Reading can cancel preparation and unanswered requests, never an allowed execution.
    let (result, ()) = tokio::join!(reading, calling);
    result
}

async fn wait_closed(closed: &mut watch::Receiver<bool>) {
    // Dropping the host also invalidates a connection's unfinished confirmation.
    let _ = closed.wait_for(|closed| *closed).await;
}

async fn write_frame(
    writer: &mut (impl tokio::io::AsyncWrite + Unpin),
    closed: &watch::Sender<bool>,
    frame: serde_json::Value,
) {
    let mut cancelled = closed.subscribe();
    let result = tokio::select! {
        biased;
        () = wait_closed(&mut cancelled) => Err(io::Error::from(io::ErrorKind::NotConnected)),
        result = async { writer.write_all(format!("{frame}\n").as_bytes()).await } => result,
    };
    if let Err(error) = result {
        tracing::warn!(
            kind = ?error.kind(),
            "agent response discarded"
        );
        closed.send_replace(true);
    }
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
