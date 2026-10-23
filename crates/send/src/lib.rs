//! Sending approved emails from the user's mailboxes, one at a time (docs/design.md#桌面端).

use std::sync::Arc;
use std::time::Duration;

use mailbox::{Letter, MailError, SendError};
use net::Net;
use profile::Rotation;
use sqlx::SqlitePool;
use tokio::sync::Notify;

const MINUTE: i64 = 60_000;
const DAY: i64 = 24 * 60 * MINUTE;

/// Why an email to a mentor without an address did not go out.
pub const NO_ADDRESS: &str = "导师尚未填写邮箱。在导师资料里填写后重试。";

/// Wakes the sender, so that an email approved or retried goes out without waiting.
#[derive(Clone, Default)]
pub struct Sender(Arc<Notify>);

impl Sender {
    pub fn wake(&self) {
        self.0.notify_one();
    }
}

/// Sends emails as they come due, for as long as the app runs.
pub async fn run(db: SqlitePool, net: Net, sender: Sender) {
    while let Err(e) = email::interrupted(&db, now()).await {
        tracing::error!("mark interrupted emails: {e}");
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
    loop {
        let wait = match step(&db, &net, now()).await {
            Ok(Next::Now) => continue,
            Ok(Next::At(at)) => Some(at - now()),
            Ok(Next::Idle) => None,
            // Nobody to tell but the log; the database is likely to work again in a while.
            Err(e) => {
                tracing::error!("send: {e}");
                Some(MINUTE)
            }
        };
        match wait {
            // Looks again within a minute: the timer stands still while the computer sleeps.
            Some(ms) => tokio::select! {
                () = tokio::time::sleep(Duration::from_millis(ms.clamp(0, MINUTE).unsigned_abs())) => {}
                () = sender.0.notified() => {}
            },
            None => sender.0.notified().await,
        }
    }
}

/// What the sender does after a step.
#[derive(Debug, PartialEq, Eq)]
pub enum Next {
    /// Look again right away: an email has just been dealt with.
    Now,
    /// Nothing can go before this time, in UTC milliseconds.
    At(i64),
    /// Nothing is waiting to go.
    Idle,
}

/// Sends the first approved email that can go at `now`, if any.
pub async fn step(db: &SqlitePool, net: &Net, now: i64) -> Result<Next, sqlx::Error> {
    let due = sqlx::query!(
        "SELECT e.id, m.mailbox_id AS own FROM email e JOIN mentor m ON m.id = e.mentor_id
         WHERE e.state = 'approved' AND e.planned_at <= ?
         ORDER BY e.planned_at, e.id",
        now
    )
    .fetch_all(db)
    .await?;
    let mut next = sqlx::query_scalar!(
        r#"SELECT min(planned_at) AS "at: i64" FROM email WHERE state = 'approved' AND planned_at > ?"#,
        now
    )
    .fetch_one(db)
    .await?;
    let rotation = profile::rotation(db).await?;
    let mailboxes = usage(db, now).await?;
    for e in due {
        match choose(rotation, e.own, &mailboxes, now) {
            Some((mailbox, at)) if at <= now => {
                send(db, net, e.id, mailbox, now).await?;
                return Ok(Next::Now);
            }
            Some((_, at)) => next = Some(next.map_or(at, |n| n.min(at))),
            // Waits for a mailbox to be added.
            None => {}
        }
    }
    Ok(next.map_or(Next::Idle, Next::At))
}

/// What a mailbox has sent lately.
#[derive(Debug)]
struct Usage {
    id: i64,
    daily_limit: i64,
    min_interval_ms: i64,
    paused_until: Option<i64>,
    /// Sent in the last 24 hours, and the earliest of those.
    recent: i64,
    earliest_recent: Option<i64>,
    last: Option<i64>,
}

impl Usage {
    /// Neither paused nor at its daily limit.
    fn open(&self, now: i64) -> bool {
        self.paused_until.is_none_or(|t| t <= now) && self.recent < self.daily_limit
    }

    /// When it may send next: after its pause and the minimum interval, and at its limit, once the
    /// earliest of the last 24 hours' emails is a day old.
    fn ready_at(&self, now: i64) -> i64 {
        let mut at = now.max(self.paused_until.unwrap_or(now));
        if let Some(last) = self.last {
            at = at.max(last + self.min_interval_ms);
        }
        if self.recent >= self.daily_limit {
            at = at.max(self.earliest_recent.unwrap_or(now) + DAY);
        }
        at
    }
}

/// The mailboxes in rotation order, with what each has sent lately.
async fn usage(db: &SqlitePool, now: i64) -> Result<Vec<Usage>, sqlx::Error> {
    sqlx::query_as!(
        Usage,
        r#"SELECT b.id, b.daily_limit, b.min_interval_s * 1000 AS "min_interval_ms!: i64", b.paused_until,
             (SELECT count(*) FROM email e
              WHERE e.mailbox_id = b.id AND e.state IN ('sending', 'maybe_sent', 'sent', 'bounced')
                AND e.sent_at > ?1) AS "recent!: i64",
             (SELECT min(e.sent_at) FROM email e
              WHERE e.mailbox_id = b.id AND e.state IN ('sending', 'maybe_sent', 'sent', 'bounced')
                AND e.sent_at > ?1) AS "earliest_recent: i64",
             (SELECT max(e.sent_at) FROM email e
              WHERE e.mailbox_id = b.id AND e.state IN ('sending', 'maybe_sent', 'sent', 'bounced'))
               AS "last: i64"
           FROM mailbox b ORDER BY b.rotation_order, b.id"#,
        now - DAY
    )
    .fetch_all(db)
    .await
}

/// The mailbox to send an email from and when it can go: the mentor's own mailbox, or one by
/// rotation (docs/design.md#多邮箱轮转). None when there are no mailboxes.
fn choose(
    rotation: Rotation,
    own: Option<i64>,
    mailboxes: &[Usage],
    now: i64,
) -> Option<(i64, i64)> {
    let chosen = match own {
        Some(id) => mailboxes.iter().find(|m| m.id == id),
        None => {
            // Ties go to the mailbox that has gone longest without sending, then to rotation order.
            let soonest = mailboxes.iter().min_by_key(|m| (m.ready_at(now), m.last));
            match rotation {
                Rotation::Failover => mailboxes.iter().find(|m| m.open(now)).or(soonest),
                Rotation::PerEmail => soonest,
            }
        }
    };
    chosen.map(|m| (m.id, m.ready_at(now)))
}

/// Logs in, marks the email as being sent, and hands it over (docs/design.md#桌面端).
async fn send(
    db: &SqlitePool,
    net: &Net,
    id: i64,
    mailbox_id: i64,
    now: i64,
) -> Result<(), sqlx::Error> {
    // Deleted since the mailboxes were read; the next step picks another.
    let Some(account) = mailbox::account(db, mailbox_id).await? else {
        return Ok(());
    };
    let e = sqlx::query!(
        r#"SELECT e.mentor_id, e.message_id, e.subject AS "subject!", e.html AS "html!",
             e.text AS "text!", m.name, m.email
           FROM email e JOIN mentor m ON m.id = e.mentor_id WHERE e.id = ?"#,
        id
    )
    .fetch_one(db)
    .await?;
    let Some(to) = e.email else {
        return email::failed(db, id, NO_ADDRESS, now).await;
    };
    let message_id = e
        .message_id
        .unwrap_or_else(|| mailbox::message_id(&account));
    let letter = Letter {
        message_id: &message_id,
        to_name: &e.name,
        to: &to,
        subject: &e.subject,
        html: &e.html,
        text: &e.text,
    };
    let prepared = async {
        let outgoing = mailbox::compose(&account, &letter)?;
        Ok::<_, MailError>((mailbox::login(net, &account).await?, outgoing))
    };
    let (smtp, outgoing) = match prepared.await {
        Ok(prepared) => prepared,
        Err(err) => {
            tracing::warn!("email {id} not sent from mailbox {mailbox_id}: {err}");
            return email::failed(db, id, &err.message(), now).await;
        }
    };
    let mut tx = db.begin().await?;
    if !email::start_sending(
        &mut tx,
        id,
        mailbox_id,
        account.address.as_ref(),
        &message_id,
        now,
    )
    .await?
    {
        smtp.quit();
        return Ok(());
    }
    mentor::assign_mailbox(&mut tx, e.mentor_id, mailbox_id).await?;
    tx.commit().await?;
    match smtp.send(&outgoing).await {
        Ok(()) => {
            tracing::info!("email {id} sent from mailbox {mailbox_id}");
            email::sent(db, id, now).await
        }
        Err(err) => {
            tracing::warn!("email {id} from mailbox {mailbox_id}: {err}");
            match err {
                SendError::NotSent(_) => email::failed(db, id, &err.message(), now).await,
                SendError::Unknown(_) => email::maybe_sent(db, id, &err.message(), now).await,
            }
        }
    }
}

/// UTC milliseconds.
fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 100 * DAY;

    /// A mailbox allowed two emails a day, a minute apart, that last sent at `last`.
    fn mailbox(id: i64, recent: i64, last: Option<i64>) -> Usage {
        Usage {
            id,
            daily_limit: 2,
            min_interval_ms: MINUTE,
            paused_until: None,
            recent,
            earliest_recent: last,
            last,
        }
    }

    #[test]
    fn failover_keeps_to_the_first_open_mailbox() {
        let waiting = [mailbox(1, 1, Some(NOW - 1000)), mailbox(2, 0, None)];
        assert_eq!(
            choose(Rotation::Failover, None, &waiting, NOW),
            Some((1, NOW - 1000 + MINUTE))
        );
        let full = [mailbox(1, 2, Some(NOW - 1000)), mailbox(2, 0, None)];
        assert_eq!(choose(Rotation::Failover, None, &full, NOW), Some((2, NOW)));
        let paused = [
            Usage {
                paused_until: Some(NOW + MINUTE),
                ..mailbox(1, 0, None)
            },
            mailbox(2, 0, None),
        ];
        assert_eq!(
            choose(Rotation::Failover, None, &paused, NOW),
            Some((2, NOW))
        );
        let all_full = [
            Usage {
                earliest_recent: Some(NOW - 5 * MINUTE),
                ..mailbox(1, 2, Some(NOW - 1000))
            },
            Usage {
                earliest_recent: Some(NOW - 9 * MINUTE),
                ..mailbox(2, 2, Some(NOW - 2000))
            },
        ];
        assert_eq!(
            choose(Rotation::Failover, None, &all_full, NOW),
            Some((2, NOW - 9 * MINUTE + DAY))
        );
    }

    #[test]
    fn per_email_takes_the_mailbox_that_has_waited_longest() {
        let unused = [mailbox(1, 1, Some(NOW - 2 * MINUTE)), mailbox(2, 0, None)];
        assert_eq!(
            choose(Rotation::PerEmail, None, &unused, NOW),
            Some((2, NOW))
        );
        let used = [
            mailbox(1, 1, Some(NOW - 2 * MINUTE)),
            mailbox(2, 1, Some(NOW - 3 * MINUTE)),
        ];
        assert_eq!(choose(Rotation::PerEmail, None, &used, NOW), Some((2, NOW)));
        let busy = [
            mailbox(1, 1, Some(NOW - 1000)),
            mailbox(2, 1, Some(NOW - 2000)),
        ];
        assert_eq!(
            choose(Rotation::PerEmail, None, &busy, NOW),
            Some((2, NOW - 2000 + MINUTE))
        );
    }

    #[test]
    fn a_mentors_own_mailbox_is_waited_for() {
        let mailboxes = [mailbox(1, 2, Some(NOW - 1000)), mailbox(2, 0, None)];
        for rotation in [Rotation::Failover, Rotation::PerEmail] {
            assert_eq!(
                choose(rotation, Some(1), &mailboxes, NOW),
                Some((1, NOW - 1000 + DAY))
            );
        }
        assert_eq!(choose(Rotation::Failover, None, &[], NOW), None);
    }
}
