//! Token authentication and the lifetime of the desktop's local Agent channel.

use std::future::pending;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde::Deserialize;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc, oneshot};
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::Instant;
use ts_rs::TS;

use crate::{
    CallError, Context, FirstDraftEdit, ImportMentors, MailboxEdit, MentorAddress, MentorEdit,
    Nothing, ReplyDraftEdit,
};

// Four slots let a few tools connect together while keeping untrusted pipe resources small.
const MAX_AUTHENTICATING: usize = 4;

/// These operations require the user's confirmation in the app before an Agent can run them.
const AGENT_CONFIRMATION_REQUIRED: &[&str] = &[
    "approveTaskEmails",
    "approveFirstEmail",
    "approveFollowUp",
    "approveReply",
    "sendEmailNow",
    "resendEmail",
    "retryEmail",
    "resumeTask",
    "resumeMailbox",
    "changeTaskSchedule",
    "rescheduleEmail",
    "deleteMentors",
    "deleteTask",
    "deleteTemplate",
    "deleteMailbox",
    "deleteMaterial",
    "deleteCrawl",
    "deleteTag",
    "deleteTodo",
    "deleteModelConfig",
    "mergeMentors",
    "agreeModel",
];

// This check precedes the write; concurrent UI or background changes order before or after it.
pub(crate) async fn requires_confirmation(
    ctx: &Context,
    op: &str,
    input: &serde_json::Value,
) -> Result<bool, CallError> {
    if AGENT_CONFIRMATION_REQUIRED.contains(&op) {
        return Ok(true);
    }
    match op {
        "updateMailbox" => {
            let input: MailboxEdit =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            let at = crate::now();
            Ok(sqlx::query_scalar!(
                r#"SELECT EXISTS (
                    SELECT 1 FROM mailbox WHERE id = ? AND paused_until > ?
                ) AS "paused!: bool""#,
                input.id,
                at
            )
            .fetch_one(&ctx.db)
            .await?)
        }
        "saveFirstDraft" => {
            let input: FirstDraftEdit =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            if !input.subject.trim().is_empty() || !input.body_md.trim().is_empty() {
                return Ok(false);
            }
            Ok(sqlx::query_scalar!(
                r#"SELECT EXISTS (
                    SELECT 1 FROM email
                    WHERE mentor_id = ? AND kind = 'first' AND task_id IS NULL AND state = 'review'
                ) AS "present!: bool""#,
                input.mentor_id
            )
            .fetch_one(&ctx.db)
            .await?)
        }
        "saveReplyDraft" => {
            let input: ReplyDraftEdit =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            if !input.body_md.trim().is_empty() || !input.attachments.is_empty() {
                return Ok(false);
            }
            Ok(sqlx::query_scalar!(
                r#"SELECT EXISTS (
                    SELECT 1 FROM email
                    WHERE mentor_id = ? AND kind = 'reply' AND state = 'review'
                ) AS "present!: bool""#,
                input.mentor_id
            )
            .fetch_one(&ctx.db)
            .await?)
        }
        "updateMentor" => {
            let input: MentorEdit =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            let fields = input.form.check().map_err(CallError::invalid)?;
            changes_approved_recipient(ctx, input.id, fields.emails.first().map(String::as_str))
                .await
        }
        "addMentorEmail" => {
            let input: MentorAddress =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            let address = input.address.trim();
            if !mentor::is_email(address) {
                return Err(CallError::invalid(format!("邮箱地址“{address}”格式不正确")));
            }
            let addresses = sqlx::query_scalar!(
                "SELECT address FROM mentor_email WHERE mentor_id = ? ORDER BY position",
                input.id
            )
            .fetch_all(&ctx.db)
            .await?;
            if addresses
                .iter()
                .any(|old| old.eq_ignore_ascii_case(address))
                || mentor::place(&addresses, address) != 0
            {
                return Ok(false);
            }
            changes_approved_recipient(ctx, input.id, Some(address)).await
        }
        "importMentors" => {
            let input: ImportMentors =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            let sheets = crate::read_import(&input.file).await?;
            let mut tx = ctx.db.begin().await?;
            let (_, changes) =
                mentor::import::calculate(&mut tx, &sheets, &input.file.setup, &input.decisions)
                    .await?;
            approved_changes(&mut tx, &changes).await
        }
        "acceptCandidates" => {
            let input: crawl::review::ReviewInput =
                serde_json::from_value(input.clone()).map_err(CallError::bad_input)?;
            let mut tx = ctx.db.begin().await?;
            let plan = crawl::review::plan(&mut tx, &input).await?;
            approved_changes(&mut tx, &plan.changes).await
        }
        _ => Ok(false),
    }
}

async fn changes_approved_recipient(
    ctx: &Context,
    mentor: i64,
    address: Option<&str>,
) -> Result<bool, CallError> {
    Ok(sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1 FROM email
            WHERE mentor_id = ?1 AND state = 'approved'
            AND (SELECT address FROM mentor_email
                WHERE mentor_id = ?1 ORDER BY position LIMIT 1) IS NOT ?2 COLLATE NOCASE
        ) AS "changed!: bool""#,
        mentor,
        address
    )
    .fetch_one(&ctx.db)
    .await?)
}

async fn approved_changes(
    conn: &mut sqlx::SqliteConnection,
    changes: &[mentor::intake::Change],
) -> Result<bool, CallError> {
    let mentors = sqlx::types::Json(
        changes
            .iter()
            .filter_map(mentor::intake::Change::changed_recipient)
            .collect::<Vec<_>>(),
    );
    Ok(sqlx::query_scalar!(
        r#"SELECT EXISTS (
            SELECT 1 FROM email
            WHERE state = 'approved' AND mentor_id IN (SELECT value FROM json_each(?))
        ) AS "present!: bool""#,
        mentors
    )
    .fetch_one(conn)
    .await?)
}

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
                let result = change_access(&ctx, &dir, &mut running, &mut clients, change.enabled)
                    .await;
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
                        clients.spawn(async move { session(&ctx, stream, &token, permit).await });
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
    enabled: bool,
) -> Result<(), CallError> {
    if enabled {
        if running.is_none() {
            let started = listen(dir)?;
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

async fn session(
    ctx: &Context,
    stream: ::agent::Incoming,
    token: &str,
    permit: OwnedSemaphorePermit,
) -> io::Result<()> {
    let mut stream = BufReader::new(stream);
    let authentication = tokio::time::timeout(Duration::from_secs(5), line(&mut stream, 128)).await;
    match authentication {
        Ok(Ok(Some(line))) if line == token.as_bytes() => {}
        // Each Windows pipe instance has its own client. Never write before authentication,
        // including to a client that opened its instance read-only under the default DACL.
        Ok(Ok(_)) | Err(_) => return Ok(()),
        Ok(Err(error)) => return Err(error),
    }
    drop(permit);
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
