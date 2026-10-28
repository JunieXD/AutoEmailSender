//! Official account access. Passwords live only for derivation; saved tokens never reach callers.

mod key;
pub use cloud_api::Sponsor;
pub use key::login_key;

use std::time::Duration;

use cloud_api::{ApiError, Empty, ErrorCode, Session, path};
use reqwest::{Method, RequestBuilder};
use serde::{Serialize, de::DeserializeOwned};
use sqlx::SqlitePool;
use tokio::sync::{Mutex, watch};
use ts_rs::TS;

/// Development builds use the local server from the server repository's `cargo xtask dev`,
/// so trying things out never creates accounts on the official service.
const SERVER_URL: &str = if cfg!(debug_assertions) {
    "http://127.0.0.1:8787"
} else {
    "https://api.auto-email-sender.cn"
};

#[derive(Debug, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
#[ts(rename = "AccountState")]
pub enum State {
    LoggedOut,
    LoggedIn { email: String },
    Expired { email: String },
}

pub async fn state(db: &SqlitePool) -> Result<State, sqlx::Error> {
    let row = sqlx::query!(
        r#"SELECT email, token IS NOT NULL AS "logged_in!: bool"
        FROM account"#,
    )
    .fetch_optional(db)
    .await?;
    Ok(match row {
        None => State::LoggedOut,
        Some(row) if row.logged_in => State::LoggedIn { email: row.email },
        Some(row) => State::Expired { email: row.email },
    })
}

pub struct Client {
    // Serializing account changes prevents an old response from replacing a newer login.
    change: Mutex<()>,
    address: String,
    http: reqwest::Client,
    changed: watch::Sender<()>,
}

impl Client {
    pub fn new(net: &net::Net) -> Self {
        let address = std::env::var_os("AUTOEMAIL_SERVER_URL")
            .map(|address| address.to_string_lossy().into_owned())
            .unwrap_or_else(|| SERVER_URL.into());
        Self::with_address(net, address)
    }

    pub fn with_address(net: &net::Net, address: String) -> Self {
        // Net already built this TLS configuration; disabling redirects cannot invalidate it.
        // A redirect must never forward a login key or verification code to a different service.
        let http = net.page_http_builder().build().unwrap_or_else(|_| {
            unreachable!("validated network settings must build an account HTTP client")
        });
        Self {
            address: address.trim_end_matches('/').into(),
            change: Mutex::new(()),
            http,
            changed: watch::channel(()).0,
        }
    }

    pub fn subscribe(&self) -> watch::Receiver<()> {
        self.changed.subscribe()
    }

    fn request(&self, method: Method, path: &str) -> RequestBuilder {
        self.http
            .request(method, format!("{}{path}", self.address))
            .timeout(Duration::from_secs(30))
    }

    pub async fn send_code(&self, email: &str, reset: bool) -> Result<(), Error> {
        let email = checked_email(email)?;
        let path = if reset {
            path::RESET_CODE
        } else {
            path::REGISTER_CODE
        };
        response::<Empty>(
            self.request(Method::POST, path)
                .json(&cloud_api::EmailRequest { email }),
        )
        .await?;
        Ok(())
    }

