use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json, value::RawValue};
use ts_rs::TS;

use super::{paths::plain_path, shell::command_line};
use crate::CallError;

const CLAUDE_UNAVAILABLE: &str = "找不到 claude 命令，请在终端里运行下面的命令。";

#[derive(Clone, Copy, PartialEq, Eq, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum McpClient {
    ClaudeDesktop,
    ClaudeCode,
    Cursor,
}

#[derive(Deserialize, TS)]
pub struct McpConfigQuery {
    pub client: McpClient,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct McpConfigPreview {
    pub path: String,
    /// Complete named entry, suitable for displaying without exposing other services.
    pub server: String,
    pub exists: bool,
    /// Ready to copy into the platform's default shell.
    pub commands: Vec<String>,
    pub write_unavailable: Option<String>,
}

#[derive(Clone)]
pub(super) struct Plan {
    pub client: McpClient,
    logical_path: PathBuf,
    path: PathBuf,
    original: Option<String>,
    updated: String,
    server: String,
    previous: Option<String>,
    claude: Option<PathBuf>,
    executable: PathBuf,
}

impl Plan {
    pub fn new(client: McpClient, executable: &Path) -> Result<Self, CallError> {
        let home = home()?;
        let path = match client {
            McpClient::ClaudeDesktop => {
                #[cfg(windows)]
                let base = env_path("APPDATA")?;
                #[cfg(not(windows))]
                let base = home.join("Library/Application Support");
                base.join("Claude/claude_desktop_config.json")
            }
            McpClient::ClaudeCode => match std::env::var_os("CLAUDE_CONFIG_DIR") {
                Some(dir) => PathBuf::from(dir).join(".claude.json"),
                None => home.join(".claude.json"),
            },
            McpClient::Cursor => home.join(".cursor/mcp.json"),
        };
        let claude = if client == McpClient::ClaudeCode {
            find_claude(&home)?
        } else {
            None
        };
        Self::at(client, path, executable, claude)
    }

    fn at(
        client: McpClient,
        path: PathBuf,
        executable: &Path,
        claude: Option<PathBuf>,
    ) -> Result<Self, CallError> {
        let logical_path = plain_path(std::path::absolute(path).map_err(file_error)?);
        let path = resolve(&logical_path)?;
        let original = read(&path)?;
        let source = original.as_deref().unwrap_or("{}");
        let root = object(source)?;
        let servers = root.get("mcpServers").map(|v| v.get()).unwrap_or("{}");
        let services = object(servers)?;
        let previous = services.get("autoemail").map(|v| v.get().to_owned());
        let entry = service(executable);
        let value = serde_json::to_string_pretty(&entry).map_err(CallError::internal)?;
        let server = serde_json::to_string_pretty(&json!({"autoemail": entry}))
            .map_err(CallError::internal)?;
        let updated = if original.is_none() {
            config_text(executable)?
        } else {
            let servers = set(servers, "autoemail", &value)?;
            set(source, "mcpServers", &servers)?
        };
        Ok(Self {
            client,
            logical_path,
            path,
            original,
            updated,
            server,
            previous,
            claude,
            executable: executable.to_owned(),
        })
    }

    pub fn preview(&self) -> McpConfigPreview {
        let mut commands = Vec::new();
        if self.client == McpClient::ClaudeCode {
            let claude = self
                .claude
                .as_deref()
                .unwrap_or_else(|| Path::new("claude"));
            if self.previous.is_some() {
                commands.push(command_line(&remove_args(claude)));
            }
            commands.push(command_line(&add_args(claude, &self.executable)));
        }
        McpConfigPreview {
            path: self.path.display().to_string(),
            server: self.server.clone(),
            exists: self.previous.is_some(),
            commands,
            write_unavailable: (self.client == McpClient::ClaudeCode && self.claude.is_none())
                .then(|| CLAUDE_UNAVAILABLE.into()),
        }
    }

