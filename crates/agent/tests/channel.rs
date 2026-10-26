use std::error::Error;
use std::path::{Path, PathBuf};
use std::process::Output;
use std::sync::Arc;

use agent::Credentials;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

type Result<T = ()> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

async fn setting(ctx: &api::Context, enabled: bool) -> Result {
    api::call(ctx, "setAgentAccess", json!({"enabled": enabled}))
        .await
        .map_err(|error| error.message)?;
    Ok(())
}

fn credentials(dir: &Path) -> Result<Credentials> {
    Ok(serde_json::from_slice(&std::fs::read(agent::token_path(
        dir,
    ))?)?)
}

async fn cli(dir: &Path, op: &str) -> Result<Output> {
    let (dir, op): (PathBuf, String) = (dir.to_owned(), op.into());
    Ok(tokio::task::spawn_blocking(move || {
        std::process::Command::new(env!("CARGO_BIN_EXE_autoemail"))
            .env("AUTOEMAIL_DATA_DIR", dir)
            .args(["call", &op])
            .output()
    })
    .await??)
}

#[tokio::test]
async fn authentication_permissions_cli_and_switch_share_the_real_local_channel() -> Result {
    let dir = tempfile::tempdir()?;
    let _lock = api::startup::DataLock::acquire(dir.path())?;
    let (ctx, _) = api::startup::open(dir.path()).await?;
    let ctx = Arc::new(ctx);
    let server = api::agent::start(ctx.clone(), dir.path().to_owned(), |_| {})
        .await
        .map_err(|error| error.message)?;
    api::call(
        &ctx,
        "updateProfile",
        json!({"name":"测试学生", "researchInterests":"人工智能", "rewritePreference":""}),
    )
    .await
    .map_err(|error| error.message)?;
    assert_eq!(
        api::call(&ctx, "getAgentAccess", json!({}))
            .await
            .map_err(|error| error.message)?,
        false
    );
    let closed = cli(dir.path(), "getProfile").await?;
    assert!(!closed.status.success());
    assert!(String::from_utf8(closed.stderr)?.contains("请先打开 Auto Email Sender"));

    setting(&ctx, true).await?;
    let first = credentials(dir.path())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(agent::token_path(dir.path()))?
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }

    let mut wrong = agent::connect(dir.path(), &first).await?;
    wrong.write_all(b"wrong-token\n").await?;
    assert_eq!(wrong.read(&mut [0]).await?, 0);

    // A silent (or Windows read-only) client must release its instance after authentication times out.
    tokio::time::pause();
    #[cfg(unix)]
    let mut silent = agent::connect(dir.path(), &first).await?;
    #[cfg(windows)]
    let mut silent = tokio::net::windows::named_pipe::ClientOptions::new()
        .write(false)
        .open(&first.pipe)?;
    assert_eq!(
        tokio::time::timeout(std::time::Duration::from_secs(10), silent.read(&mut [0])).await??,
        0
    );
    tokio::time::resume();

    let mut stream = BufReader::new(agent::connect(dir.path(), &first).await?);
    stream
        .get_mut()
        .write_all(
            format!(
                "{}\r\n{}\r\n",
                first.token,
                json!({"op":"setAiFactCheck", "input":{"on":false}})
            )
            .as_bytes(),
        )
        .await?;
    let mut line = String::new();
    stream.read_line(&mut line).await?;
    let configured: Value = serde_json::from_str(&line)?;
    assert_eq!(configured, json!({"ok":null}));
    assert!(!profile::ai_fact_check(&ctx.db).await?);

    // These UI-only operations are denied even before their inputs are deserialized.
    for op in ["setAgentAccess", "listAgentRequests", "answerAgentRequest"] {
        send(stream.get_mut(), op, Value::Null).await?;
        let denied = frame(&mut stream).await?;
        assert_eq!(denied["error"]["code"], "invalid");
    }
    assert!(profile::agent_access(&ctx.db).await?);

    let output = cli(dir.path(), "getProfile").await?;
    assert!(output.status.success());
    let output: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(output["ok"]["name"], "测试学生");
    let unknown = cli(dir.path(), "notAnOperation").await?;
    assert!(!unknown.status.success());
    let output: Value = serde_json::from_slice(&unknown.stdout)?;
    assert_eq!(output["error"]["code"], "bad_input");

    setting(&ctx, false).await?;
    assert!(!agent::token_path(dir.path()).exists());
    assert_eq!(stream.read_line(&mut String::new()).await?, 0);
    assert!(agent::connect(dir.path(), &first).await.is_err());
    setting(&ctx, true).await?;
    let second = credentials(dir.path())?;
    assert!(first.token != second.token);
    assert!(profile::agent_access(&ctx.db).await?);

    // Restore the saved enabled flag and crash leftovers without racing an old listener's cleanup.
    setting(&ctx, false).await?;
    profile::set_agent_access(&ctx.db, true).await?;
    drop(server);
    ctx.db.close().await;
    let (reopened, _) = api::startup::open(dir.path()).await?;
    let reopened = Arc::new(reopened);
    #[cfg(unix)]
    std::fs::write(agent::socket_path(dir.path()), b"stale socket")?;
    std::fs::write(agent::token_path(dir.path()), serde_json::to_vec(&second)?)?;
    let _server = api::agent::start(reopened.clone(), dir.path().to_owned(), |_| {})
        .await
        .map_err(|error| error.message)?;
    let third = credentials(dir.path())?;
    assert!(second.token != third.token);
    let output = cli(dir.path(), "getProfile").await?;
    assert!(output.status.success());
    setting(&reopened, false).await?;
    reopened.db.close().await;
    Ok(())
}

