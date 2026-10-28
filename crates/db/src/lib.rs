//! The app's SQLite database: connection settings and migrations. Tables and rules are described in
//! docs/data-model.md; each table is written only by the crate that owns it.

use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Once};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use sqlx::SqlitePool;
use sqlx::migrate::{MigrateError, Migrator};
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous};

mod writes;
pub use writes::Writes;

pub static MIGRATOR: LazyLock<Migrator> = LazyLock::new(|| {
    initialize_sqlite();
    sqlx::migrate!()
});

/// Runs SQLite's one-time setup on one thread before any connection opens: its lazy setup races
/// on ARM until https://sqlite.org/src/info/37b83ad2f5245217. Tests reach this through
/// `MIGRATOR`, which SQLx reads before creating a test database. A failure resurfaces when a
/// connection opens, since opening sets SQLite up again.
fn initialize_sqlite() {
    static DONE: Once = Once::new();
    DONE.call_once(|| {
        #[expect(unsafe_code, reason = "SQLite's setup has no safe binding")]
        // SAFETY: it takes no arguments, and `Once` runs it on one thread and publishes the result.
        unsafe {
            libsqlite3_sys::sqlite3_initialize();
        }
        // SQLx creates test databases without db::open, so set their defaults before migrations.
        #[cfg(feature = "testing")]
        {
            #[expect(
                unsafe_code,
                reason = "SQLite's extension registration has no safe binding"
            )]
            // SAFETY: the callback has SQLite's required ABI and lives for the whole process.
            let result = unsafe { libsqlite3_sys::sqlite3_auto_extension(Some(skip_sync)) };
            assert_eq!(
                result,
                libsqlite3_sys::SQLITE_OK,
                "register test SQLite setup"
            );
        }
    });
}

#[cfg(feature = "testing")]
#[expect(unsafe_code, reason = "SQLite calls this extension through its C API")]
unsafe extern "C" fn skip_sync(
    db: *mut libsqlite3_sys::sqlite3,
    error: *mut *mut std::ffi::c_char,
    _api: *const libsqlite3_sys::sqlite3_api_routines,
) -> std::ffi::c_int {
    // SAFETY: SQLite supplies an open connection and an error output it frees after this call.
    // The SQL is a static C string; no callback or callback data is used.
    unsafe {
        libsqlite3_sys::sqlite3_exec(
            db,
            c"PRAGMA synchronous = OFF".as_ptr(),
            None,
            std::ptr::null_mut(),
            error,
        )
    }
}

/// Copies taken before upgrades are named with this and a millisecond timestamp; the newest
/// `BACKUPS_KEPT` are kept.
const BACKUP_PREFIX: &str = "before-upgrade-";
const BACKUPS_KEPT: usize = 3;

#[derive(Debug)]
pub enum OpenError {
    Open(sqlx::Error),
    /// An existing database could not be copied before upgrading, so it was left as it was.
    Backup(sqlx::Error),
    /// The schema could not be brought up to date. `backup` is the copy taken just before, absent
    /// when the database was new.
    Upgrade {
        error: MigrateError,
        backup: Option<PathBuf>,
    },
}

impl std::fmt::Display for OpenError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpenError::Open(e) => write!(f, "open database: {e}"),
            OpenError::Backup(e) => write!(f, "back up database before upgrading: {e}"),
            OpenError::Upgrade { error, backup } => {
                write!(f, "upgrade database: {error}")?;
                match backup {
                    Some(path) => write!(f, " (backup at {})", path.display()),
                    None => Ok(()),
                }
            }
        }
    }
}

impl std::error::Error for OpenError {}

/// Opens the database at `path`, creating it if needed, and brings its schema up to date. An
/// existing database is first copied into `backups` beside it.
pub async fn open(path: &Path) -> Result<SqlitePool, OpenError> {
    initialize_sqlite();
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true)
        .journal_mode(SqliteJournalMode::Wal)
        // With WAL this still survives the app crashing; only a power cut can lose the last
        // commits.
        .synchronous(SqliteSynchronous::Normal)
        .foreign_keys(true)
        .busy_timeout(Duration::from_secs(5));
    let pool = SqlitePoolOptions::new()
        .connect_with(options)
        .await
        .map_err(OpenError::Open)?;
    let backup = if upgrading(&pool).await.map_err(OpenError::Open)? {
        Some(
            back_up(&pool, &path.with_file_name("backups"))
                .await
                .map_err(OpenError::Backup)?,
        )
    } else {
        None
    };
    MIGRATOR
        .run(&pool)
        .await
        .map_err(|error| OpenError::Upgrade { error, backup })?;
    Ok(pool)
}

/// Whether this is an existing database with migrations still to run.
async fn upgrading(pool: &SqlitePool) -> Result<bool, sqlx::Error> {
    let existing: bool = sqlx::query_scalar(
        "SELECT count(*) > 0 FROM sqlite_master WHERE type = 'table' AND name = '_sqlx_migrations'",
    )
    .fetch_one(pool)
    .await?;
    if !existing {
        return Ok(false);
    }
    let applied: Vec<i64> = sqlx::query_scalar("SELECT version FROM _sqlx_migrations")
        .fetch_all(pool)
        .await?;
    Ok(MIGRATOR.iter().any(|m| !applied.contains(&m.version)))
}

/// Copies the database into `dir` and drops the oldest copies beyond `BACKUPS_KEPT`.
async fn back_up(pool: &SqlitePool, dir: &Path) -> Result<PathBuf, sqlx::Error> {
    std::fs::create_dir_all(dir)?;
    // Copied under another name first, so a copy cut short by a full disk or a crash never counts
    // as a backup. VACUUM INTO refuses to overwrite such a leftover.
    let copying = dir.join("copying.db");
    if copying.exists() {
        std::fs::remove_file(&copying)?;
    }
    sqlx::query("VACUUM INTO ?")
        .bind(copying.to_string_lossy())
        .execute(pool)
        .await?;
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis());
    let file = dir.join(format!("{BACKUP_PREFIX}{ms}.db"));
    std::fs::rename(&copying, &file)?;
    let mut older = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        // The new copy is never dropped, even when the clock was set back since the older ones.
        if path != file
            && path
                .file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with(BACKUP_PREFIX))
        {
            older.push(path);
        }
    }
    // Same-length millisecond timestamps sort by age.
    older.sort();
    for old in &older[..older.len().saturating_sub(BACKUPS_KEPT - 1)] {
        std::fs::remove_file(old)?;
    }
    Ok(file)
}
