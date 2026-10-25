#![cfg(test)]

use std::fs;
use std::io::{Read, Write};
use std::path::Path;

use api::backup;
use serde_json::json;
use sqlx::sqlite::SqliteConnectOptions;
use sqlx::{Connection, SqliteConnection, SqlitePool};
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const NOW: i64 = 1_792_958_400_000;

async fn fixture(db: &SqlitePool, dir: &Path) {
    fs::create_dir_all(dir.join("materials")).unwrap();
    fs::write(dir.join("materials/1"), b"resume").unwrap();
    sqlx::raw_sql("INSERT INTO mailbox(address,sender_name,provider,smtp_host,smtp_port,smtp_security,
        imap_host,imap_port,imap_security,auth,secret,daily_limit,min_interval_s,rotation_order,created_at)
        VALUES('me@example.invalid','test','custom','localhost',465,'tls','localhost',993,'tls','password','private-mail-secret',50,60,0,0);
        INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES
        ('deepseek',NULL,'m','private-model-key',1,0),('custom','http://localhost/v1','m','',1,0),
        ('custom','http://localhost/other','m','private-custom-key',1,0);
        INSERT INTO mentor(name,created_at,updated_at) VALUES('测试导师',0,0);
        INSERT INTO material(file_name,size,for_matching,for_writing,attach_by_default,created_at)
        VALUES('简历.pdf',6,0,0,0,0);
        INSERT INTO crawl(kind,start_urls,state,created_at) VALUES('site','[]','paused',0);
        INSERT INTO crawl_page(crawl_id,url,kind,mode,status,text) VALUES(1,'https://example.invalid','list','http','success','private-crawled-page');
        INSERT INTO email(mentor_id,kind,reply_to,state,planned_at,subject,body_md,html,text,created_at,updated_at)
        VALUES(1,'reply','<parent@example.invalid>','approved',1000,'Re: 测试','已批准','<p>已批准</p>','已批准',0,0);
        INSERT INTO email(mentor_id,kind,reply_to,state,body_md,created_at,updated_at)
        VALUES(1,'reply','<parent@example.invalid>','review','新草稿',0,0);
        INSERT INTO email_attachment(email_id,material_id,file_name,size) VALUES(1,1,'简历.pdf',6);")
        .execute(db).await.unwrap();
}

async fn readonly(path: &Path) -> SqliteConnection {
    SqliteConnection::connect_with(&SqliteConnectOptions::new().filename(path).read_only(true))
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn round_trip_strips_private_data_preserves_approval_and_installs_on_start(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    fixture(&db, dir.path()).await;
    let target = dir.path().join("backup.zip");
    backup::export(&db, &dir.path().join("materials"), &target, NOW)
        .await
        .unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT secret FROM mailbox")
            .fetch_one(&db)
            .await
            .unwrap(),
        "private-mail-secret"
    );
    let mut zip = ZipArchive::new(fs::File::open(&target).unwrap()).unwrap();
    let mut snapshot = Vec::new();
    zip.by_name("app.db")
        .unwrap()
        .read_to_end(&mut snapshot)
        .unwrap();
    for private in [
        "private-mail-secret",
        "private-model-key",
        "private-custom-key",
        "private-crawled-page",
    ] {
        assert!(
            !snapshot
                .windows(private.len())
                .any(|w| w == private.as_bytes())
        );
    }
    let info = backup::prepare(dir.path(), &target).await.unwrap();
    assert_eq!(
        (info.exported_at, info.mentor_count, info.email_count),
        (NOW, 1, 2)
    );
    let mut conn = readonly(&dir.path().join("restore/app.db")).await;
    assert_eq!(
        sqlx::query_scalar::<_, Option<String>>("SELECT secret FROM mailbox")
            .fetch_one(&mut conn)
            .await
            .unwrap(),
        None
    );
    assert_eq!(
        sqlx::query_scalar::<_, Option<String>>("SELECT api_key FROM model_config ORDER BY id")
            .fetch_all(&mut conn)
            .await
            .unwrap(),
        [None, Some(String::new()), None]
    );
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT text FROM crawl_page")
            .fetch_one(&mut conn)
            .await
            .unwrap(),
        ""
    );
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM email ORDER BY id")
            .fetch_all(&mut conn)
            .await
            .unwrap(),
        ["approved", "review"]
    );
    assert_eq!(
        fs::read(dir.path().join("restore/materials/1")).unwrap(),
        b"resume"
    );
    conn.close().await.unwrap();
    fs::write(dir.path().join("app.db"), b"old database").unwrap();
    fs::write(dir.path().join("app.db-wal"), b"old wal").unwrap();
    fs::write(dir.path().join("app.db-shm"), b"old shm").unwrap();
    backup::confirm(&db, dir.path(), NOW + 1).await.unwrap();
    assert!(
        dir.path()
            .join("backups")
            .join(format!("before-restore-{}.zip", NOW + 1))
            .exists()
    );
    assert!(backup::cancel(dir.path()).is_err());
    backup::apply_pending(dir.path()).unwrap();
    backup::apply_pending(dir.path()).unwrap();
    assert!(!dir.path().join("restore.pending").exists());
    assert!(!dir.path().join("restore-old").exists());
    assert!(!dir.path().join("app.db-wal").exists());
    let restored = db::open(&dir.path().join("app.db")).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM mentor")
            .fetch_one(&restored)
            .await
            .unwrap(),
        1
    );
    assert!(
        mailbox::receivable(&restored, NOW)
            .await
            .unwrap()
            .is_empty()
    );
    restored.close().await;
}

