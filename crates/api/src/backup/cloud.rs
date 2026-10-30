//! Cloud backup (docs/development.md#云端备份): the scrubbed database and each material, compressed
//! and encrypted on this computer, kept as objects on the official server.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use chacha20poly1305::aead::{Aead, Payload};
use chacha20poly1305::{KeyInit, XChaCha20Poly1305, XNonce};
use cloud_api::backup::{self as wire, LIMIT, OBJECTS, RETENTION};
use cloud_api::{ApiError, Empty, ErrorCode, path};
use reqwest::{Method, header};
use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

use super::{APPLICATION, Manifest as Archive, RestoreInfo, Snapshot};
use crate::Context;

const DAY: i64 = 24 * 60 * 60 * 1000;
const LEVEL: i32 = 3;
const MAGIC: &[u8; 8] = b"AESCBK\0\0";
const VERSION: u8 = 1;
/// Magic, version and kind.
const HEADER: usize = 10;
const NONCE: usize = 24;
/// Long enough for a 20 MB material on a slow connection.
const TRANSFER: std::time::Duration = std::time::Duration::from_secs(600);

#[derive(Clone, Copy)]
enum Kind {
    Manifest = 1,
    Database = 2,
    Material = 3,
}

/// Lives in the data directory; one backup or restore runs at a time.
pub struct CloudBackup {
    dir: PathBuf,
    running: tokio::sync::Mutex<()>,
}

impl CloudBackup {
    pub fn new(data: &Path) -> Self {
        Self {
            dir: data.join("cloud-backup"),
            running: Default::default(),
        }
    }
}

#[derive(Debug)]
pub enum Error {
    Account(account::Error),
    Local(super::Error),
    Database(sqlx::Error),
    Io(io::Error),
    NeedsPassword,
    /// The account changed while the operation waited for the server.
    AccountChanged,
    Disabled,
    SponsorRequired,
    OtherDevice,
    TooLarge(String),
    NotFound,
    /// The manifest is not readable with this account's key: the password was reset since.
    OldPassword,
    Corrupted,
    Newer,
}

impl From<account::Error> for Error {
    fn from(e: account::Error) -> Self {
        match e {
            account::Error::Server(ApiError {
                code: ErrorCode::SponsorRequired,
                ..
            }) => Self::SponsorRequired,
            e => Self::Account(e),
        }
    }
}
impl From<super::Error> for Error {
    fn from(e: super::Error) -> Self {
        Self::Local(e)
    }
}
impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Self::Database(e)
    }
}
impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Self::Io(e)
    }
}
impl From<tokio::task::JoinError> for Error {
    fn from(e: tokio::task::JoinError) -> Self {
        Self::Local(super::Error::Worker(e))
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Account(e) => write!(f, "cloud backup: {e}"),
            Self::Local(e) => write!(f, "cloud backup: {e}"),
            Self::Database(e) => write!(f, "cloud backup database: {e}"),
            Self::Io(e) => write!(f, "cloud backup file: {e}"),
            Self::NeedsPassword => write!(f, "cloud backup: no backup key"),
            Self::AccountChanged => write!(f, "cloud backup: account changed"),
            Self::Disabled => write!(f, "cloud backup: turned off"),
            Self::SponsorRequired => write!(f, "cloud backup: not sponsoring"),
            Self::OtherDevice => write!(f, "cloud backup: from another computer"),
            Self::TooLarge(_) => write!(f, "cloud backup: over the limit"),
            Self::NotFound => write!(f, "cloud backup: none on the server"),
            Self::OldPassword => write!(f, "cloud backup: manifest not readable with this key"),
            Self::Corrupted => write!(f, "cloud backup: object not authentic"),
            Self::Newer => write!(f, "cloud backup: newer object format"),
        }
    }
}
impl std::error::Error for Error {}

