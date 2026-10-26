use std::{
    future::Future,
    sync::{Arc, Mutex, MutexGuard},
    time::Duration,
};

/// Shared by the host's background loops; failed operations keep their results until saved.
#[derive(Clone, Default)]
pub struct Writes(Arc<Mutex<State>>);

#[derive(Default)]
struct State {
    failing: usize,
    reason: Option<&'static str>,
}

impl Writes {
    pub fn failure(&self) -> Option<&'static str> {
        self.state().reason
    }

    pub async fn retry<T, F: Future<Output = Result<T, sqlx::Error>>>(
        &self,
        what: &str,
        mut write: impl FnMut() -> F,
    ) -> T {
        let mut pending = Pending {
            writes: self,
            failed: false,
        };
        loop {
            match write().await {
                Ok(value) => return value,
                Err(error) => {
                    tracing::error!(
                        what,
                        error = %error,
                        "background database operation failed; retrying in 5 seconds"
                    );
                    {
                        let mut state = self.state();
                        if !pending.failed {
                            state.failing += 1;
                            pending.failed = true;
                        }
                        state.reason = Some(reason(&error));
                    }
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.0.lock().unwrap_or_else(|_| {
            unreachable!("write failure updates cannot panic while holding the lock")
        })
    }
}

// Cancellation must release the failure too, even if the next attempt never runs.
struct Pending<'a> {
    writes: &'a Writes,
    failed: bool,
}

impl Drop for Pending<'_> {
    fn drop(&mut self) {
        if self.failed {
            let mut state = self.writes.state();
            state.failing -= 1;
            if state.failing == 0 {
                state.reason = None;
            }
        }
    }
}

fn reason(error: &sqlx::Error) -> &'static str {
    use libsqlite3_sys::{
        SQLITE_CANTOPEN, SQLITE_FULL, SQLITE_IOERR, SQLITE_PERM, SQLITE_READONLY,
    };
    let code = error
        .as_database_error()
        .and_then(|error| error.code())
        .and_then(|code| code.parse::<i32>().ok());
    // SQLite's extended codes keep the primary result code in the low byte.
    match code.map(|code| code & 0xff) {
        Some(SQLITE_FULL) => "磁盘空间不足，腾出空间后会自动继续",
        Some(SQLITE_IOERR | SQLITE_CANTOPEN | SQLITE_READONLY | SQLITE_PERM) => {
            "数据文件读写出错，请确认数据所在的磁盘还连着"
        }
        _ => "数据暂时无法保存，请导出日志发给开发者",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::{Connection, SqliteConnection};
    use std::{collections::VecDeque, future::ready};
    use tokio::sync::mpsc;

    async fn connection() -> SqliteConnection {
        std::sync::LazyLock::force(&crate::MIGRATOR);
        SqliteConnection::connect(":memory:").await.unwrap()
    }

    async fn full() -> sqlx::Error {
        let mut conn = connection().await;
        sqlx::query("PRAGMA max_page_count = 1")
            .execute(&mut conn)
            .await
            .unwrap();
        sqlx::query("CREATE TABLE data (value TEXT)")
            .execute(&mut conn)
            .await
            .expect_err("the schema needs a second page")
    }

    async fn cant_open() -> sqlx::Error {
        // An executable is a file, so it cannot contain a database on either platform.
        let path = std::env::current_exe().unwrap().join("app.db");
        SqliteConnection::connect_with(
            &sqlx::sqlite::SqliteConnectOptions::new()
                .filename(path)
                .read_only(true),
        )
        .await
        .expect_err("the database parent is not a directory")
    }

    #[tokio::test]
    async fn sqlite_errors_give_actions_for_full_and_unavailable_storage() {
        assert_eq!(reason(&full().await), "磁盘空间不足，腾出空间后会自动继续");
        assert_eq!(
            reason(&cant_open().await),
            "数据文件读写出错，请确认数据所在的磁盘还连着"
        );
        let mut conn = connection().await;
        sqlx::query("CREATE TABLE data (value INTEGER UNIQUE)")
            .execute(&mut conn)
            .await
            .unwrap();
        sqlx::query("INSERT INTO data VALUES (1)")
            .execute(&mut conn)
            .await
            .unwrap();
        let error = sqlx::query("INSERT INTO data VALUES (1)")
            .execute(&mut conn)
            .await
            .expect_err("a duplicate violates the unique constraint");
        assert_eq!(reason(&error), "数据暂时无法保存，请导出日志发给开发者");
        sqlx::query("PRAGMA query_only = ON")
            .execute(&mut conn)
            .await
            .unwrap();
        let error = sqlx::query("INSERT INTO data VALUES (2)")
            .execute(&mut conn)
            .await
            .expect_err("query-only connections cannot write");
        assert_eq!(
            reason(&error),
            "数据文件读写出错，请确认数据所在的磁盘还连着"
        );
    }

    #[tokio::test]
    async fn retries_every_five_seconds_and_clears_failure_after_success() {
        let mut results = VecDeque::from([Err(full().await), Err(full().await), Ok(42)]);
        tokio::time::pause();
        let writes = Writes::default();
        assert_eq!(writes.failure(), None);
        let worker_writes = writes.clone();
        let (tx, mut attempts) = mpsc::unbounded_channel();
        let worker = tokio::spawn(async move {
            worker_writes
                .retry("save result", || {
                    tx.send(()).unwrap();
                    ready(results.pop_front().unwrap())
                })
                .await
        });
        attempts.recv().await.unwrap();
        assert_eq!(writes.failure(), Some("磁盘空间不足，腾出空间后会自动继续"));
        tokio::time::advance(Duration::from_secs(4)).await;
        assert!(attempts.try_recv().is_err());
        tokio::time::advance(Duration::from_secs(1)).await;
        attempts.recv().await.unwrap();
        assert!(writes.failure().is_some());
        tokio::time::advance(Duration::from_secs(5)).await;
        assert_eq!(worker.await.unwrap(), 42);
        assert_eq!(writes.failure(), None);
    }

    #[tokio::test]
    async fn one_recovered_loop_does_not_clear_another_loops_failure() {
        let first = VecDeque::from([Err(full().await), Ok(1)]);
        let second = VecDeque::from([Err(cant_open().await), Err(cant_open().await), Ok(2)]);
        tokio::time::pause();
        let writes = Writes::default();
        let independent = Writes::default();
        let (tx, mut attempts) = mpsc::unbounded_channel();
        let mut workers = Vec::new();
        for mut results in [first, second] {
            let writes = writes.clone();
            let tx = tx.clone();
            workers.push(tokio::spawn(async move {
                writes
                    .retry("save result", || {
                        tx.send(()).unwrap();
                        ready(results.pop_front().unwrap())
                    })
                    .await
            }));
        }
        attempts.recv().await.unwrap();
        attempts.recv().await.unwrap();
        assert!(writes.failure().is_some());
        assert_eq!(independent.failure(), None);
        tokio::time::advance(Duration::from_secs(5)).await;
        assert_eq!(workers.remove(0).await.unwrap(), 1);
        attempts.recv().await.unwrap();
        attempts.recv().await.unwrap();
        assert_eq!(
            writes.failure(),
            Some("数据文件读写出错，请确认数据所在的磁盘还连着")
        );
        tokio::time::advance(Duration::from_secs(5)).await;
        assert_eq!(workers.remove(0).await.unwrap(), 2);
        assert_eq!(writes.failure(), None);
    }
}
