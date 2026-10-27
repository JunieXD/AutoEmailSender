use std::error::Error;
use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use api::agent::Event;
use serde_json::{Value, json};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt, BufReader};

type Result<T = ()> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

async fn access(ctx: &api::Context, enabled: bool) -> Result {
    api::call(ctx, "setAgentAccess", json!({"enabled":enabled}))
        .await
        .map_err(|e| e.message)?;
    Ok(())
}

async fn send(writer: &mut (impl AsyncWrite + Unpin), request: Value) -> Result {
    writer.write_all(format!("{request}\n").as_bytes()).await?;
    writer.flush().await?;
    Ok(())
}

async fn read(reader: &mut (impl AsyncBufRead + Unpin)) -> Result<Value> {
    let mut line = String::new();
    assert_ne!(
        tokio::time::timeout(Duration::from_secs(10), reader.read_line(&mut line)).await??,
        0
    );
    Ok(serde_json::from_str(&line)?)
}

fn tool(id: u64, name: &str, arguments: Value) -> Value {
    json!({"jsonrpc":"2.0", "id":id, "method":"tools/call", "params":{
        "name":name, "arguments":arguments
    }})
}

fn content(response: &Value) -> Result<Value> {
    Ok(serde_json::from_str(
        response["result"]["content"][0]["text"]
            .as_str()
            .ok_or("tool result is missing text")?,
    )?)
}

async fn cli(dir: &Path, args: &[&str]) -> Result<std::process::Output> {
    Ok(
        tokio::process::Command::new(env!("CARGO_BIN_EXE_autoemail"))
            .env("AUTOEMAIL_DATA_DIR", dir)
            .args(args)
            .output()
            .await?,
    )
}