    pub async fn login(
        &self,
        db: &SqlitePool,
        email: &str,
        password: String,
    ) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let email = checked_email(email)?;
        let login_key = login_key(&email, password).await?;
        let session: Session = response(
            self.request(Method::POST, path::LOGIN)
                .json(&cloud_api::LoginRequest { email, login_key }),
        )
        .await?;
        save_session(db, session).await
    }

    pub async fn register(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let request = verified(email, code, password).await?;
        let session: Session =
            response(self.request(Method::POST, path::REGISTER).json(&request)).await?;
        save_session(db, session).await
    }

    pub async fn reset_password(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        let request = verified(email, code, password).await?;
        response::<Empty>(
            self.request(Method::POST, path::RESET_PASSWORD)
                .json(&request),
        )
        .await?;
        sqlx::query!(
            "UPDATE account SET token = NULL WHERE email = ?",
            request.email
        )
        .execute(db)
        .await?;
        Ok(())
    }

    pub async fn logout(&self, db: &SqlitePool) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        match self
            .authenticated::<Empty>(db, Method::POST, path::LOGOUT, None::<&Empty>)
            .await
        {
            Ok(_) | Err(Error::SignedOut) => {}
            Err(error) => return Err(error),
        }
        sqlx::query!("DELETE FROM account").execute(db).await?;
        Ok(())
    }

    pub async fn get_sponsor(&self, db: &SqlitePool) -> Result<Sponsor, Error> {
        let account: cloud_api::Account = self
            .authenticated(db, Method::GET, path::ACCOUNT, None::<&Empty>)
            .await?;
        Ok(Sponsor {
            sponsor_until: account.sponsor_until,
        })
    }

    pub async fn sponsor_tiers(
        &self,
        db: &SqlitePool,
    ) -> Result<Vec<cloud_api::SponsorTier>, Error> {
        let tiers: cloud_api::SponsorTiers = self
            .authenticated(db, Method::GET, path::SPONSOR_TIERS, None::<&Empty>)
            .await?;
        Ok(tiers.tiers)
    }

    pub async fn invite(&self, db: &SqlitePool) -> Result<cloud_api::Invite, Error> {
        self.authenticated(db, Method::GET, path::INVITE, None::<&Empty>)
            .await
    }

    pub async fn fill_invite(&self, db: &SqlitePool, code: &str) -> Result<Sponsor, Error> {
        let code = cloud_api::normalize_code(code);
        if code.is_empty() {
            return Err(Error::Invalid("请输入邀请码"));
        }
        self.authenticated(
            db,
            Method::POST,
            path::INVITE,
            Some(&cloud_api::CodeRequest { code }),
        )
        .await
    }

    pub async fn redeem(&self, db: &SqlitePool, code: &str) -> Result<cloud_api::Redeemed, Error> {
        let code = cloud_api::normalize_code(code);
        if code.is_empty() {
            return Err(Error::Invalid("请输入兑换码"));
        }
        self.authenticated(
            db,
            Method::POST,
            path::REDEEM,
            Some(&cloud_api::CodeRequest { code }),
        )
        .await
    }

    pub async fn claim_sponsor_order(
        &self,
        db: &SqlitePool,
        order_number: &str,
    ) -> Result<Sponsor, Error> {
        let order_number = order_number.trim();
        if order_number.is_empty() {
            return Err(Error::Invalid("请填写爱发电的订单号。"));
        }
        if order_number.len() > 64 || !order_number.bytes().all(|c| c.is_ascii_alphanumeric()) {
            return Err(Error::Invalid(
                "订单号只有字母和数字，请从爱发电的“我的订单”里复制。",
            ));
        }
        let account: cloud_api::Account = self
            .authenticated(
                db,
                Method::POST,
                path::SPONSOR_CLAIM,
                Some(&cloud_api::ClaimRequest {
                    order_number: order_number.into(),
                }),
            )
            .await?;
        Ok(Sponsor {
            sponsor_until: account.sponsor_until,
        })
    }

    pub async fn delete(
        &self,
        db: &SqlitePool,
        email: &str,
        password: String,
    ) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        let current_email =
            sqlx::query_scalar!("SELECT email FROM account WHERE token IS NOT NULL")
                .fetch_optional(db)
                .await?
                .ok_or(Error::SignedOut)?;
        let email = checked_email(email)?;
        if current_email != email {
            return Err(Error::AccountChanged);
        }
        let login_key = login_key(&email, password).await?;
        self.authenticated::<Empty>(
            db,
            Method::DELETE,
            path::ACCOUNT,
            Some(&cloud_api::DeleteRequest { login_key }),
        )
        .await?;
        sqlx::query!("DELETE FROM account").execute(db).await?;
        Ok(())
    }

    /// All official services use this: an expired session clears only that token, retaining the
    /// email for the next login. A response for an older token cannot clear a newer login.
    pub async fn authenticated<T: DeserializeOwned>(
        &self,
        db: &SqlitePool,
        method: Method,
        path: &str,
        body: Option<&impl Serialize>,
    ) -> Result<T, Error> {
        let token = sqlx::query_scalar!("SELECT token FROM account WHERE token IS NOT NULL")
            .fetch_optional(db)
            .await?
            .flatten()
            .ok_or(Error::SignedOut)?;
        let mut request = self.request(method, path).bearer_auth(&token);
        if let Some(body) = body {
            request = request.json(body);
        }
        match response(request).await {
            Err(Error::Server(ApiError {
                code: ErrorCode::SessionExpired,
                ..
            })) => {
                let cleared =
                    sqlx::query!("UPDATE account SET token = NULL WHERE token = ?", token)
                        .execute(db)
                        .await?;
                if cleared.rows_affected() > 0 {
                    self.changed.send_replace(());
                }
                Err(Error::SignedOut)
            }
            result => result,
        }
    }
}

async fn response<T: DeserializeOwned>(request: RequestBuilder) -> Result<T, Error> {
    let response = request.send().await.map_err(Error::Network)?;
    if response.status().is_success() {
        response.json().await.map_err(Error::Network)
    } else {
        Err(Error::Server(
            response.json().await.map_err(Error::Network)?,
        ))
    }
}

fn checked_email(email: &str) -> Result<String, Error> {
    let email = cloud_api::normalize_email(email);
    if email.len() > 254 || !mentor::is_email(&email) {
        return Err(Error::Invalid("请填写正确的邮箱地址。"));
    }
    Ok(email)
}

async fn verified(
    email: &str,
    code: &str,
    password: String,
) -> Result<cloud_api::VerifyRequest, Error> {
    let email = checked_email(email)?;
    if password.chars().count() < 8 {
        return Err(Error::Invalid("密码至少需要 8 位。"));
    }
    let code = code.trim();
    if code.len() != 6 || !code.bytes().all(|c| c.is_ascii_digit()) {
        return Err(Error::Invalid("请填写 6 位数字验证码。"));
    }
    let login_key = login_key(&email, password).await?;
    Ok(cloud_api::VerifyRequest {
        email,
        code: code.into(),
        login_key,
    })
}

