//! Receiving from the user's mailboxes: mail to and from mentors linked to them, the app's own
//! emails confirmed sent or bounced, and the earlier mail with each mentor looked up
//! (docs/design.md#桌面端).

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use email::SentCopy;
use mailbox::{Imap, MailError};
use net::Net;
use serde::Serialize;
use sqlx::SqlitePool;
use sqlx::types::Json;
use tokio::sync::{Mutex, MutexGuard, Notify, watch};
use ts_rs::TS;

mod mail;
mod quote;

const MINUTE: i64 = 60_000;
/// How long to wait before receiving again.
const ROUND: Duration = Duration::from_secs(5 * 60);
/// How long to wait when mentors are still to be looked up.
const SOON: Duration = Duration::from_secs(30);
/// How long to leave a server that refused, so that a wrong password is not tried until the
/// provider blocks the account.
const REFUSED: i64 = 60 * MINUTE;
/// How far back earlier mail is looked up.
const HISTORY: i64 = 730 * 24 * 60 * MINUTE;
/// Mentor addresses looked up in one search.
const BATCH: usize = 20;
/// Mentor addresses looked up in one round, in one mailbox.
const SCANS: i64 = 200;
/// Messages whose headers are fetched at once.
const CHUNK: usize = 50;
/// How much of a message is fetched: its text, without large attachments.
const START: u32 = 256 * 1024;

/// Wakes the receiver, so that a new mailbox or mentor is looked at without waiting, and holds it
/// off while a mailbox changes.
#[derive(Clone, Default)]
pub struct Receiver(Arc<Shared>);

#[derive(Default)]
struct Shared {
    wake: Notify,
    ended: watch::Sender<Ended>,
    stop: Notify,
    /// Held by a round, and by a mailbox changing.
    busy: Mutex<()>,
}

/// How the last round ended.
#[derive(Default)]
struct Ended {
    /// When a round last received.
    received_at: Option<i64>,
    /// Why the last round failed, if it did.
    error: Option<Arc<sqlx::Error>>,
}

impl Receiver {
    pub fn received_at(&self) -> Option<i64> {
        self.0.ended.borrow().received_at
    }

    /// Receives from every mailbox, returning once a round has ended.
    pub async fn receive_now(&self) -> Result<(), Arc<sqlx::Error>> {
        let mut ended = self.0.ended.subscribe();
        self.wake();
        // The sender stays in this Receiver, so the channel cannot close while waiting.
        let _ = ended.changed().await;
        ended.borrow().error.clone().map_or(Ok(()), Err)
    }

    pub fn wake(&self) {
        self.0.wake.notify_one();
    }

    /// Stops the round under way and keeps the next from starting until the guard is dropped, so
    /// that no round goes on with a mailbox's old settings, or with one deleted.
    pub async fn hold(&self) -> MutexGuard<'_, ()> {
        self.0.stop.notify_waiters();
        self.0.busy.lock().await
    }
}

/// Receives from every mailbox now and then, for as long as the app runs; `changed` is called
/// after a round changed what the UI shows.
pub async fn run(db: SqlitePool, net: Net, receiver: Receiver, changed: impl Fn(Vec<NewReply>)) {
    let shared = &receiver.0;
    loop {
        // Before taking the lock, so that a hold asked for meanwhile still stops the round.
        let stop = shared.stop.notified();
        let busy = shared.busy.lock().await;
        let done = tokio::select! {
            done = round(&db, &net, now()) => Some(done),
            () = stop => None,
        };
        drop(busy);
        let wait = match done {
            Some(Ok(r)) => {
                shared.ended.send_replace(Ended {
                    received_at: Some(now()),
                    error: None,
                });
                if r.changed {
                    changed(r.replies);
                }
                if r.more { SOON } else { ROUND }
            }
            // Only someone receiving now hears of it; the database is likely to work again in a
            // while.
            Some(Err(e)) => {
                tracing::error!("receive: {e}");
                shared
                    .ended
                    .send_modify(|ended| ended.error = Some(Arc::new(e)));
                ROUND
            }
            // Stopped for a mailbox to change, perhaps after storing mail. Every write is safe to
            // stop at, and the next round starts once the change is made.
            None => {
                changed(Vec::new());
                continue;
            }
        };
        tokio::select! {
            () = tokio::time::sleep(wait) => {}
            () = shared.wake.notified() => {}
        }
    }
}

