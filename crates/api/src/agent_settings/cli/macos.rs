use std::fs;
use std::io;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::Command;

use super::AgentCliState;
use crate::CallError;
use crate::agent_settings::shell::quote;

const APPLICATION_CLI: &str = "Auto Email Sender.app/Contents/MacOS/autoemail";

pub(in crate::agent_settings) fn location(_: &Path) -> Result<PathBuf, CallError> {
    Ok(PathBuf::from("/usr/local/bin/autoemail"))
}

pub(in crate::agent_settings) fn state(executable: &Path) -> Result<AgentCliState, CallError> {
    state_at(executable, &location(executable)?).map_err(error)
}

fn state_at(executable: &Path, link: &Path) -> io::Result<AgentCliState> {
    match fs::symlink_metadata(link) {
        Ok(metadata) if !metadata.file_type().is_symlink() => return Ok(AgentCliState::Occupied),
        Ok(_) => {}
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(AgentCliState::NotInstalled),
        Err(e) => return Err(e),
    }
    let target = fs::read_link(link)?;
    let target = std::path::absolute(link.parent().unwrap_or_else(|| Path::new(".")).join(target))?;
    if target == executable {
        Ok(AgentCliState::Installed)
    } else if !target.try_exists()? || target.ends_with(APPLICATION_CLI) {
        Ok(AgentCliState::OldLocation {
            target: target.display().to_string(),
        })
    } else {
        Ok(AgentCliState::Occupied)
    }
}

pub(in crate::agent_settings) fn install(executable: &Path) -> Result<(), CallError> {
    let link = location(executable)?;
    match install_at(executable, &link) {
        Err(e) if needs_authorization(&e) => authorize(executable, &link, true),
        result => result.map_err(error),
    }
}

pub(in crate::agent_settings) fn uninstall(executable: &Path) -> Result<(), CallError> {
    let link = location(executable)?;
    match uninstall_at(executable, &link) {
        Err(e) if needs_authorization(&e) => authorize(executable, &link, false),
        result => result.map_err(error),
    }
}

fn needs_authorization(e: &io::Error) -> bool {
    matches!(
        e.kind(),
        io::ErrorKind::PermissionDenied | io::ErrorKind::NotFound
    )
}

fn install_at(executable: &Path, link: &Path) -> io::Result<()> {
    let state = state_at(executable, link)?;
    match state {
        AgentCliState::Installed => Ok(()),
        AgentCliState::NotInstalled => symlink(executable, link),
        AgentCliState::Occupied => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "同名位置已被其他文件或链接占用，未覆盖",
        )),
        AgentCliState::OldLocation { .. } => {
            let parent = link.parent().unwrap_or_else(|| Path::new("."));
            let temporary = tempfile::Builder::new()
                .prefix(".autoemail-")
                .tempdir_in(parent)?;
            let replacement = temporary.path().join("autoemail");
            symlink(executable, &replacement)?;
            if state_at(executable, link)? != state {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "链接已变化，请重新安装",
                ));
            }
            fs::rename(replacement, link)
        }
    }
}

fn uninstall_at(executable: &Path, link: &Path) -> io::Result<()> {
    match state_at(executable, link)? {
        AgentCliState::Installed | AgentCliState::OldLocation { .. } => fs::remove_file(link),
        AgentCliState::NotInstalled | AgentCliState::Occupied => Ok(()),
    }
}

