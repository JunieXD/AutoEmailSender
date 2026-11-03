//! Official account access. Passwords live only for derivation; saved tokens never reach callers.

mod key;
pub use cloud_api::Sponsor;
pub use key::{Keys, keys};

use std::time::Duration;

use cloud_api::{ApiError, Empty, ErrorCode, Session, path};
use reqwest::{Method, RequestBuilder, StatusCode};
use reqwest_websocket::Upgrade;
pub use reqwest_websocket::WebSocket;
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

/// Read once by operations that must keep using one account throughout.
pub async fn saved_login(db: &SqlitePool) -> Result<Login, Error> {
    let row = sqlx::query!(
        r#"SELECT token AS "token!", backup_key FROM account WHERE token IS NOT NULL"#
    )
    .fetch_optional(db)
    .await?
    .ok_or(Error::SignedOut)?;
    Ok(Login {
        token: row.token,
        backup_key: row.backup_key.and_then(|key| key.try_into().ok()),
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
        // WebSockets upgrade only HTTP/1.1 connections.
        let http = net
            .page_http_builder()
            .http1_only()
            .build()
            .unwrap_or_else(|_| {
                unreachable!("validated network settings must build an account HTTP client")
            });
        Self {
            address: address.trim_end_matches('/').into(),
            change: Mutex::new(()),
            http,
            changed: watch::channel(()).0,
        }
    }

    /// The official server, such as `https://api.auto-email-sender.cn`.
    pub fn address(&self) -> &str {
        &self.address
    }

    /// Hears every change of who is logged in: logging in, out, a reset password or deleted
    /// account, an expired session.
    pub fn subscribe(&self) -> watch::Receiver<()> {
        self.changed.subscribe()
    }

    pub fn request(&self, method: Method, path: &str) -> RequestBuilder {
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
        let keys = keys(&email, password).await?;
        let session: Session = response(self.request(Method::POST, path::LOGIN).json(
            &cloud_api::LoginRequest {
                email,
                login_key: keys.login,
            },
        ))
        .await?;
        let state = save_session(db, session, &keys.backup).await?;
        self.changed.send_replace(());
        Ok(state)
    }

    pub async fn register(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let (request, backup) = verified(email, code, password).await?;
        let session: Session =
            response(self.request(Method::POST, path::REGISTER).json(&request)).await?;
        let state = save_session(db, session, &backup).await?;
        self.changed.send_replace(());
        Ok(state)
    }

    pub async fn reset_password(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        let (request, _) = verified(email, code, password).await?;
        response::<Empty>(
            self.request(Method::POST, path::RESET_PASSWORD)
                .json(&request),
        )
        .await?;
        let signed_out = sqlx::query!(
            "UPDATE account SET token = NULL WHERE email = ? AND token IS NOT NULL",
            request.email
        )
        .execute(db)
        .await?;
        if signed_out.rows_affected() > 0 {
            self.changed.send_replace(());
        }
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
        self.changed.send_replace(());
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
        let login_key = keys(&email, password).await?.login;
        self.authenticated::<Empty>(
            db,
            Method::DELETE,
            path::ACCOUNT,
            Some(&cloud_api::DeleteRequest { login_key }),
        )
        .await?;
        sqlx::query!("DELETE FROM account").execute(db).await?;
        self.changed.send_replace(());
        Ok(())
    }

    pub async fn authenticated<T: DeserializeOwned>(
        &self,
        db: &SqlitePool,
        method: Method,
        path: &str,
        body: Option<&impl Serialize>,
    ) -> Result<T, Error> {
        let login = saved_login(db).await?;
        let mut request = self.request(method, path);
        if let Some(body) = body {
            request = request.json(body);
        }
        self.send(db, &login, request)
            .await?
            .json()
            .await
            .map_err(Error::Network)
    }

    /// All official services use this: an expired session clears only that token, retaining the
    /// email for the next login. A response for an older token cannot clear a newer login.
    pub async fn send(
        &self,
        db: &SqlitePool,
        login: &Login,
        request: RequestBuilder,
    ) -> Result<reqwest::Response, Error> {
        self.signed_in(db, login, checked(request.bearer_auth(&login.token)).await)
            .await
    }

    /// Opens a WebSocket to an official service, such as the relay, as `send` sends a request.
    pub async fn websocket(
        &self,
        db: &SqlitePool,
        login: &Login,
        path: &str,
    ) -> Result<WebSocket, Error> {
        let response = self
            .request(Method::GET, path)
            .bearer_auth(&login.token)
            .upgrade()
            .send()
            .await
            .map_err(Error::WebSocket)?;
        if response.status() != StatusCode::SWITCHING_PROTOCOLS {
            let refused = Err(Error::Server(
                response.into_inner().json().await.map_err(Error::Network)?,
            ));
            return self.signed_in(db, login, refused).await;
        }
        response.into_websocket().await.map_err(Error::WebSocket)
    }

    async fn signed_in<T>(
        &self,
        db: &SqlitePool,
        login: &Login,
        result: Result<T, Error>,
    ) -> Result<T, Error> {
        let Err(Error::Server(ApiError {
            code: ErrorCode::SessionExpired,
            ..
        })) = result
        else {
            return result;
        };
        let cleared = sqlx::query!(
            "UPDATE account SET token = NULL WHERE token = ?",
            login.token
        )
        .execute(db)
        .await?;
        if cleared.rows_affected() > 0 {
            self.changed.send_replace(());
        }
        Err(Error::SignedOut)
    }
}

/// A saved login. Without Debug, so that neither secret ends up in a log.
pub struct Login {
    token: String,
    /// Missing for logins from before cloud backup; logging in again derives it.
    pub backup_key: Option<[u8; 32]>,
}

async fn checked(request: RequestBuilder) -> Result<reqwest::Response, Error> {
    let response = request.send().await.map_err(Error::Network)?;
    if response.status().is_success() {
        Ok(response)
    } else {
        Err(Error::Server(
            response.json().await.map_err(Error::Network)?,
        ))
    }
}

async fn response<T: DeserializeOwned>(request: RequestBuilder) -> Result<T, Error> {
    checked(request).await?.json().await.map_err(Error::Network)
}

fn checked_email(email: &str) -> Result<String, Error> {
    let email = cloud_api::normalize_email(email);
    if email.len() > 254 || !cloud_api::is_email(&email) {
        return Err(Error::Invalid("请填写正确的邮箱地址。"));
    }
    Ok(email)
}

async fn verified(
    email: &str,
    code: &str,
    password: String,
) -> Result<(cloud_api::VerifyRequest, [u8; 32]), Error> {
    let email = checked_email(email)?;
    if password.chars().count() < 8 {
        return Err(Error::Invalid("密码至少需要 8 位。"));
    }
    let code = code.trim();
    if code.len() != 6 || !code.bytes().all(|c| c.is_ascii_digit()) {
        return Err(Error::Invalid("请填写 6 位数字验证码。"));
    }
    let keys = keys(&email, password).await?;
    Ok((
        cloud_api::VerifyRequest {
            email,
            code: code.into(),
            login_key: keys.login,
        },
        keys.backup,
    ))
}

/// Another account's row goes first, taking its library progress and cloud backup state along.
async fn save_session(
    db: &SqlitePool,
    session: Session,
    backup_key: &[u8; 32],
) -> Result<State, Error> {
    let backup_key = backup_key.as_slice();
    let mut tx = db.begin().await?;
    sqlx::query!(
        "DELETE FROM account WHERE email <> ?",
        session.account.email
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        r#"INSERT INTO account (id, email, token, backup_key) VALUES (1, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET token = excluded.token, backup_key = excluded.backup_key"#,
        session.account.email,
        session.token,
        backup_key
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
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
    WebSocket(reqwest_websocket::Error),
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
            Self::WebSocket(error) => write!(f, "account websocket: {error}"),
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
            Self::Network(_) | Self::WebSocket(_) => {
                "连不上服务器，请检查网络和代理设置后重试。".into()
            }
            Self::SignedOut => "登录已失效，请重新登录。".into(),
            Self::AccountChanged => "要注销的账号已经不是当前登录的账号，请刷新后重试。".into(),
            Self::Server(error) => match error.code {
                ErrorCode::InvalidInput => "填写的账号信息不正确，请检查后重试。".into(),
                ErrorCode::EmailRegistered => "这个邮箱已注册，请直接登录。".into(),
                ErrorCode::EmailNotRegistered => "这个邮箱还没注册，请先注册。".into(),
                ErrorCode::InvalidCode => "验证码不对或已过期，请重新获取验证码。".into(),
                ErrorCode::InvalidCredentials => "邮箱或密码不对，请检查后重试。".into(),
                ErrorCode::RateLimited => match error.retry_after_seconds {
                    // A busy server asks for a few seconds; the limits per hour, for minutes.
                    Some(seconds @ ..60) => format!("请求太频繁，请 {seconds} 秒后再试。"),
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
                ErrorCode::SponsorRequired => "赞助已到期，云端备份已停止。".into(),
                ErrorCode::BackupNotFound => {
                    "云端的备份刚被另一台电脑替换或删除了，请刷新后重试。".into()
                }
                ErrorCode::BackupIncomplete | ErrorCode::BackupChanged => {
                    "另一台电脑同时在备份，这次没能传完，稍后会再试。".into()
                }
                ErrorCode::BackupTooLarge => {
                    "云端备份的空间暂时满了：没传完的数据一天内会清掉，之后再试。".into()
                }
                // Logging in to another account gives the computer a new room, so this is not
                // expected to happen.
                ErrorCode::RoomTaken => {
                    "手机远程的连接属于另一个账号，请导出日志发给开发者。".into()
                }
                ErrorCode::NotFound | ErrorCode::MethodNotAllowed => {
                    "服务器不支持这个操作，请升级应用后重试。".into()
                }
            },
        }
    }
}
