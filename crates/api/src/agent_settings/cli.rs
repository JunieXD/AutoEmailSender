use std::path::Path;

use serde::Serialize;
use ts_rs::TS;

use crate::CallError;

#[cfg(not(windows))]
mod macos;
#[cfg(not(windows))]
use macos as platform;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
use windows as platform;

#[derive(Serialize, TS)]
pub struct AgentCli {
    pub installed: bool,
    pub command: String,
    /// Absolute executable path, also used by MCP clients.
    pub path: String,
    /// Symlink on macOS, directory added to the user PATH on Windows.
    pub location: String,
}

pub(super) fn status(executable: &Path) -> Result<AgentCli, CallError> {
    Ok(AgentCli {
        installed: platform::installed(executable)?,
        command: "autoemail".into(),
        path: executable.display().to_string(),
        location: platform::location(executable)?.display().to_string(),
    })
}

pub(super) use platform::{install, uninstall};
