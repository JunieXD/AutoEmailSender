//! Local snapshots and restart-time replacement; archive contents are validated at the boundary.

mod archive;
pub mod cloud;

use std::fs::{self, File};
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};
use sqlx::migrate::MigrateError;
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode};
use sqlx::{Connection, SqliteConnection, SqlitePool};
use ts_rs::TS;

const APPLICATION: &str = "io.github.juniexd.autoemailsender";
const PENDING: &str = "restore.pending";
const BACKUP_PREFIX: &str = "before-restore-";

type Result<T> = std::result::Result<T, Error>;

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Io {
        operation: &'static str,
        source: io::Error,
    },
    Archive {
        detail: &'static str,
        source: Box<dyn std::error::Error + Send + Sync>,
    },
    Database(sqlx::Error),
    Migration(MigrateError),
    Worker(tokio::task::JoinError),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) => f.write_str(message),
            Self::Io { operation, source } => write!(f, "backup file ({operation}): {source}"),
            Self::Archive { detail, source } => write!(f, "backup archive ({detail}): {source}"),
            Self::Database(e) => write!(f, "backup database: {e}"),
            Self::Migration(e) => write!(f, "backup migration: {e}"),
            Self::Worker(e) => write!(f, "backup worker: {e}"),
        }
    }
}
impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Invalid(_) => None,
            Self::Io { source, .. } => Some(source),
            Self::Archive { source, .. } => Some(source.as_ref()),
            Self::Database(e) => Some(e),
            Self::Migration(e) => Some(e),
            Self::Worker(e) => Some(e),
        }
    }
}
impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Self::Io {
            operation: "file access",
            source: e,
        }
    }
}
impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Self::Database(e)
    }
}
impl From<tokio::task::JoinError> for Error {
    fn from(e: tokio::task::JoinError) -> Self {
        Self::Worker(e)
    }
}
impl Error {
    pub fn message(&self) -> String {
        match self {
            Self::Invalid(message) => message.clone(),
            Self::Archive { detail, .. } => archive::invalid(detail).message(),
            Self::Io { .. } => {
                "备份文件无法读写。请检查文件是否存在、磁盘空间和目录权限后重试。".into()
            }
            Self::Database(_) => "备份数据库无法读取或处理，文件可能已损坏。请换一份备份重试；\
                仍然失败时导出日志发给开发者。"
                .into(),
            Self::Migration(_) => "这份备份的数据库和本应用的对不上，不能恢复。".into(),
            Self::Worker(_) => "备份处理未能完成，请重试；仍然失败时导出日志发给开发者。".into(),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Manifest {
    application: String,
    app_version: String,
    database_version: i64,
    exported_at: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct RestoreInfo {
    /// UTC milliseconds.
    pub exported_at: i64,
    pub app_version: String,
    pub mentor_count: i64,
    pub email_count: i64,
    pub backup_directory: String,
}

fn database_version() -> i64 {
    db::MIGRATOR.iter().map(|m| m.version).max().unwrap_or(0)
}

/// The caller serializes backup commands. Material files are immutable; a concurrent deletion
/// can fail this export, but never produce an archive with missing files.
pub async fn export(db: &SqlitePool, materials: &Path, target: &Path, now: i64) -> Result<()> {
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let stage = tempfile::Builder::new()
        .prefix(".aes-snapshot-")
        .tempdir_in(parent)?;
    let path = stage.path().join("app.db");
    let snapshot = snapshot(db, &path).await?;
    let manifest = Manifest {
        application: APPLICATION.into(),
        app_version: env!("CARGO_PKG_VERSION").into(),
        database_version: snapshot.version,
        exported_at: now,
    };
    let ids: Vec<i64> = snapshot.materials.iter().map(|(id, _)| *id).collect();
    let (materials, target) = (materials.to_owned(), target.to_owned());
    tokio::task::spawn_blocking(move || {
        archive::write(&target, &path, &materials, &ids, &manifest)
    })
    .await??;
    Ok(())
}

/// What a snapshot holds, besides the database file itself.
struct Snapshot {
    version: i64,
    /// Ids and file names.
    materials: Vec<(i64, String)>,
    mentors: i64,
    emails: i64,
}

/// Copies the database to the new file at `path` and scrubs the copy.
async fn snapshot(db: &SqlitePool, path: &Path) -> Result<Snapshot> {
    let name = path
        .to_str()
        .ok_or_else(|| Error::Invalid("保存位置不能用于 SQLite 备份，请选择其他目录。".into()))?;
    sqlx::query("VACUUM INTO ?").bind(name).execute(db).await?;
    let mut conn = SqliteConnection::connect_with(
        &SqliteConnectOptions::new()
            .filename(path)
            .journal_mode(SqliteJournalMode::Delete),
    )
    .await?;
    let snapshot = async {
        scrub(&mut conn).await?;
        Ok::<_, Error>(Snapshot {
            version: sqlx::query_scalar(
                "SELECT max(version) FROM _sqlx_migrations WHERE success = 1",
            )
            .fetch_one(&mut conn)
            .await?,
            materials: sqlx::query_as("SELECT id, file_name FROM material ORDER BY id")
                .fetch_all(&mut conn)
                .await?,
            mentors: sqlx::query_scalar("SELECT count(*) FROM mentor")
                .fetch_one(&mut conn)
                .await?,
            emails: sqlx::query_scalar("SELECT count(*) FROM email")
                .fetch_one(&mut conn)
                .await?,
        })
    }
    .await;
    conn.close().await?;
    snapshot
}

async fn scrub(conn: &mut SqliteConnection) -> Result<()> {
    sqlx::raw_sql(
        r#"UPDATE mailbox
        SET secret = NULL;
        UPDATE model_config
        SET api_key = CASE WHEN provider = 'custom' AND api_key = '' THEN '' END;
        UPDATE account SET token = NULL, backup_key = NULL, library_checked_at = NULL;
        UPDATE profile SET cloud_backup = 0, last_backup_at = NULL, cloud_backup_device = NULL,
          cloud_backup_commit = NULL, cloud_backup_replace = NULL, cloud_backup_failed_at = NULL,
          cloud_backup_error = NULL;
        DELETE FROM cloud_backup_material;
        UPDATE crawl_page
        SET text = CASE WHEN status = 'success' THEN '' END;
        VACUUM;"#,
    )
    .execute(conn)
    .await?;
    Ok(())
}

pub async fn prepare(dir: &Path, source: &Path) -> Result<RestoreInfo> {
    let source = source.to_owned();
    stage(dir, move |stage| archive::unpack(&source, stage)).await
}

/// Stages only a checked restore: `fill` writes app.db and materials/ into the empty stage.
/// Failed checks leave no prepared restore to confirm.
async fn stage(
    dir: &Path,
    fill: impl FnOnce(&Path) -> Result<Manifest> + Send + 'static,
) -> Result<RestoreInfo> {
    ensure_not_pending(dir)?;
    cancel(dir)?;
    let stage = dir.join("restore");
    let destination = stage.clone();
    let unpacked = tokio::task::spawn_blocking(move || fill(&destination)).await?;
    let result = match unpacked {
        Ok(mut manifest) => {
            async {
                let info = inspect(dir, &stage, &manifest).await?;
                // Treat imported credentials as untrusted even if the archive claims to be ours.
                let mut conn = SqliteConnection::connect_with(
                    &SqliteConnectOptions::new()
                        .filename(stage.join("app.db"))
                        .journal_mode(SqliteJournalMode::Delete),
                )
                .await?;
                let scrubbed = scrub(&mut conn).await;
                conn.close().await?;
                scrubbed?;
                // Confirmation checks the staged database, which has already been upgraded.
                manifest.database_version = database_version();
                serde_json::to_writer(File::create(stage.join("manifest.json"))?, &manifest)
                    .map_err(io::Error::other)?;
                Ok(info)
            }
            .await
        }
        Err(e) => Err(e),
    };
    if result.is_err() {
        remove_dir(&stage)?;
    }
    result
}

async fn inspect(dir: &Path, stage: &Path, manifest: &Manifest) -> Result<RestoreInfo> {
    if manifest.application != APPLICATION {
        return Err(Error::Invalid(
            "这不是 Auto Email Sender 导出的备份，请选择本应用的备份文件。".into(),
        ));
    }
    if manifest.database_version > database_version() {
        return Err(Error::Invalid(
            "备份的数据库版本比当前应用新，请先升级应用再恢复。".into(),
        ));
    }
    let mut conn = SqliteConnection::connect_with(
        &SqliteConnectOptions::new()
            .filename(stage.join("app.db"))
            .read_only(true),
    )
    .await?;
    let result = inspect_version(&mut conn, manifest.database_version).await;
    conn.close().await?;
    result?;

    let mut conn = SqliteConnection::connect_with(
        &SqliteConnectOptions::new()
            .filename(stage.join("app.db"))
            .journal_mode(SqliteJournalMode::Delete),
    )
    .await?;
    let result = async {
        db::MIGRATOR
            .run(&mut conn)
            .await
            .map_err(Error::Migration)?;
        inspect_schema(&mut conn).await?;
        inspect_database(&mut conn, dir, stage, manifest).await
    }
    .await;
    conn.close().await?;
    result
}

async fn inspect_version(conn: &mut SqliteConnection, manifest_version: i64) -> Result<()> {
    let integrity: Vec<String> = sqlx::query_scalar("PRAGMA integrity_check")
        .fetch_all(&mut *conn)
        .await?;
    if integrity != ["ok"] {
        return Err(Error::Invalid(
            "备份数据库已损坏，请换一份备份文件。".into(),
        ));
    }
    let version: Option<i64> = sqlx::query_scalar("SELECT max(version) FROM _sqlx_migrations")
        .fetch_one(&mut *conn)
        .await?;
    if version.is_some_and(|v| v > database_version()) {
        return Err(Error::Invalid(
            "备份的数据库版本比当前应用新，请先升级应用再恢复。".into(),
        ));
    }
    if version != Some(manifest_version) {
        return Err(Error::Invalid(
            "备份记录的数据库版本不一致或不受支持，请选择完整的本应用备份。".into(),
        ));
    }
    Ok(())
}

type SchemaEntry = (String, String, String, Option<String>);

async fn database_schema(conn: &mut SqliteConnection) -> Result<Vec<SchemaEntry>> {
    // SQLite reserves the literal sqlite_ prefix for internal objects, including ANALYZE tables.
    Ok(sqlx::query_as(
        r"SELECT type, name, tbl_name, sql FROM sqlite_schema
          WHERE name NOT LIKE 'sqlite\_%' ESCAPE '\' ORDER BY name",
    )
    .fetch_all(conn)
    .await?)
}

async fn inspect_schema(conn: &mut SqliteConnection) -> Result<()> {
    let actual = database_schema(conn).await?;
    let mut expected = SqliteConnection::connect("sqlite::memory:").await?;
    let result = async {
        db::MIGRATOR
            .run(&mut expected)
            .await
            .map_err(Error::Migration)?;
        if actual != database_schema(&mut expected).await? {
            return Err(Error::Invalid(
                "这份备份的数据库和本应用的对不上，不能恢复。".into(),
            ));
        }
        Ok(())
    }
    .await;
    expected.close().await?;
    result
}

async fn inspect_database(
    conn: &mut SqliteConnection,
    dir: &Path,
    stage: &Path,
    manifest: &Manifest,
) -> Result<RestoreInfo> {
    let violation = sqlx::query("PRAGMA foreign_key_check")
        .fetch_optional(&mut *conn)
        .await?;
    if violation.is_some() {
        return Err(Error::Invalid(
            "备份数据库的关联数据不完整，请换一份备份文件。".into(),
        ));
    }
    let materials: Vec<(i64, i64)> = sqlx::query_as("SELECT id, size FROM material")
        .fetch_all(&mut *conn)
        .await?;
    for (id, size) in materials {
        let path = material::file(&stage.join("materials"), id);
        if !path.is_file() || fs::metadata(path)?.len() != u64::try_from(size).unwrap_or(u64::MAX) {
            return Err(Error::Invalid(format!(
                "备份缺少材料文件或材料大小不正确（编号 {id}），请重新导出完整备份。"
            )));
        }
    }
    let mentor_count = sqlx::query_scalar("SELECT count(*) FROM mentor")
        .fetch_one(&mut *conn)
        .await?;
    let email_count = sqlx::query_scalar("SELECT count(*) FROM email")
        .fetch_one(&mut *conn)
        .await?;
    Ok(RestoreInfo {
        exported_at: manifest.exported_at,
        app_version: manifest.app_version.clone(),
        mentor_count,
        email_count,
        backup_directory: dir.join("backups").display().to_string(),
    })
}

pub fn cancel(dir: &Path) -> Result<()> {
    ensure_not_pending(dir)?;
    remove_dir(&dir.join("restore"))?;
    Ok(())
}

fn ensure_not_pending(dir: &Path) -> Result<()> {
    if dir.join(PENDING).try_exists()? {
        return Err(Error::Invalid("恢复已确认，请重启应用完成恢复。".into()));
    }
    Ok(())
}

/// The live database remains untouched until the next process has acquired its data lock.
pub async fn confirm(db: &SqlitePool, dir: &Path, now: i64) -> Result<()> {
    ensure_not_pending(dir)?;
    let stage = dir.join("restore");
    let manifest = archive::manifest(&stage.join("manifest.json")).map_err(|e| match e {
        Error::Io { source: e, .. } if e.kind() == io::ErrorKind::NotFound => {
            Error::Invalid("请先选择并检查备份文件，再确认恢复。".into())
        }
        e => e,
    })?;
    inspect(dir, &stage, &manifest).await?;
    let backups = dir.join("backups");
    fs::create_dir_all(&backups)?;
    let target = backups.join(format!("{BACKUP_PREFIX}{now}.zip"));
    export(db, &dir.join("materials"), &target, now).await?;
    let dir = dir.to_owned();
    tokio::task::spawn_blocking(move || {
        prune(&dir.join("backups"), &target)?;
        // Sync every staged file before making the recovery intent durable.
        sync_stage(&dir.join("restore"))?;
        let mut marker = tempfile::NamedTempFile::new_in(&dir)?;
        use std::io::Write;
        marker.write_all(b"pending")?;
        marker.as_file().sync_all()?;
        marker.persist(dir.join(PENDING)).map_err(|e| e.error)?;
        sync_dir(&dir)?;
        Ok::<_, Error>(())
    })
    .await??;
    Ok(())
}

/// Keeps `latest` and the two newest others. `latest` is kept by name, as the clock may have been
/// put back since the others were made.
fn prune(dir: &Path, latest: &Path) -> Result<()> {
    let mut backups = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        if entry.path() != latest
            && let Some(name) = entry.file_name().to_str()
            && let Some(at) = name
                .strip_prefix(BACKUP_PREFIX)
                .and_then(|n| n.strip_suffix(".zip"))
                .and_then(|n| n.parse::<i64>().ok())
            && entry.file_type()?.is_file()
        {
            backups.push((at, entry.path()));
        }
    }
    backups.sort_by_key(|(at, _)| *at);
    let discard = backups.len().saturating_sub(2);
    for (_, path) in backups.into_iter().take(discard) {
        fs::remove_file(path)?;
    }
    sync_dir(dir)?;
    Ok(())
}

/// Run with the data lock held and no open database. Each rename is its own durable checkpoint:
/// a missing staged component means it has already been installed, never that it should be reset.
pub fn apply_pending(dir: &Path) -> Result<bool> {
    if !dir.join(PENDING).try_exists()? {
        // Cleanup can itself have been interrupted after removing the intent marker.
        remove_dir(&dir.join("restore-old"))?;
        return Ok(false);
    }
    let stage = dir.join("restore");
    let old = dir.join("restore-old");
    fs::create_dir_all(&old)?;
    for name in ["app.db", "materials"] {
        if stage.join(name).try_exists()? {
            if !old.join(name).try_exists()? && dir.join(name).try_exists()? {
                rename(&dir.join(name), &old.join(name))?;
            }
            if name == "app.db" {
                for sidecar in ["app.db-wal", "app.db-shm"] {
                    if dir.join(sidecar).try_exists()? {
                        rename(&dir.join(sidecar), &old.join(sidecar))?;
                    }
                }
            }
            rename(&stage.join(name), &dir.join(name))?;
        } else if !dir.join(name).try_exists()? {
            return Err(Error::Invalid(
                "恢复暂存文件不完整，现有数据已保留，请导出日志联系开发者。".into(),
            ));
        }
    }
    fs::remove_file(dir.join(PENDING))?;
    sync_dir(dir)?;
    remove_dir(&stage)?;
    remove_dir(&old)?;
    Ok(true)
}

fn rename(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)?;
    if let Some(parent) = from.parent() {
        sync_dir(parent)?;
    }
    if let Some(parent) = to.parent() {
        sync_dir(parent)?;
    }
    Ok(())
}

fn remove_dir(path: &Path) -> io::Result<()> {
    match fs::remove_dir_all(path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

fn sync_stage(dir: &Path) -> io::Result<()> {
    for name in ["app.db", "manifest.json"] {
        File::options()
            .write(true)
            .open(dir.join(name))?
            .sync_all()?;
    }
    let materials = dir.join("materials");
    for entry in fs::read_dir(&materials)? {
        File::options()
            .write(true)
            .open(entry?.path())?
            .sync_all()?;
    }
    sync_dir(&materials)?;
    sync_dir(dir)
}

fn sync_dir(dir: &Path) -> io::Result<()> {
    // Windows does not expose directory fsync through std; file data is synced before renaming.
    #[cfg(unix)]
    File::open(dir)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = dir;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_interrupted_replacement_resumes_without_old_sidecars() {
        let moves = [
            ("app.db", "restore-old/app.db"),
            ("app.db-wal", "restore-old/app.db-wal"),
            ("app.db-shm", "restore-old/app.db-shm"),
            ("restore/app.db", "app.db"),
            ("materials", "restore-old/materials"),
            ("restore/materials", "materials"),
        ];
        for interrupted_after in 0..=moves.len() {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            fs::create_dir_all(root.join("restore/materials")).unwrap();
            fs::create_dir(root.join("materials")).unwrap();
            fs::create_dir(root.join("restore-old")).unwrap();
            for (name, data) in [
                ("app.db", "old database"),
                ("app.db-wal", "old wal"),
                ("app.db-shm", "old shm"),
                ("materials/1", "old material"),
                ("restore/app.db", "restored database"),
                ("restore/materials/1", "restored material"),
                (PENDING, "pending"),
            ] {
                fs::write(root.join(name), data).unwrap();
            }
            for (from, to) in moves.iter().take(interrupted_after) {
                rename(&root.join(from), &root.join(to)).unwrap();
            }
            assert!(apply_pending(root).unwrap());
            assert!(!apply_pending(root).unwrap());
            assert_eq!(
                fs::read_to_string(root.join("app.db")).unwrap(),
                "restored database"
            );
            assert_eq!(
                fs::read_to_string(root.join("materials/1")).unwrap(),
                "restored material"
            );
            for removed in [
                PENDING,
                "restore",
                "restore-old",
                "app.db-wal",
                "app.db-shm",
            ] {
                assert!(
                    !root.join(removed).exists(),
                    "{interrupted_after}: {removed}"
                );
            }
        }
    }
}
