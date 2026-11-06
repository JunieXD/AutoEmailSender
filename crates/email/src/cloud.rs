//! Emails handed to the official server, which sends them at their time from the mailbox the app
//! chose (docs/design.md#云端定时发送, docs/data-model.md#联系).

use cloud_api::cloud_send::Outcome;
use sqlx::SqliteConnection;
use sqlx::types::Json;

use crate::{Attached, Handed};

/// An approved, scheduled email that might go to the official server, with what decides whether
/// it can.
#[derive(Debug)]
pub struct Candidate {
    pub id: i64,
    pub mentor_name: String,
    pub task_id: Option<i64>,
    pub task_name: Option<String>,
    pub follow_up: bool,
    pub planned_at: i64,
    pub latest_at: i64,
    /// An earlier attempt may have gone out.
    pub tried: bool,
    /// Its task is paused or stopped.
    pub task_paused: bool,
    pub has_address: bool,
    /// The mentor's own mailbox.
    pub own: Option<i64>,
    /// About how large it is: its letter and files.
    pub size: i64,
}

/// The emails of `ids`, or with none every approved scheduled email still to go, of `task` when
/// given, in planned order.
pub async fn candidates(
    conn: &mut SqliteConnection,
    ids: Option<&[i64]>,
    task: Option<i64>,
    now: i64,
) -> Result<Vec<Candidate>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT e.id, m.name AS mentor_name, e.task_id, t.name AS "task_name?",
             e.kind = 'follow_up' AS "follow_up!: bool", e.planned_at AS "planned_at!",
             e.latest_at AS "latest_at!", e.sent_at IS NOT NULL AS "tried!: bool",
             (t.state IS NOT NULL AND (t.state <> 'active' OR t.generation_paused_at IS NOT NULL))
               AS "task_paused!: bool",
             EXISTS (SELECT 1 FROM mentor_email a WHERE a.mentor_id = e.mentor_id)
               AS "has_address!: bool",
             own.mailbox_id AS own, s.size AS "size!: i64"
           FROM email e
           JOIN mentor m ON m.id = e.mentor_id
           JOIN email_own_mailbox own ON own.email_id = e.id
           JOIN email_size s ON s.email_id = e.id
           LEFT JOIN task t ON t.id = e.task_id
           WHERE e.state = 'approved' AND e.kind IN ('first', 'follow_up')
             AND e.planned_at IS NOT NULL AND e.latest_at > ?1
             AND (?2 IS NULL OR e.id IN (SELECT value FROM json_each(?2)))
             AND (?3 IS NULL OR e.task_id = ?3)
           ORDER BY e.planned_at, e.id"#,
        now,
        ids.map(Json),
        task,
    )
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| Candidate {
            id: r.id,
            mentor_name: r.mentor_name,
            task_id: r.task_id,
            task_name: r.task_name,
            follow_up: r.follow_up,
            planned_at: r.planned_at,
            latest_at: r.latest_at,
            tried: r.tried,
            task_paused: r.task_paused,
            has_address: r.has_address,
            own: r.own,
            size: r.size,
        })
        .collect())
}

/// Hands an email to the official server as `cloud_id`, for `account` (`account_id` on the
/// server), to go at `at` from `mailbox_id`: marked uploading until `uploaded`. False when it no longer can go, as when it was changed meanwhile.
/// Its Message-ID is chosen now, or kept from a try turned down, so that replies and its copy in
/// the sent folder are known.
#[expect(
    clippy::too_many_arguments,
    reason = "the handover's facts, written in one update"
)]
pub async fn hand_off(
    conn: &mut SqliteConnection,
    id: i64,
    cloud_id: &str,
    account: &str,
    account_id: i64,
    mailbox_id: i64,
    from_address: &str,
    message_id: &str,
    at: i64,
    now: i64,
) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        r#"UPDATE email SET state = 'cloud', cloud_id = ?1, cloud_account = ?2,
             cloud_account_id = ?9, cloud_at = ?3, cloud_uploading = 1, mailbox_id = ?4,
             from_address = ?5,
             to_address = (SELECT address FROM mentor_email WHERE mentor_id = email.mentor_id
               ORDER BY position LIMIT 1),
             message_id = coalesce(message_id, ?6), error = NULL, updated_at = ?7
           WHERE id = ?8 AND state = 'approved' AND kind = 'first' AND sent_at IS NULL
             AND planned_at IS NOT NULL AND latest_at > ?3
             AND (task_id IS NULL OR task_id IN (SELECT id FROM task WHERE state = 'active'
               AND generation_paused_at IS NULL))
             AND EXISTS (SELECT 1 FROM mentor_email WHERE mentor_id = email.mentor_id)"#,
        cloud_id,
        account,
        at,
        mailbox_id,
        from_address,
        message_id,
        now,
        id,
        account_id,
    )
    .execute(conn)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// What the email handed over as `cloud_id` is made of, as it goes to the server.
