pub(super) fn quote(argument: &str) -> String {
    #[cfg(windows)]
    let quoted = argument.replace('\'', "''");
    #[cfg(not(windows))]
    let quoted = argument.replace('\'', "'\\''");
    format!("'{quoted}'")
}

pub(super) fn command_line(arguments: &[String]) -> String {
    let command = arguments
        .iter()
        .map(|arg| quote(arg))
        .collect::<Vec<_>>()
        .join(" ");
    #[cfg(windows)]
    let command = format!("& {command}");
    command
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn copied_command_preserves_spaces_dollars_and_quotes() -> Result<(), Box<dyn std::error::Error>>
    {
        let argument = "/应用 $USER 'single' \"double\" `hostname`/autoemail";
        #[cfg(not(windows))]
        let output = Command::new("/bin/zsh")
            .args([
                "-c",
                &command_line(&["printf".into(), "%s".into(), argument.into()]),
            ])
            .output()?;
        #[cfg(windows)]
        let output = {
            let shell = std::path::PathBuf::from(
                std::env::var_os("SystemRoot").ok_or("missing SystemRoot")?,
            )
            .join("System32/WindowsPowerShell/v1.0/powershell.exe");
            let command = command_line(&["Write-Output".into(), argument.into()]);
            let script = format!(
                "[Console]::OutputEncoding = \
                [System.Text.UTF8Encoding]::new($false); {command}"
            );
            Command::new(shell)
                .args(["-NoProfile", "-NonInteractive", "-Command", &script])
                .output()?
        };
        assert!(output.status.success());
        assert_eq!(
            String::from_utf8(output.stdout)?.trim_end_matches(['\r', '\n']),
            argument
        );
        Ok(())
    }
}