/// What a round of receiving did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Round {
    /// Mail was stored, emails' states changed, or a mailbox's receiving failed or recovered.
    pub changed: bool,
    /// Mentors are still to be looked up.
    pub more: bool,
    pub replies: Vec<NewReply>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct NewReply {
    pub mentors: Vec<String>,
    pub subject: Option<String>,
}

/// Receives once from every mailbox the server has not lately refused. A mailbox that fails says
/// why in its settings; the others go on.
pub async fn round(db: &SqlitePool, net: &Net, now: i64) -> Result<Round, sqlx::Error> {
    let mut round = Round::default();
    for id in mailbox::receivable(db, now).await? {
        match receive(db, net, id, now, &mut round).await {
            Ok(()) => round.changed |= mailbox::received(db, id).await?,
            Err(Error::Mail(e)) => {
                tracing::warn!(mailbox = id, "receive: {e}");
                let refused = matches!(e, MailError::Login(_) | MailError::Refused(_));
                mailbox::receive_failed(db, id, &e.message(), refused.then_some(now + REFUSED))
                    .await?;
                round.changed = true;
            }
            Err(Error::Db(e)) => return Err(e),
        }
    }
    Ok(round)
}

#[derive(Debug)]
enum Error {
    Mail(MailError),
    Db(sqlx::Error),
}

impl From<MailError> for Error {
    fn from(e: MailError) -> Self {
        Error::Mail(e)
    }
}

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Error::Db(e)
    }
}

/// Which folder of a mailbox.
#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
enum Role {
    Inbox,
    Sent,
}

/// Receives from one mailbox: new mail in its sent folder, then its inbox, where bounces may
/// refer to mail in the sent folder; then earlier mail with the mentors still to be looked up.
async fn receive(
    db: &SqlitePool,
    net: &Net,
    mailbox_id: i64,
    now: i64,
    round: &mut Round,
) -> Result<(), Error> {
    let Some(account) = mailbox::account(db, mailbox_id).await? else {
        return Ok(());
    };
    let own = sqlx::query_scalar!(r#"SELECT lower(address) AS "address!: String" FROM mailbox"#)
        .fetch_all(db)
        .await?;
    let mut session = Session {
        db,
        imap: Imap::open(net, &account).await?,
        mailbox_id,
        own,
        now,
        round,
    };
    let sent = session.imap.sent_folder().await?;
    let folders: Vec<(Role, String)> = sent
        .map(|name| (Role::Sent, name))
        .into_iter()
        .chain([(Role::Inbox, "INBOX".to_owned())])
        .collect();
    let names: Vec<_> = folders.iter().map(|(_, n)| n).collect();
    // A sent folder that is no longer one.
    sqlx::query!(
        "DELETE FROM mailbox_folder WHERE mailbox_id = ? AND folder NOT IN (SELECT value FROM json_each(?))",
        mailbox_id,
        Json(&names)
    )
    .execute(db)
    .await?;
    for (role, folder) in &folders {
        session.new_mail(*role, folder).await?;
    }
    session.look_up(&folders).await?;
    session.imap.close();
    Ok(())
}

struct Session<'a> {
    db: &'a SqlitePool,
    imap: Imap,
    mailbox_id: i64,
    /// The user's addresses, in lowercase.
    own: Vec<String>,
    now: i64,
    round: &'a mut Round,
}

