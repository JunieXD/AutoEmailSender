use serde::Deserialize;
use ts_rs::TS;

use crate::{CallError, Context, ErrorCode, Nothing};

#[derive(Deserialize, TS)]
pub struct AccountEmail {
    pub email: String,
}

#[derive(Deserialize, TS)]
pub struct AccountCredentials {
    pub email: String,
    pub password: String,
}

#[derive(Deserialize, TS)]
pub struct AccountVerification {
    pub email: String,
    pub code: String,
    pub password: String,
}

#[derive(Deserialize, TS)]
pub struct AccountLogin {
    pub email: String,
    pub password: String,
    /// Cancels the deletion of an account in its cooling-off period.
    #[serde(default)]
    #[ts(optional)]
    pub restore: Option<bool>,
}

#[derive(Deserialize, TS)]
pub struct PlusOrderInput {
    pub item: String,
    pub method: cloud_api::plus::Method,
}

#[derive(Deserialize, TS)]
pub struct PlusOrderId {
    pub order: i64,
}

pub async fn get_plus(ctx: &Context, _: Nothing) -> Result<account::Plus, CallError> {
    let login = account::saved_login(&ctx.db).await?;
    let account = crate::tracking::read(ctx, &login).await?;
    Ok(account::Plus {
        plus_until: account.plus_until,
    })
}

pub async fn get_plus_items(
    ctx: &Context,
    _: Nothing,
) -> Result<cloud_api::plus::Items, CallError> {
    Ok(ctx.account.plus_items(&ctx.db).await?)
}

pub async fn create_plus_order(
    ctx: &Context,
    input: PlusOrderInput,
) -> Result<cloud_api::plus::Created, CallError> {
    Ok(ctx
        .account
        .create_plus_order(&ctx.db, input.item, input.method)
        .await?)
}

pub async fn get_plus_order(
    ctx: &Context,
    input: PlusOrderId,
) -> Result<cloud_api::plus::OrderState, CallError> {
    let login = account::saved_login(&ctx.db).await?;
    let state = ctx.account.plus_order(&ctx.db, &login, input.order).await?;
    if let cloud_api::plus::OrderState::Applied {
        plus_until,
        tracking,
    } = &state
    {
        crate::tracking::note(&ctx.db, &login, *plus_until, tracking).await?;
    }
    Ok(state)
}

pub async fn accept_terms(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    Ok(ctx.account.accept_terms(&ctx.db).await?)
}

pub async fn get_invite(ctx: &Context, _: Nothing) -> Result<cloud_api::Invite, CallError> {
    Ok(ctx.account.invite(&ctx.db).await?)
}

pub async fn fill_invite(
    ctx: &Context,
    input: cloud_api::CodeRequest,
) -> Result<account::Plus, CallError> {
    Ok(ctx.account.fill_invite(&ctx.db, &input.code).await?)
}

pub async fn redeem_code(
    ctx: &Context,
    input: cloud_api::CodeRequest,
) -> Result<cloud_api::Redeemed, CallError> {
    Ok(ctx.account.redeem(&ctx.db, &input.code).await?)
}

pub async fn get_account(ctx: &Context, _: Nothing) -> Result<account::State, CallError> {
    Ok(account::state(&ctx.db).await?)
}

pub async fn send_registration_code(ctx: &Context, input: AccountEmail) -> Result<(), CallError> {
    Ok(ctx.account.send_code(&input.email, false).await?)
}

pub async fn register_account(
    ctx: &Context,
    input: AccountVerification,
) -> Result<account::State, CallError> {
    Ok(ctx
        .account
        .register(&ctx.db, &input.email, &input.code, input.password)
        .await?)
}

pub async fn login_account(
    ctx: &Context,
    input: AccountLogin,
) -> Result<account::State, CallError> {
    Ok(ctx
        .account
        .login(
            &ctx.db,
            &input.email,
            input.password,
            input.restore.unwrap_or(false),
        )
        .await?)
}

pub async fn send_password_reset_code(ctx: &Context, input: AccountEmail) -> Result<(), CallError> {
    Ok(ctx.account.send_code(&input.email, true).await?)
}

pub async fn reset_account_password(
    ctx: &Context,
    input: AccountVerification,
) -> Result<(), CallError> {
    Ok(ctx
        .account
        .reset_password(&ctx.db, &input.email, &input.code, input.password)
        .await?)
}

pub async fn logout_account(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    Ok(ctx.account.logout(&ctx.db).await?)
}

/// When the account goes, after its cooling-off period.
pub async fn delete_account(ctx: &Context, input: AccountCredentials) -> Result<i64, CallError> {
    Ok(ctx
        .account
        .delete(&ctx.db, &input.email, input.password)
        .await?)
}

impl From<account::Error> for CallError {
    fn from(error: account::Error) -> Self {
        tracing::warn!(error = %error, "account operation failed");
        let code = match error {
            account::Error::Invalid(_) | account::Error::AccountChanged => ErrorCode::Invalid,
            account::Error::Database(_) | account::Error::Worker(_) => ErrorCode::Internal,
            account::Error::SignedOut => ErrorCode::SignedOut,
            account::Error::Deleting(_) => ErrorCode::AccountDeleting,
            account::Error::TermsOutdated => ErrorCode::Invalid,
            _ => ErrorCode::External,
        };
        Self {
            code,
            message: error.message(),
        }
    }
}
