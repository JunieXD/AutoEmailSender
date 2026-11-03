//! Emails sent with a tracking image and the opens the server counted (docs/design.md#邮件追踪).
use serde::Serialize;
use sqlx::SqliteConnection;
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
#[ts(rename = "TrackingState")]
pub enum State {
    /// Not registered with the server yet.
    Pending,
    Tracked,
    NoQuota,
    NotSponsored,
    /// Not registered within `cloud_api::tracking::REGISTER_WITHIN`.
    Expired,
}

/// How an email that went with a tracking image fares; opens come only once it is tracked.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "EmailTracking")]
pub struct Tracking {
    pub state: State,
    pub opens: u32,
    pub first_open_at: Option<i64>,
    pub last_open_at: Option<i64>,
}

/// The tracking id the email goes with this time: the one an earlier attempt chose, which may be
/// out, so that a resend looks the same; or else `fresh` when tracking is on, the account logged
/// in is sponsored at `now`, the email is a first letter or follow-up and the quota left covers it
/// besides the account's others not registered yet. None for an email that goes without the image.
pub async fn assign(
    conn: &mut SqliteConnection,
    email: i64,
    fresh: &str,
    now: i64,
) -> Result<Option<String>, sqlx::Error> {
    sqlx::query!(
        r#"UPDATE email SET tracking_id = ?2, tracking_state = 'pending',
            tracking_account = (SELECT email FROM account WHERE token IS NOT NULL),
            tracking_chosen_at = ?3
        WHERE id = ?1 AND tracking_id IS NULL AND kind IN ('first', 'follow_up')
          AND (SELECT email_tracking FROM profile) = 1
          AND (SELECT a.tracking_until > ?3 AND a.tracking_left > (
                SELECT count(*) FROM email
                WHERE tracking_state = 'pending' AND tracking_account = a.email)
              FROM account a WHERE a.token IS NOT NULL)"#,
        email,
        fresh,
        now
    )
    .execute(&mut *conn)
    .await?;
    sqlx::query_scalar!("SELECT tracking_id FROM email WHERE id = ?", email)
        .fetch_one(conn)
        .await
}

/// Stops trying to register ids that first went out `before` and are still not registered.
pub async fn expire(conn: &mut SqliteConnection, before: i64) -> Result<bool, sqlx::Error> {
    let expired = sqlx::query!(
        r#"UPDATE email SET tracking_state = 'expired'
        WHERE tracking_state = 'pending' AND state <> 'sending' AND tracking_chosen_at < ?"#,
        before
    )
    .execute(conn)
    .await?;
    Ok(expired.rows_affected() > 0)
}

/// Up to `limit` ids of `account`'s emails that may be out and are not registered yet, oldest
/// first. One being handed over waits: it may yet turn out to have reached nobody.
pub async fn pending(
    conn: &mut SqliteConnection,
    account: &str,
    limit: i64,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT tracking_id AS "tracking_id!" FROM email
        WHERE tracking_state = 'pending' AND state <> 'sending' AND tracking_account = ?
        ORDER BY id LIMIT ?"#,
        account,
        limit
    )
    .fetch_all(conn)
    .await
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejected {
    NoQuota,
    NotSponsored,
}

/// What the server made of a registration: `accepted` are tracked from `now`, `rejected` not.
pub async fn registered(
    conn: &mut SqliteConnection,
    accepted: &[String],
    rejected: &[String],
    why: Rejected,
    now: i64,
) -> Result<(), sqlx::Error> {
    for id in accepted {
        sqlx::query!(
            r#"UPDATE email SET tracking_state = 'tracked', tracked_at = ?
            WHERE tracking_id = ? AND tracking_state = 'pending'"#,
            now,
            id
        )
        .execute(&mut *conn)
        .await?;
    }
    let state = match why {
        Rejected::NoQuota => State::NoQuota,
        Rejected::NotSponsored => State::NotSponsored,
    };
    for id in rejected {
        sqlx::query!(
            r#"UPDATE email SET tracking_state = ?
            WHERE tracking_id = ? AND tracking_state = 'pending'"#,
            state,
            id
        )
        .execute(&mut *conn)
        .await?;
    }
    Ok(())
}

/// An email's opens as the server counts them.
pub async fn opened(
    conn: &mut SqliteConnection,
    id: &str,
    opens: u32,
    first_at: i64,
    last_at: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"UPDATE email SET open_count = ?, first_opened_at = ?, last_opened_at = ?
        WHERE tracking_id = ?"#,
        opens,
        first_at,
        last_at,
        id
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// Whether synchronizing has anything to do for `account`: tracking is on, its emails with the
/// image wait to be registered, or opens of its emails registered `since` or later can still come.
pub async fn wanted(
    conn: &mut SqliteConnection,
    account: &str,
    since: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar!(
        r#"SELECT (SELECT email_tracking FROM profile) = 1
            OR EXISTS (SELECT 1 FROM email WHERE tracking_account = ?1 AND tracked_at >= ?2)
            OR EXISTS (SELECT 1 FROM email WHERE tracking_account = ?1
                AND tracking_state = 'pending' AND state <> 'sending')
            AS "wanted!: bool""#,
        account,
        since
    )
    .fetch_one(conn)
    .await
}
