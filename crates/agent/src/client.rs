use std::io;
use std::path::PathBuf;

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

const OPEN_APP: &str = "请先打开 Auto Email Sender，并在设置里允许 Agent 连接";

pub async fn call(op: &str, input: Value, waiting: impl FnOnce(String)) -> Result<Value, String> {
    let mut waiting = Some(waiting);
    let dir = data_dir()?;
    let file = std::fs::read(agent::token_path(&dir))
        .map_err(|error| connection_error("读取连接令牌", error))?;
    let credentials: agent::Credentials = serde_json::from_slice(&file)
        .map_err(|_| "连接令牌文件不正确，请在设置中关闭 Agent 连接后重新开启".to_owned())?;
    #[cfg(windows)]
    if !credentials.pipe.starts_with(r"\\.\pipe\autoemail-") {
        return Err("连接令牌文件不正确，请在设置中关闭 Agent 连接后重新开启".into());
    }
    let mut stream = agent::connect(&dir, &credentials)
        .await
        .map_err(|error| connection_error("连接应用", error))?;
    let request = serde_json::json!({"op": op, "input": input});
    stream
        .write_all(format!("{}\n{request}\n", credentials.token).as_bytes())
        .await
        .map_err(|error| connection_error("发送请求", error))?;
    let mut reader = BufReader::new(stream);
    loop {
        let mut response = String::new();
        reader
            .read_line(&mut response)
            .await
            .map_err(|error| connection_error("读取结果", error))?;
        if response.is_empty() {
            return Err(OPEN_APP.into());
        }
        let result: Value = serde_json::from_str(&response)
            .map_err(|error| format!("应用返回的 JSON 不正确：{error}"))?;
        if let Some(confirmation) = result.get("waiting") {
            let action = confirmation
                .get("action")
                .and_then(Value::as_str)
                .ok_or("应用返回的确认说明不正确")?;
            if let Some(waiting) = waiting.take() {
                waiting(action.to_owned());
            }
            continue;
        }
        if result.get("ok").is_none() && result.get("error").is_none() {
            return Err("应用返回的结果不正确".into());
        }
        return Ok(result);
    }
}

fn connection_error(action: &str, error: io::Error) -> String {
    match error.kind() {
        io::ErrorKind::NotFound
        | io::ErrorKind::ConnectionRefused
        | io::ErrorKind::BrokenPipe
        | io::ErrorKind::ConnectionReset => OPEN_APP.into(),
        _ => format!("{action}失败：{error}"),
    }
}

fn data_dir() -> Result<PathBuf, String> {
    if let Some(dir) = std::env::var_os("AUTOEMAIL_DATA_DIR") {
        return Ok(dir.into());
    }
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(".dev-data"));
    }
    let config: Value = serde_json::from_str(include_str!("../../app/tauri.conf.json"))
        .map_err(|error| format!("应用目录配置不正确：{error}"))?;
    let identifier = config["identifier"]
        .as_str()
        .ok_or("应用目录配置缺少应用标识")?;
    #[cfg(windows)]
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .ok_or("无法找到应用数据目录：APPDATA 未设置")?;
    #[cfg(unix)]
    let base = std::env::var_os("HOME")
        .map(|home| PathBuf::from(home).join("Library/Application Support"))
        .ok_or("无法找到应用数据目录：HOME 未设置")?;
    Ok(base.join(identifier))
}
