//! Email tracking's registration and synchronization (docs/development.md#邮件追踪).

use std::time::Duration;

use cloud_api::{
    Empty, ErrorCode,
    tracking::{
        MAX_IDS, OPENS, Opens, Quota, REGISTER, REGISTER_WITHIN, RETENTION, Register, Registered,
    },
};
use email::tracking::Rejected;
use reqwest::Method;
use serde::{Deserialize, Serialize};
use sqlx::SqliteConnection;
use tokio::sync::Notify;
use ts_rs::TS;

use crate::{CallError, Context, Nothing};

const EVERY: Duration = Duration::from_secs(15 * 60);
/// Emails that go out close together are registered together.
const GATHER: Duration = Duration::from_secs(60);

#[derive(Default)]
pub struct Tracker {
    /// An email may have gone out with a tracking image.
    sent: Notify,
    /// Tracking was turned on.
    started: Notify,
}

impl Tracker {
    pub fn sent(&self) {
        self.sent.notify_one();
    }
}

/// Keeps tracking in step with the server, calling `changed` when what the UI shows may differ.
pub async fn run(ctx: std::sync::Arc<Context>, changed: impl Fn()) {
    let mut ticks = tokio::time::interval(EVERY);
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        let full = tokio::select! {
            _ = ticks.tick() => true,
            () = ctx.tracking.started.notified() => true,
            () = ctx.tracking.sent.notified() => {
                tokio::time::sleep(GATHER).await;
                false
            }
        };
        match sync(&ctx, full, crate::now()).await {
            Ok(false) => {}
            Ok(true) => changed(),
            Err(error) => {
                tracing::warn!(
                    error = error.message(),
                    "email tracking synchronization failed"
                );
                changed();
            }
        }
    }
}

/// Registers what went out with a tracking image and, when `full`, reads the quota and fetches
/// the opens, all as the login saved at the start; true when what the UI shows changed. A failure
/// is kept for that login, until one succeeds.
pub async fn sync(ctx: &Context, full: bool, now: i64) -> Result<bool, account::Error> {
    let login = match account::saved_login(&ctx.db).await {
        // Paused, not failing, until the terms are agreed to.
        Ok(login) if login.terms_outdated => return Ok(false),
        Ok(login) => login,
        Err(account::Error::SignedOut) => return Ok(false),
        Err(error) => return Err(error),
    };
    if !email::tracking::wanted(&mut *ctx.db.acquire().await?, &login.email, now - RETENTION)
        .await?
    {
        return Ok(false);
    }
    let result = exchange(ctx, &login, full, now).await;
    let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
    if !login.is_saved(&mut tx).await? {
        return result;
    }
    let recovered = match &result {
        Err(error) => {
            let message = error.message();
            sqlx::query!(
                r#"UPDATE account SET tracking_failed_at = coalesce(tracking_failed_at, ?),
                    tracking_error = ?"#,
                now,
                message
            )
            .execute(&mut *tx)
            .await?;
            false
        }
        Ok(_) if full => {
            sqlx::query!(
                r#"UPDATE account SET tracking_failed_at = NULL, tracking_error = NULL
                WHERE tracking_error IS NOT NULL"#
            )
            .execute(&mut *tx)
            .await?
            .rows_affected()
                > 0
        }
        Ok(_) => false,
    };
    tx.commit().await?;
    result.map(|changed| changed || recovered)
}

async fn exchange(
    ctx: &Context,
    login: &account::Login,
    full: bool,
    now: i64,
) -> Result<bool, account::Error> {
    let db = &ctx.db;
    let mut changed = false;
    if full {
        let account = ctx.account.account(db, login).await?;
        changed |= note(db, login, account.plus_until, &account.tracking).await?;
    }
    changed |= email::tracking::expire(&mut *db.acquire().await?, now - REGISTER_WITHIN).await?;
    loop {
        let ids = email::tracking::pending(&mut *db.acquire().await?, &login.email, MAX_IDS as i64)
            .await?;
        if ids.is_empty() {
            break;
        }
        let registered = ctx
            .account
            .authenticated_as::<Registered>(
                db,
                login,
                Method::POST,
                REGISTER,
                Some(&Register { ids: ids.clone() }),
            )
            .await;
        let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
        same_login(&mut tx, login).await?;
        match registered {
            Ok(r) => {
                email::tracking::registered(
                    &mut tx,
                    &r.accepted,
                    &r.rejected,
                    Rejected::NoQuota,
                    now,
                )
                .await?;
                let left = available(&r.tracking);
                sqlx::query!("UPDATE account SET tracking_left = ?", left)
                    .execute(&mut *tx)
                    .await?;
            }
            Err(account::Error::Server(cloud_api::ApiError {
                code: ErrorCode::PlusRequired,
                ..
            })) => {
                email::tracking::registered(&mut tx, &[], &ids, Rejected::NoPlus, now).await?;
                sqlx::query!("UPDATE account SET tracking_until = NULL")
                    .execute(&mut *tx)
                    .await?;
            }
            Err(error) => return Err(error),
        }
        tx.commit().await?;
        changed = true;
    }
    if !full {
        return Ok(changed);
    }
    let mut after = sqlx::query_scalar!("SELECT tracking_revision FROM account")
        .fetch_one(db)
        .await?;
    loop {
        let page: Opens = ctx
            .account
            .authenticated_as(
                db,
                login,
                Method::GET,
                &format!("{OPENS}?after={after}"),
                None::<&Empty>,
            )
            .await?;
        // A malformed page must not move the progress back or keep this looping.
        if page.revision < after || page.more && page.revision == after {
            return Err(account::Error::Invalid(
                "服务器返回的打开记录不正确，请稍后重试或升级应用。",
            ));
        }
        let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
        same_login(&mut tx, login).await?;
        for open in &page.opens {
            email::tracking::opened(&mut tx, &open.id, open.opens, open.first_at, open.last_at)
                .await?;
        }
        sqlx::query!("UPDATE account SET tracking_revision = ?", page.revision)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        changed |= !page.opens.is_empty();
        if !page.more {
            return Ok(changed);
        }
        after = page.revision;
    }
}