#[tokio::test]
async fn mcp_and_help_use_the_running_app_through_real_stdio_and_local_channels() -> Result {
    let dir = tempfile::tempdir()?;
    let _lock = api::startup::DataLock::acquire(dir.path())?;
    let (ctx, _) = api::startup::open(dir.path()).await?;
    let ctx = Arc::new(ctx);
    let (events, mut changed) = tokio::sync::mpsc::unbounded_channel();
    let server = api::agent::start(ctx.clone(), dir.path().to_owned(), move |event| {
        let _ = events.send(event);
    })
    .await
    .map_err(|e| e.message)?;
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_autoemail"))
        .env("AUTOEMAIL_DATA_DIR", dir.path())
        .arg("mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;
    let mut stdin = child.stdin.take().ok_or("missing stdin")?;
    let mut stdout = BufReader::new(child.stdout.take().ok_or("missing stdout")?);
    send(
        &mut stdin,
        json!({"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{
            "protocolVersion":"2025-11-25", "capabilities":{},
            "clientInfo":{"name":"integration-test", "version":"1"}
        }}),
    )
    .await?;
    let initialized = read(&mut stdout).await?;
    assert_eq!(initialized["id"], 1);
    assert!(initialized["result"]["capabilities"]["tools"].is_object());
    send(
        &mut stdin,
        json!({"jsonrpc":"2.0", "method":"notifications/initialized"}),
    )
    .await?;
    send(
        &mut stdin,
        json!({"jsonrpc":"2.0", "id":2, "method":"tools/list"}),
    )
    .await?;
    let tools = read(&mut stdout).await?;
    let tools = tools["result"]["tools"].as_array().ok_or("missing tools")?;
    assert_eq!(tools.len(), 3);
    assert!(tools.iter().any(|tool| tool["name"] == "call_operation"));

    send(&mut stdin, tool(12, "no_such_tool", json!({}))).await?;
    let unknown = read(&mut stdout).await?;
    assert_eq!(unknown["id"], 12);
    assert_eq!(unknown["error"]["code"], -32602);
    assert!(unknown.get("result").is_none());
    send(&mut stdin, tool(13, "describe_operation", json!({}))).await?;
    let invalid = read(&mut stdout).await?;
    assert_eq!(invalid["result"]["isError"], true);
    assert!(invalid.get("error").is_none());

    // Initialization and local help work even when app access is closed.
    for args in [["--help"], ["--version"]] {
        assert!(cli(dir.path(), &args).await?.status.success());
    }
    send(&mut stdin, tool(3, "list_operations", json!({}))).await?;
    let closed = read(&mut stdout).await?;
    assert_eq!(closed["result"]["isError"], true);
    assert!(
        closed["result"]["content"][0]["text"]
            .as_str()
            .is_some_and(|message| message.contains("请先打开 Auto Email Sender"))
    );

    access(&ctx, true).await?;
    for args in [vec!["list"], vec!["help", "getProfile"]] {
        let output = cli(dir.path(), &args).await?;
        assert!(output.status.success());
        assert!(String::from_utf8(output.stdout)?.contains("getProfile"));
    }
    send(&mut stdin, tool(4, "list_operations", json!({}))).await?;
    assert!(content(&read(&mut stdout).await?)?.is_array());
    send(
        &mut stdin,
        tool(5, "describe_operation", json!({"name":"getProfile"})),
    )
    .await?;
    assert!(content(&read(&mut stdout).await?)?["typescript"].is_string());

    api::call(&ctx, "setAiFactCheck", json!({"on":true}))
        .await
        .map_err(|e| e.message)?;
    send(
        &mut stdin,
        tool(
            6,
            "call_operation",
            json!({"name":"setAiFactCheck", "input":{"on":false}}),
        ),
    )
    .await?;
    assert_eq!(read(&mut stdout).await?["result"]["isError"], false);
    assert!(!profile::ai_fact_check(&ctx.db).await?);
    send(
        &mut stdin,
        tool(
            7,
            "call_operation",
            json!({"name":"notAnOperation", "input":{}}),
        ),
    )
    .await?;
    let failed = read(&mut stdout).await?;
    assert_eq!(failed["result"]["isError"], true);
    assert_eq!(content(&failed)?["code"], "bad_input");

    let tag = api::call(
        &ctx,
        "createTag",
        json!({
            "name":"MCP 测试", "color":"#112233", "mentorIds":[]
        }),
    )
    .await
    .map_err(|e| e.message)?;
    let mut deleting = tool(
        8,
        "call_operation",
        json!({"name":"deleteTag", "input":{"id":tag["id"]}}),
    );
    deleting["params"]["_meta"] = json!({"progressToken":"confirmation-test"});
    send(&mut stdin, deleting).await?;
    let progress = read(&mut stdout).await?;
    assert_eq!(progress["method"], "notifications/progress");
    assert_eq!(progress["params"]["progressToken"], "confirmation-test");
    let pending = api::call(&ctx, "listAgentRequests", json!({}))
        .await
        .map_err(|e| e.message)?;
    api::call(
        &ctx,
        "answerAgentRequest",
        json!({"id":pending[0]["id"], "allow":true}),
    )
    .await
    .map_err(|e| e.message)?;
    let deleted = read(&mut stdout).await?;
    assert_eq!(deleted["id"], 8);
    assert_eq!(deleted["result"]["isError"], false);
    assert_eq!(
        api::call(&ctx, "listTags", json!({}))
            .await
            .map_err(|e| e.message)?,
        json!([])
    );

    // A client without a progress token still waits for the user's answer.
    while changed.try_recv().is_ok() {}
    send(
        &mut stdin,
        tool(
            9,
            "call_operation",
            json!({"name":"deleteTag", "input":{"id":-1}}),
        ),
    )
    .await?;
    while !matches!(changed.recv().await, Some(Event::Requested(_))) {}
    let pending = api::call(&ctx, "listAgentRequests", json!({}))
        .await
        .map_err(|e| e.message)?;
    api::call(
        &ctx,
        "answerAgentRequest",
        json!({"id":pending[0]["id"], "allow":false}),
    )
    .await
    .map_err(|e| e.message)?;
    assert_eq!(read(&mut stdout).await?["result"]["isError"], true);

    access(&ctx, false).await?;
    drop(server);
    ctx.db.close().await;
    send(
        &mut stdin,
        tool(
            10,
            "call_operation",
            json!({"name":"getProfile", "input":{}}),
        ),
    )
    .await?;
    assert_eq!(read(&mut stdout).await?["result"]["isError"], true);
    let (reopened, _) = api::startup::open(dir.path()).await?;
    let reopened = Arc::new(reopened);
    let _server = api::agent::start(reopened.clone(), dir.path().to_owned(), |_| {})
        .await
        .map_err(|e| e.message)?;
    access(&reopened, true).await?;
    send(
        &mut stdin,
        tool(
            11,
            "call_operation",
            json!({"name":"getProfile", "input":{}}),
        ),
    )
    .await?;
    assert_eq!(read(&mut stdout).await?["result"]["isError"], false);

    drop(stdin);
    assert!(
        tokio::time::timeout(Duration::from_secs(10), child.wait())
            .await??
            .success()
    );
    access(&reopened, false).await?;
    reopened.db.close().await;
    Ok(())
}