fn authorize(executable: &Path, link: &Path, install: bool) -> Result<(), CallError> {
    let target = quote(&executable.to_string_lossy());
    let link = quote(&link.to_string_lossy());
    let application = quote(APPLICATION_CLI);
    // Recheck ownership after authorization: the filesystem may have changed while waiting.
    let ownership = format!(
        r#"application={application}
        owned() {{
            [ -L {link} ] || return 1
            [ ! -e {link} ] && return 0
            previous="$(/usr/bin/readlink {link})" || return 1
            [ "$previous" = {target} ] && return 0
            case "$previous" in
                "$application"|*"/$application") return 0 ;;
                *) return 1 ;;
            esac
        }}"#
    );
    let command = if install {
        format!(
            "{ownership}\n\
            if [ -e {link} ] || [ -L {link} ]; then \
            owned || exit 17; /bin/ln -sfn {target} {link}; \
            else /bin/mkdir -p /usr/local/bin && /bin/ln -s {target} {link}; fi"
        )
    } else {
        format!("{ownership}\nif owned; then /usr/bin/unlink {link}; fi")
    };
    let output = Command::new("/usr/bin/osascript")
        .args([
            "-e",
            "on run argv\n do shell script (item 1 of argv) with administrator privileges\nend run",
            &command,
        ])
        .output()
        .map_err(error)?;
    if output.status.success() {
        return Ok(());
    }
    let message = String::from_utf8_lossy(&output.stderr);
    if message.contains("(-128)") {
        return Err(CallError::invalid(
            "你取消了管理员授权，命令行工具未更改。".into(),
        ));
    }
    if message.contains("(17)") {
        return Err(CallError::invalid(
            "/usr/local/bin/autoemail 已被其他文件占用，未覆盖。".into(),
        ));
    }
    Err(CallError::invalid(format!(
        "管理员操作未完成：{message}。请检查 /usr/local/bin 的权限后重试。"
    )))
}

fn error(e: impl std::fmt::Display) -> CallError {
    CallError::invalid(format!("命令行工具未能更改：{e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repairs_old_application_locations_and_dangling_links()
    -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let current = dir
            .path()
            .join("current/Auto Email Sender.app/Contents/MacOS/autoemail");
        let old = dir
            .path()
            .join("old/Auto Email Sender.app/Contents/MacOS/autoemail");
        for executable in [&current, &old] {
            fs::create_dir_all(executable.parent().ok_or("missing parent")?)?;
            fs::write(executable, "binary")?;
        }
        let link = dir.path().join("autoemail");
        symlink(&old, &link)?;
        assert!(matches!(
            state_at(&current, &link)?,
            AgentCliState::OldLocation { .. }
        ));
        install_at(&current, &link)?;
        assert_eq!(state_at(&current, &link)?, AgentCliState::Installed);
        assert_eq!(fs::read_link(&link)?, current);
        fs::remove_file(&link)?;
        symlink(&old, &link)?;
        uninstall_at(&current, &link)?;
        assert_eq!(state_at(&current, &link)?, AgentCliState::NotInstalled);
        assert!(!link.try_exists()?);
        symlink("missing binary", &link)?;
        assert!(matches!(
            state_at(&current, &link)?,
            AgentCliState::OldLocation { .. }
        ));
        install_at(&current, &link)?;
        assert_eq!(fs::read_link(&link)?, current);
        fs::remove_file(&link)?;
        symlink("missing binary", &link)?;
        uninstall_at(&current, &link)?;
        assert!(fs::symlink_metadata(&link).is_err());
        Ok(())
    }

    #[test]
    fn install_and_uninstall_only_our_link() -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let executable = dir.path().join("应用 ' 中文/autoemail");
        fs::create_dir_all(executable.parent().ok_or("no parent")?)?;
        fs::write(&executable, "temporary binary")?;
        let link = dir.path().join("autoemail");
        install_at(&executable, &link)?;
        install_at(&executable, &link)?;
        assert_eq!(state_at(&executable, &link)?, AgentCliState::Installed);
        uninstall_at(&executable, &link)?;
        uninstall_at(&executable, &link)?;
        assert!(!link.try_exists()?);
        fs::write(&link, "someone else's command")?;
        assert_eq!(state_at(&executable, &link)?, AgentCliState::Occupied);
        assert!(install_at(&executable, &link).is_err());
        uninstall_at(&executable, &link)?;
        assert_eq!(fs::read_to_string(&link)?, "someone else's command");
        fs::remove_file(&link)?;
        let other = dir.path().join("another tool");
        fs::write(&other, "another tool")?;
        symlink(&other, &link)?;
        assert_eq!(state_at(&executable, &link)?, AgentCliState::Occupied);
        assert!(install_at(&executable, &link).is_err());
        uninstall_at(&executable, &link)?;
        assert_eq!(fs::read_link(&link)?, other);
        Ok(())
    }
}