async fn frame(reader: &mut (impl tokio::io::AsyncBufRead + Unpin)) -> Result<Value> {
    let mut line = String::new();
    assert_ne!(reader.read_line(&mut line).await?, 0);
    Ok(serde_json::from_str(&line)?)
}

async fn send(writer: &mut (impl tokio::io::AsyncWrite + Unpin), op: &str, input: Value) -> Result {
    writer
        .write_all(format!("{}\n", json!({"op":op, "input":input})).as_bytes())
        .await?;
    Ok(())
}

async fn pending(ctx: &api::Context) -> Result<Vec<Value>> {
    Ok(serde_json::from_value(
        api::call(ctx, "listAgentRequests", json!({}))
            .await
            .map_err(|e| e.message)?,
    )?)
}

async fn answer(ctx: &api::Context, id: &Value, allow: bool) -> Result {
    api::call(ctx, "answerAgentRequest", json!({"id":id, "allow":allow}))
        .await
        .map_err(|e| e.message)?;
    Ok(())
}

#[tokio::test]
async fn confirmations_follow_the_real_channel_and_cli_until_answer_expiry_or_disconnect() -> Result
{
    use api::agent::Event;
    let dir = tempfile::tempdir()?;
    let _lock = api::startup::DataLock::acquire(dir.path())?;
    let (ctx, _) = api::startup::open(dir.path()).await?;
    let ctx = Arc::new(ctx);
    let (events, mut changed) = tokio::sync::mpsc::unbounded_channel();
    let _server = api::agent::start(ctx.clone(), dir.path().to_owned(), move |event| {
        let _ = events.send(event);
    })
    .await
    .map_err(|e| e.message)?;
    setting(&ctx, true).await?;
    let token = credentials(dir.path())?;
    let mut stream = BufReader::new(agent::connect(dir.path(), &token).await?);
    stream
        .get_mut()
        .write_all(format!("{}\n", token.token).as_bytes())
        .await?;
    let tag = api::call(
        &ctx,
        "createTag",
        json!({
            "name":"准备联系", "color":"#112233", "mentorIds":[]
        }),
    )
    .await
    .map_err(|e| e.message)?;
    let tag_id = tag["id"].clone();

    // A queued request is visible before the intermediate frame, and approval runs the real handler.
    send(stream.get_mut(), "deleteTag", json!({"id":tag_id})).await?;
    let waiting = frame(&mut stream).await?;
    let requests = pending(&ctx).await?;
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["op"], "deleteTag");
    assert_eq!(requests[0]["description"], waiting["waiting"]);
    assert_eq!(requests[0]["description"]["targets"], json!(["准备联系"]));
    assert_eq!(requests[0]["description"]["more"], 0);
    assert!(
        requests[0]["description"]["action"]
            .as_str()
            .is_some_and(|s| !s.is_empty())
    );
    assert!(requests[0]["createdAt"].as_i64().is_some());
    let first_id = requests[0]["id"].clone();
    answer(&ctx, &first_id, true).await?;
    assert_eq!(frame(&mut stream).await?, json!({"ok":null}));
    assert_eq!(
        api::call(&ctx, "listTags", json!({}))
            .await
            .map_err(|e| e.message)?,
        json!([])
    );
    assert!(pending(&ctx).await?.is_empty());
    assert_eq!(
        answer(&ctx, &first_id, true).await.unwrap_err().to_string(),
        "这个请求已经失效"
    );
    let mut called = false;
    while let Ok(event) = changed.try_recv() {
        called |= matches!(event, Event::Called);
    }
    assert!(called);

    // A missing object is still explained and queued; normal validation happens after approval.
    send(
        stream.get_mut(),
        "rescheduleEmail",
        json!({"id":-1, "plannedAt":0}),
    )
    .await?;
    frame(&mut stream).await?;
    let requests = pending(&ctx).await?;
    answer(&ctx, &requests[0]["id"], true).await?;
    let failed = frame(&mut stream).await?;
    assert_eq!(failed["error"]["code"], "invalid");
    assert!(pending(&ctx).await?.is_empty());

    let retained = api::call(
        &ctx,
        "createTag",
        json!({
            "name":"保留", "color":"#112233", "mentorIds":[]
        }),
    )
    .await
    .map_err(|e| e.message)?;
    // Separate connections queue in registration order. Denial leaves the other request pending.
    let mut second = BufReader::new(agent::connect(dir.path(), &token).await?);
    second
        .get_mut()
        .write_all(format!("{}\n", token.token).as_bytes())
        .await?;
    send(stream.get_mut(), "deleteTag", json!({"id":retained["id"]})).await?;
    frame(&mut stream).await?;
    send(second.get_mut(), "deleteTag", json!({"id":retained["id"]})).await?;
    frame(&mut second).await?;
    let requests = pending(&ctx).await?;
    assert_eq!(requests.len(), 2);
    assert!(requests[0]["id"].as_u64() < requests[1]["id"].as_u64());
    answer(&ctx, &requests[0]["id"], false).await?;
    assert_eq!(
        frame(&mut stream).await?["error"]["message"],
        "你在应用里拒绝了这个操作"
    );
    assert_eq!(pending(&ctx).await?.len(), 1);
    assert_eq!(pending(&ctx).await?[0]["id"], requests[1]["id"]);

    assert_eq!(
        api::call(&ctx, "listTags", json!({}))
            .await
            .map_err(|e| e.message)?,
        json!([retained])
    );

    // The timer is driven explicitly, without waiting five minutes or sleeping a fixed duration.
    tokio::time::pause();
    tokio::time::advance(std::time::Duration::from_secs(301)).await;
    let expired = frame(&mut second).await?;
    tokio::time::resume();
    assert_eq!(expired["error"]["message"], "你没有在应用里确认");
    assert!(pending(&ctx).await?.is_empty());
    assert!(answer(&ctx, &requests[1]["id"], true).await.is_err());

    send(stream.get_mut(), "deleteTag", json!({"id":-1})).await?;
    frame(&mut stream).await?;
    let disconnected_id = pending(&ctx).await?[0]["id"].clone();
    while changed.try_recv().is_ok() {}
    drop(stream);
    while !matches!(changed.recv().await, Some(Event::RequestsChanged)) {}
    assert!(pending(&ctx).await?.is_empty());
    assert!(answer(&ctx, &disconnected_id, true).await.is_err());

    // Closing the switch waits for cancellation and request removal before returning.
    send(second.get_mut(), "deleteTag", json!({"id":-1})).await?;
    frame(&mut second).await?;
    assert_eq!(pending(&ctx).await?.len(), 1);
    setting(&ctx, false).await?;
    assert!(pending(&ctx).await?.is_empty());
    assert_eq!(second.read_line(&mut String::new()).await?, 0);
    setting(&ctx, true).await?;

    // The actual executable prints progress on stderr and exactly one final JSON line on stdout.
    while changed.try_recv().is_ok() {}
    let cli_dir = dir.path().to_owned();
    let output = tokio::task::spawn_blocking(move || {
        std::process::Command::new(env!("CARGO_BIN_EXE_autoemail"))
            .env("AUTOEMAIL_DATA_DIR", cli_dir)
            .args(["call", "deleteTag", "{\"id\":-1}"])
            .output()
    });
    while !matches!(changed.recv().await, Some(Event::Requested(_))) {}
    let requests = pending(&ctx).await?;
    answer(&ctx, &requests[0]["id"], false).await?;
    let output = output.await??;
    assert!(!output.status.success());
    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout.lines().count(), 1);
    assert_eq!(
        serde_json::from_str::<Value>(&stdout)?["error"]["message"],
        "你在应用里拒绝了这个操作"
    );
    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.lines().count(), 1);
    assert!(stderr.starts_with("等你在 Auto Email Sender 里确认："));
    setting(&ctx, false).await?;
    ctx.db.close().await;
    Ok(())
}
