//! Kept in its own test binary: a child process that another test spawns at the same moment
//! briefly inherits the lock's open file, so a released lock can still look held.
#![cfg(test)]

use std::error::Error as StdError;

use api::startup::{DataLock, Error};

#[test]
fn hosts_cannot_share_a_data_directory_until_the_lock_is_released() -> Result<(), Box<dyn StdError>>
{
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