/// What to do with a message, from its headers.
enum Plan {
    Skip,
    /// Perhaps a bounce, which the start of the message tells.
    Bounce,
    /// Mail to or from these mentors.
    Keep {
        kind: Kind,
        mentors: Vec<i64>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
enum Kind {
    Reply,
    AutoReply,
    Sent,
}

impl Session<'_> {
    /// Takes the mail that came since the last round. A folder seen for the first time, or
    /// renumbered, starts from its latest mail; the earlier mail is looked up mentor by mentor.
    async fn new_mail(&mut self, role: Role, folder: &str) -> Result<(), Error> {
        let opened = self.imap.examine(folder).await?;
        let known = sqlx::query!(
            r#"SELECT uidvalidity AS "uidvalidity: u32", last_uid AS "last_uid: u32"
               FROM mailbox_folder WHERE mailbox_id = ? AND folder = ?"#,
            self.mailbox_id,
            folder
        )
        .fetch_optional(self.db)
        .await?;
        let Some(known) = known.filter(|k| k.uidvalidity == opened.uidvalidity) else {
            let mut tx = self.db.begin().await?;
            sqlx::query!(
                "INSERT INTO mailbox_folder (mailbox_id, folder, role, uidvalidity, last_uid)
                 VALUES (?1, ?2, ?3, ?4, ?5)
                 ON CONFLICT DO UPDATE SET role = ?3, uidvalidity = ?4, last_uid = ?5",
                self.mailbox_id,
                folder,
                role,
                opened.uidvalidity,
                opened.last_uid,
            )
            .execute(&mut *tx)
            .await?;
            sqlx::query!(
                "INSERT OR IGNORE INTO mentor_scan (mailbox_id, address)
                 SELECT ?, address FROM mentor_email",
                self.mailbox_id
            )
            .execute(&mut *tx)
            .await?;
            tx.commit().await?;
            return Ok(());
        };
        if opened.last_uid <= known.last_uid {
            return Ok(());
        }
        let uids = self
            .imap
            .uids_between(known.last_uid, opened.last_uid)
            .await?;
        for chunk in uids.chunks(CHUNK) {
            self.take(role, folder, opened.uidvalidity, chunk, true)
                .await?;
            if let Some(&last) = chunk.last() {
                self.reached(folder, last).await?;
            }
        }
        self.reached(folder, opened.last_uid).await
    }

    async fn reached(&self, folder: &str, uid: u32) -> Result<(), Error> {
        sqlx::query!(
            "UPDATE mailbox_folder SET last_uid = max(last_uid, ?) WHERE mailbox_id = ? AND folder = ?",
            uid,
            self.mailbox_id,
            folder
        )
        .execute(self.db)
        .await?;
        Ok(())
    }

    /// Looks up the earlier mail at the mentor addresses whose turn it is, the newest mentors'
    /// first: up to `SCANS` of them, `BATCH` to a search.
    async fn look_up(&mut self, folders: &[(Role, String)]) -> Result<(), Error> {
        let pending = sqlx::query_scalar!(
            "SELECT s.address FROM mentor_scan s JOIN mentor_email e ON e.address = s.address
             WHERE s.mailbox_id = ? ORDER BY e.mentor_id DESC LIMIT ?",
            self.mailbox_id,
            SCANS + 1
        )
        .fetch_all(self.db)
        .await?;
        if pending.is_empty() {
            return Ok(());
        }
        self.round.more |= pending.len() > SCANS as usize;
        let pending = &pending[..pending.len().min(SCANS as usize)];
        // Searches are in ASCII; an address with other characters can only be matched as it comes.
        let addresses: Vec<&str> = pending
            .iter()
            .map(String::as_str)
            .filter(|a| a.is_ascii())
            .collect();
        let since = self.now - HISTORY;
        for (role, folder) in folders {
            let opened = self.imap.examine(folder).await?;
            for batch in addresses.chunks(BATCH) {
                let uids = match role {
                    Role::Sent => self.imap.sent_to(batch, since).await?,
                    Role::Inbox => self.imap.sent_by(batch, since).await?,
                };
                for chunk in uids.chunks(CHUNK) {
                    self.take(*role, folder, opened.uidvalidity, chunk, false)
                        .await?;
                }
            }
        }
        let mut tx = self.db.begin().await?;
        for address in pending {
            sqlx::query!(
                "DELETE FROM mentor_scan WHERE mailbox_id = ? AND address = ?",
                self.mailbox_id,
                address
            )
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        Ok(())
    }

    /// Reads the headers of these messages, then the start of those to keep or that may be
    /// bounces.
    async fn take(
        &mut self,
        role: Role,
        folder: &str,
        uidvalidity: u32,
        uids: &[u32],
        new: bool,
    ) -> Result<(), Error> {
        let heads = self.imap.headers(uids, &mail::FIELDS).await?;
        let mut wanted = Vec::new();
        // The mentors of the mail kept from these messages, which later ones may follow up on.
        let mut threads: HashMap<String, Vec<i64>> = HashMap::new();
        for head in heads {
            let headers = mail::headers(&head.header);
            let place = Location {
                folder,
                uidvalidity,
                uid: head.uid,
            };
            let sent_at = head.received_at.or(headers.date).unwrap_or(self.now);
            let plan = self.plan(role, &headers, &threads).await?;
            if let (Plan::Keep { mentors, .. }, Some(id)) = (&plan, &headers.message_id) {
                threads.insert(id.clone(), mentors.clone());
            }
            match plan {
                Plan::Skip => {}
                Plan::Bounce => wanted.push((place, sent_at, headers, None)),
                Plan::Keep { kind, mentors } => {
                    let mut conn = self.db.acquire().await?;
                    match existing(&mut conn, self.mailbox_id, &headers, &place).await? {
                        Some(id) => {
                            let mut tx = self.db.begin().await?;
                            self.link(&mut tx, id, &mentors).await?;
                            tx.commit().await?;
                        }
                        None => wanted.push((place, sent_at, headers, Some((kind, mentors)))),
                    }
                }
            }
        }
        if wanted.is_empty() {
            return Ok(());
        }
        let uids: Vec<u32> = wanted.iter().map(|(place, ..)| place.uid).collect();
        let starts: HashMap<u32, Vec<u8>> =
            self.imap.starts(&uids, START).await?.into_iter().collect();
        for (place, sent_at, headers, keep) in wanted {
            // Gone from the folder since its headers were read.
            let Some(raw) = starts.get(&place.uid) else {
                continue;
            };
            let Some((kind, mentors)) = keep else {
                self.bounced(raw).await?;
                continue;
            };
            let text = mail::text(raw);
            // Only the start was fetched.
            let truncated = raw.len() >= START as usize;
            if kind == Kind::Sent && self.renamed_copy(&headers, sent_at, raw, truncated).await? {
                continue;
            }
            let message = Stored {
                kind,
                sent_at,
                text,
                truncated,
            };
            self.store(&place, &headers, &message, &mentors, new)
                .await?;
        }
        Ok(())
    }

    async fn plan(
        &mut self,
        role: Role,
        headers: &mail::Headers,
        threads: &HashMap<String, Vec<i64>>,
    ) -> Result<Plan, Error> {
        let Some(from) = &headers.from else {
            return Ok(Plan::Skip);
        };
        // A read receipt is neither a reply nor a bounce.
        if headers.receipt {
            return Ok(Plan::Skip);
        }
        if role == Role::Sent {
            if let Some(id) = &headers.message_id {
                let mut conn = self.db.acquire().await?;
                match email::sent_copy(&mut conn, id, &headers.recipients, self.now).await? {
                    SentCopy::Foreign => {}
                    SentCopy::Own => return Ok(Plan::Skip),
                    SentCopy::Confirmed => {
                        self.round.changed = true;
                        return Ok(Plan::Skip);
                    }
                }
            }
            let mentors = self.mentors_at(&headers.recipients).await?;
            return Ok(if mentors.is_empty() {
                Plan::Skip
            } else {
                Plan::Keep {
                    kind: Kind::Sent,
                    mentors,
                }
            });
        }
        if self.own.contains(from) {
            return Ok(Plan::Skip);
        }
        if headers.report {
            return Ok(Plan::Bounce);
        }
        // A mentor writing from another address, such as a secretary, answers in the thread.
        let mut mentors = self.mentors_at(std::slice::from_ref(from)).await?;
        if mentors.is_empty() {
            let thread = headers.thread();
            mentors = self.mentors_in_thread(&thread).await?;
            mentors.extend(thread.iter().filter_map(|id| threads.get(id)).flatten());
            mentors.sort_unstable();
            mentors.dedup();
        }
        let kind = if headers.automatic {
            Kind::AutoReply
        } else {
            Kind::Reply
        };
        Ok(if mentors.is_empty() {
            Plan::Skip
        } else {
            Plan::Keep { kind, mentors }
        })
    }

    async fn mentors_at(&self, addresses: &[String]) -> Result<Vec<i64>, Error> {
        Ok(sqlx::query_scalar!(
            r#"SELECT DISTINCT mentor_id FROM mentor_email
               WHERE address IN (SELECT value FROM json_each(?))"#,
            Json(addresses)
        )
        .fetch_all(self.db)
        .await?)
    }

    /// The mentors of the mail a message follows up on.
    async fn mentors_in_thread(&self, ids: &[String]) -> Result<Vec<i64>, Error> {
        Ok(sqlx::query_scalar!(
            r#"SELECT mentor_id AS "mentor_id!" FROM email
               WHERE message_id IN (SELECT value FROM json_each(?1))
                 OR copy_message_id IN (SELECT value FROM json_each(?1))
               UNION
               SELECT mm.mentor_id FROM message m JOIN message_mentor mm ON mm.message_id = m.id
               WHERE m.message_id IN (SELECT value FROM json_each(?1))"#,
            Json(ids)
        )
        .fetch_all(self.db)
        .await?)
    }

    /// Whether a copy in the sent folder under a Message-ID of the provider's own is of an email the
    /// app sent: one with the same text in full and the same attachments. `raw` is the start of it,
    /// which holds the text unless pictures or files come first; a letter whose text runs on past
    /// it is not taken for the app's, as one that only starts the same is another.
    async fn renamed_copy(
        &mut self,
        headers: &mail::Headers,
        sent_at: i64,
        raw: &[u8],
        truncated: bool,
    ) -> Result<bool, Error> {
        let ([recipient], Some(message_id), Some(subject)) = (
            headers.recipients.as_slice(),
            &headers.message_id,
            &headers.subject,
        ) else {
            return Ok(false);
        };
        let copy = email::Copy {
            mailbox_id: self.mailbox_id,
            recipient,
            subject,
            at: sent_at,
        };
        let candidates = email::copy_candidates(&mut *self.db.acquire().await?, &copy).await?;
        if candidates.is_empty() {
            return Ok(false);
        }
        let Some(c) = mail::letter(raw, truncated).and_then(|letter| {
            candidates
                .into_iter()
                .find(|c| letter.is_copy_of(&c.text, &c.attachments))
        }) else {
            return Ok(false);
        };
        let mut conn = self.db.acquire().await?;
        Ok(
            match email::copied(&mut conn, c.id, message_id, &headers.recipients, self.now).await? {
                SentCopy::Foreign => false,
                SentCopy::Own => true,
                SentCopy::Confirmed => {
                    self.round.changed = true;
                    true
                }
            },
        )
    }

    async fn store(
        &mut self,
        at: &Location<'_>,
        headers: &mail::Headers,
        message: &Stored,
        mentors: &[i64],
        new: bool,
    ) -> Result<(), Error> {
        let mut tx = self.db.begin().await?;
        let inserted = sqlx::query_scalar!(
            "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id, in_reply_to,
               sent_at, from_address, from_name, subject, refs, body_text, truncated)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT DO NOTHING RETURNING id AS \"id!\"",
            self.mailbox_id,
            at.folder,
            at.uidvalidity,
            at.uid,
            message.kind,
            headers.message_id,
            headers.in_reply_to,
            message.sent_at,
            headers.from,
            headers.from_name,
            headers.subject,
            headers.references,
            message.text,
            message.truncated,
        )
        .fetch_optional(&mut *tx)
        .await?;
        let id = match inserted {
            Some(id) => {
                self.round.changed = true;
                id
            }
            // Stored meanwhile, such as from the other folder in this round.
            None => match existing(&mut tx, self.mailbox_id, headers, at).await? {
                Some(id) => id,
                None => return Ok(()),
            },
        };
        self.link(&mut tx, id, mentors).await?;
        let reply = if inserted.is_some() && new && message.kind == Kind::Reply {
            let mentors = sqlx::query_scalar!(
                "SELECT m.name FROM mentor m JOIN message_mentor mm ON mm.mentor_id = m.id
                 WHERE mm.message_id = ? ORDER BY m.id",
                id
            )
            .fetch_all(&mut *tx)
            .await?;
            Some(NewReply {
                mentors,
                subject: headers.subject.clone(),
            })
        } else {
            None
        };
        tx.commit().await?;
        if let Some(reply) = reply {
            self.round.replies.push(reply);
        }
        Ok(())
    }

    /// Links a message to its mentors. A mentor without a mailbox to contact them from gets the
    /// one the mail was found in (docs/data-model.md#导师).
    async fn link(
        &mut self,
        tx: &mut sqlx::SqliteConnection,
        id: i64,
        mentors: &[i64],
    ) -> Result<(), Error> {
        for &mentor in mentors {
            let linked = sqlx::query!(
                "INSERT OR IGNORE INTO message_mentor (mentor_id, message_id) VALUES (?, ?)",
                mentor,
                id
            )
            .execute(&mut *tx)
            .await?;
            if linked.rows_affected() == 1 {
                self.round.changed = true;
                mentor::assign_mailbox(tx, mentor, self.mailbox_id).await?;
            }
        }
        Ok(())
    }

    /// Marks the mail a bounce returns, whether the app sent it or it was sent elsewhere.
    async fn bounced(&mut self, raw: &[u8]) -> Result<(), Error> {
        let Some(bounce) = mail::bounce(raw) else {
            return Ok(());
        };
        let mut tx = self.db.begin().await?;
        let mut marked = false;
        // The app sends each email to one address: the first failure naming it, or naming no one,
        // says why it came back.
        for failure in &bounce.failures {
            let (to, reason) = (&failure.recipients, &failure.reason);
            marked |= email::bounced(&mut tx, &bounce.original, to, reason, self.now).await?;
        }
        for failure in &bounce.failures {
            // Mail sent elsewhere to several mentors bounces for those the bounce names, or for
            // all when it does not say who.
            marked |= sqlx::query!(
                "UPDATE message_mentor SET bounce_reason = ?1
                 WHERE bounce_reason IS NULL
                   AND message_id = (SELECT id FROM message WHERE message_id = ?2 AND kind = 'sent')
                   AND (json_array_length(?3) = 0 OR mentor_id IN (
                     SELECT mentor_id FROM mentor_email
                     WHERE address IN (SELECT value FROM json_each(?3))))",
                failure.reason,
                bounce.original,
                Json(&failure.recipients)
            )
            .execute(&mut *tx)
            .await?
            .rows_affected()
                > 0;
        }
        tx.commit().await?;
        self.round.changed |= marked;
        Ok(())
    }
}

/// The message stored already: the same Message-ID seen elsewhere, or the same place in the
/// mailbox.
async fn existing(
    conn: &mut sqlx::SqliteConnection,
    mailbox_id: i64,
    headers: &mail::Headers,
    at: &Location<'_>,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar!(
        "SELECT id FROM message WHERE message_id = ?1
           OR (mailbox_id = ?2 AND folder = ?3 AND uidvalidity = ?4 AND uid = ?5)",
        headers.message_id,
        mailbox_id,
        at.folder,
        at.uidvalidity,
        at.uid,
    )
    .fetch_optional(conn)
    .await
}

