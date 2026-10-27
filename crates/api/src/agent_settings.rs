//! Desktop-only installation and client configuration; no Agent can change its own access.

mod cli;
mod mcp;
mod paths;
mod shell;

use std::path::PathBuf;

use tokio::sync::Mutex;

use crate::{CallError, Context, Nothing};
pub use cli::{AgentCli, AgentCliState};
pub use mcp::{McpClient, McpConfigPreview, McpConfigQuery};

#[derive(Default)]
pub struct Settings {
    cli: Mutex<()>,
    previews: Mutex<Vec<mcp::Plan>>,
}

fn executable() -> Result<PathBuf, CallError> {
    let app = std::env::current_exe().map_err(CallError::internal)?;
    let directory = app
        .parent()
        .ok_or_else(|| CallError::internal("application has no parent directory"))?;
    let path = directory.join(if cfg!(windows) {
        "autoemail.exe"
    } else {
        "autoemail"
    });
    std::fs::metadata(&path).map_err(|e| {
        CallError::invalid(format!(
            "找不到应用附带的命令行工具：{e}。开发版请用 pnpm dev 启动，安装版请重新安装应用。"
        ))
    })?;
    Ok(paths::plain_path(path))
}

async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, CallError> + Send + 'static,
) -> Result<T, CallError> {
    tokio::task::spawn_blocking(work)
        .await
        .map_err(CallError::internal)?
}

pub async fn get_agent_cli(_: &Context, _: Nothing) -> Result<AgentCli, CallError> {
    blocking(|| cli::status(&executable()?)).await
}

pub async fn install_agent_cli(ctx: &Context, _: Nothing) -> Result<AgentCli, CallError> {
    let _guard = ctx.agent_settings.cli.lock().await;
    blocking(|| {
        let path = executable()?;
        cli::install(&path)?;
        cli::status(&path)
    })
    .await
}

pub async fn uninstall_agent_cli(ctx: &Context, _: Nothing) -> Result<AgentCli, CallError> {
    let _guard = ctx.agent_settings.cli.lock().await;
    blocking(|| {
        let path = executable()?;
        cli::uninstall(&path)?;
        cli::status(&path)
    })
    .await
}

pub async fn preview_mcp_config(
    ctx: &Context,
    input: McpConfigQuery,
) -> Result<McpConfigPreview, CallError> {
    let mut previews = ctx.agent_settings.previews.lock().await;
    let plan = blocking(move || mcp::Plan::new(input.client, &executable()?)).await?;
    let preview = plan.preview();
    previews.retain(|p| p.client != input.client);
    previews.push(plan);
    Ok(preview)
}

pub async fn write_mcp_config(ctx: &Context, input: McpConfigQuery) -> Result<(), CallError> {
    let previews = ctx.agent_settings.previews.lock().await;
    let plan = previews
        .iter()
        .find(|p| p.client == input.client)
        .cloned()
        .ok_or_else(|| CallError::invalid("请先预览 MCP 配置，确认后再写入。".into()))?;
    blocking(move || plan.write()).await
}

pub async fn mcp_config_text(_: &Context, _: Nothing) -> Result<String, CallError> {
    blocking(|| mcp::config_text(&executable()?)).await
}
