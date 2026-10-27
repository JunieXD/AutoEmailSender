use std::path::PathBuf;

pub(super) fn plain_path(path: PathBuf) -> PathBuf {
    #[cfg(windows)]
    {
        use std::ffi::OsString;
        use std::path::{Component, Prefix};

        let mut components = path.components();
        let prefix = match components.next() {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::VerbatimDisk(drive) => OsString::from(format!("{}:", char::from(drive))),
                Prefix::VerbatimUNC(server, share) => {
                    let mut prefix = OsString::from(r"\\");
                    prefix.push(server);
                    prefix.push(r"\");
                    prefix.push(share);
                    prefix
                }
                _ => return path,
            },
            _ => return path,
        };
        PathBuf::from(prefix).join(components.as_path())
    }
    #[cfg(not(windows))]
    path
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn exposes_drive_and_unc_paths_without_extended_prefixes() {
        for (source, expected) in [
            (
                r"\\?\C:\Users\Student\App\autoemail.exe",
                r"C:\Users\Student\App\autoemail.exe",
            ),
            (
                r"\\?\UNC\server\share\App\autoemail.exe",
                r"\\server\share\App\autoemail.exe",
            ),
            (
                r"C:\Users\中文 用户\App\autoemail.exe",
                r"C:\Users\中文 用户\App\autoemail.exe",
            ),
        ] {
            assert_eq!(plain_path(PathBuf::from(source)), PathBuf::from(expected));
        }
    }
}