impl Error {
    pub fn message(&self) -> String {
        match self {
            Self::Account(e) => e.message(),
            Self::Local(e) => e.message(),
            Self::Database(_) | Self::Io(_) => {
                "云端备份未能完成，请重试；仍然失败时导出日志发给开发者。".into()
            }
            Self::NeedsPassword => "请先输入账号密码，算出这台电脑的备份钥匙。".into(),
            Self::AccountChanged => "登录的账号刚刚变了，请重试。".into(),
            Self::Disabled => "云端备份没有开启。".into(),
            Self::SponsorRequired => "赞助已到期，云端备份已停止。".into(),
            Self::OtherDevice => "云端备份来自另一台电脑，这台电脑不再自动备份。".into(),
            Self::TooLarge(message) => message.clone(),
            Self::NotFound => "云端没有备份。".into(),
            Self::OldPassword => {
                "云端备份是用旧密码加密的，解不开了。在还有数据的电脑上用新密码登录，确认替换后重新备份。"
                    .into()
            }
            Self::Corrupted => "云端备份已损坏，无法恢复。请在还有数据的电脑上重新备份。".into(),
            Self::Newer => "这份备份来自更新版本的应用，请先升级应用再恢复。".into(),
        }
    }
}

type Result<T> = std::result::Result<T, Error>;

/// What the backup page shows without asking the server.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudBackup")]
pub struct Status {
    pub enabled: bool,
    pub running: bool,
    /// Logged in before cloud backup existed, so the password must derive the key once.
    pub needs_password: bool,
    /// The last successful backup, whether it uploaded or found nothing changed.
    pub last_backup_at: Option<i64>,
    /// The `committedAt` of the cloud's backup the user agreed to replace, until it is replaced.
    pub replace: Option<i64>,
    pub failure: Option<Failure>,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "CloudBackupFailure")]
