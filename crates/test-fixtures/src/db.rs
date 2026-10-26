use sqlx::SqlitePool;
use std::error::Error;
use tempfile::TempDir;

/// Concurrent readers must not block background writes, so use the app's WAL configuration.
/// Callers close the directory after the pool so cleanup failures cannot be ignored.
pub async fn open() -> Result<(TempDir, SqlitePool), Box<dyn Error + Send + Sync>> {
    let dir = tempfile::tempdir()?;
    let db = ::db::open(&dir.path().join("app.db")).await?;
    Ok((dir, db))
}