async fn save_session(db: &SqlitePool, session: Session) -> Result<State, Error> {
    sqlx::query!(
        r#"INSERT INTO account (id, email, token) VALUES (1, ?, ?)
        ON CONFLICT (id) DO UPDATE SET email = excluded.email, token = excluded.token,
            library_revision = CASE WHEN account.email = excluded.email
                THEN account.library_revision ELSE 0 END,
            library_checked_at = CASE WHEN account.email = excluded.email
                THEN account.library_checked_at ELSE NULL END"#,
        session.account.email,
        session.token
    )
    .execute(db)
    .await?;
    Ok(State::LoggedIn {
        email: session.account.email,
    })
}

#[derive(Debug)]
pub enum Error {
    Invalid(&'static str),
    Worker(tokio::task::JoinError),
    Database(sqlx::Error),
    Network(reqwest::Error),
    Server(ApiError),
    SignedOut,
    AccountChanged,
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Database(error) => write!(f, "account database: {error}"),
            Self::Worker(error) => write!(f, "account derivation worker: {error}"),
            Self::Network(error) => write!(f, "account network: {error}"),
            Self::Server(error) => write!(f, "account server: {:?}", error.code),
            Self::Invalid(_) => write!(f, "invalid account input"),
            Self::SignedOut => write!(f, "account session expired"),
            Self::AccountChanged => write!(f, "account deletion target changed"),
        }
    }
}
impl std::error::Error for Error {}

impl Error {
    pub fn message(&self) -> String {
        match self {
            Self::Invalid(message) => (*message).into(),
            Self::Worker(_) | Self::Database(_) => {
                "账号处理未能完成，请重试；仍然失败时导出日志联系开发者。".into()
            }
            Self::Network(error) if error.is_decode() => {
                "服务器返回的数据无法读取，请稍后重试或升级应用。".into()
            }
            Self::Network(_) => "连不上服务器，请检查网络和代理设置后重试。".into(),
            Self::SignedOut => "登录已失效，请重新登录。".into(),
            Self::AccountChanged => "要注销的账号已经不是当前登录的账号，请刷新后重试。".into(),
            Self::Server(error) => match error.code {
                ErrorCode::InvalidInput => "填写的账号信息不正确，请检查后重试。".into(),
                ErrorCode::EmailRegistered => "这个邮箱已注册，请直接登录。".into(),
                ErrorCode::EmailNotRegistered => "这个邮箱还没注册，请先注册。".into(),
                ErrorCode::InvalidCode => "验证码不对或已过期，请重新获取验证码。".into(),
                ErrorCode::InvalidCredentials => "邮箱或密码不对，请检查后重试。".into(),
                ErrorCode::RateLimited => match error.retry_after_seconds {
                    Some(seconds) => {
                        format!("请求太频繁，请 {} 分钟后再试。", seconds.div_ceil(60))
                    }
                    None => "请求太频繁，请稍后再试。".into(),
                },
                ErrorCode::SessionExpired => "登录已失效，请重新登录。".into(),
                ErrorCode::MailUnavailable => "验证码暂时无法发送，请稍后重试。".into(),
                ErrorCode::Internal => "服务器暂时无法处理，请稍后重试。".into(),
                ErrorCode::QuotaExceeded => "今天还能添加的数量不够了，少选几位或明天再来。".into(),
                ErrorCode::OrderNotFound => {
                    "查不到这笔订单，请检查订单号；刚付款请稍等几分钟再试。".into()
                }
                ErrorCode::OrderClaimed => "这笔订单已经给别的账号开通过赞助。".into(),
                ErrorCode::OrderNotSponsorship => {
                    "这笔订单不是赞助方案，请在爱发电的赞助方案付款后再开通。".into()
                }
                ErrorCode::SponsorUnavailable => "赞助暂时不可用，请稍后重试。".into(),
                ErrorCode::InviteNotFound => "没有这个邀请码，请检查后重试。".into(),
                ErrorCode::InviteFull => "这个邀请码已经邀请满 4 位同学了。".into(),
                ErrorCode::InviteOwn => "不能填自己的邀请码。".into(),
                ErrorCode::InviteExpired => "注册超过 7 天，不能再填邀请码了。".into(),
                ErrorCode::InviteUsed => "这个邮箱已经领过邀请奖励了。".into(),
                ErrorCode::CodeNotFound => "没有这个兑换码，请检查后重试。".into(),
                ErrorCode::CodeUsedUp => "这个兑换码已经被领完了。".into(),
                ErrorCode::CodeExpired => "这个兑换码已经过期了。".into(),
                ErrorCode::CodeUsed => "你已经用过这个兑换码了。".into(),
                ErrorCode::EmailIneligible => {
                    "用常用邮箱（QQ、163、Gmail 等）或学校邮箱注册的账号才能参加。".into()
                }
                ErrorCode::NotFound | ErrorCode::MethodNotAllowed => {
                    "服务器不支持这个操作，请升级应用后重试。".into()
                }
            },
        }
    }
}