    pub fn write(&self) -> Result<(), CallError> {
        if self.client == McpClient::ClaudeCode && self.claude.is_none() {
            return Err(CallError::invalid(CLAUDE_UNAVAILABLE.into()));
        }
        let current = self.current()?;
        if current.as_deref() == Some(&self.updated) {
            return Ok(());
        }
        // Claude Code adds an empty env object and formats its file itself.
        if self.claude.is_some()
            && let Some(current) = &current
            && configured(current, &self.executable)?
        {
            return Ok(());
        }
        if current != self.original {
            return Err(CallError::invalid(
                "配置在预览后发生了变化，请重新预览并确认。".into(),
            ));
        }
        if let Some(claude) = &self.claude {
            if self.previous.is_some() {
                run(&remove_args(claude))?;
            }
            if let Err(error) = run(&add_args(claude, &self.executable)) {
                if let Some(previous) = &self.previous {
                    let restore = vec![
                        claude.display().to_string(),
                        "mcp".into(),
                        "add-json".into(),
                        "--scope".into(),
                        "user".into(),
                        "autoemail".into(),
                        previous.clone(),
                    ];
                    if run(&restore).is_err() {
                        return Err(CallError::invalid(
                            "Claude Code 写入失败，旧的 autoemail 配置也未能恢复。请在 Claude Code 中重新添加该服务。".into(),
                        ));
                    }
                }
                return Err(error);
            }
            return Ok(());
        }
        let parent = self
            .path
            .parent()
            .ok_or_else(|| CallError::internal("MCP config has no parent directory"))?;
        fs::create_dir_all(parent).map_err(file_error)?;
        let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(file_error)?;
        if self.original.is_some() {
            let permissions = fs::metadata(&self.path).map_err(file_error)?.permissions();
            temporary
                .as_file()
                .set_permissions(permissions)
                .map_err(file_error)?;
        }
        temporary
            .write_all(self.updated.as_bytes())
            .map_err(file_error)?;
        temporary.as_file().sync_all().map_err(file_error)?;
        // A second check narrows the interval in which another editor could change the file.
        if self.current()? != self.original {
            return Err(CallError::invalid("配置已变化，请重新预览并确认。".into()));
        }
        temporary
            .persist(&self.path)
            .map_err(|e| file_error(e.error))?;
        Ok(())
    }

    fn current(&self) -> Result<Option<String>, CallError> {
        if resolve(&self.logical_path)? != self.path {
            return Err(CallError::invalid(
                "配置文件的链接指向已变化，请重新预览并确认。".into(),
            ));
        }
        read(&self.path)
    }
}

fn resolve(path: &Path) -> Result<PathBuf, CallError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => path.canonicalize().map(plain_path).map_err(|e| {
            if metadata.file_type().is_symlink() && e.kind() == io::ErrorKind::NotFound {
                CallError::invalid(format!(
                    "MCP 配置的符号链接已失效，请先修复链接：{}",
                    path.display()
                ))
            } else {
                file_error(e)
            }
        }),
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            let parent = path
                .parent()
                .ok_or_else(|| CallError::internal("config path has no parent"))?;
            let name = path
                .file_name()
                .ok_or_else(|| CallError::internal("config path has no file name"))?;
            Ok(resolve(parent)?.join(name))
        }
        Err(e) => Err(file_error(e)),
    }
}

fn service(executable: &Path) -> Value {
    json!({"type": "stdio", "command": executable, "args": ["mcp"]})
}

fn configured(source: &str, executable: &Path) -> Result<bool, CallError> {
    let root = object(source)?;
    let Some(servers) = root.get("mcpServers") else {
        return Ok(false);
    };
    let services = object(servers.get())?;
    let Some(entry) = services.get("autoemail") else {
        return Ok(false);
    };
    let mut entry: Value = serde_json::from_str(entry.get()).map_err(CallError::internal)?;
    if let Some(entry) = entry.as_object_mut()
        && entry.get("env") == Some(&json!({}))
    {
        entry.remove("env");
    }
    Ok(entry == service(executable))
}

