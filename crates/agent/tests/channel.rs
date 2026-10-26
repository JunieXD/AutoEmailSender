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
    let server = api::agent::start(ctx.clone(), dir.path().to_owned())
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
        for path in [
            agent::token_path(dir.path()),
            agent::socket_path(dir.path()),
        ] {
            assert_eq!(std::fs::metadata(path)?.permissions().mode() & 0o777, 0o600);
        }
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

    stream
        .get_mut()
        .write_all(b"{\"op\":\"sendEmailNow\",\"input\":{\"id\":1}}\n")
        .await?;
    line.clear();
    stream.read_line(&mut line).await?;
    let confirmation: Value = serde_json::from_str(&line)?;
    assert_eq!(confirmation["error"]["code"], "invalid");
    assert!(
        confirmation["error"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("这个操作要你在应用里确认，下一版支持"))
    );

    // The permission check happens before input deserialization or any handler side effect.
    stream
        .get_mut()
        .write_all(b"{\"op\":\"setAgentAccess\",\"input\":{\"enabled\":false}}\n")
        .await?;
    line.clear();
    stream.read_line(&mut line).await?;
    let denied: Value = serde_json::from_str(&line)?;
    assert_eq!(denied["error"]["code"], "invalid");
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
    let _server = api::agent::start(reopened.clone(), dir.path().to_owned())
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
