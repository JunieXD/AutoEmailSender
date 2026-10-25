//! Opening the local core without starting background work.

use std::error::Error as StdError;
use std::fs::{File, TryLockError};
use std::path::Path;

use crate::Context;

/// Kept alive by the host so that no other host can write the same data directory.
pub struct DataLock(#[expect(dead_code, reason = "only kept open")] File);

impl DataLock {
    pub fn acquire(dir: &Path) -> Result<Self, Error> {
        std::fs::create_dir_all(dir).map_err(Error::DataDir)?;
        let file = File::options()
            .write(true)
            .create(true)
            .truncate(false)
            .open(dir.join("app.lock"))
            .map_err(Error::DataDir)?;
        match file.try_lock() {
            Ok(()) => Ok(Self(file)),
            Err(TryLockError::WouldBlock) => Err(Error::InUse),
            Err(TryLockError::Error(e)) => Err(Error::DataDir(e)),
        }
    }
}

/// The host must hold the data directory lock before opening the core.
pub async fn open(dir: &Path) -> Result<Context, Error> {
    let db = db::open(&dir.join("app.db"))
        .await
        .map_err(Error::Database)?;
    let net = net::Net::new(Vec::new()).map_err(Error::Net)?;
    Ok(Context {
        db,
        net,
        materials: dir.join("materials"),
        sender: Default::default(),
        receiver: Default::default(),
        matcher: Default::default(),
        reply_processor: Default::default(),
        crawler: Default::default(),
        generator: Default::default(),
        model_limit: Default::default(),
    })
}

#[derive(Debug)]
pub enum Error {
    InUse,
    DataDir(std::io::Error),
    Database(db::OpenError),
    Net(Box<dyn StdError + Send + Sync>),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InUse => write!(f, "data directory already in use"),
            Self::DataDir(e) => write!(f, "data directory: {e}"),
            Self::Database(e) => write!(f, "{e}"),
            Self::Net(e) => write!(f, "network: {e}"),
        }
    }
}

impl StdError for Error {}

impl Error {
    pub fn message(&self) -> String {
        use db::OpenError;
        match self {
            Self::InUse => {
                "数据目录正在使用中，请先关闭这个工作树的桌面开发版或浏览器预览，再重试。".into()
            }
            Self::DataDir(e) => format!("数据目录无法使用：{e}"),
            Self::Net(e) => format!("网络连接无法初始化：{e}"),
            Self::Database(OpenError::Open(e)) => format!("数据库无法打开：{e}"),
            Self::Database(OpenError::Backup(e)) => {
                format!("升级数据库前未能备份，为保护数据未做升级：{e}")
            }
            Self::Database(OpenError::Upgrade {
                error,
                backup: Some(path),
            }) => format!(
                "数据库升级失败：{error}。升级前的数据库已备份到 {}。",
                path.display()
            ),
            Self::Database(OpenError::Upgrade {
                error,
                backup: None,
            }) => {
                format!("数据库创建失败：{error}")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hosts_cannot_share_a_data_directory_until_the_lock_is_released()
    -> Result<(), Box<dyn StdError>> {
        let dir = tempfile::tempdir()?;
        let first = DataLock::acquire(dir.path())?;
        let error = match DataLock::acquire(dir.path()) {
            Err(error) => error,
            Ok(_) => return Err("a second host acquired the data directory".into()),
        };
        assert!(matches!(error, Error::InUse));
        assert!(error.message().contains("数据目录正在使用中"));
        drop(first);
        let _next = DataLock::acquire(dir.path())?;
        Ok(())
    }
}