pub(super) fn config_text(executable: &Path) -> Result<String, CallError> {
    serde_json::to_string_pretty(&json!({"mcpServers": {"autoemail": service(executable)}}))
        .map_err(CallError::internal)
}

fn object(source: &str) -> Result<BTreeMap<String, &RawValue>, CallError> {
    serde_json::from_str(source).map_err(|e| {
        CallError::invalid(format!(
            "MCP 配置必须是合法的 JSON 对象，不能包含注释（第 {} 行，第 {} 列）；原文件未改动。",
            e.line(),
            e.column(),
        ))
    })
}

/// RawValue borrows slices of the source, so unrelated bytes never need reserialization.
fn set(source: &str, key: &str, value: &str) -> Result<String, CallError> {
    let entries = object(source)?;
    let mut result = source.to_owned();
    if let Some(old) = entries.get(key) {
        let offset = old.get().as_ptr() as usize - source.as_ptr() as usize;
        result.replace_range(offset..offset + old.get().len(), value);
    } else {
        let end = source.trim_end().len() - 1;
        let separator = if entries.is_empty() { "" } else { "," };
        result.insert_str(end, &format!("{separator}\n  \"{key}\": {value}\n"));
    }
    Ok(result)
}

fn read(path: &Path) -> Result<Option<String>, CallError> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(file_error(e)),
    }
}

fn file_error(e: io::Error) -> CallError {
    CallError::invalid(format!(
        "无法读写 MCP 配置：{e}。请检查文件和目录权限后重试。"
    ))
}

fn env_path(name: &str) -> Result<PathBuf, CallError> {
    std::env::var_os(name)
        .map(PathBuf::from)
        .ok_or_else(|| CallError::invalid(format!("无法找到用户目录：{name} 未设置。")))
}

fn home() -> Result<PathBuf, CallError> {
    env_path(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
}

fn find_claude(home: &Path) -> Result<Option<PathBuf>, CallError> {
    let mut directories: Vec<PathBuf> = match std::env::var_os("PATH") {
        Some(path) => std::env::split_paths(&path).collect(),
        None => Vec::new(),
    };
    // GUI-launched applications may not inherit the terminal's PATH.
    directories.push(home.join(".local/bin"));
    #[cfg(not(windows))]
    directories.extend([
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ]);
    #[cfg(windows)]
    for (variable, suffix) in [
        ("LOCALAPPDATA", "Microsoft/WinGet/Links"),
        ("ProgramFiles", "WinGet/Links"),
        ("APPDATA", "npm"),
    ] {
        if let Some(base) = std::env::var_os(variable) {
            directories.push(PathBuf::from(base).join(suffix));
        }
    }
    find_claude_in(directories)
}

fn find_claude_in(directories: Vec<PathBuf>) -> Result<Option<PathBuf>, CallError> {
    for directory in directories {
        let names: &[&str] = if cfg!(windows) {
            &["claude.exe", "claude.cmd"]
        } else {
            &["claude"]
        };
        for name in names {
            let path = directory.join(name);
            match fs::metadata(&path) {
                Ok(metadata) if metadata.is_file() => {
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        if metadata.permissions().mode() & 0o111 == 0 {
                            continue;
                        }
                    }
                    return std::path::absolute(path)
                        .map(plain_path)
                        .map(Some)
                        .map_err(file_error);
                }
                Ok(_) => {}
                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                Err(e) => return Err(file_error(e)),
            }
        }
    }
    Ok(None)
}

fn remove_args(claude: &Path) -> Vec<String> {
    [
        claude.display().to_string(),
        "mcp".into(),
        "remove".into(),
        "--scope".into(),
        "user".into(),
        "autoemail".into(),
    ]
    .into()
}