pub struct Letter {
    pub id: i64,
    pub mailbox_id: i64,
    pub from: String,
    pub to_name: String,
    pub to: String,
    pub message_id: String,
    pub subject: String,
    pub html: String,
    pub text: String,
    pub tracking_id: Option<String>,
    pub planned_at: i64,
    pub latest_at: i64,
    pub attachments: Vec<Attached>,
}

pub async fn letter(conn: &mut SqliteConnection, cloud_id: &str) -> Result<Letter, sqlx::Error> {
    let r = sqlx::query!(
        r#"SELECT e.id, e.mailbox_id AS "mailbox_id!", e.from_address AS "from!",
             m.name AS to_name, e.to_address AS "to!",
             e.message_id AS "message_id!", e.subject AS "subject!", e.html AS "html!",
             e.text AS "text!", e.tracking_id, e.cloud_at AS "planned_at!",
             e.latest_at AS "latest_at!"
           FROM email e JOIN mentor m ON m.id = e.mentor_id
           WHERE e.cloud_id = ?"#,
        cloud_id
    )
    .fetch_one(&mut *conn)
    .await?;
    let attachments = crate::attachment_files(conn, r.id).await?;
    Ok(Letter {
        id: r.id,
        mailbox_id: r.mailbox_id,
        from: r.from,
        to_name: r.to_name,
        to: r.to,
        message_id: r.message_id,
        subject: r.subject,
        html: r.html,
        text: r.text,
        tracking_id: r.tracking_id,
        planned_at: r.planned_at,
        latest_at: r.latest_at,
        attachments,
    })
}

/// An email with the official server: the id it knows it by, and the account it belongs to.
pub struct InCloud {
    pub cloud_id: String,
    pub account: String,
}

/// The email `id`, or the emails of `task`, that are with the server.
pub async fn in_cloud(
    conn: &mut SqliteConnection,
    id: Option<i64>,
    task: Option<i64>,
) -> Result<Vec<InCloud>, sqlx::Error> {
    sqlx::query_as!(
        InCloud,
        r#"SELECT cloud_id AS "cloud_id!", cloud_account AS "account!" FROM email
           WHERE (id = ?1 OR task_id = ?2) AND state = 'cloud'
           ORDER BY id"#,
        id,
        task
    )
    .fetch_all(conn)
    .await
}

/// The server has it.
pub async fn uploaded(conn: &mut SqliteConnection, cloud_id: &str) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE email SET cloud_uploading = 0 WHERE cloud_id = ?",
        cloud_id
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// How many emails `account` has on the server, and about how much they come to.
pub async fn waiting(
    conn: &mut SqliteConnection,
    account: &str,
) -> Result<(usize, i64), sqlx::Error> {
    let r = sqlx::query!(
        r#"SELECT count(*) AS "count!: u32", coalesce(sum(s.size), 0) AS "size!: i64"
           FROM email e JOIN email_size s ON s.email_id = e.id
           WHERE e.state = 'cloud' AND e.cloud_account = ?"#,
        account
    )
    .fetch_one(conn)
    .await?;
    Ok((r.count as usize, r.size))
}

