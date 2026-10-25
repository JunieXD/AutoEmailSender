//! Replace an export only after its complete contents have reached the sibling temporary file.

use std::fs::File;
use std::io;
use std::path::Path;

pub fn replace(path: &Path, write: impl FnOnce(&mut File) -> io::Result<()>) -> io::Result<()> {
    let mut file = tempfile::Builder::new()
        .prefix(".aes-export-")
        .tempfile_in(path.parent().unwrap_or_else(|| Path::new(".")))?;
    write(file.as_file_mut())?;
    file.as_file().sync_all()?;
    let temporary = file.into_temp_path();
    std::fs::rename(&temporary, path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn failed_write_preserves_the_previous_export_and_cleans_up() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("导师.csv");
        std::fs::write(&target, b"original complete export").unwrap();
        let result = replace(&target, |file| {
            file.write_all(b"incomplete")?;
            Err(io::Error::other("disk full"))
        });
        assert!(result.is_err());
        assert_eq!(std::fs::read(&target).unwrap(), b"original complete export");
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
        replace(&target, |file| file.write_all(b"complete replacement")).unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"complete replacement");
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn failed_rename_cleans_up_without_changing_the_target() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("occupied");
        std::fs::create_dir(&target).unwrap();
        let original = target.join("original.csv");
        std::fs::write(&original, b"original").unwrap();
        assert!(replace(&target, |file| file.write_all(b"replacement")).is_err());
        assert_eq!(std::fs::read(original).unwrap(), b"original");
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    }
}
