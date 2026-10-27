mod client;
mod mcp;

use std::process::ExitCode;

use serde_json::{Value, json};

const USAGE: &str = "Auto Email Sender：联系导师的本机 Agent 接口

用法：
  autoemail list                       按组列出操作及确认方式
  autoemail help <操作名>               查看操作说明及输入输出类型
  autoemail call <操作名> [JSON 输入]   调用操作，省略输入时为 {}
  autoemail mcp                        启动标准输入输出 MCP 服务器
  autoemail --help                     显示帮助
  autoemail --version                  显示命令行版本

先 list，再 help 查看输入，最后 call。需要确认时请在应用里回答，最多等 5 分钟。
使用 list、help 和 call 前，请打开应用并在设置里允许 Agent 连接。";

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    match run().await {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<bool, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let args = args.iter().map(String::as_str).collect::<Vec<_>>();
    // Rust writes Unicode directly to Windows consoles; redirected output remains UTF-8.
    match args.as_slice() {
        ["--help"] => println!("{USAGE}"),
        ["--version"] => println!("autoemail {}", env!("CARGO_PKG_VERSION")),
        ["mcp"] => mcp::run().await?,
        ["call", op, rest @ ..] if rest.len() <= 1 => {
            let input = match rest.first() {
                Some(input) => serde_json::from_str::<Value>(input)
                    .map_err(|error| format!("JSON 输入不正确：{error}"))?,
                None => json!({}),
            };
            let result = call(op, input).await?;
            println!("{result}");
            return Ok(result.get("error").is_none());
        }
        ["list"] => {
            let result = call("listOperations", json!({})).await?;
            let operations = output(&result)?.as_array().ok_or("应用返回的目录不正确")?;
            let mut group = "";
            for operation in operations {
                let next_group = text(operation, "group")?;
                if group != next_group {
                    println!("\n{next_group}");
                    group = next_group;
                }
                println!(
                    "  {}{}  {}",
                    text(operation, "name")?,
                    confirmation(&operation["confirmation"])?,
                    text(operation, "summary")?,
                );
            }
        }
        ["help", name] => {
            let result = call("describeOperation", json!({"name": name})).await?;
            let operation = output(&result)?;
            println!(
                "{}{}\n{}",
                text(operation, "name")?,
                confirmation(&operation["confirmation"])?,
                text(operation, "summary")?,
            );
            if let Some(details) = operation["details"].as_str() {
                println!("\n{details}");
            }
            println!("\n{}", text(operation, "typescript")?);
        }
        _ => return Err(USAGE.into()),
    }
    Ok(true)
}

async fn call(op: &str, input: Value) -> Result<Value, String> {
    client::call(op, input, |action| {
        eprintln!("等你在 Auto Email Sender 里确认：{action}");
    })
    .await
}

fn output(result: &Value) -> Result<&Value, String> {
    if let Some(error) = result.get("error") {
        return Err(text(error, "message")?.into());
    }
    result.get("ok").ok_or("应用返回的结果不正确".into())
}

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, String> {
    value[field]
        .as_str()
        .ok_or_else(|| format!("应用返回的 {field} 不正确"))
}

fn confirmation(value: &Value) -> Result<String, String> {
    match text(value, "kind")? {
        "none" => Ok(String::new()),
        "always" => Ok("（需在应用里确认）".into()),
        "conditional" => Ok(format!("（{}，需确认）", text(value, "condition")?)),
        _ => Err("应用返回的确认方式不正确".into()),
    }
}