fn manifest() -> serde_json::Value {
    json!({"application":"io.github.juniexd.autoemailsender", "appVersion":"3.0.0-alpha.0", "databaseVersion":4, "exportedAt":NOW})
}

fn archive(path: &Path, entries: &[(&str, &[u8])]) {
    let mut zip = ZipWriter::new(fs::File::create(path).unwrap());
    for (name, data) in entries {
        zip.start_file(*name, SimpleFileOptions::default()).unwrap();
        zip.write_all(data).unwrap();
    }
    zip.finish().unwrap();
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invalid_archives_are_rejected_and_cleaned_up(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    fixture(&db, dir.path()).await;
    let good = dir.path().join("good.zip");
    backup::export(&db, &dir.path().join("materials"), &good, NOW)
        .await
        .unwrap();
    let mut zip = ZipArchive::new(fs::File::open(&good).unwrap()).unwrap();
    let mut database = Vec::new();
    zip.by_name("app.db")
        .unwrap()
        .read_to_end(&mut database)
        .unwrap();
    let target = dir.path().join("bad.zip");
    fs::write(&target, b"not zip").unwrap();
    assert!(
        backup::prepare(dir.path(), &target)
            .await
            .unwrap_err()
            .message()
            .contains("zip")
    );
    let normal = serde_json::to_vec(&manifest()).unwrap();
    let mut foreign = manifest();
    foreign["application"] = json!("another.app");
    let foreign = serde_json::to_vec(&foreign).unwrap();
    let mut future = manifest();
    future["databaseVersion"] = json!(5);
    let future = serde_json::to_vec(&future).unwrap();
    let oversized = vec![b'x'; 64 * 1024 + 1];
    let cases = vec![
        (
            vec![
                ("manifest.json", oversized.as_slice()),
                ("app.db", &database),
            ],
            "过大",
        ),
        (
            vec![
                ("manifest.json", &normal),
                ("app.db", &database),
                ("materials/1", b"short"),
            ],
            "材料",
        ),
        (vec![("app.db", &database)], "清单"),
        (
            vec![("manifest.json", &foreign), ("app.db", &database)],
            "本应用",
        ),
        (
            vec![("manifest.json", &future), ("app.db", &database)],
            "升级",
        ),
        (
            vec![("manifest.json", &normal), ("app.db", &database)],
            "材料",
        ),
        (
            vec![
                ("manifest.json", &normal),
                ("app.db", &database),
                ("../escape", b"x"),
            ],
            "路径",
        ),
        (
            vec![("manifest.json", &normal), ("app.db", b"broken")],
            "数据库",
        ),
        (
            vec![
                ("manifest.json", &normal),
                ("app.db", &database),
                ("materials/../escape", b"x"),
            ],
            "路径",
        ),
        (
            vec![
                ("manifest.json", &normal),
                ("app.db", &database),
                ("materials\\1", b"x"),
            ],
            "路径",
        ),
    ];
    for (entries, reason) in cases {
        archive(&target, &entries);
        let error = backup::prepare(dir.path(), &target).await.unwrap_err();
        assert!(error.message().contains(reason), "{error}");
        assert!(!dir.path().join("restore").exists());
    }
    assert!(!dir.path().join("escape").exists());
    backup::prepare(dir.path(), &good).await.unwrap();
    backup::cancel(dir.path()).unwrap();
    backup::cancel(dir.path()).unwrap();
    assert!(!dir.path().join("restore").exists());
    assert!(
        backup::confirm(&db, dir.path(), NOW)
            .await
            .unwrap_err()
            .message()
            .contains("先选择")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn before_restore_retention_does_not_touch_upgrade_backups(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    fixture(&db, dir.path()).await;
    let target = dir.path().join("backup.zip");
    backup::export(&db, &dir.path().join("materials"), &target, NOW)
        .await
        .unwrap();
    fs::create_dir(dir.path().join("backups")).unwrap();
    let upgrade = dir.path().join("backups/before-upgrade-1.db");
    fs::write(&upgrade, b"upgrade").unwrap();
    for n in 0..4 {
        backup::prepare(dir.path(), &target).await.unwrap();
        backup::confirm(&db, dir.path(), NOW + n).await.unwrap();
        // Model the process having finished installation; keep the test's source pool separate.
        backup::apply_pending(dir.path()).unwrap();
    }
    let names: Vec<_> = fs::read_dir(dir.path().join("backups"))
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    assert_eq!(names.len(), 4);
    assert!(upgrade.exists());
    assert!(
        !dir.path()
            .join(format!("backups/before-restore-{NOW}.zip"))
            .exists()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn failed_export_preserves_the_previous_file_and_cleans_temporary_files(db: SqlitePool) {
    let dir = tempfile::tempdir().unwrap();
    fixture(&db, dir.path()).await;
    let target = dir.path().join("backup.zip");
    fs::write(&target, b"previous complete backup").unwrap();
    fs::remove_file(dir.path().join("materials/1")).unwrap();
    assert!(
        backup::export(&db, &dir.path().join("materials"), &target, NOW)
            .await
            .is_err()
    );
    assert_eq!(fs::read(target).unwrap(), b"previous complete backup");
    let names: Vec<_> = fs::read_dir(dir.path())
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    assert_eq!(names.len(), 2);
}
