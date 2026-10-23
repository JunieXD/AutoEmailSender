//! Opening an existing database that needs upgrading copies it first, so a failed upgrade loses
//! nothing.
// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use std::path::Path;

use sqlx::SqlitePool;
use sqlx::sqlite::SqliteConnectOptions;

#[tokio::test]
async fn failed_upgrade_keeps_a_copy() {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("failed-upgrade");
    if dir.exists() {
        std::fs::remove_dir_all(&dir).unwrap();
    }
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("app.db");

    let db = db::open(&path).await.unwrap();
    assert!(
        !dir.join("backups").exists(),
        "a new database has nothing to copy"
    );
    sqlx::query("INSERT INTO mentor (name, created_at, updated_at) VALUES ('甲', 0, 0)")
        .execute(&db)
        .await
        .unwrap();
    // Forget the applied migration: opening again reruns it over the existing tables, which fails.
    sqlx::query("DELETE FROM _sqlx_migrations")
        .execute(&db)
        .await
        .unwrap();
    db.close().await;
    // A copy cut short last time is in the way.
    std::fs::create_dir_all(dir.join("backups")).unwrap();
    std::fs::write(dir.join("backups/copying.db"), "cut short").unwrap();

    let Err(db::OpenError::Upgrade {
        backup: Some(backup),
        ..
    }) = db::open(&path).await
    else {
        panic!("expected a failed upgrade with a copy");
    };
    let copy = SqlitePool::connect_with(SqliteConnectOptions::new().filename(&backup))
        .await
        .unwrap();
    let names: Vec<String> = sqlx::query_scalar("SELECT name FROM mentor")
        .fetch_all(&copy)
        .await
        .unwrap();
    assert_eq!(names, ["甲"]);
}
