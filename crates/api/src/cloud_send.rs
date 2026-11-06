//! Cloud sending (docs/development.md#云端定时发送): handing scheduled first emails to the
//! official server, withdrawing them, and bringing back what became of them.

use std::sync::Arc;
use std::time::Duration;

use base64::prelude::{BASE64_URL_SAFE_NO_PAD, Engine};
use cloud_api::Empty;
use cloud_api::cloud_send::{
    EMAILS, EmailUpload, Ids, LEAD, MAILBOXES, MAX_IDS, MAX_SIZE, MAX_WAITING, MAX_WAITING_SIZE,
    MailboxSaved, Mailboxes, Outcome, RESULTS, RESULTS_ACK, Results, Withdrawal,
};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use sqlx::SqliteConnection;
use tokio::sync::{Mutex, Notify};
use ts_rs::TS;

use crate::accounts::same_login;
use crate::{CallError, Context};

const EVERY: Duration = Duration::from_secs(5 * 60);
/// zstd's default.
const LEVEL: i32 = 3;

#[derive(Default)]
pub struct CloudSend {
    wake: Notify,
    /// Held while handing over, withdrawing or syncing: a sync must not take an upload under way
    /// for one cut short.
    busy: Mutex<()>,
}

impl CloudSend {
    /// There may be something to withdraw or bring back now.
    pub fn wake(&self) {
        self.wake.notify_one();
    }
}

/// Syncs at startup, when woken and every few minutes, calling `changed` when what the UI shows
/// may differ.
pub async fn run(ctx: Arc<Context>, changed: impl Fn()) {
    let mut ticks = tokio::time::interval(EVERY);
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = ticks.tick() => {}
            () = ctx.cloud_send.wake.notified() => {}
        }
        let _busy = ctx.cloud_send.busy.lock().await;
        match sync(&ctx, crate::now()).await {
            Ok(false) => {}
            Ok(true) => changed(),
            Err(error) => tracing::warn!(
                error = error.message(),
                "cloud sending synchronization failed"
            ),
        }
    }
}

/// Withdraws what was cut short or canceled, gives the server what changed of their mailboxes,
/// and brings back what became of the rest, all as the login saved at the start: only that
/// account's emails, the others waiting for theirs. True when what the UI shows changed. Asks the
/// server nothing when it has nothing of the account's.
pub async fn sync(ctx: &Context, now: i64) -> Result<bool, account::Error> {
    let login = match account::saved_login(&ctx.db).await {
        Ok(login) => login,
        Err(account::Error::SignedOut) => return Ok(false),
        Err(error) => return Err(error),
    };
    let db = &ctx.db;
    let account = &login.email;
    let mut changed = false;
    for cloud_id in email::cloud::to_withdraw(&mut *db.acquire().await?, account).await? {
        changed |= !matches!(
            take_back(ctx, &login, &cloud_id, now).await?,
            Withdrawal::Sending
        );
    }
    // Before the results, which then carry the end of a pause a new secret brings.
    // Giving the server a mailbox takes the current terms; getting emails back does not.
    if !login.terms_outdated {
        for id in mailbox::cloud::stale(&mut *db.acquire().await?, account).await? {
            if let Some(error) = upload_mailbox(ctx, &login, id).await? {
                // The server keeps what it had: emails still go with that, or pause there.
                tracing::warn!(
                    mailbox = id,
                    error,
                    "the official server could not log in to a changed mailbox"
                );
            }
        }
    }
    let handed = email::cloud::handed(&mut *db.acquire().await?, account).await?;
    for ids in handed.chunks(MAX_IDS) {
        let ids = ids.to_vec();
        let answer: Results = ctx
            .account
            .authenticated_as(db, &login, Method::POST, RESULTS, Some(&Ids { ids }))
            .await?;
        let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
        same_login(&mut tx, &login).await?;
        // Only those asked about: an acknowledged id is deleted on the server.
        for r in answer.results.iter().filter(|r| handed.contains(&r.id)) {
            record(&mut tx, &r.id, &r.outcome, account, now).await?;
            changed = true;
        }
        changed |= mailbox::cloud::synced(&mut tx, account, &answer.mailboxes).await?;
        tx.commit().await?;
    }
    let written = email::cloud::unacknowledged(&mut *db.acquire().await?, account).await?;
    for ids in written.chunks(MAX_IDS) {
        let _: Empty = ctx
            .account
            .authenticated_as(
                db,
                &login,
                Method::POST,
                RESULTS_ACK,
                Some(&Ids { ids: ids.to_vec() }),
            )
            .await?;
        email::cloud::acknowledged(&mut *db.acquire().await?, ids).await?;
    }
    if changed {
        ctx.sender.wake();
    }
    Ok(changed)
}