/// What is stored of a message besides its headers.
struct Stored {
    kind: Kind,
    sent_at: i64,
    text: Option<String>,
    truncated: bool,
}

/// Where a message is in the mailbox.
struct Location<'a> {
    folder: &'a str,
    uidvalidity: u32,
    uid: u32,
}

/// A message in the correspondence with a mentor. Times are UTC milliseconds.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    pub id: i64,
    pub from_address: String,
    pub subject: Option<String>,
    pub sent_at: i64,
    /// What the message says, without the earlier mail it quotes.
    pub text: String,
    /// The earlier mail it quotes, shown folded.
    pub quote: Option<String>,
    /// Only its start was read, which may leave out some of the text.
    pub truncated: bool,
    #[serde(flatten)]
    pub kind: MessageKind,
}

#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum MessageKind {
    /// From the mentor, or someone writing for them.
    Reply {
        unread: bool,
    },
    AutoReply,
    /// Sent to the mentor by the user from elsewhere, such as the mailbox's website.
    Sent {
        bounce: Option<String>,
    },
}

/// The mail seen in the mailboxes with a mentor, oldest first.
pub async fn list(db: &SqlitePool, mentor_id: i64) -> Result<Vec<Message>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT m.id, m.kind AS "kind: Kind", m.from_address, m.subject, m.sent_at, m.read_at,
             mm.bounce_reason, m.body_text, m.truncated AS "truncated: bool"
           FROM message m JOIN message_mentor mm ON mm.message_id = m.id
           WHERE mm.mentor_id = ? ORDER BY m.sent_at, m.id"#,
        mentor_id
    )
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| {
            let body = r.body_text.unwrap_or_default();
            let (text, quote) = quote::split(&body);
            Message {
                id: r.id,
                from_address: r.from_address,
                subject: r.subject,
                sent_at: r.sent_at,
                text: text.to_owned(),
                quote: (!quote.is_empty()).then(|| quote.to_owned()),
                truncated: r.truncated,
                kind: match r.kind {
                    Kind::Reply => MessageKind::Reply {
                        unread: r.read_at.is_none(),
                    },
                    Kind::AutoReply => MessageKind::AutoReply,
                    Kind::Sent => MessageKind::Sent {
                        bounce: r.bounce_reason,
                    },
                },
            }
        })
        .collect())
}

/// Marks the mentor's replies as read, which clears their red dot.
pub async fn mark_read(db: &SqlitePool, mentor_id: i64, now: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE message SET read_at = ?
         WHERE kind = 'reply' AND read_at IS NULL
           AND id IN (SELECT message_id FROM message_mentor WHERE mentor_id = ?)",
        now,
        mentor_id
    )
    .execute(db)
    .await?;
    Ok(())
}

/// Whether the earlier mail at any of a mentor's addresses is still to be looked up.
pub async fn queued(db: &SqlitePool, mentor_id: i64) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT EXISTS (SELECT 1 FROM mentor_scan s JOIN mentor_email e ON e.address = s.address
                          WHERE e.mentor_id = ?) AS "queued: bool""#,
        mentor_id
    )
    .fetch_one(db)
    .await
}

/// UTC milliseconds.
fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}