fn add_args(claude: &Path, executable: &Path) -> Vec<String> {
    [
        claude.display().to_string(),
        "mcp".into(),
        "add".into(),
        "--transport".into(),
        "stdio".into(),
        "--scope".into(),
        "user".into(),
        "autoemail".into(),
        "--".into(),
        executable.display().to_string(),
        "mcp".into(),
    ]
    .into()
}

fn run(args: &[String]) -> Result<(), CallError> {
    let mut command = Command::new(&args[0]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let result = command
        .args(&args[1..])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| CallError::invalid(format!("无法运行 Claude Code：{e}")))?;
    if !result.status.success() {
        // Client output can contain credentials from its configuration.
        return Err(CallError::invalid(format!(
            "Claude Code 未能更新 MCP 配置（{}），请检查 Claude Code 的配置和文件权限后重试。",
            result.status
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

    fn plan(path: &Path) -> Result<Plan> {
        Plan::at(
            McpClient::ClaudeDesktop,
            path.to_owned(),
            Path::new("/Applications/中文 应用.app/Contents/MacOS/autoemail"),
            None,
        )
        .map_err(|e| e.message.into())
    }

    #[test]
    fn creates_config_and_directories_only_after_preview() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("Claude/claude_desktop_config.json");
        let plan = plan(&path)?;
        assert!(!path.try_exists()?);
        assert!(!plan.preview().exists);
        plan.write().map_err(|e| e.message)?;
        plan.write().map_err(|e| e.message)?;
        let actual: Value = serde_json::from_str(&fs::read_to_string(&path)?)?;
        assert_eq!(actual["mcpServers"]["autoemail"], service(&plan.executable));
        assert_eq!(
            fs::read_dir(path.parent().ok_or("missing parent")?)?.count(),
            1
        );
        Ok(())
    }

    #[test]
    fn preserves_every_unrelated_byte_when_adding_or_replacing() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let before = "{\r\n \"other\": [1e2, \"中文\"], \"mcpServers\" : {\n\
            \"first\": { \"args\": [\"x\"], \"command\": \"keep\" },\
            \"autoemail\" : {\"command\":\"old\"},\
            \"last\": {\"command\":\"also keep\"} }, \"tail\":true\r\n}\n";
        fs::write(&path, before)?;
        let replacement = plan(&path)?;
        assert!(replacement.preview().exists);
        replacement.write().map_err(|e| e.message)?;
        let after = fs::read_to_string(&path)?;
        let expected = before.replace(
            "{\"command\":\"old\"}",
            &serde_json::to_string_pretty(&service(&replacement.executable))?,
        );
        assert_eq!(after, expected);
        let without_service = before.replace("\"autoemail\" : {\"command\":\"old\"},", "");
        fs::write(&path, &without_service)?;
        plan(&path)?.write().map_err(|e| e.message)?;
        let after = fs::read_to_string(&path)?;
        assert!(
            after.starts_with(
                without_service
                    .split(" }, \"tail\"")
                    .next()
                    .ok_or("prefix")?
            )
        );
        assert!(after.ends_with("}, \"tail\":true\r\n}\n"));
        fs::write(&path, "{\"preferences\": { \"theme\": \"dark\" }}")?;
        plan(&path)?.write().map_err(|e| e.message)?;
        assert!(fs::read_to_string(&path)?.contains("\"preferences\": { \"theme\": \"dark\" }"));
        Ok(())
    }

    #[test]
    fn invalid_or_changed_config_is_not_overwritten() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        for invalid in [
            "{ // comment\n}",
            "[]",
            "{\"mcpServers\": null}",
            "{\"mcpServers\": []}",
        ] {
            fs::write(&path, invalid)?;
            assert!(plan(&path).is_err());
            assert_eq!(fs::read_to_string(&path)?, invalid);
        }
        fs::write(&path, "{}")?;
        let preview = plan(&path)?;
        fs::write(&path, "{\"new\":true}")?;
        assert!(preview.write().is_err());
        assert_eq!(fs::read_to_string(&path)?, "{\"new\":true}");
        Ok(())
    }

    #[test]
    fn missing_claude_still_previews_commands_and_cannot_write() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join(".claude.json");
        let executable = dir.path().join("应用 $USER 'quoted'/autoemail");
        let claude = find_claude_in(vec![dir.path().join("missing-bin")]).map_err(|e| e.message)?;
        assert!(claude.is_none());
        let plan = Plan::at(McpClient::ClaudeCode, path.clone(), &executable, claude)
            .map_err(|e| e.message)?;
        let preview = plan.preview();
        assert_eq!(preview.commands.len(), 1);
        assert!(preview.write_unavailable.is_some());
        let server: Value = serde_json::from_str(&preview.server)?;
        assert_eq!(server["autoemail"]["command"], json!(executable));
        assert!(plan.write().is_err());
        assert!(!path.try_exists()?);
        Ok(())
    }

    #[test]
    fn searches_other_install_directories_after_a_minimal_path() -> Result {
        let dir = tempfile::tempdir()?;
        let install = dir.path().join("install/bin");
        fs::create_dir_all(&install)?;
        let executable = install.join(if cfg!(windows) {
            "claude.exe"
        } else {
            "claude"
        });
        fs::write(&executable, "fixture")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&executable, fs::Permissions::from_mode(0o755))?;
        }
        assert_eq!(
            find_claude_in(vec![dir.path().join("minimal-path"), install]).map_err(|e| e.message)?,
            Some(executable)
        );
        Ok(())
    }

    #[test]
    #[cfg(unix)]
    fn config_link_remains_a_link_when_writing_its_target() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let target = dir.path().join("target.json");
        fs::write(&target, "{}")?;
        std::os::unix::fs::symlink(&target, &path)?;
        plan(&path)?.write().map_err(|e| e.message)?;
        assert_eq!(fs::read_link(&path)?, target);
        let actual: Value = serde_json::from_str(&fs::read_to_string(&target)?)?;
        assert!(actual["mcpServers"]["autoemail"].is_object());
        Ok(())
    }

    #[test]
    #[cfg(unix)]
    fn broken_config_link_is_rejected_and_kept() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let missing = dir.path().join("missing.json");
        std::os::unix::fs::symlink(&missing, &path)?;
        assert!(plan(&path).is_err());
        assert_eq!(fs::read_link(&path)?, missing);
        Ok(())
    }

    #[test]
    #[cfg(unix)]
    fn changed_config_link_does_not_write_either_target() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let first = dir.path().join("first.json");
        let second = dir.path().join("second.json");
        fs::write(&first, "{}")?;
        fs::write(&second, "{}")?;
        std::os::unix::fs::symlink(&first, &path)?;
        let preview = plan(&path)?;
        fs::remove_file(&path)?;
        std::os::unix::fs::symlink(&second, &path)?;
        assert!(preview.write().is_err());
        assert_eq!(fs::read_link(&path)?, second);
        assert_eq!(fs::read_to_string(&first)?, "{}");
        assert_eq!(fs::read_to_string(&second)?, "{}");
        Ok(())
    }

    #[test]
    fn retry_after_claude_code_saved_the_service_does_not_rewrite_config() -> Result {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join(".claude.json");
        let executable = dir.path().join("autoemail");
        fs::write(&path, "{}")?;
        let plan = Plan::at(
            McpClient::ClaudeCode,
            path.clone(),
            &executable,
            Some(dir.path().join("missing-claude")),
        )
        .map_err(|e| e.message)?;
        let mut entry = service(&executable);
        entry["env"] = json!({});
        let saved =
            serde_json::to_string(&json!({"other": true, "mcpServers": {"autoemail": entry}}))?;
        fs::write(&path, &saved)?;
        plan.write().map_err(|e| e.message)?;
        assert_eq!(fs::read_to_string(&path)?, saved);
        Ok(())
    }
}