pub struct Failure {
    /// Failing since then.
    pub since: i64,
    pub message: String,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudBackupServer")]
pub struct Server {
    pub sponsor_until: Option<i64>,
    /// When a backup whose sponsorship has ended is deleted.
    pub delete_at: Option<i64>,
    pub backup: Option<Backup>,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudBackupCopy")]
pub struct Backup {
    pub committed_at: i64,
    pub size: u64,
    pub this_device: bool,
    /// None when this account's key cannot read it, such as before a password reset.
    pub summary: Option<Summary>,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudBackupSummary")]
pub struct Summary {
    pub device_name: Option<String>,
    pub exported_at: i64,
    pub mentor_count: i64,
    pub email_count: i64,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
#[ts(rename = "EnableCloudBackup")]
pub struct Enable {
    /// For a login without a backup key.
    #[ts(optional)]
    pub password: Option<String>,
    /// The `committedAt` of another computer's backup the user agreed to replace.
    #[ts(optional)]
    pub replace: Option<i64>,
}

#[derive(Debug, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
#[ts(rename = "CloudBackupEnabled")]
pub enum Enabled {
    Enabled,
    /// Another computer's backup is in the cloud; enabling replaces it.
    Replace {
        backup: Backup,
    },
}

/// The encrypted manifest of a backup.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Contents {
    app_version: String,
    database_version: i64,
    exported_at: i64,
    mentor_count: i64,
    email_count: i64,
    device_name: Option<String>,
    database: String,
    /// Material ids and their objects.
    materials: Vec<(i64, String)>,
}

pub async fn status(ctx: &Context) -> Result<Status> {
    let profile = sqlx::query!(
        r#"SELECT cloud_backup AS "enabled: bool", last_backup_at, cloud_backup_replace,
            cloud_backup_failed_at, cloud_backup_error,
            (SELECT backup_key IS NULL FROM account WHERE token IS NOT NULL)
                AS "needs_password: bool"
        FROM profile"#
    )
    .fetch_one(&ctx.db)
    .await?;
    Ok(Status {
        enabled: profile.enabled,
        running: ctx.cloud_backup.running.try_lock().is_err(),
        needs_password: profile.needs_password.unwrap_or(false),
        last_backup_at: profile.last_backup_at,
        replace: profile.cloud_backup_replace,
        failure: profile
            .cloud_backup_failed_at
            .zip(profile.cloud_backup_error)
            .map(|(since, message)| Failure { since, message }),
    })
}

pub async fn server(ctx: &Context, now: i64) -> Result<Server> {
    let login = account::saved_login(&ctx.db).await?;
    let status = remote(ctx, &login).await?;
    let device = sqlx::query_scalar!("SELECT cloud_backup_device FROM profile")
        .fetch_one(&ctx.db)
        .await?;
    let backup = match &status.current {
        Some(current) => Some(describe(ctx, &login, current, device.as_deref()).await?),
        None => None,
    };
    Ok(Server {
        delete_at: status
            .sponsor_until
            .filter(|until| backup.is_some() && *until <= now)
            .map(|until| until + RETENTION),
        sponsor_until: status.sponsor_until,
        backup,
    })
}

pub async fn enable(ctx: &Context, input: Enable, now: i64) -> Result<Enabled> {
    if let Some(password) = input.password {
        let (account::State::LoggedIn { email } | account::State::Expired { email }) =
            account::state(&ctx.db).await?
        else {
            return Err(account::Error::SignedOut.into());
        };
        ctx.account.login(&ctx.db, &email, password).await?;
    }
    let login = account::saved_login(&ctx.db).await?;
    if login.backup_key.is_none() {
        return Err(Error::NeedsPassword);
    }
    let status = remote(ctx, &login).await?;
    if !status.sponsor_until.is_some_and(|until| until > now) {
        return Err(Error::SponsorRequired);
    }
    let device = device(&ctx.db).await?;
    if let Some(current) = &status.current
        && input.replace != Some(current.committed_at)
    {
        let backup = describe(ctx, &login, current, Some(&device)).await?;
        // Only this computer's own backup that the key still reads is continued without asking.
        if !backup.this_device || backup.summary.is_none() {
            return Ok(Enabled::Replace { backup });
        }
    }
    let key = login.backup_key.as_ref().map(|key| key.as_slice());
    let enabled = sqlx::query!(
        r#"UPDATE profile SET cloud_backup = 1, cloud_backup_replace = ?
        WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
        input.replace,
        key
    )
    .execute(&ctx.db)
    .await?;
    if enabled.rows_affected() == 0 {
        return Err(Error::AccountChanged);
    }
    Ok(Enabled::Enabled)
}

pub async fn disable(db: &SqlitePool) -> Result<()> {
    sqlx::query!(
        r#"UPDATE profile SET cloud_backup = 0, cloud_backup_failed_at = NULL,
            cloud_backup_error = NULL"#
    )
    .execute(db)
    .await?;
    Ok(())
}

pub async fn delete(ctx: &Context) -> Result<()> {
    let _running = ctx.cloud_backup.running.lock().await;
    let login = account::saved_login(&ctx.db).await?;
    ctx.account
        .send(
            &ctx.db,
            &login,
            ctx.account.request(Method::DELETE, path::BACKUP),
        )
        .await?;
    // Logging out or into another account clears all of this already.
    let key = login.backup_key.as_ref().map(|key| key.as_slice());
    let mut tx = ctx.db.begin().await?;
    sqlx::query!(
        r#"UPDATE profile SET cloud_backup = 0, last_backup_at = NULL, cloud_backup_commit = NULL,
            cloud_backup_replace = NULL, cloud_backup_failed_at = NULL, cloud_backup_error = NULL
        WHERE EXISTS (SELECT 1 FROM account WHERE backup_key IS ?)"#,
        key
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        r#"DELETE FROM cloud_backup_material
        WHERE EXISTS (SELECT 1 FROM account WHERE backup_key IS ?)"#,
        key
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(())
}

/// Backs up now, recording the outcome for the backup page.
pub async fn back_up(ctx: &Context, now: i64) -> Result<()> {
    let _running = ctx.cloud_backup.running.lock().await;
    // Turning it on is where the user agrees to send the data.
    if !sqlx::query_scalar!(r#"SELECT cloud_backup AS "enabled: bool" FROM profile"#)
        .fetch_one(&ctx.db)
        .await?
    {
        return Err(Error::Disabled);
    }
    let login = account::saved_login(&ctx.db).await?;
    let key = login.backup_key.ok_or(Error::NeedsPassword)?;
    let result = upload(ctx, &login, &key, now).await;
    let key = key.as_slice();
    // A run for an account that has changed since must leave the new account's state alone.
    match &result {
        Ok(()) => {
            sqlx::query!(
                r#"UPDATE profile SET last_backup_at = ?, cloud_backup_failed_at = NULL,
                    cloud_backup_error = NULL
                WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
                now,
                key
            )
            .execute(&ctx.db)
            .await?;
        }
        // Not failing: backups pause until the user sponsors again.
        Err(Error::SponsorRequired) => {
            sqlx::query!(
                r#"UPDATE profile SET cloud_backup_failed_at = NULL, cloud_backup_error = NULL
                WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
                key
            )
            .execute(&ctx.db)
            .await?;
        }
        Err(error) => {
            let message = error.message();
            sqlx::query!(
                r#"UPDATE profile SET cloud_backup_failed_at = coalesce(cloud_backup_failed_at, ?),
                    cloud_backup_error = ?
                WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
                now,
                message,
                key
            )
            .execute(&ctx.db)
            .await?;
        }
    }
    result
}

async fn upload(ctx: &Context, login: &account::Login, key: &[u8; 32], now: i64) -> Result<()> {
    let mut retried = false;
    loop {
        match attempt(ctx, login, key, now).await {
            // Another computer's commit removed this one's uploads or replaced the backup it saw;
            // look at the cloud again, so that replacing another computer's backup still asks.
            Err(Error::Account(account::Error::Server(ApiError {
                code: ErrorCode::BackupIncomplete | ErrorCode::BackupChanged,
                ..
            }))) if !retried => {
                let key = key.as_slice();
                sqlx::query!(
                    r#"DELETE FROM cloud_backup_material
                    WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
                    key
                )
                .execute(&ctx.db)
                .await?;
                retried = true;
            }
            result => return result,
        }
    }
}

async fn attempt(ctx: &Context, login: &account::Login, key: &[u8; 32], now: i64) -> Result<()> {
    let status = remote(ctx, login).await?;
    if !status.sponsor_until.is_some_and(|until| until > now) {
        return Err(Error::SponsorRequired);
    }
    let device = device(&ctx.db).await?;
    let profile = sqlx::query!("SELECT cloud_backup_commit, cloud_backup_replace FROM profile")
        .fetch_one(&ctx.db)
        .await?;
    // The cloud still holds this computer's last backup, made with the current key; the key's
    // trigger clears the commit when it changes.
    let ours = status.current.as_ref().is_some_and(|current| {
        current.device_id == device && Some(current.committed_at) == profile.cloud_backup_commit
    });
    if let Some(current) = &status.current
        && !ours
        && profile.cloud_backup_replace != Some(current.committed_at)
    {
        if current.device_id != device {
            return Err(Error::OtherDevice);
        }
        // This computer's backup without its commit recorded: interrupted right after
        // committing, or encrypted before a password reset, which only the user may replace.
        contents(ctx, login, key, &current.manifest).await?;
    }
    let dir = &ctx.cloud_backup.dir;
    fs::create_dir_all(dir)?;
    let (next, last) = (dir.join("next.db"), dir.join("last.db"));
    match fs::remove_file(&next) {
        Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(e.into()),
        _ => {}
    }
    let snapshot = super::snapshot(&ctx.db, &next).await?;
    // Unchanged bytes are the same data only while the cloud holds this computer's last backup.
    let compared = (next.clone(), last.clone());
    if ours && tokio::task::spawn_blocking(move || same_file(&compared.0, &compared.1)).await?? {
        fs::remove_file(&next)?;
        return Ok(());
    }
    let (manifest, objects) = send_objects(ctx, login, key, &snapshot, &next, now).await?;
    let commit = wire::Commit {
        device_id: device,
        manifest,
        objects,
        replaces: status.current.map(|current| current.manifest),
    };
    let committed: wire::Committed = ctx
        .account
        .send(
            &ctx.db,
            login,
            ctx.account
                .request(Method::POST, path::BACKUP)
                .json(&commit),
        )
        .await?
        .json()
        .await
        .map_err(account::Error::Network)?;
    // Renamed before the commit is recorded: an interruption in between only uploads again.
    fs::rename(&next, &last)?;
    let key = key.as_slice();
    sqlx::query!(
        r#"UPDATE profile SET cloud_backup_commit = ?, cloud_backup_replace = NULL
        WHERE EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
        committed.committed_at,
        key
    )
    .execute(&ctx.db)
    .await?;
    Ok(())
}

/// One upload, sealed in advance so that its size is known before anything is sent.
struct Sealed {
    database: (String, Vec<u8>),
    /// Materials not in the cloud yet: material id, object id and data.
    materials: Vec<(i64, String, Vec<u8>)>,
    /// Objects of the materials already in the cloud.
    uploaded: Vec<String>,
    manifest: (String, Vec<u8>),
}

/// Uploads what the cloud does not have yet, returning the manifest and the other objects.
async fn send_objects(
    ctx: &Context,
    login: &account::Login,
    key: &[u8; 32],
    snapshot: &Snapshot,
    next: &Path,
    now: i64,
) -> Result<(String, Vec<String>)> {
    let uploaded: HashMap<i64, (String, i64)> =
        sqlx::query!("SELECT material_id, object, size FROM cloud_backup_material")
            .fetch_all(&ctx.db)
            .await?
            .into_iter()
            .map(|row| (row.material_id, (row.object, row.size)))
            .collect();
    let contents = Contents {
        app_version: env!("CARGO_PKG_VERSION").into(),
        database_version: snapshot.version,
        exported_at: now,
        mentor_count: snapshot.mentors,
        email_count: snapshot.emails,
        device_name: whoami::devicename()
            .inspect_err(|e| tracing::warn!("computer name: {e}"))
            .ok(),
        database: random_id()?,
        materials: Vec::new(),
    };
    let (key_copy, materials, names, next) = (
        *key,
        ctx.materials.clone(),
        snapshot.materials.clone(),
        next.to_owned(),
    );
    let sealed = tokio::task::spawn_blocking(move || {
        seal_all(&key_copy, contents, &next, &materials, &names, &uploaded)
    })
    .await??;
    let mut objects = sealed.uploaded;
    for (material, object, data) in sealed.materials {
        let size = data.len() as i64;
        put(ctx, login, &object, data).await?;
        // Recorded at once: a later attempt reuses it even if this one never commits.
        let key = key.as_slice();
        sqlx::query!(
            r#"INSERT INTO cloud_backup_material (material_id, object, size)
            SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM material WHERE id = ?)
                AND EXISTS (SELECT 1 FROM account WHERE backup_key = ?)"#,
            material,
            object,
            size,
            material,
            key
        )
        .execute(&ctx.db)
        .await?;
        objects.push(object);
    }
    let (database, data) = sealed.database;
    put(ctx, login, &database, data).await?;
    objects.push(database);
    let (manifest, data) = sealed.manifest;
    put(ctx, login, &manifest, data).await?;
    Ok((manifest, objects))
}

fn seal_all(
    key: &[u8; 32],
    mut contents: Contents,
    database: &Path,
    materials: &Path,
    names: &[(i64, String)],
    uploaded: &HashMap<i64, (String, i64)>,
) -> Result<Sealed> {
    // The manifest and the database are objects too.
    if names.len() + 2 > OBJECTS {
        return Err(Error::TooLarge(format!(
            "材料超过 {} 份，云端备份放不下。删掉一些不用的材料再备份。",
            OBJECTS - 2
        )));
    }
    let database_data = seal(
        key,
        Kind::Database,
        &contents.database,
        &zstd::encode_all(File::open(database)?, LEVEL)?,
    )?;
    let mut total = database_data.len() as u64;
    let mut sizes = Vec::with_capacity(names.len());
    let (mut new, mut kept) = (Vec::new(), Vec::new());
    for (id, name) in names {
        let (object, size) = match uploaded.get(id) {
            Some((object, size)) => {
                kept.push(object.clone());
                (object.clone(), *size as u64)
            }
            None => {
                let object = random_id()?;
                let file = File::open(material::file(materials, *id))?;
                let data = seal(
                    key,
                    Kind::Material,
                    &object,
                    &zstd::encode_all(file, LEVEL)?,
                )?;
                let size = data.len() as u64;
                // Past the limit only sizes are needed, for saying what is too large.
                if total + size <= LIMIT {
                    new.push((*id, object.clone(), data));
                }
                (object, size)
            }
        };
        total += size;
        sizes.push((name.as_str(), size));
        contents.materials.push((*id, object));
    }
    let manifest = random_id()?;
    let manifest_data = seal(
        key,
        Kind::Manifest,
        &manifest,
        &zstd::encode_all(
            &serde_json::to_vec(&contents).map_err(io::Error::other)?[..],
            LEVEL,
        )?,
    )?;
    total += manifest_data.len() as u64;
    if total > LIMIT {
        sizes.sort_by_key(|(_, size)| std::cmp::Reverse(*size));
        let mut message = format!(
            "备份超过 30 MB：数据库 {}",
            megabytes(database_data.len() as u64)
        );
        if !sizes.is_empty() {
            let largest: Vec<String> = sizes
                .iter()
                .take(5)
                .map(|(name, size)| format!("{name} {}", megabytes(*size)))
                .collect();
            message.push_str(&format!("，材料 {}", largest.join("、")));
        }
        message.push_str("。先把大的文件压小再备份，PDF 和图片很难再压缩。");
        return Err(Error::TooLarge(message));
    }
    Ok(Sealed {
        database: (contents.database, database_data),
        materials: new,
        uploaded: kept,
        manifest: (manifest, manifest_data),
    })
}

fn megabytes(size: u64) -> String {
    format!("{:.1} MB", size as f64 / (1024.0 * 1024.0))
}

/// Downloads every object first, then decrypts them into the stage of an ordinary restore, which
/// checks the result like a local backup.
pub async fn prepare_restore(ctx: &Context, dir: &Path) -> Result<RestoreInfo> {
    let _running = ctx.cloud_backup.running.lock().await;
    let login = account::saved_login(&ctx.db).await?;
    let key = login.backup_key.ok_or(Error::NeedsPassword)?;
    let current = remote(ctx, &login).await?.current.ok_or(Error::NotFound)?;
    let contents = contents(ctx, &login, &key, &current.manifest).await?;
    let database = get(ctx, &login, &contents.database).await?;
    let mut materials = Vec::with_capacity(contents.materials.len());
    for (id, object) in contents.materials {
        let data = get(ctx, &login, &object).await?;
        materials.push((id, object, data));
    }
    let manifest = Archive {
        application: APPLICATION.into(),
        app_version: contents.app_version,
        database_version: contents.database_version,
        exported_at: contents.exported_at,
    };
    let database_object = contents.database;
    Ok(super::stage(dir, move |stage| {
        let unseal = |kind, object: &str, data: &[u8], target: &Path| -> Result<()> {
            let compressed = open(&key, kind, object, data)?;
            zstd::stream::copy_decode(&compressed[..], File::create(target)?)?;
            Ok(())
        };
        let materials_dir = stage.join("materials");
        fs::create_dir_all(&materials_dir)?;
        unseal(
            Kind::Database,
            &database_object,
            &database,
            &stage.join("app.db"),
        )
        .map_err(staged)?;
        for (id, object, data) in &materials {
            unseal(
                Kind::Material,
                object,
                data,
                &material::file(&materials_dir, *id),
            )
            .map_err(staged)?;
        }
        Ok(manifest)
    })
    .await?)
}

fn staged(error: Error) -> super::Error {
    match error {
        Error::Io(source) => super::Error::Io {
            operation: "restore cloud backup",
            source,
        },
        error => super::Error::Invalid(error.message()),
    }
}

pub async fn run(ctx: std::sync::Arc<Context>) {
    // Out of the way of starting up.
    let start = tokio::time::Instant::now() + std::time::Duration::from_secs(60);
    let mut ticks = tokio::time::interval_at(start, std::time::Duration::from_secs(60 * 60));
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        ticks.tick().await;
        let now = crate::now();
        let due = sqlx::query_scalar!(
            r#"SELECT cloud_backup = 1 AND (last_backup_at IS NULL OR last_backup_at <= ?)
                AND EXISTS (SELECT 1 FROM account
                    WHERE token IS NOT NULL AND backup_key IS NOT NULL) AS "due!: bool"
            FROM profile"#,
            now - DAY
        )
        .fetch_one(&ctx.db)
        .await;
        // The backup page shows a failure; the next hour tries again.
        let result = match due {
            Ok(true) => back_up(&ctx, now).await,
            Ok(false) => Ok(()),
            Err(e) => Err(e.into()),
        };
        if let Err(error) = result {
            tracing::warn!(error = %error, "scheduled cloud backup failed");
        }
    }
}

async fn remote(ctx: &Context, login: &account::Login) -> Result<wire::Status> {
    Ok(ctx
        .account
        .send(
            &ctx.db,
            login,
            ctx.account.request(Method::GET, path::BACKUP),
        )
        .await?
        .json()
        .await
        .map_err(account::Error::Network)?)
}

fn object_path(object: &str) -> String {
    format!("{}/{object}", path::BACKUP_OBJECTS)
}

async fn put(ctx: &Context, login: &account::Login, object: &str, data: Vec<u8>) -> Result<()> {
    let request = ctx
        .account
        .request(Method::PUT, &object_path(object))
        .timeout(TRANSFER)
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .body(data);
    ctx.account
        .send(&ctx.db, login, request)
        .await?
        .json::<Empty>()
        .await
        .map_err(account::Error::Network)?;
    Ok(())
}

async fn get(ctx: &Context, login: &account::Login, object: &str) -> Result<Vec<u8>> {
    // Ids come from the server and from manifests; only well-formed ones go into a path.
    if !wire::valid_id(object) {
        return Err(Error::Corrupted);
    }
    let request = ctx
        .account
        .request(Method::GET, &object_path(object))
        .timeout(TRANSFER);
    Ok(ctx
        .account
        .send(&ctx.db, login, request)
        .await?
        .bytes()
        .await
        .map_err(account::Error::Network)?
        .into())
}

async fn contents(
    ctx: &Context,
    login: &account::Login,
    key: &[u8; 32],
    manifest: &str,
) -> Result<Contents> {
    let data = get(ctx, login, manifest).await?;
    let compressed = open(key, Kind::Manifest, manifest, &data).map_err(|e| match e {
        Error::Corrupted => Error::OldPassword,
        e => e,
    })?;
    serde_json::from_slice(&zstd::decode_all(&compressed[..])?).map_err(|_| Error::Corrupted)
}

async fn describe(
    ctx: &Context,
    login: &account::Login,
    current: &wire::Current,
    device: Option<&str>,
) -> Result<Backup> {
    let summary = match &login.backup_key {
        Some(key) => match contents(ctx, login, key, &current.manifest).await {
            Ok(contents) => Some(Summary {
                device_name: contents.device_name,
                exported_at: contents.exported_at,
                mentor_count: contents.mentor_count,
                email_count: contents.email_count,
            }),
            Err(Error::OldPassword) => None,
            Err(e) => return Err(e),
        },
        None => None,
    };
    Ok(Backup {
        committed_at: current.committed_at,
        size: current.size,
        this_device: device == Some(current.device_id.as_str()),
        summary,
    })
}

async fn device(db: &SqlitePool) -> Result<String> {
    let id = random_id()?;
    Ok(sqlx::query_scalar!(
        r#"UPDATE profile SET cloud_backup_device = coalesce(cloud_backup_device, ?)
        RETURNING cloud_backup_device AS "device!""#,
        id
    )
    .fetch_one(db)
    .await?)
}

fn random_id() -> Result<String> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(io::Error::other)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Encrypts with the object's id as associated data, so the server cannot swap objects.
fn seal(key: &[u8; 32], kind: Kind, id: &str, data: &[u8]) -> Result<Vec<u8>> {
    let mut nonce = XNonce::default();
    getrandom::fill(&mut nonce).map_err(io::Error::other)?;
    let header = [MAGIC.as_slice(), &[VERSION, kind as u8]].concat();
    let aad = [header.as_slice(), id.as_bytes()].concat();
    let sealed = XChaCha20Poly1305::new(key.into())
        .encrypt(
            &nonce,
            Payload {
                msg: data,
                aad: &aad,
            },
        )
        .unwrap_or_else(|_| unreachable!("backup objects are far below the cipher's limit"));
    Ok([header.as_slice(), nonce.as_slice(), &sealed].concat())
}

fn open(key: &[u8; 32], kind: Kind, id: &str, object: &[u8]) -> Result<Vec<u8>> {
    let (header, rest) = object.split_at_checked(HEADER).ok_or(Error::Corrupted)?;
    if &header[..8] != MAGIC {
        return Err(Error::Corrupted);
    }
    if header[8] > VERSION {
        return Err(Error::Newer);
    }
    if header[8] != VERSION || header[9] != kind as u8 {
        return Err(Error::Corrupted);
    }
    let (nonce, sealed) = rest.split_at_checked(NONCE).ok_or(Error::Corrupted)?;
    let nonce = XNonce::try_from(nonce).map_err(|_| Error::Corrupted)?;
    XChaCha20Poly1305::new(key.into())
        .decrypt(
            &nonce,
            Payload {
                msg: sealed,
                aad: &[header, id.as_bytes()].concat(),
            },
        )
        .map_err(|_| Error::Corrupted)
}

fn same_file(a: &Path, b: &Path) -> io::Result<bool> {
    let (mut a, mut b) = match (File::open(a)?, File::open(b)) {
        (a, Ok(b)) => (a, b),
        (_, Err(e)) if e.kind() == io::ErrorKind::NotFound => return Ok(false),
        (_, Err(e)) => return Err(e),
    };
    let mut remaining = a.metadata()?.len();
    if remaining != b.metadata()?.len() {
        return Ok(false);
    }
    let (mut x, mut y) = (vec![0; 1 << 16], vec![0; 1 << 16]);
    while remaining > 0 {
        let n = remaining.min(1 << 16) as usize;
        a.read_exact(&mut x[..n])?;
        b.read_exact(&mut y[..n])?;
        if x[..n] != y[..n] {
            return Ok(false);
        }
        remaining -= n as u64;
    }
    Ok(true)
}
