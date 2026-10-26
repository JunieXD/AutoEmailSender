use std::fs;
use std::io;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::CallError;

pub(in crate::agent_settings) fn location(_: &Path) -> Result<PathBuf, CallError> {
    Ok(PathBuf::from("/usr/local/bin/autoemail"))
}

pub(in crate::agent_settings) fn installed(executable: &Path) -> Result<bool, CallError> {
    matches(&location(executable)?, executable)
}

fn matches(link: &Path, executable: &Path) -> Result<bool, CallError> {
    match fs::read_link(link) {
        Ok(target) => Ok(target == executable),
        Err(e)
            if matches!(
                e.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::InvalidInput
            ) =>
        {
            Ok(false)
        }
        Err(e) => Err(error(e)),
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
    match symlink(executable, link) {
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => match fs::read_link(link) {
            Ok(target) if target == executable => Ok(()),
            Ok(_) => Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "同名位置已被其他文件或链接占用，未覆盖",
            )),
            Err(error) if error.kind() == io::ErrorKind::InvalidInput => Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "同名位置已被其他文件占用，未覆盖",
            )),
            Err(error) => Err(error),
        },
        result => result,
    }
}

fn uninstall_at(executable: &Path, link: &Path) -> io::Result<()> {
    match fs::read_link(link) {
        Ok(target) if target == executable => fs::remove_file(link),
        Ok(_) => Ok(()),
        Err(e)
            if matches!(
                e.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::InvalidInput
            ) =>
        {
            Ok(())
        }
        Err(e) => Err(e),
    }
}

fn quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
}

fn authorize(executable: &Path, link: &Path, install: bool) -> Result<(), CallError> {
    let target = quote(executable);
    let link = quote(link);
    // Recheck ownership after authorization: the filesystem may have changed while waiting.
    let command = if install {
        format!(
            "if [ -e {link} ] || [ -L {link} ]; then \
            [ -L {link} ] && [ \"$(/usr/bin/readlink {link})\" = {target} ] || exit 17; \
            else /bin/mkdir -p /usr/local/bin && /bin/ln -s {target} {link}; fi"
        )
    } else {
        format!(
            "if [ -L {link} ] && [ \"$(/usr/bin/readlink {link})\" = {target} ]; \
            then /usr/bin/unlink {link}; fi"
        )
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
    fn install_and_uninstall_only_our_link() -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let executable = dir.path().join("应用 ' 中文/autoemail");
        fs::create_dir_all(executable.parent().ok_or("no parent")?)?;
        fs::write(&executable, "temporary binary")?;
        let link = dir.path().join("autoemail");
        install_at(&executable, &link)?;
        install_at(&executable, &link)?;
        assert!(matches(&link, &executable).map_err(|e| e.message)?);
        uninstall_at(&executable, &link)?;
        uninstall_at(&executable, &link)?;
        assert!(!link.try_exists()?);
        fs::write(&link, "someone else's command")?;
        assert!(install_at(&executable, &link).is_err());
        uninstall_at(&executable, &link)?;
        assert_eq!(fs::read_to_string(&link)?, "someone else's command");
        fs::remove_file(&link)?;
        symlink("another tool", &link)?;
        assert!(install_at(&executable, &link).is_err());
        uninstall_at(&executable, &link)?;
        assert_eq!(fs::read_link(&link)?, Path::new("another tool"));
        Ok(())
    }
}
