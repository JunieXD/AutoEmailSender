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
#[serde(rename_all = "camelCase")]
pub struct SponsorOrder {
    pub order_number: String,
}

pub async fn get_sponsor(ctx: &Context, _: Nothing) -> Result<account::Sponsor, CallError> {
    Ok(ctx.account.get_sponsor(&ctx.db).await?)
}

pub async fn get_sponsor_tiers(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<cloud_api::SponsorTier>, CallError> {
    Ok(ctx.account.sponsor_tiers(&ctx.db).await?)
}

pub async fn get_invite(ctx: &Context, _: Nothing) -> Result<cloud_api::Invite, CallError> {
    Ok(ctx.account.invite(&ctx.db).await?)
}

pub async fn fill_invite(
    ctx: &Context,
    input: cloud_api::CodeRequest,
) -> Result<account::Sponsor, CallError> {
    Ok(ctx.account.fill_invite(&ctx.db, &input.code).await?)
}

pub async fn redeem_code(
    ctx: &Context,
    input: cloud_api::CodeRequest,
) -> Result<cloud_api::Redeemed, CallError> {
    Ok(ctx.account.redeem(&ctx.db, &input.code).await?)
}

pub async fn claim_sponsor_order(
    ctx: &Context,
    input: SponsorOrder,
) -> Result<account::Sponsor, CallError> {
    Ok(ctx
        .account
        .claim_sponsor_order(&ctx.db, &input.order_number)
        .await?)
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
    input: AccountCredentials,
) -> Result<account::State, CallError> {
    Ok(ctx
        .account
        .login(&ctx.db, &input.email, input.password)
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

pub async fn delete_account(ctx: &Context, input: AccountCredentials) -> Result<(), CallError> {
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
            _ => ErrorCode::External,
        };
        Self {
            code,
            message: error.message(),
        }
    }
}