/// Answers that came for a login replaced meanwhile are dropped.
async fn same_login(
    conn: &mut SqliteConnection,
    login: &account::Login,
) -> Result<(), account::Error> {
    if login.is_saved(conn).await? {
        Ok(())
    } else {
        Err(account::Error::Invalid("登录的账号刚刚变了，请再试一次。"))
    }
}

/// The emails `tracking` still covers: what is left of the 30 days' allowance, and of the packs.
fn available(tracking: &Quota) -> u32 {
    tracking.limit.saturating_sub(tracking.used) + tracking.pack_left
}

/// Notes when Plus ends and the tracking quota, as the server just answered `login`; true when
/// they differ from what was noted.
pub(crate) async fn note(
    db: &sqlx::SqlitePool,
    login: &account::Login,
    plus_until: Option<i64>,
    tracking: &Quota,
) -> Result<bool, account::Error> {
    let left = available(tracking);
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    same_login(&mut tx, login).await?;
    let changed = sqlx::query!(
        r#"UPDATE account SET tracking_left = ?1, tracking_until = ?2
        WHERE tracking_left IS NOT ?1 OR tracking_until IS NOT ?2"#,
        left,
        plus_until
    )
    .execute(&mut *tx)
    .await?
    .rows_affected()
        > 0;
    tx.commit().await?;
    Ok(changed)
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "TrackingStatus")]
pub struct Status {
    pub enabled: bool,
    /// Why synchronizing last failed, until it succeeds again.
    pub failure: Option<String>,
    /// The account's Plus and quota on the server; None while logged out.
    pub account: Option<Remote>,
}

#[derive(Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
#[ts(rename = "TrackingAccount")]
pub enum Remote {
    Read {
        plus_until: Option<i64>,
        tracking: Quota,
    },
    Unreadable {
        reason: String,
    },
}

pub async fn get_tracking(ctx: &Context, _: Nothing) -> Result<Status, CallError> {
    let row = sqlx::query!(
        r#"SELECT p.email_tracking AS "enabled: bool", a.tracking_error
        FROM profile p LEFT JOIN account a"#
    )
    .fetch_one(&ctx.db)
    .await?;
    let account = match account::saved_login(&ctx.db).await {
        Ok(login) => Some(match read(ctx, &login).await {
            Ok(account) => Remote::Read {
                plus_until: account.plus_until,
                tracking: account.tracking,
            },
            Err(error) => Remote::Unreadable {
                reason: error.message(),
            },
        }),
        Err(account::Error::SignedOut) => None,
        Err(error) => return Err(error.into()),
    };
    Ok(Status {
        enabled: row.enabled,
        failure: row.tracking_error,
        account,
    })
}

/// The account on the server as `login`, noting its Plus and quota.
pub(crate) async fn read(
    ctx: &Context,
    login: &account::Login,
) -> Result<cloud_api::Account, account::Error> {
    let account = ctx.account.account(&ctx.db, login).await?;
    note(&ctx.db, login, account.plus_until, &account.tracking).await?;
    Ok(account)
}

#[derive(Deserialize, TS)]
pub struct TrackingSwitch {
    pub enabled: bool,
}

pub async fn set_tracking(ctx: &Context, input: TrackingSwitch) -> Result<(), CallError> {
    if !input.enabled {
        sqlx::query!("UPDATE profile SET email_tracking = 0")
            .execute(&ctx.db)
            .await?;
        return Ok(());
    }
    let login = match account::saved_login(&ctx.db).await {
        Err(account::Error::SignedOut) => {
            return Err(CallError::invalid(
                "登录并开通 Plus 后可以用邮件追踪。".into(),
            ));
        }
        login => login?,
    };
    // With the quota used up it can still be on, for when more is bought.
    let account = read(ctx, &login).await?;
    if !account.plus_until.is_some_and(|until| until > crate::now()) {
        return Err(CallError::invalid(
            "开通 Plus 后可以用邮件追踪，每 30 天 500 封。".into(),
        ));
    }
    let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
    same_login(&mut tx, &login).await?;
    sqlx::query!("UPDATE profile SET email_tracking = 1")
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    ctx.tracking.started.notify_one();
    Ok(())
}
