//! The app's SQLite database: connection settings and migrations. Tables and rules are described in
//! docs/data-model.md; each table is written only by the crate that owns it.

use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Once};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use sqlx::migrate::{MigrateError, Migrator};
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous};
use sqlx::{Connection, SqliteConnection, SqlitePool};

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
        // SQLx sets its wait only after this runs, and the last connection to close locks the
        // database while it folds the log back in, as a durable one does when it goes.
        libsqlite3_sys::sqlite3_busy_timeout(db, BUSY.as_millis() as std::ffi::c_int);
        libsqlite3_sys::sqlite3_exec(
            db,
            c"PRAGMA synchronous = OFF".as_ptr(),
            None,
            std::ptr::null_mut(),
            error,
        )
    }
}

/// How long a connection waits for another's lock.
const BUSY: Duration = Duration::from_secs(5);

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
        .busy_timeout(BUSY);
    // Upgraded on a connection of its own, as `migrate` leaves it outside a transaction.
    let mut conn = SqliteConnection::connect_with(&options)
        .await
        .map_err(OpenError::Open)?;
    let upgrading = upgrading(&mut conn).await.map_err(OpenError::Open)?;
    let backup = if upgrading {
        let backup = back_up(&mut conn, &path.with_file_name("backups"))
            .await
            .map_err(OpenError::Backup)?;
        tracing::info!("upgrading the database, backed up first");
        Some(backup)
    } else {
        None
    };
    migrate(&mut conn)
        .await
        .map_err(|error| OpenError::Upgrade { error, backup })?;
    conn.close().await.map_err(OpenError::Open)?;
    if upgrading {
        let version = MIGRATOR.iter().map(|m| m.version).max();
        tracing::info!(version, "upgraded the database");
    }
    SqlitePoolOptions::new()
        .connect_with(options)
        .await
        .map_err(OpenError::Open)
}

/// Brings the schema up to date with foreign keys off, as rebuilding a table takes
/// (https://sqlite.org/lang_altertable.html#otheralter): dropping the old table then neither
/// deletes nor clears what refers to it. They cannot be turned off inside a transaction, so each
/// migration still commits in one with its record. Every upgrade goes through here, including that
/// of a backup being restored.
pub async fn migrate(conn: &mut SqliteConnection) -> Result<(), MigrateError> {
    sqlx::raw_sql("PRAGMA foreign_keys = OFF")
        .execute(&mut *conn)
        .await?;
    MIGRATOR.run(&mut *conn).await?;
    sqlx::raw_sql("PRAGMA foreign_keys = ON")
        .execute(conn)
        .await?;
    Ok(())
}

/// A connection whose commits are synced to the disk itself, for a change that must not be lost
/// to a power cut once something outside the app acts on it. Closed when dropped, so that the
/// pool's connections keep their faster setting.
pub async fn durable(db: &SqlitePool) -> Result<SqliteConnection, sqlx::Error> {
    let mut conn = db.acquire().await?.detach();
    // fullfsync, on macOS, gets the commit past the drive's own cache.
    sqlx::raw_sql("PRAGMA synchronous = FULL; PRAGMA fullfsync = ON")
        .execute(&mut conn)
        .await?;
    Ok(conn)
}

/// Whether this is an existing database with migrations still to run.
async fn upgrading(conn: &mut SqliteConnection) -> Result<bool, sqlx::Error> {
    let existing: bool = sqlx::query_scalar(
        "SELECT count(*) > 0 FROM sqlite_master WHERE type = 'table' AND name = '_sqlx_migrations'",
    )
    .fetch_one(&mut *conn)
    .await?;
    if !existing {
        return Ok(false);
    }
    let applied: Vec<i64> = sqlx::query_scalar("SELECT version FROM _sqlx_migrations")
        .fetch_all(conn)
        .await?;
    Ok(MIGRATOR.iter().any(|m| !applied.contains(&m.version)))
}

/// Copies the database into `dir` and drops the oldest copies beyond `BACKUPS_KEPT`.
async fn back_up(conn: &mut SqliteConnection, dir: &Path) -> Result<PathBuf, sqlx::Error> {
    std::fs::create_dir_all(dir)?;
    // Copied under another name first, so a copy cut short by a full disk or a crash never counts
    // as a backup. VACUUM INTO refuses to overwrite such a leftover.
    let copying = dir.join("copying.db");
    if copying.exists() {
        std::fs::remove_file(&copying)?;
    }
    sqlx::query("VACUUM INTO ?")
        .bind(copying.to_string_lossy())
        .execute(conn)
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
