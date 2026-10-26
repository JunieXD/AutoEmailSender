//! Sleeps until the next todo reminder or a change to todo eligibility.

use sqlx::SqlitePool;
use std::{sync::Arc, time::Duration};
use tokio::sync::Notify;

#[derive(Clone, Default)]
pub struct Scheduler(Arc<Notify>);

impl Scheduler {
    pub fn wake(&self) {
        self.0.notify_one();
    }
}

pub async fn run(
    db: SqlitePool,
    writes: db::Writes,
    scheduler: Scheduler,
    changed: impl Fn() + Send + 'static,
    notify: impl Fn(String) + Send + 'static,
) {
    loop {
        let result = writes
            .retry("todo reminders", || {
                email::todos::local_reminders(&db, super::outcome::now())
            })
            .await;
        for message in result.messages {
            notify(message);
        }
        changed();
        tokio::select! {
            () = scheduler.0.notified() => {},
            () = async {
                if let Some(at) = result.next_at {
                    tokio::time::sleep(Duration::from_millis(
                        at.saturating_sub(super::outcome::now()).max(0) as u64,
                    ))
                    .await;
                }
            }, if result.next_at.is_some() => {},
        }
    }
}
