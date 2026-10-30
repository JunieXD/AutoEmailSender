#![cfg(test)]

use std::fs;

use api::Context;
use api::backup::cloud::{self, Enable, Enabled, Error};
use sqlx::sqlite::SqliteConnectOptions;
use sqlx::{Connection, SqliteConnection, SqlitePool};
use test_fixtures::backup::{Server, State};

const NOW: i64 = 1_800_000_000_000;
const HOUR: i64 = 60 * 60 * 1000;
const DAY: i64 = 24 * HOUR;
const KEY: [u8; 32] = [7; 32];
const RESUME: &[u8] = b"resume";

struct Setup {
    dir: tempfile::TempDir,
    ctx: Context,
    server: Server,
}

impl Setup {
    async fn new(db: SqlitePool) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let server = Server::start(State {
            now: NOW,
            sponsor_until: Some(NOW + DAY),
            ..Default::default()
        })
        .await
        .unwrap();
        let mut ctx = Context::new(db, net::Net::new(vec![]).unwrap(), dir.path());
        ctx.account = account::Client::with_address(&ctx.net, server.address.clone());
        fs::create_dir(&ctx.materials).unwrap();
        fs::write(ctx.materials.join("1"), RESUME).unwrap();
        sqlx::query(
            r#"INSERT INTO account (id, email, token, backup_key)
            VALUES (1, 'student@example.invalid', 'token', ?)"#,
        )
        .bind(KEY.as_slice())
        .execute(&ctx.db)
        .await
        .unwrap();
        sqlx::raw_sql(
            r#"INSERT INTO mentor (name, created_at, updated_at) VALUES ('测试导师', 0, 0);
            INSERT INTO material (file_name, size, for_matching, for_writing, attach_by_default,
                created_at)
            VALUES ('简历.pdf', 6, 0, 0, 0, 0);
            UPDATE profile SET cloud_backup = 1;"#,
        )
        .execute(&ctx.db)
        .await
        .unwrap();
        Self { dir, ctx, server }
    }

    fn uploads(&self) -> usize {
        self.server.state.lock().unwrap().uploads.len()
    }

    async fn add_mentor(&self) {
        sqlx::query("INSERT INTO mentor (name, created_at, updated_at) VALUES ('新导师', 0, 0)")
            .execute(&self.ctx.db)
            .await
            .unwrap();
    }

    async fn device(&self) -> String {
        sqlx::query_scalar("SELECT cloud_backup_device FROM profile")
            .fetch_one(&self.ctx.db)
            .await
            .unwrap()
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn only_changes_are_uploaded_and_restoring_stages_the_same_data(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    // The material, the database and the manifest.
    assert_eq!(setup.uploads(), 3);
    cloud::back_up(&setup.ctx, NOW + 1).await.unwrap();
    assert_eq!(setup.uploads(), 3);
    let status = cloud::status(&setup.ctx).await.unwrap();
    assert_eq!(status.last_backup_at, Some(NOW + 1));
    assert!(status.failure.is_none());

    setup.add_mentor().await;
    cloud::back_up(&setup.ctx, NOW + 2).await.unwrap();
    // The material is in the cloud already.
    assert_eq!(setup.uploads(), 5);

    let info = cloud::prepare_restore(&setup.ctx, setup.dir.path())
        .await
        .unwrap();
    assert_eq!((info.mentor_count, info.exported_at), (2, NOW + 2));
    let stage = setup.dir.path().join("restore");
    assert_eq!(fs::read(stage.join("materials/1")).unwrap(), RESUME);
    let mut staged = SqliteConnection::connect_with(
        &SqliteConnectOptions::new()
            .filename(stage.join("app.db"))
            .read_only(true),
    )
    .await
    .unwrap();
    let account: (Option<String>, Option<Vec<u8>>) =
        sqlx::query_as("SELECT token, backup_key FROM account")
            .fetch_one(&mut staged)
            .await
            .unwrap();
    assert_eq!(account, (None, None));
    let cloud: (bool, Option<String>, Option<i64>) =
        sqlx::query_as("SELECT cloud_backup, cloud_backup_device, last_backup_at FROM profile")
            .fetch_one(&mut staged)
            .await
            .unwrap();
    assert_eq!(cloud, (false, None, None));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn another_computers_backup_is_replaced_only_with_consent(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    assert!(!cloud::due(&setup.ctx.db, NOW + HOUR).await.unwrap());
    let other = "0".repeat(32);
    setup
        .server
        .state
        .lock()
        .unwrap()
        .current
        .as_mut()
        .unwrap()
        .device_id = other.clone();
    setup.add_mentor().await;
    assert!(matches!(
        cloud::back_up(&setup.ctx, NOW + 1).await,
        Err(Error::OtherDevice)
    ));
    let failure = cloud::status(&setup.ctx).await.unwrap().failure.unwrap();
    assert_eq!(failure.since, NOW + 1);
    assert_eq!(setup.uploads(), 3);
    // A failure is tried again the next hour, not a day after the last success.
    assert!(cloud::due(&setup.ctx.db, NOW + HOUR).await.unwrap());

    let enable = |replace| Enable {
        password: None,
        replace,
    };
    let Enabled::Replace { backup } = cloud::enable(&setup.ctx, enable(None), NOW).await.unwrap()
    else {
        panic!("replacing another computer's backup needs consent");
    };
    assert!(!backup.this_device);
    assert_eq!(backup.summary.unwrap().mentor_count, 1);
    assert!(matches!(
        cloud::enable(&setup.ctx, enable(Some(backup.committed_at)), NOW)
            .await
            .unwrap(),
        Enabled::Enabled
    ));
    cloud::back_up(&setup.ctx, NOW + 2).await.unwrap();
    let current = setup.server.state.lock().unwrap().current.take().unwrap();
    assert_eq!(current.device_id, setup.device().await);
    assert!(cloud::status(&setup.ctx).await.unwrap().failure.is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn materials_removed_by_another_commit_are_uploaded_again(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    let material = setup.server.state.lock().unwrap().uploads[0].clone();
    setup.server.state.lock().unwrap().objects.remove(&material);
    setup.add_mentor().await;
    cloud::back_up(&setup.ctx, NOW + 1).await.unwrap();
    // The commit missing the material, then everything again.
    assert_eq!(setup.uploads(), 3 + 2 + 3);
    let info = cloud::prepare_restore(&setup.ctx, setup.dir.path())
        .await
        .unwrap();
    assert_eq!(info.mentor_count, 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn replacing_another_computers_backup_uploads_everything_at_once(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    // Another computer's commit: its backup is current, and this one's objects are gone.
    {
        let mut state = setup.server.state.lock().unwrap();
        let current = state.current.as_mut().unwrap();
        current.device_id = "0".repeat(32);
        let manifest = current.manifest.clone();
        state.objects.retain(|id, _| *id == manifest);
    }
    let Enabled::Replace { backup } = cloud::enable(
        &setup.ctx,
        Enable {
            password: None,
            replace: None,
        },
        NOW,
    )
    .await
    .unwrap() else {
        panic!("replacing another computer's backup needs consent");
    };
    cloud::enable(
        &setup.ctx,
        Enable {
            password: None,
            replace: Some(backup.committed_at),
        },
        NOW,
    )
    .await
    .unwrap();
    setup.add_mentor().await;
    cloud::back_up(&setup.ctx, NOW + 1).await.unwrap();
    // The material, database and manifest once; no commit missing the material first.
    assert_eq!(setup.uploads(), 3 + 3);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn an_upload_whose_reply_was_lost_goes_again_under_the_same_id(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    let material = setup.server.state.lock().unwrap().uploads[0].clone();
    // As when the server stored it but the reply never arrived.
    sqlx::query("UPDATE cloud_backup_material SET uploaded = 0")
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    setup.add_mentor().await;
    cloud::back_up(&setup.ctx, NOW + 1).await.unwrap();
    // A new id would leave the first copy taking up the account's space for a day.
    assert_eq!(setup.server.state.lock().unwrap().uploads[3], material);
    assert_eq!(setup.uploads(), 6);
    let info = cloud::prepare_restore(&setup.ctx, setup.dir.path())
        .await
        .unwrap();
    assert_eq!(info.mentor_count, 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn backups_over_the_limit_fail_naming_the_largest_files(db: SqlitePool) {
    let setup = Setup::new(db).await;
    // Random bytes do not compress, as with most PDFs and images.
    let mut state = 0x2545_f491_4f6c_dd1d_u64;
    let mut noise = || {
        (0..16 * 1024 * 1024)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect::<Vec<u8>>()
    };
    for (id, name) in [(2, "成绩单.pdf"), (3, "论文.pdf")] {
        let data = noise();
        fs::write(setup.ctx.materials.join(id.to_string()), &data).unwrap();
        sqlx::query(
            r#"INSERT INTO material (id, file_name, size, for_matching, for_writing,
                attach_by_default, created_at)
            VALUES (?, ?, ?, 0, 0, 0, 0)"#,
        )
        .bind(id)
        .bind(name)
        .bind(data.len() as i64)
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    }
    let Err(Error::TooLarge(message)) = cloud::back_up(&setup.ctx, NOW).await else {
        panic!("32 MB of materials must not fit");
    };
    assert!(message.starts_with("备份超过 30 MB：数据库 "), "{message}");
    assert!(
        message.contains("，材料 成绩单.pdf 16.0 MB、论文.pdf 16.0 MB、简历.pdf 0.0 MB。"),
        "{message}"
    );
    assert_eq!(setup.uploads(), 0);
    let failure = cloud::status(&setup.ctx).await.unwrap().failure.unwrap();
    assert_eq!(failure.message, message);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tampered_objects_and_other_keys_cannot_restore(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    let (material, database) = {
        let state = setup.server.state.lock().unwrap();
        (state.uploads[0].clone(), state.uploads[1].clone())
    };
    let (original, other) = {
        let state = setup.server.state.lock().unwrap();
        (
            state.objects[&material].clone(),
            state.objects[&database].clone(),
        )
    };
    let mut changed = original.clone();
    *changed.last_mut().unwrap() ^= 1;
    // A changed byte, and another object of the same backup.
    for replaced in [changed, other] {
        setup
            .server
            .state
            .lock()
            .unwrap()
            .objects
            .insert(material.clone(), replaced);
        let error = cloud::prepare_restore(&setup.ctx, setup.dir.path())
            .await
            .unwrap_err();
        assert!(error.message().starts_with("云端备份已损坏"), "{error}");
        assert!(!setup.dir.path().join("restore").exists());
    }
    setup
        .server
        .state
        .lock()
        .unwrap()
        .objects
        .insert(material, original);

    // A password reset changes the key: the old backup cannot be read, and replacing it with
    // everything encrypted anew waits for the user, even from the computer that made it.
    sqlx::query("UPDATE account SET backup_key = ?")
        .bind([8u8; 32].as_slice())
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    let backup = cloud::server(&setup.ctx, NOW)
        .await
        .unwrap()
        .backup
        .unwrap();
    assert!(backup.this_device && backup.summary.is_none());
    assert!(matches!(
        cloud::prepare_restore(&setup.ctx, setup.dir.path()).await,
        Err(Error::OldPassword)
    ));
    assert!(matches!(
        cloud::back_up(&setup.ctx, NOW + 1).await,
        Err(Error::OldPassword)
    ));
    let enable = |replace| Enable {
        password: None,
        replace,
    };
    let Enabled::Replace { backup } = cloud::enable(&setup.ctx, enable(None), NOW).await.unwrap()
    else {
        panic!("replacing a backup the key cannot read needs consent");
    };
    cloud::enable(&setup.ctx, enable(Some(backup.committed_at)), NOW)
        .await
        .unwrap();
    cloud::back_up(&setup.ctx, NOW + 1).await.unwrap();
    assert_eq!(setup.uploads(), 6);
    cloud::prepare_restore(&setup.ctx, setup.dir.path())
        .await
        .unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn another_computers_commit_during_a_backup_is_not_replaced(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::back_up(&setup.ctx, NOW).await.unwrap();
    setup.add_mentor().await;
    setup.server.state.lock().unwrap().before_commit = Some(Box::new(|state| {
        let current = state.current.as_mut().unwrap();
        current.device_id = "0".repeat(32);
        current.manifest = "1".repeat(32);
    }));
    assert!(matches!(
        cloud::back_up(&setup.ctx, NOW + 1).await,
        Err(Error::OtherDevice)
    ));
    let current = setup.server.state.lock().unwrap().current.take().unwrap();
    assert_eq!(current.manifest, "1".repeat(32));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn backups_stop_when_off_unsponsored_or_logged_out(db: SqlitePool) {
    let setup = Setup::new(db).await;
    cloud::disable(&setup.ctx.db).await.unwrap();
    // Turning it on is where the user agrees, so nothing goes out while it is off.
    assert!(matches!(
        cloud::back_up(&setup.ctx, NOW).await,
        Err(Error::Disabled)
    ));
    sqlx::query("UPDATE profile SET cloud_backup = 1")
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    setup.server.state.lock().unwrap().sponsor_until = Some(NOW);
    assert!(matches!(
        cloud::back_up(&setup.ctx, NOW).await,
        Err(Error::SponsorRequired)
    ));
    let status = cloud::status(&setup.ctx).await.unwrap();
    assert!(status.enabled && status.failure.is_none());
    assert_eq!(setup.uploads(), 0);

    sqlx::query("DELETE FROM account")
        .execute(&setup.ctx.db)
        .await
        .unwrap();
    assert!(!cloud::status(&setup.ctx).await.unwrap().enabled);
}