/// Writes down what became of an email, to be acknowledged: one that went out, or may have, is
/// handled as one sent here.
async fn record(
    conn: &mut SqliteConnection,
    cloud_id: &str,
    outcome: &Outcome,
    account: &str,
    now: i64,
) -> Result<(), sqlx::Error> {
    if let Some(handed) = email::cloud::finished(conn, cloud_id, outcome, now).await?
        && let Some(mailbox) = handed.mailbox_id
    {
        mentor::assign_mailbox(conn, handed.mentor_id, mailbox).await?;
    }
    email::cloud::to_acknowledge(conn, cloud_id, account).await
}

/// Asks the server to give an email back, and writes down what it answered.
async fn take_back(
    ctx: &Context,
    login: &account::Login,
    cloud_id: &str,
    now: i64,
) -> Result<Withdrawal, account::Error> {
    let answer: Withdrawal = ctx
        .account
        .authenticated_as(
            &ctx.db,
            login,
            Method::DELETE,
            &format!("{EMAILS}/{cloud_id}"),
            None::<&Empty>,
        )
        .await?;
    let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
    same_login(&mut tx, login).await?;
    match &answer {
        Withdrawal::Withdrawn => email::cloud::withdrawn(&mut tx, cloud_id, now).await?,
        Withdrawal::Finished { outcome } => {
            record(&mut tx, cloud_id, outcome, &login.email, now).await?;
        }
        Withdrawal::Sending => {}
    }
    tx.commit().await?;
    Ok(answer)
}

/// Gives the server a mailbox as it is now; why the server could not log in with it, when it
/// could not, and then kept what it had.
async fn upload_mailbox(
    ctx: &Context,
    login: &account::Login,
    id: i64,
) -> Result<Option<String>, account::Error> {
    let Some(copy) = mailbox::cloud::for_cloud(&mut *ctx.db.acquire().await?, id).await? else {
        return Ok(Some(NO_SECRET.into()));
    };
    let saved: MailboxSaved = ctx
        .account
        .authenticated_as(&ctx.db, login, Method::PUT, MAILBOXES, Some(&copy.upload()))
        .await?;
    let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
    mailbox::cloud::given(&mut tx, id, &copy).await?;
    tx.commit().await?;
    Ok(saved.login_error)
}

const NO_SECRET: &str = "发件邮箱的授权码不在这台电脑上了，请在邮箱设置里重新填写。";
const CHANGED: &str = "这些邮件或邮箱刚刚有了变化，请重新打开“交给云端发送”再交。";

#[derive(Deserialize, TS)]
#[ts(rename = "CloudSendPreviewInput")]
pub struct PreviewInput {
    /// The emails to plan; when left out, every approved scheduled email still to go.
    #[serde(default)]
    #[ts(optional)]
    pub emails: Option<Vec<i64>>,
    /// Only the task's.
    #[serde(default)]
    #[ts(optional)]
    pub task: Option<i64>,
}