/// The emails of `account` the server may have but was never told to keep or give up: uploads cut
/// short, and those canceled since. Also those past their latest time, which by then have an
/// outcome there, unless it was dropped after this computer was away that long.
pub async fn to_withdraw(
    conn: &mut SqliteConnection,
    account: &str,
    now: i64,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT cloud_id AS "cloud_id!" FROM email
           WHERE state = 'cloud' AND cloud_account = ?1
             AND (cloud_uploading = 1 OR cancel_reason IS NOT NULL OR latest_at <= ?2)
           ORDER BY id"#,
        account,
        now
    )
    .fetch_all(conn)
    .await
}

/// Every email `account` has on the server.
pub async fn handed(
    conn: &mut SqliteConnection,
    account: &str,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT cloud_id AS "cloud_id!" FROM email WHERE state = 'cloud' AND cloud_account = ?
           ORDER BY id"#,
        account
    )
    .fetch_all(conn)
    .await
}

/// Back here, never sent: approved again, or canceled with the reason recorded meanwhile. Its
/// cloud id is spent, and a tracking id chosen for it never went out.
pub async fn withdrawn(
    conn: &mut SqliteConnection,
    cloud_id: &str,
    now: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"UPDATE email SET
             state = CASE WHEN cancel_reason IS NULL THEN 'approved' ELSE 'canceled' END,
             planned_at = CASE WHEN cancel_reason IS NULL THEN planned_at END,
             latest_at = CASE WHEN cancel_reason IS NULL THEN latest_at END,
             cloud_id = NULL, cloud_account = NULL, cloud_account_id = NULL, cloud_at = NULL, cloud_uploading = 0,
             sent_at = NULL, updated_at = ?1
           WHERE cloud_id = ?2"#,
        now,
        cloud_id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

const GONE: &str = "官方服务器收到过这封邮件，但结果已经不在了（比如超过 30 天没有同步），没法确定是否发出，请到发件邮箱的“已发送”里核对。";

/// The server had an email and its outcome is gone, as when this computer was away longer than
/// the server keeps outcomes, or a backup from before it came was restored: it may have gone out
/// at its time, like `finished`.
pub async fn unknown(
    conn: &mut SqliteConnection,
    cloud_id: &str,
    now: i64,
) -> Result<Option<Handed>, sqlx::Error> {
    let Some(at) = sqlx::query_scalar!(
        r#"SELECT cloud_at AS "at!" FROM email WHERE cloud_id = ?"#,
        cloud_id
    )
    .fetch_optional(&mut *conn)
    .await?
    else {
        return Ok(None);
    };
    let reason = GONE.into();
    finished(conn, cloud_id, &Outcome::MaybeSent { at, reason }, now).await
}

const ACCOUNT_GONE: &str =
    "交出这封邮件的账号已经注销，没法确定是否发出，请到发件邮箱的“已发送”里核对。";

/// The emails handed over as `account` by an earlier account with that email, deleted since with
/// what became of them: written down as maybe sent at their time, as `unknown` does. Each one's id,
/// and its mentor and mailbox.
pub async fn left_by_deleted_account(
    conn: &mut SqliteConnection,
    account: &str,
    account_id: i64,
    now: i64,
) -> Result<Vec<(String, Option<Handed>)>, sqlx::Error> {
    let left = sqlx::query!(
        r#"SELECT cloud_id AS "cloud_id!", cloud_at AS "at!" FROM email
           WHERE state = 'cloud' AND cloud_account = ? AND cloud_account_id <> ?"#,
        account,
        account_id
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut settled = Vec::new();
    for r in left {
        let reason = ACCOUNT_GONE.into();
        let outcome = Outcome::MaybeSent { at: r.at, reason };
        let handed = finished(conn, &r.cloud_id, &outcome, now).await?;
        settled.push((r.cloud_id, handed));
    }
    Ok(settled)
}

/// What became of an email the server had, written down; the mentor and mailbox of one that went
/// out or may have, whose later emails go from that mailbox. Asked again, nothing changes.
pub async fn finished(
    conn: &mut SqliteConnection,
    cloud_id: &str,
    outcome: &Outcome,
    now: i64,
) -> Result<Option<Handed>, sqlx::Error> {
    let (at, error) = match outcome {
        Outcome::Returned { .. } => {
            withdrawn(conn, cloud_id, now).await?;
            return Ok(None);
        }
        Outcome::Sent { at } => (*at, None),
        Outcome::Failed { at, reason } | Outcome::MaybeSent { at, reason } => {
            (*at, Some(reason.as_str()))
        }
    };
    let state = match outcome {
        Outcome::Sent { .. } => "sent",
        Outcome::MaybeSent { .. } => "maybe_sent",
        _ => "failed",
    };
    // Turned down, it never went: like a refusal here, it fails, or is canceled with the reason
    // recorded, and its tracking id goes.
    let row = sqlx::query!(
        r#"UPDATE email SET
             state = CASE WHEN ?1 = 'failed' AND cancel_reason IS NOT NULL THEN 'canceled'
               ELSE ?1 END,
             planned_at = CASE WHEN ?1 = 'failed' AND cancel_reason IS NOT NULL THEN NULL
               ELSE planned_at END,
             latest_at = NULL, error = CASE WHEN ?1 = 'failed' AND cancel_reason IS NOT NULL
               THEN NULL ELSE ?2 END,
             cancel_reason = CASE WHEN ?1 = 'sent' THEN NULL ELSE cancel_reason END,
             sent_at = CASE WHEN ?1 = 'failed' THEN NULL ELSE ?3 END,
             tracking_chosen_at = CASE WHEN tracking_state = 'pending' THEN ?3
               ELSE tracking_chosen_at END,
             by_cloud = 1, cloud_id = NULL, cloud_account = NULL, cloud_account_id = NULL, cloud_at = NULL,
             cloud_uploading = 0, updated_at = ?4
           WHERE cloud_id = ?5
           RETURNING mentor_id, mailbox_id"#,
        state,
        error,
        at,
        now,
        cloud_id,
    )
    .fetch_optional(conn)
    .await?;
    Ok(row.filter(|_| state != "failed").map(|r| Handed {
        mentor_id: r.mentor_id,
        mailbox_id: r.mailbox_id,
    }))
}

/// An email still with the official server that its mailbox shows went out, to one of
/// `recipients` (to any when none are known), as its copy in the sent folder or a bounce does:
/// written down as sent when they show, `at`, though not before its planned time, and its result
/// on the server no longer wanted. None when it is not with the server.
pub(crate) async fn went_out(
    conn: &mut SqliteConnection,
    id: i64,
    recipients: &[String],
    at: i64,
    now: i64,
) -> Result<Option<Handed>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT cloud_id AS "cloud_id!", cloud_account AS "account!", cloud_at AS "at!"
           FROM email
           WHERE id = ?1 AND state = 'cloud'
             AND (json_array_length(?2) = 0 OR to_address IN (SELECT value FROM json_each(?2)))"#,
        id,
        Json(recipients),
    )
    .fetch_optional(&mut *conn)
    .await?
    else {
        return Ok(None);
    };
    to_acknowledge(conn, &r.cloud_id, &r.account).await?;
    let at = at.max(r.at);
    finished(conn, &r.cloud_id, &Outcome::Sent { at }, now).await
}

/// Outcomes written down here that the server is to be told of.
pub async fn to_acknowledge(
    conn: &mut SqliteConnection,
    cloud_id: &str,
    account: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "INSERT INTO cloud_ack (cloud_id, account) VALUES (?, ?) ON CONFLICT DO NOTHING",
        cloud_id,
        account
    )
    .execute(conn)
    .await?;
    Ok(())
}

pub async fn unacknowledged(
    conn: &mut SqliteConnection,
    account: &str,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar!(
        "SELECT cloud_id FROM cloud_ack WHERE account = ? ORDER BY cloud_id",
        account
    )
    .fetch_all(conn)
    .await
}

pub async fn acknowledged(conn: &mut SqliteConnection, ids: &[String]) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "DELETE FROM cloud_ack WHERE cloud_id IN (SELECT value FROM json_each(?))",
        Json(ids)
    )
    .execute(conn)
    .await?;
    Ok(())
}
