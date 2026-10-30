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
                    until(at, super::outcome::now).await;
                }
            }, if result.next_at.is_some() => {},
        }
    }
}

/// Returns once `now` reaches `at`. The timer stands still while the computer sleeps, so the clock
/// is read again every minute; a reminder due during the sleep comes soon after waking.
async fn until(at: i64, now: impl Fn() -> i64) {
    loop {
        let left = at.saturating_sub(now());
        if left <= 0 {
            return;
        }
        tokio::time::sleep(Duration::from_millis(left.unsigned_abs()).min(MINUTE)).await;
    }
}

const MINUTE: Duration = Duration::from_secs(60);

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicI64, Ordering};

    use super::*;

    const HOUR: i64 = 3_600_000;

    #[tokio::test(start_paused = true)]
    async fn a_reminder_due_while_the_computer_slept_comes_within_a_minute_of_waking() {
        let clock = AtomicI64::new(0);
        let started = tokio::time::Instant::now();
        // Reads 08:00 for a 09:00 reminder, then sleeps until 10:00 without the timer counting.
        until(HOUR, || clock.swap(2 * HOUR, Ordering::Relaxed)).await;
        assert!(started.elapsed() <= MINUTE);
    }
}