/// What handing over the emails would do.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudSendPreview")]
pub struct Preview {
    /// What handing over needs first; nothing is planned then.
    pub blocked: Option<Blocked>,
    /// Those that can go, in planned order, with the mailbox and time planned for each.
    pub emails: Vec<Planned>,
    pub unfit: Vec<Unfit>,
    /// The mailboxes they go from.
    pub mailboxes: Vec<UsedMailbox>,
    /// About how much goes up, in bytes: their letters and files.
    pub size: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
#[ts(rename = "CloudSendBlocked")]
pub enum Blocked {
    SignedOut,
    TermsOutdated,
    NoPlus,
}

/// Where and when an email is to go from the official server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudSendChoice")]
pub struct Choice {
    pub id: i64,
    pub mailbox_id: i64,
    pub at: i64,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudSendPlanned")]
pub struct Planned {
    #[serde(flatten)]
    pub choice: Choice,
    pub mentor_name: String,
    pub task_id: Option<i64>,
    pub task_name: Option<String>,
    /// When it was to go from here.
    pub planned_at: i64,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudSendUnfit")]
pub struct Unfit {
    pub id: i64,
    pub mentor_name: String,
    pub task_name: Option<String>,
    pub reason: Reason,
}

/// Why an email cannot go to the official server.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
#[ts(rename = "CloudSendUnfitReason")]
pub enum Reason {
    /// Only first emails go: a follow-up is canceled by a reply only this computer sees.
    FollowUp,
    /// An earlier attempt may have gone out.
    Tried,
    TaskPaused,
    NoAddress,
    /// Less than `LEAD` from now.
    TooSoon,
    AfterPlus,
    /// Its mentor's mailbox is one the server cannot send from.
    ForeignMailbox,
    /// No mailbox the server can send from.
    NoMailbox,
    /// No room in its mailbox before its latest time.
    TooLate,
    /// Over what one email, or all of an account's, may come to.
    TooLarge,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudSendMailbox")]
pub struct UsedMailbox {
    pub id: i64,
    pub address: String,
    /// The server does not have its secret: it goes up with them, once the user agrees.
    pub needs_consent: bool,
}

pub(crate) async fn cloud_send_preview(
    ctx: &Context,
    input: PreviewInput,
) -> Result<Preview, CallError> {
    preview(ctx, input, crate::now()).await
}

pub async fn preview(ctx: &Context, input: PreviewInput, now: i64) -> Result<Preview, CallError> {
    let (login, plus_until) = match ready(ctx, now).await? {
        Ok(ready) => ready,
        Err(blocked) => {
            return Ok(Preview {
                blocked: Some(blocked),
                emails: Vec::new(),
                unfit: Vec::new(),
                mailboxes: Vec::new(),
                size: 0,
            });
        }
    };
    let assessed = assess(
        &mut *ctx.db.acquire().await?,
        input.emails.as_deref(),
        input.task,
        &login.email,
        plus_until,
        now,
    )
    .await?;
    let mailboxes = if assessed.planned.is_empty() {
        Vec::new()
    } else {
        used_mailboxes(ctx, &login, &assessed.planned, now).await?
    };
    Ok(Preview {
        blocked: None,
        size: assessed.size,
        emails: assessed.planned,
        unfit: assessed.unfit,
        mailboxes,
    })
}

/// The saved login and when its Plus ends, as the server says now, when emails can be handed
/// over.
async fn ready(
    ctx: &Context,
    now: i64,
) -> Result<Result<(account::Login, i64), Blocked>, CallError> {
    let login = match account::saved_login(&ctx.db).await {
        Ok(login) => login,
        Err(account::Error::SignedOut) => return Ok(Err(Blocked::SignedOut)),
        Err(error) => return Err(error.into()),
    };
    if login.terms_outdated {
        return Ok(Err(Blocked::TermsOutdated));
    }
    let account = crate::tracking::read(ctx, &login).await?;
    Ok(match account.plus_until.filter(|until| *until > now) {
        Some(until) => Ok((login, until)),
        None => Err(Blocked::NoPlus),
    })
}

/// The mailboxes `planned` go from, and which of them the server lacks.
async fn used_mailboxes(
    ctx: &Context,
    login: &account::Login,
    planned: &[Planned],
    now: i64,
) -> Result<Vec<UsedMailbox>, CallError> {
    let on_server: Mailboxes = ctx
        .account
        .authenticated_as(&ctx.db, login, Method::GET, MAILBOXES, None::<&Empty>)
        .await?;
    let mut used: Vec<UsedMailbox> = Vec::new();
    for m in mailbox::list(&ctx.db, now).await? {
        if planned.iter().any(|p| p.choice.mailbox_id == m.id) {
            used.push(UsedMailbox {
                id: m.id,
                needs_consent: !on_server.mailboxes.iter().any(|s| s.address == m.address),
                address: m.address,
            });
        }
    }
    Ok(used)
}

struct Assessed {
    planned: Vec<Planned>,
    unfit: Vec<Unfit>,
    size: i64,
}

/// Which of the emails of `ids` (or of all those still to go) can go to the server as `account`,
/// whose Plus ends at `plus_until`, and when and from which mailbox; the rest with why not.
async fn assess(
    conn: &mut SqliteConnection,
    ids: Option<&[i64]>,
    task: Option<i64>,
    account: &str,
    plus_until: i64,
    now: i64,
) -> Result<Assessed, sqlx::Error> {
    let candidates = email::cloud::candidates(conn, ids, task, now).await?;
    let (mut count, mut total) = email::cloud::waiting(conn, account).await?;
    let mut fit = Vec::new();
    let mut unfit = Vec::new();
    for c in candidates {
        let reason = if c.follow_up {
            Some(Reason::FollowUp)
        } else if c.tried {
            Some(Reason::Tried)
        } else if c.task_paused {
            Some(Reason::TaskPaused)
        } else if !c.has_address {
            Some(Reason::NoAddress)
        } else if c.planned_at < now + LEAD {
            Some(Reason::TooSoon)
        } else if c.planned_at > plus_until {
            Some(Reason::AfterPlus)
        } else if c.size > MAX_SIZE as i64
            || count >= MAX_WAITING
            || total + c.size > MAX_WAITING_SIZE as i64
        {
            Some(Reason::TooLarge)
        } else {
            None
        };
        match reason {
            Some(reason) => unfit.push(Unfit {
                id: c.id,
                mentor_name: c.mentor_name,
                task_name: c.task_name,
                reason,
            }),
            None => {
                count += 1;
                total += c.size;
                fit.push(c);
            }
        }
    }
    let wanted: Vec<send::Wanted> = fit
        .iter()
        .map(|c| send::Wanted {
            own: c.own,
            from: c.planned_at,
            latest_at: c.latest_at,
        })
        .collect();
    let plans = send::plan_cloud(conn, &wanted, now).await?;
    let mut planned = Vec::new();
    let mut size = 0;
    for (c, plan) in fit.into_iter().zip(plans) {
        let reason = match plan {
            Ok((mailbox_id, at)) if at <= plus_until => {
                size += c.size;
                planned.push(Planned {
                    choice: Choice {
                        id: c.id,
                        mailbox_id,
                        at,
                    },
                    mentor_name: c.mentor_name,
                    task_id: c.task_id,
                    task_name: c.task_name,
                    planned_at: c.planned_at,
                });
                continue;
            }
            Ok(_) => Reason::AfterPlus,
            Err(send::Unplanned::OwnMailbox) => Reason::ForeignMailbox,
            Err(send::Unplanned::NoMailbox) => Reason::NoMailbox,
            Err(send::Unplanned::TooLate) => Reason::TooLate,
        };
        unfit.push(Unfit {
            id: c.id,
            mentor_name: c.mentor_name,
            task_name: c.task_name,
            reason,
        });
    }
    Ok(Assessed {
        planned,
        unfit,
        size,
    })
}

#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudHandOff")]
pub struct HandOff {
    /// As the preview of just these planned them.
    pub emails: Vec<Choice>,
    /// The mailboxes whose secret the user agreed to give the server.
    pub consents: Vec<i64>,
}

#[derive(Default, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudHandedOff")]
pub struct HandedOff {
    /// Now with the server.
    pub handed: Vec<i64>,
    /// Left here, with why.
    pub kept: Vec<Kept>,
    /// The server could not log in to these, so their emails were left here.
    pub login_failed: Vec<LoginFailed>,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudKept")]
pub struct Kept {
    pub id: i64,
    pub reason: String,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudLoginFailed")]
pub struct LoginFailed {
    pub mailbox_id: i64,
    pub address: String,
    pub error: String,
}

/// Hands the emails over as planned (docs/development.md#云端定时发送): marked first, then their
/// mailboxes and themselves uploaded. Cut short, those still marked uploading are withdrawn by
/// the next sync, so that an email never goes from both or from neither.
pub(crate) async fn hand_off_to_cloud(
    ctx: &Context,
    input: HandOff,
) -> Result<HandedOff, CallError> {
    hand_off(ctx, input, crate::now()).await
}

pub async fn hand_off(ctx: &Context, input: HandOff, now: i64) -> Result<HandedOff, CallError> {
    let _busy = ctx.cloud_send.busy.lock().await;
    let (login, plus_until) = ready(ctx, now).await?.map_err(|blocked| {
        CallError::invalid(
            match blocked {
                Blocked::SignedOut => "请先登录。",
                Blocked::TermsOutdated => "尚未同意新版条款，同意后方可使用官方服务。",
                Blocked::NoPlus => "云端定时发送是 Plus 专享。",
            }
            .into(),
        )
    })?;
    let on_server: Mailboxes = ctx
        .account
        .authenticated_as(&ctx.db, &login, Method::GET, MAILBOXES, None::<&Empty>)
        .await?;
    let ids: Vec<i64> = input.emails.iter().map(|c| c.id).collect();
    let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
    same_login(&mut tx, &login).await?;
    let assessed = assess(&mut tx, Some(&ids), None, &login.email, plus_until, now).await?;
    let mut chosen = input.emails.clone();
    chosen.sort_by_key(|c| c.id);
    let mut planned: Vec<Choice> = assessed.planned.iter().map(|p| p.choice.clone()).collect();
    planned.sort_by_key(|c| c.id);
    if !assessed.unfit.is_empty() || planned != chosen {
        return Err(CallError::invalid(CHANGED.into()));
    }
    let mut mailboxes: Vec<(i64, mailbox::cloud::ForCloud)> = Vec::new();
    for c in &planned {
        if mailboxes.iter().any(|(id, _)| *id == c.mailbox_id) {
            continue;
        }
        let copy = mailbox::cloud::for_cloud(&mut tx, c.mailbox_id)
            .await?
            .ok_or_else(|| CallError::invalid(CHANGED.into()))?;
        let address = copy.sender.address.to_string();
        if !on_server.mailboxes.iter().any(|s| s.address == address)
            && !input.consents.contains(&c.mailbox_id)
        {
            return Err(CallError::invalid(format!(
                "请先勾选同意把 {address} 的授权码交给官方服务器，用来按时发信。"
            )));
        }
        mailboxes.push((c.mailbox_id, copy));
    }
    let mut handed: Vec<(i64, String, i64)> = Vec::new();
    for c in &planned {
        let Some((_, copy)) = mailboxes.iter().find(|(id, _)| *id == c.mailbox_id) else {
            unreachable!("every planned mailbox was read above");
        };
        let cloud_id = uuid::Uuid::new_v4().simple().to_string();
        let from: &str = copy.sender.address.as_ref();
        let message_id = transport::message_id(&copy.sender);
        if !email::cloud::hand_off(
            &mut tx,
            c.id,
            &cloud_id,
            &login.email,
            c.mailbox_id,
            from,
            &message_id,
            c.at,
            now,
        )
        .await?
        {
            return Err(CallError::invalid(CHANGED.into()));
        }
        let fresh = uuid::Uuid::new_v4().simple().to_string();
        email::tracking::assign(&mut tx, c.id, &fresh, account::terms(), now).await?;
        handed.push((c.id, cloud_id, c.mailbox_id));
    }
    tx.commit().await?;
    // They no longer go from here; any left uploading are withdrawn by the sync right after.
    ctx.sender.wake();
    ctx.cloud_send.wake();
    upload(ctx, &login, &on_server, &mailboxes, handed, now).await
}

/// The second half of handing over: uploading the mailboxes the server lacks or has old, then
/// the emails, those of a mailbox it could not log in to left here.
async fn upload(
    ctx: &Context,
    login: &account::Login,
    on_server: &Mailboxes,
    mailboxes: &[(i64, mailbox::cloud::ForCloud)],
    mut handed: Vec<(i64, String, i64)>,
    now: i64,
) -> Result<HandedOff, CallError> {
    let mut result = HandedOff::default();
    for (id, copy) in mailboxes {
        let address = copy.sender.address.to_string();
        // The server has it, and nothing changed since it went up.
        let current = on_server.mailboxes.iter().any(|s| s.address == address)
            && !mailbox::cloud::is_stale(&mut *ctx.db.acquire().await?, *id).await?;
        if current {
            continue;
        }
        let Some(error) = upload_mailbox(ctx, login, *id).await? else {
            continue;
        };
        let mut tx = ctx.db.begin_with("BEGIN IMMEDIATE").await?;
        for (_, cloud_id, _) in handed.iter().filter(|(_, _, m)| m == id) {
            email::cloud::withdrawn(&mut tx, cloud_id, now).await?;
        }
        tx.commit().await?;
        handed.retain(|(_, _, m)| m != id);
        result.login_failed.push(LoginFailed {
            mailbox_id: *id,
            address,
            error,
        });
    }
    for (id, cloud_id, _) in handed {
        match upload_email(ctx, login, &cloud_id).await? {
            Ok(()) => result.handed.push(id),
            Err(reason) => {
                email::cloud::withdrawn(&mut *ctx.db.acquire().await?, &cloud_id, now).await?;
                result.kept.push(Kept { id, reason });
            }
        }
    }
    ctx.sender.wake();
    Ok(result)
}

/// Puts an email together and uploads it, then marks it up; why the server did not take it, when
/// it surely does not have it.
async fn upload_email(
    ctx: &Context,
    login: &account::Login,
    cloud_id: &str,
) -> Result<Result<(), String>, CallError> {
    let (letter, message) = match send::cloud_message(&ctx.db, &ctx.materials, cloud_id).await? {
        Ok(composed) => composed,
        Err(reason) => return Ok(Err(reason)),
    };
    let compressed = zstd::encode_all(&message[..], LEVEL).map_err(CallError::internal)?;
    if compressed.len() > MAX_SIZE {
        return Ok(Err(format!(
            "这封邮件压缩后超过 {} MB，不能交给云端。",
            MAX_SIZE / 1024 / 1024
        )));
    }
    let upload = EmailUpload {
        mailbox: letter.from,
        recipient: letter.to,
        planned_at: letter.planned_at,
        latest_at: letter.latest_at,
        tracking_id: letter.tracking_id,
        message: BASE64_URL_SAFE_NO_PAD.encode(compressed),
    };
    let sent: Result<Empty, _> = ctx
        .account
        .authenticated_as(
            &ctx.db,
            login,
            Method::PUT,
            &format!("{EMAILS}/{cloud_id}"),
            Some(&upload),
        )
        .await;
    match sent {
        Ok(Empty {}) => {
            email::cloud::uploaded(&mut *ctx.db.acquire().await?, cloud_id).await?;
            Ok(Ok(()))
        }
        // The server answered no.
        Err(error @ (account::Error::Server(_) | account::Error::SignedOut)) => {
            Ok(Err(error.message()))
        }
        Err(error) => Err(error.into()),
    }
}

/// What to take back from the official server.
#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudWithdraw")]
pub enum Withdraw {
    Email(i64),
    /// All of the task's.
    Task(i64),
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CloudWithdrawn")]
pub struct Withdrawn {
    /// Being sent that moment, so not taken back: their outcome comes with the next sync.
    pub sending: u32,
}

pub(crate) async fn withdraw_from_cloud(
    ctx: &Context,
    input: Withdraw,
) -> Result<Withdrawn, CallError> {
    withdraw(ctx, input, crate::now()).await
}

/// Takes emails back from the official server; one it already sent comes back with its result.
pub async fn withdraw(ctx: &Context, input: Withdraw, now: i64) -> Result<Withdrawn, CallError> {
    let _busy = ctx.cloud_send.busy.lock().await;
    let (id, task) = match input {
        Withdraw::Email(id) => (Some(id), None),
        Withdraw::Task(task) => (None, Some(task)),
    };
    let emails = email::cloud::in_cloud(&mut *ctx.db.acquire().await?, id, task).await?;
    if id.is_some() && emails.is_empty() {
        return Err(CallError::not_found("这封邮件已不在云端。"));
    }
    let mut result = Withdrawn { sending: 0 };
    if emails.is_empty() {
        return Ok(result);
    }
    let login = account::saved_login(&ctx.db).await?;
    if let Some(other) = emails.iter().find(|e| e.account != login.email) {
        return Err(CallError::invalid(format!(
            "有邮件是账号 {} 交给云端的，登录这个账号后才能撤回。",
            other.account
        )));
    }
    for e in &emails {
        if matches!(
            take_back(ctx, &login, &e.cloud_id, now).await?,
            Withdrawal::Sending
        ) {
            result.sending += 1;
        }
    }
    ctx.sender.wake();
    Ok(result)
}
