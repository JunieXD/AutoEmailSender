use std::path::{Path, PathBuf};
use std::process::Command;

use crate::CallError;

pub(in crate::agent_settings) fn location(executable: &Path) -> Result<PathBuf, CallError> {
    executable
        .parent()
        .map(Path::to_owned)
        .ok_or_else(|| CallError::internal("CLI has no parent directory"))
}

pub(in crate::agent_settings) fn installed(executable: &Path) -> Result<bool, CallError> {
    run(executable, "status")
}

pub(in crate::agent_settings) fn install(executable: &Path) -> Result<(), CallError> {
    run(executable, "install").map(|_| ())
}

pub(in crate::agent_settings) fn uninstall(executable: &Path) -> Result<(), CallError> {
    run(executable, "uninstall").map(|_| ())
}

fn run(executable: &Path, action: &str) -> Result<bool, CallError> {
    use std::os::windows::process::CommandExt;

    let root = std::env::var_os("SystemRoot").ok_or_else(|| {
        CallError::invalid("无法找到 Windows PowerShell：SystemRoot 未设置。".into())
    })?;
    let output =
        Command::new(PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe"))
            .creation_flags(0x0800_0000)
            .args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                include_str!("windows.ps1"),
            ])
            .env("AUTOEMAIL_CLI_DIRECTORY", location(executable)?)
            .env("AUTOEMAIL_CLI_ACTION", action)
            .output()
            .map_err(|e| CallError::invalid(format!("无法读写用户 PATH：{e}")))?;
    if !output.status.success() {
        return Err(CallError::invalid(format!(
            "无法更新当前用户的 PATH 或广播环境变量变化：{}。请检查用户环境变量权限后重试。",
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    serde_json::from_slice(&output.stdout).map_err(CallError::internal)
}
