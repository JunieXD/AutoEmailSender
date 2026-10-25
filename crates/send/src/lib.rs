//! Sending approved emails from the user's mailboxes, one at a time (docs/design.md#桌面端).

pub mod plan;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use mailbox::pause::{self, Failure};
use mailbox::{Letter, SendError, SendStatus};
use net::Net;
use profile::Rotation;
use sqlx::{Connection, SqlitePool};
use tokio::sync::Notify;

const MINUTE: i64 = 60_000;
const DAY: i64 = 24 * 60 * MINUTE;
/// How long to wait before trying the database again.
const RETRY: Duration = Duration::from_secs(5);

/// Wakes the sender, so that an email approved or retried goes out without waiting.
#[derive(Clone, Default)]
pub struct Sender(Arc<Notify>);

impl Sender {
    pub fn wake(&self) {
        self.0.notify_one();
    }
}

/// Sends emails as they come due, for as long as the app runs, with the files of the materials
/// attached from `materials`.
pub async fn run(db: SqlitePool, net: Net, materials: PathBuf, sender: Sender) {
    while let Err(e) = recover(&db, now()).await {
        tracing::error!("recover interrupted emails: {e}");
        tokio::time::sleep(RETRY).await;
    }
    loop {
        let wait = match step(&db, &net, &materials, now).await {
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

/// Emails the last run left being sent may have gone out, so they are marked maybe sent, and their
/// mentors are contacted from those mailboxes from now on. `run` does this first.
pub async fn recover(db: &SqlitePool, now: i64) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    for e in email::interrupted(&mut tx, now).await? {
        if let Some(mailbox_id) = e.mailbox_id {
            mentor::assign_mailbox(&mut tx, e.mentor_id, mailbox_id).await?;
        }
    }
    tx.commit().await
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

/// Sends the first approved email that can go now, if any. `now` tells the time in UTC
/// milliseconds; sending takes a while.
pub async fn step(
    db: &SqlitePool,
    net: &Net,
    materials: &Path,
    now: impl Fn() -> i64,
) -> Result<Next, sqlx::Error> {
    step_in_zone(db, net, materials, now, &chrono::Local, &mut |n| {
        fastrand::i64(0..n)
    })
    .await
}

pub async fn step_in_zone<T: chrono::TimeZone>(
    db: &SqlitePool,
    net: &Net,
    materials: &Path,
    now: impl Fn() -> i64,
    zone: &T,
    random: &mut impl FnMut(i64) -> i64,
) -> Result<Next, sqlx::Error> {
    let at = now();
    email::schedule::rollover(db, at, zone, random).await?;
    let due = sqlx::query!(
        r#"SELECT e.id,
             CASE WHEN e.kind IN ('reply', 'follow_up') THEN coalesce(m.mailbox_id,
               (SELECT p.mailbox_id FROM email p
                WHERE p.mentor_id = e.mentor_id
                  AND (p.message_id = e.reply_to OR p.copy_message_id = e.reply_to)),
               (SELECT r.mailbox_id FROM message r
                JOIN message_mentor mm ON mm.message_id = r.id
                WHERE mm.mentor_id = e.mentor_id AND r.message_id = e.reply_to
                  AND r.kind = CASE e.kind WHEN 'reply' THEN 'reply' ELSE 'sent' END))
             ELSE m.mailbox_id END AS own,
             e.kind IN ('reply', 'follow_up') AS "reply!: bool"
           FROM email e
           JOIN mentor m ON m.id = e.mentor_id
           WHERE e.state = 'approved'
             AND e.planned_at <= ?1
             AND (e.latest_at IS NULL OR e.latest_at > ?1)
             AND (e.task_id IS NULL OR e.task_id IN (SELECT id FROM task WHERE state = 'active'
               AND generation_paused_at IS NULL))
           ORDER BY e.planned_at, e.id"#,
        at
    )
    .fetch_all(db)
    .await?;
    let mut next = sqlx::query_scalar!(
        r#"SELECT min(planned_at) AS "at: i64"
           FROM email
           WHERE state = 'approved'
             AND planned_at > ?1
             AND (latest_at IS NULL OR latest_at > ?1)
             AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state = 'active' AND
               generation_paused_at IS NULL))"#,
        at
    )
    .fetch_one(db)
    .await?;
    let deadline = sqlx::query_scalar!(
        r#"SELECT min(latest_at) AS "at: i64"
           FROM email
           WHERE state = 'approved'
             AND latest_at > ?"#,
        at
    )
    .fetch_one(db)
    .await?;
    if let Some(deadline) = deadline {
        next = Some(next.map_or(deadline, |n| n.min(deadline)));
    }
    let rotation = profile::rotation(db).await?;
    let mailboxes = usage_on(&mut *db.acquire().await?, at).await?;
    for e in due {
        match choose(rotation, e.own, e.reply, &mailboxes, at) {
            Some((mailbox, ready)) if ready <= at => {
                send(db, net, materials, e.id, mailbox, &now, zone).await?;
                return Ok(Next::Now);
            }
            Some((_, ready)) => next = Some(next.map_or(ready, |n| n.min(ready))),
            None if e.reply => {
                email::failed(
                    &mut *db.acquire().await?,
                    e.id,
                    "没有能发信的邮箱，去设置里添加或在导师资料里指定负责邮箱后重试。",
                    at,
                )
                .await?;
                return Ok(Next::Now);
            }
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
async fn usage_on(conn: &mut sqlx::SqliteConnection, now: i64) -> Result<Vec<Usage>, sqlx::Error> {
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
    .fetch_all(conn)
    .await
}

/// The mailbox to send an email from and when it can go: the mentor's own mailbox, or one by
/// rotation (docs/design.md#多邮箱轮转). None when there are no mailboxes.
fn choose(
    rotation: Rotation,
    own: Option<i64>,
    reply: bool,
    mailboxes: &[Usage],
    now: i64,
) -> Option<(i64, i64)> {
    let chosen = match own {
        Some(id) => mailboxes.iter().find(|m| m.id == id),
        None if reply => None,
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

/// Logs in, marks the email as being handed over, and hands it over (docs/design.md#桌面端).
async fn send<T: chrono::TimeZone>(
    db: &SqlitePool,
    net: &Net,
    materials: &Path,
    id: i64,
    mailbox_id: i64,
    now: &impl Fn() -> i64,
    zone: &T,
) -> Result<(), sqlx::Error> {
    // Deleted since the mailboxes were read; the next step picks another.
    let Some(account) = mailbox::account(db, mailbox_id).await? else {
        return Ok(());
    };
    let smtp = match mailbox::login(net, &account).await {
        Ok(smtp) => smtp,
        Err(err) => {
            tracing::warn!("email {id} not sent from mailbox {mailbox_id}: {err}");
            return match pause::classify(&err) {
                Failure::Pause(kind) => {
                    let pause = kind.in_zone(now(), zone);
                    pause::apply_for_account(
                        &mut *db.acquire().await?,
                        mailbox_id,
                        &account,
                        &pause,
                    )
                    .await
                }
                Failure::Email => {
                    email::failed(&mut *db.acquire().await?, id, &err.message(), now()).await
                }
            };
        }
    };
    let attached = email::attachment_files(&mut *db.acquire().await?, id).await?;
    let files = files(materials, &attached).await;
    let Some(m) = mark_sending(db, id, &account, mailbox_id, now, &attached, files).await? else {
        smtp.quit();
        return Ok(());
    };
    let outcome = match smtp
        .send(&m.outgoing, || {
            m.before.latest_at.is_some_and(|latest| now() >= latest)
        })
        .await
    {
        Ok(SendStatus::Expired) => return m.before.restore(&mut *db.acquire().await?, id).await,
        Ok(SendStatus::Sent) => {
            tracing::info!("email {id} sent from mailbox {mailbox_id}");
            Outcome::Sent
        }
        Err(err) => {
            tracing::warn!("email {id} from mailbox {mailbox_id}: {err}");
            match err {
                SendError::NotSent(ref error) => match pause::classify(error) {
                    Failure::Pause(kind) => Outcome::Paused(kind.in_zone(now(), zone)),
                    Failure::Email => Outcome::Failed(err.message()),
                },
                SendError::Unknown(_) => Outcome::MaybeSent(err.message()),
            }
        }
    };
    // Only this run knows how SMTP answered, so it keeps trying until that is written.
    let answered = now();
    while let Err(e) = record(db, id, mailbox_id, &account, &m, &outcome, answered).await {
        tracing::error!("record email {id}: {e}");
        tokio::time::sleep(RETRY).await;
    }
    Ok(())
}

/// The files to attach, read now: a material may be attached to emails for days before they go.
async fn files(dir: &Path, attachments: &[email::Attached]) -> Result<Vec<mailbox::File>, String> {
    let mut files = Vec::with_capacity(attachments.len());
    for a in attachments {
        let data = tokio::fs::read(material::file(dir, a.material_id)).await.map_err(|e| {
            tracing::warn!("reading attachment {}: {e}", a.file_name);
            format!(
                "附件“{}”的文件读不到了。在“设置 › 个人 › 材料”里重新添加它，改回草稿换上新的附件后再发送。",
                a.file_name
            )
        })?;
        files.push(mailbox::File {
            name: a.file_name.clone(),
            data,
        });
    }
    Ok(files)
}

struct Marked {
    mentor_id: i64,
    outgoing: mailbox::Outgoing,
    before: BeforeHandover,
}

/// A retry can already carry delivery evidence from an earlier attempt; keep it intact if this
/// attempt ends without handing anything to SMTP.
struct BeforeHandover {
    latest_at: Option<i64>,
    mailbox_id: Option<i64>,
    from_address: Option<String>,
    to_address: Option<String>,
    message_id: Option<String>,
    sent_at: Option<i64>,
    updated_at: i64,
}

impl BeforeHandover {
    /// A known rejection proves nothing about this attempt; only the previous attempt may still
    /// have a late copy or bounce. Keep the Message-ID for duplicate suppression on another retry.
    async fn failed(
        &self,
        conn: &mut sqlx::SqliteConnection,
        id: i64,
        error: &str,
        now: i64,
    ) -> Result<(), sqlx::Error> {
        email::failed(conn, id, error, now).await?;
        sqlx::query!(
            r#"UPDATE email SET mailbox_id = (SELECT id FROM mailbox WHERE id = ?1),
                 from_address = ?2, to_address = ?3, message_id = coalesce(?4, message_id),
                 sent_at = ?5
               WHERE id = ?6 AND state IN ('failed', 'canceled')"#,
            self.mailbox_id,
            self.from_address,
            self.to_address,
            self.message_id,
            self.sent_at,
            id,
        )
        .execute(conn)
        .await?;
        Ok(())
    }

    async fn restore(&self, conn: &mut sqlx::SqliteConnection, id: i64) -> Result<(), sqlx::Error> {
        sqlx::query!(
            r#"UPDATE email SET
                 state = CASE WHEN cancel_reason IS NULL THEN 'approved' ELSE 'canceled' END,
                 planned_at = CASE WHEN cancel_reason IS NULL THEN planned_at END,
                 latest_at = CASE WHEN cancel_reason IS NULL THEN ?1 END,
                 mailbox_id = (SELECT id FROM mailbox WHERE id = ?2),
                 from_address = ?3, to_address = ?4, message_id = ?5, sent_at = ?6,
                 error = NULL,
                 updated_at = CASE WHEN cancel_reason IS NULL THEN ?7 ELSE updated_at END
               WHERE id = ?8 AND state = 'sending'"#,
            self.latest_at,
            self.mailbox_id,
            self.from_address,
            self.to_address,
            self.message_id,
            self.sent_at,
            self.updated_at,
            id,
        )
        .execute(conn)
        .await?;
        Ok(())
    }
}

/// Marks the email as being handed over in a transaction synced to the disk itself: were the mark
/// lost to a power cut, the email would go out again after a restart. The connection is closed
/// after, so the pool's connections keep their faster setting.
async fn mark_sending(
    db: &SqlitePool,
    id: i64,
    account: &mailbox::Account,
    mailbox_id: i64,
    now: &impl Fn() -> i64,
    attached: &[email::Attached],
    files: Result<Vec<mailbox::File>, String>,
) -> Result<Option<Marked>, sqlx::Error> {
    let mut conn = db.acquire().await?.detach();
    // fullfsync, on macOS, gets the commit past the drive's own cache.
    sqlx::raw_sql("PRAGMA synchronous = FULL; PRAGMA fullfsync = ON")
        .execute(&mut conn)
        .await?;
    let mut tx = conn.begin_with("BEGIN IMMEDIATE").await?;
    let at = now();
    let Some(before) = sqlx::query_as!(
        BeforeHandover,
        r#"SELECT latest_at, mailbox_id, from_address, to_address, message_id, sent_at,
             updated_at
           FROM email
           WHERE id = ?"#,
        id
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(None);
    };
    let message_id = mailbox::message_id(account);
    let from: &str = account.address.as_ref();
    let Some(handover) =
        email::start_sending(&mut tx, id, mailbox_id, from, &message_id, at).await?
    else {
        return Ok(None);
    };
    // The approval may have changed while files were being read. Re-read its files next step.
    if handover.attachments != attached {
        return Ok(None);
    }
    let to_name = sqlx::query_scalar!("SELECT name FROM mentor WHERE id = ?", handover.mentor_id)
        .fetch_one(&mut *tx)
        .await?;
    let outgoing = files.and_then(|files| {
        let to = handover.to_address.as_deref().ok_or(email::NO_ADDRESS)?;
        mailbox::compose(
            account,
            &Letter {
                message_id: &handover.message_id,
                in_reply_to: handover.reply_to.as_deref(),
                references: handover.refs.as_deref(),
                to_name: &to_name,
                to,
                subject: &handover.subject,
                html: &handover.html,
                text: &handover.text,
                attachments: &files,
            },
        )
        .map_err(|e| e.message())
    });
    if before.latest_at.is_some_and(|latest| now() >= latest) {
        return Ok(None);
    }
    let outgoing = match outgoing {
        Ok(outgoing) => outgoing,
        Err(error) => {
            before.failed(&mut tx, id, &error, now()).await?;
            tx.commit().await?;
            return Ok(None);
        }
    };
    tx.commit().await?;
    conn.close().await?;
    Ok(Some(Marked {
        mentor_id: handover.mentor_id,
        outgoing,
        before,
    }))
}

/// How SMTP answered.
enum Outcome {
    Sent,
    MaybeSent(String),
    Failed(String),
    Paused(pause::Pause),
}

async fn record(
    db: &SqlitePool,
    id: i64,
    mailbox_id: i64,
    account: &mailbox::Account,
    marked: &Marked,
    outcome: &Outcome,
    now: i64,
) -> Result<(), sqlx::Error> {
    let mut tx = db.begin().await?;
    match outcome {
        Outcome::Sent => email::sent(&mut tx, id, now).await?,
        Outcome::MaybeSent(error) => email::maybe_sent(&mut tx, id, error, now).await?,
        Outcome::Paused(pause) => {
            pause::apply_for_account(&mut tx, mailbox_id, account, pause).await?;
            marked.before.restore(&mut tx, id).await?;
            return tx.commit().await;
        }
        Outcome::Failed(error) => {
            marked.before.failed(&mut tx, id, error, now).await?;
            return tx.commit().await;
        }
    }
    // It went out, or may have: later emails to this mentor go from the same mailbox.
    mentor::assign_mailbox(&mut tx, marked.mentor_id, mailbox_id).await?;
    tx.commit().await
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
            choose(Rotation::Failover, None, false, &waiting, NOW),
            Some((1, NOW - 1000 + MINUTE))
        );
        let full = [mailbox(1, 2, Some(NOW - 1000)), mailbox(2, 0, None)];
        assert_eq!(
            choose(Rotation::Failover, None, false, &full, NOW),
            Some((2, NOW))
        );
        let paused = [
            Usage {
                paused_until: Some(NOW + MINUTE),
                ..mailbox(1, 0, None)
            },
            mailbox(2, 0, None),
        ];
        assert_eq!(
            choose(Rotation::Failover, None, false, &paused, NOW),
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
            choose(Rotation::Failover, None, false, &all_full, NOW),
            Some((2, NOW - 9 * MINUTE + DAY))
        );
    }

    #[test]
    fn per_email_takes_the_mailbox_that_has_waited_longest() {
        let unused = [mailbox(1, 1, Some(NOW - 2 * MINUTE)), mailbox(2, 0, None)];
        assert_eq!(
            choose(Rotation::PerEmail, None, false, &unused, NOW),
            Some((2, NOW))
        );
        let used = [
            mailbox(1, 1, Some(NOW - 2 * MINUTE)),
            mailbox(2, 1, Some(NOW - 3 * MINUTE)),
        ];
        assert_eq!(
            choose(Rotation::PerEmail, None, false, &used, NOW),
            Some((2, NOW))
        );
        let busy = [
            mailbox(1, 1, Some(NOW - 1000)),
            mailbox(2, 1, Some(NOW - 2000)),
        ];
        assert_eq!(
            choose(Rotation::PerEmail, None, false, &busy, NOW),
            Some((2, NOW - 2000 + MINUTE))
        );
    }

    #[test]
    fn a_mentors_own_mailbox_is_waited_for() {
        let mailboxes = [mailbox(1, 2, Some(NOW - 1000)), mailbox(2, 0, None)];
        for rotation in [Rotation::Failover, Rotation::PerEmail] {
            assert_eq!(
                choose(rotation, Some(1), false, &mailboxes, NOW),
                Some((1, NOW - 1000 + DAY))
            );
        }
        assert_eq!(choose(Rotation::Failover, None, false, &[], NOW), None);
    }
}
